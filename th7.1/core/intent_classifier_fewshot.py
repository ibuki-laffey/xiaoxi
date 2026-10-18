# -*- coding: utf-8 -*-
# core/intent_classifier_fewshot.py
import json
import re
import functools
from typing import Dict, Any, Optional, List

from .config import call_ollama_safe, REQUEST_TIMEOUT
from .constants import INTENT_CONFIDENCE_THRESHOLD

try:
    from .constants import FEW_SHOT_EXAMPLES_PER_INTENT
except ImportError:
    FEW_SHOT_EXAMPLES_PER_INTENT = 5

class FewShotIntentClassifier:
    INTENTS = [
        "project_creation", "file_generation", "outline_generation", "outline_modification",
        "chapter_writing", "batch_generation", "continue_generation", "chapter_rewrite",
        "chapter_evaluation", "setting_discussion", "custom_setting", "chat"
    ]

    DEFAULT_EXAMPLES: Dict[str, List[str]] = {
        "project_creation": ["我想创建一个名为《星际漫游》的科幻小说项目。", "请帮我新建一个奇幻世界观的长篇小说。"],
        "file_generation": ["请在目录下生成一个名为 meeting.txt 的纯文本文件。", "帮我写入一个叫 data.csv 的文件。"],
        "outline_generation": ["请为我刚才提到的小说生成大纲。", "根据上面的设定，制作章节大纲。"],
        "outline_modification": ["大纲不太行，把第二章的剧情改一下。", "调整一下大纲，主角出场太晚了。"],
        "chapter_writing": ["请继续写作第一章。", "根据大纲，写出第一章的开头场景。"],
        "batch_generation": ["请一次性生成所有章节的内容。", "批量输出全文，不用分章。"],
        "continue_generation": ["从第 10 章继续写作。", "我暂停了，现在请从断点继续。"],
        "chapter_rewrite": ["第3章写得太烂了，完全重写。", "把第2章推翻重写，细节不够。"],
        "chapter_evaluation": ["给第2章打个分。", "评估一下第4章的质量，提取核心伏笔。"],
        "setting_discussion": ["我们来讨论一下这个世界的魔法体系。", "谈谈故事发生的时代背景。"],
        "custom_setting": ["我想自定义一个小说设定。", "定制一个带有时间旅行元素的科幻故事。"],
        "chat": ["你好，今天天气怎么样？", "你能讲一个笑话吗？"]
    }

    def __init__(self, log_callback=None, examples: Optional[Dict[str, List[str]]] = None):
        self._log = log_callback if callable(log_callback) else print
        self.examples = examples if examples is not None else self.DEFAULT_EXAMPLES
        self._cached_classify = functools.lru_cache(maxsize=256)(self._classify_uncached)

    def classify(self, user_prompt: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, float]:
        examples_key = tuple((k, tuple(v)) for k, v in sorted(self.examples.items())) if self.examples else None
        return self._cached_classify(user_prompt, examples_key)

    def _classify_uncached(self, user_prompt: str, examples_key: Optional[tuple]) -> Dict[str, float]:
        prompt = self._build_fewshot_prompt(user_prompt)
        try:
            response = call_ollama_safe(
                [{"role": "system", "content": prompt}, {"role": "user", "content": user_prompt}],
                timeout=REQUEST_TIMEOUT, options={"temperature": 0.1, "num_predict": 250}
            )
            if isinstance(response, dict) and not response.get("content", "").strip():
                self._log("[FewShotIntentClassifier] LLM 首次返回为空，触发重试...", level="WARN")
                response = call_ollama_safe(
                    [{"role": "system", "content": prompt}, {"role": "user", "content": user_prompt}],
                    timeout=REQUEST_TIMEOUT, options={"temperature": 0.3, "num_predict": 250}
                )
        except Exception as exc:
            self._log(f"[FewShotIntentClassifier] LLM 调用异常，回退均匀分布: {exc}", level="WARN")
            uniform = 1.0 / len(self.INTENTS)
            return {intent: uniform for intent in self.INTENTS}

        raw_content = ""
        if isinstance(response, dict):
            if not response.get("success"):
                uniform = 1.0 / len(self.INTENTS)
                return {intent: uniform for intent in self.INTENTS}
            raw_content = response.get("content", "")
        else:
            raw_content = str(response)

        raw_content = str(raw_content).strip()
        parsed = {}
        try:
            parsed = json.loads(raw_content)
        except Exception:
            match = re.search(r"\{.*\}", raw_content, re.DOTALL)
            if match:
                try: parsed = json.loads(match.group())
                except Exception: pass

        intent_name = str(parsed.get("intent", "")).strip()
        try: confidence = max(0.0, min(1.0, float(parsed.get("confidence", 0.0))))
        except Exception: confidence = 0.0

        scores = {intent: 0.0 for intent in self.INTENTS}
        if intent_name in self.INTENTS:
            scores[intent_name] = confidence
        else:
            self._log("[FewShotIntentClassifier] LLM 返回空或解析失败，降级为规则分类器兜底。", level="WARN")
            try:
                from .intent_analyzer import IntentAnalyzer
                rule_scores = IntentAnalyzer._RuleBasedClassifier().classify(user_prompt, context={})
                if rule_scores and max(rule_scores.values()) > 0.1: return rule_scores
            except Exception: pass
            uniform = 1.0 / len(self.INTENTS)
            return {intent: uniform for intent in self.INTENTS}
        return scores

    def _build_fewshot_prompt(self, user_prompt: str) -> str:
        header = "你是一个意图分类专家。请根据用户输入判断其意图类型，并给出置信度（0-1之间的小数）。\n请只返回 JSON 格式：{\"intent\": \"意图名称\", \"confidence\": 0.xx}\n\n"
        sections = []
        for intent in self.INTENTS:
            examples = self.examples.get(intent, [])[:FEW_SHOT_EXAMPLES_PER_INTENT]
            example_str = "\n".join(f"- {ex}" for ex in examples) if examples else "- （暂无示例）"
            sections.append(f"{intent}：\n{example_str}")
        body = "\n\n".join(sections)
        footer = f"\n\n现在请判断以下用户输入所属的意图类型：\n用户输入：\"{user_prompt}\"\n请只返回 JSON："
        return header + body + footer
