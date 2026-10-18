# core/outline_detail_filler.py
import os
import json
import re
import copy
import time
from typing import Dict, Any, List
from .config import TASK_TRACKING_DIR, MODEL_NAME, OUTLINE_TEMPERATURES
from .log_mixin import LogMixin
from .vector_memory import VectorMemory

class OutlineDetailFiller(LogMixin):
    def __init__(self, log_callback=None):
        self.log_callback = log_callback
        self.vm = VectorMemory(log_callback=log_callback)
        self.outline_dir = os.path.join(TASK_TRACKING_DIR, "outlines")
        os.makedirs(self.outline_dir, exist_ok=True)

    def _call_ollama(self, prompt: str, temperature: float, model_name: str = None) -> Dict[str, Any]:
        from .llm_utils import llm_call, MAX_JSON_TOKENS
        use_model = model_name or MODEL_NAME
        try:
            resp = llm_call(user_prompt=prompt, system_prompt="", temperature=temperature, max_tokens=MAX_JSON_TOKENS, enforce_json=True)
            if not resp.get("success", False): raise RuntimeError(resp.get("error", "未知错误"))
            content = resp.get("content")
            if not isinstance(content, (dict, list)): raise ValueError("模型返回的 content 不是合法 JSON 对象")
            return content
        except Exception as e:
            raise RuntimeError(f"Ollama 调用失败 (model={use_model}): {e}")

    def _call_llm_for_subtask(self, subtask: Dict[str, Any], task_plan: Dict[str, Any], temperature: float) -> Dict[str, Any]:
        from .llm_utils import llm_call, MAX_JSON_TOKENS
        prompt = self._build_subtask_prompt(subtask, task_plan)
        resp = llm_call(user_prompt=prompt, system_prompt="", temperature=temperature, max_tokens=MAX_JSON_TOKENS, enforce_json=True)
        if not resp.get("success", False): raise RuntimeError(resp.get("error", "LLM 调用失败"))
        content = resp.get("content")
        if not isinstance(content, (dict, list)): raise ValueError("LLM 返回的 content 不是合法 JSON")
        return content

    # 🌟 致命修复：补全缺失的 _build_subtask_prompt 方法
    def _build_subtask_prompt(self, subtask: Dict[str, Any], task_plan: Dict[str, Any]) -> str:
        chapter_title = subtask.get("title", "未知章节")
        chapter_num = subtask.get("chapter_num", 0)
        genre = task_plan.get("genre", "通用")
        main_char = task_plan.get("main_character", "主角")
        return f"""请为小说《{task_plan.get('title', '未命名')}》的第 {chapter_num} 章（标题：{chapter_title}）生成详细的剧情大纲。
题材：{genre}，主角：{main_char}。
要求：生成 150-300 字的详细 summary，提取 3-5 个核心 keywords。
只返回纯 JSON：{{"summary": "...", "keywords": ["...", "..."]}}。绝不输出其他文字！"""

    def _try_main_llm(self, task_plan: Dict[str, Any], skeleton: Dict[str, Any]) -> Dict[str, Any]:
        prompt = self._build_prompt(task_plan, skeleton)
        for idx, temp in enumerate(OUTLINE_TEMPERATURES):
            try:
                self._log(f"🧠 大纲 LLM 第 {idx + 1}/{len(OUTLINE_TEMPERATURES)} 次 (temp={temp})")
                result = self._call_ollama(prompt, temperature=temp)
                if isinstance(result, dict) and "chapters" in result: return {"success": True, "outline": result}
                raise ValueError("返回缺少 `chapters` 字段")
            except Exception as exc:
                self._log(f"⚠️ LLM 第 {idx + 1} 次失败: {exc}")
                time.sleep(1)
        raise RuntimeError("主 LLM 失败")

    def _try_secondary_model(self, task_plan: Dict[str, Any], skeleton: Dict[str, Any]) -> Dict[str, Any]:
        prompt = self._build_prompt(task_plan, skeleton)
        try:
            result = self._call_ollama(prompt, temperature=0.4, model_name="gemma2:latest")
            if isinstance(result, dict) and "chapters" in result: return {"success": True, "outline": result}
            raise ValueError("返回缺少 `chapters` 字段")
        except Exception as exc:
            raise RuntimeError("次模型失败") from exc

    def _try_vector_retrieval(self, task_plan: Dict[str, Any]) -> Dict[str, Any]:
        query = {"genre": task_plan.get("genre", ""), "total_chapters": task_plan.get("total_chapters", 1)}
        candidates = self.vm.search_content(json.dumps(query), limit=3)
        for cand in candidates:
            outline_path = os.path.join(self.outline_dir, f"outline_machine_{cand['content_id']}.json")
            if os.path.exists(outline_path):
                try:
                    with open(outline_path, "r", encoding="utf-8") as f: outline = json.load(f)
                    if "chapters" in outline: return {"success": True, "outline": outline}
                except Exception: continue
        raise RuntimeError("向量检索失败")

    def _fallback_rule_based(self, task_plan: Dict[str, Any], skeleton: Dict[str, Any]) -> Dict[str, Any]:
        outline = copy.deepcopy(skeleton)
        for chap in outline.get("chapters", []):
            chap["summary"] = f"{chap.get('title', '章节')} 的初步概要（待后期完善）。"
            chap["keywords"] = [task_plan.get("genre", "通用")]
        self._log("✅ 使用规则兜底生成最小合法大纲")
        return {"success": True, "outline": outline}

    def fill_outline(self, task_plan: Dict[str, Any], skeleton_path: str) -> Dict[str, Any]:
        try:
            with open(skeleton_path, "r", encoding="utf-8") as f: skeleton = json.load(f)
        except Exception as e:
            return {"success": False, "error": str(e)}

        # 🌟 优化：统一捕获异常，防止 LogMixin 的 _log 方法不支持 level 参数导致崩溃
        for step, func in enumerate([self._try_main_llm, self._try_secondary_model, self._try_vector_retrieval, self._fallback_rule_based], 1):
            try:
                if step == 4: return func(task_plan, skeleton)
                return func(task_plan, skeleton) if step != 3 else func(task_plan)
            except Exception as e:
                self._log(f"⚠️ 路径 {step} 失败: {e}")
        return {"success": False, "error": "所有路径均失败"}

    def _build_prompt(self, task_plan: Dict[str, Any], skeleton: Dict[str, Any]) -> str:
        return f"""请生成完整大纲，只返回 JSON：
{{"title": "{skeleton.get('title', '未命名')}", "genre": "{skeleton.get('genre', '通用')}", "total_chapters": {skeleton.get('total_chapters')}, "words_per_chapter": {skeleton.get('words_per_chapter')}, "chapters": [{{"title": "第 1 章", "summary": "150-300字", "keywords": ["k1","k2"]}}]}}
题材：{task_plan.get('genre')}，主角：{task_plan.get('main_character')}。直接返回 JSON！"""

    def write_outline(self, task_id: str, outline: Dict[str, Any]) -> Dict[str, Any]:
        machine_path = os.path.join(self.outline_dir, f"outline_machine_{task_id}.json")
        human_path = os.path.join(self.outline_dir, f"总体大纲_人类版_{task_id}.txt")
        tmp_machine = f"{machine_path}.tmp"
        tmp_human = f"{human_path}.tmp"
        try:
            with open(tmp_machine, "w", encoding="utf-8") as f: 
                json.dump(outline, f, ensure_ascii=False, indent=2)
                f.flush(); os.fsync(f.fileno())
            with open(tmp_human, "w", encoding="utf-8") as f:
                f.write(f"书名：{outline.get('title')}\n总章节：{outline.get('total_chapters')}\n")
                for idx, chap in enumerate(outline.get("chapters", []), 1):
                    f.write(f"{idx}. {chap.get('title')} – {chap.get('summary', '')[:100]}…\n")
                f.flush(); os.fsync(f.fileno())
                
            os.replace(tmp_machine, machine_path)
            os.replace(tmp_human, human_path)
            return {"success": True, "machine_path": machine_path, "human_path": human_path}
        except Exception as exc:
            return {"success": False, "error": str(exc)}
