# -*- coding: utf-8 -*-
# core/intent_analyzer.py
# ==================== 意图分析与高级参数提取引擎（工业级完美版） ====================
# 核心架构创新：
# 1. 状态-意图白名单矩阵（State-Intent Matrix）：通过前置框架限制，让 LLM 在允许的意图内思考，彻底消灭硬编码关键词打地鼠。
# 2. 子句隔离与动词绑定：纯 NLP 参数提取，杜绝“提及即操作”的误触。
# 3. 话题切换平滑兜底：用户随时切回闲聊，系统自动清空参数并降级。
# 4. 三引擎动态融合：Rule + LLM + FewShot，带防哑火机制与后置边界校验。
# ==============================================================================

import re
import json
import functools
from typing import Dict, Any, Optional, List

from .config import call_ollama_safe, REQUEST_TIMEOUT
from .constants import INTENT_CONFIDENCE_THRESHOLD

# 中文数字映射表
CN_NUM_MAP = {
    '零': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5,
    '六': 6, '七': 7, '八': 8, '九': 9, '十': 10, '百': 100, '千': 1000
}

def _cn_to_int(cn_str: str) -> int:
    """中文数字转阿拉伯数字（支持一到九十九）"""
    if not cn_str: return 0
    if cn_str.isdigit(): return int(cn_str)
    total, temp = 0, 0
    for char in cn_str:
        if char in CN_NUM_MAP:
            val = CN_NUM_MAP[char]
            if val >= 10:
                if temp == 0: temp = 1
                total += temp * val
                temp = 0
            else:
                temp = val
    total += temp
    return total if total > 0 else 1


class IntentAnalyzer:
    """意图分析与参数提取引擎"""

    INTENTS = [
        "project_creation", "file_generation", "outline_generation", 
        "outline_modification", "outline_confirmation",
        "chapter_writing", "batch_generation", "continue_generation",
        "chapter_rewrite", "chapter_evaluation",
        "setting_discussion", "custom_setting", "chat"
    ]

    # 🌟 核心创新：状态-意图白名单矩阵 (State-Intent Matrix)
    # 物理框架限制：不同状态下，LLM 只能从白名单中选择意图，彻底避免无关意图的幻觉。
    STATE_INTENT_MATRIX = {
        "no_project": ["project_creation", "setting_discussion", "custom_setting", "chat"],
        # 修复：planned 态下用户明确说"创建工程/项目"应可触发 project_creation。
        # 原白名单缺 project_creation，导致 planned 态（AIController 初始化即为该态）
        # "创建工程"被强制降级 chat，项目从未创建，后续 task_id=None 全链断。
        "planned": ["project_creation", "outline_generation", "setting_discussion", "custom_setting", "chat"],
        "outline_generated": ["outline_confirmation", "outline_modification", "setting_discussion", "chat"],
        "in_progress": ["chapter_writing", "batch_generation", "continue_generation", "chapter_rewrite", "chapter_evaluation", "outline_modification", "setting_discussion", "chat"],
        "completed": ["chapter_rewrite", "chapter_evaluation", "setting_discussion", "chat"]
    }

    def __init__(self, log_callback=None):
        self._log = log_callback if callable(log_callback) else print
        self.rule_based_classifier = self._RuleBasedClassifier()
        self.llm_classifier = self._LLMClassifier(self._log)
        try:
            from .intent_classifier_fewshot import FewShotIntentClassifier
            self.fewshot_classifier = FewShotIntentClassifier(log_callback=self._log)
        except Exception:
            self.fewshot_classifier = None

    def _determine_system_state(self, ctx: dict) -> str:
        """根据上下文精准推断当前系统状态"""
        if not (ctx.get("active_project_id") or ctx.get("project_id")):
            return "no_project"
        task_status = ctx.get("task_status", "none")
        project_status = ctx.get("project_status", "none")
        
        if task_status == "outline_generated":
            return "outline_generated"
        if project_status == "in_progress" or ctx.get("outline_status") == "confirmed":
            return "in_progress"
        if project_status == "completed":
            return "completed"
        return "planned"

    # -----------------------------------------------------------------
    # 主入口：意图分析、边界校验与参数提取
    # -----------------------------------------------------------------
    def analyze_intent(self, user_prompt: str, context: dict = None) -> Dict[str, Any]:
        _ctx = context or {}
        current_state = self._determine_system_state(_ctx)
        allowed_intents = self.STATE_INTENT_MATRIX.get(current_state, ["chat"])
        
        self._log(f"🧠 [IntentAnalyzer] 当前系统状态: {current_state}, 允许意图白名单: {allowed_intents}")

        # 🌟 绝对防弹初始化
        rule_result, llm_result, llm_params, fewshot_result = {}, {}, {}, {}

        # 1. 三引擎并行分类（全部传入 allowed_intents 进行框架限制）
        try: 
            rule_result = self.rule_based_classifier.classify(user_prompt, _ctx, allowed_intents) or {}
        except Exception as e: 
            self._log(f"⚠️ 规则分类器异常: {e}", level="WARN")

        try:
            llm_raw = self.llm_classifier.classify(user_prompt, _ctx, current_state, allowed_intents)
            if isinstance(llm_raw, tuple) and len(llm_raw) == 2: 
                llm_result, llm_params = llm_raw
            else: 
                llm_result = llm_raw if isinstance(llm_raw, dict) else {}
        except Exception as e: 
            self._log(f"⚠️ LLM 分类器异常: {e}", level="WARN")

        if self.fewshot_classifier:
            try: 
                fewshot_result = self.fewshot_classifier.classify(user_prompt, _ctx) or {}
                # FewShot 也要受白名单限制，过滤掉不允许的意图
                fewshot_result = {k: v for k, v in fewshot_result.items() if k in allowed_intents}
            except Exception as e: 
                self._log(f"⚠️ FewShot 分类器异常: {e}", level="WARN")

        # 2. 动态加权融合
        rule_weight, llm_weight, fewshot_weight = 0.4, 0.4, 0.2
        fewshot_failed = False
        if self.fewshot_classifier and fewshot_result:
            uniform_val = 1.0 / len(self.INTENTS)
            if all(abs(v - uniform_val) < 0.05 for v in fewshot_result.values()):
                fewshot_failed = True
        if fewshot_failed:
            fewshot_weight, rule_weight, llm_weight = 0.0, 0.6, 0.4

        all_intents = set(rule_result.keys()) | set(llm_result.keys()) | set(fewshot_result.keys()) or set(self.INTENTS)
        fused_scores = {}
        for intent in all_intents:
            # 🌟 核心限制：如果意图不在白名单中，直接得分为 0
            if intent not in allowed_intents:
                fused_scores[intent] = 0.0
                continue
                
            r = rule_result.get(intent, 0.0)
            l = llm_result.get(intent, 0.0)
            f = fewshot_result.get(intent, 0.0) if not fewshot_failed else 0.0
            fused_scores[intent] = rule_weight * r + llm_weight * l + fewshot_weight * f

        if not fused_scores or max(fused_scores.values()) == 0.0:
            primary_intent, confidence, reasoning = "chat", 0.0, "无合法意图，默认聊天"
        else:
            primary_intent = max(fused_scores, key=fused_scores.get)
            confidence = fused_scores[primary_intent]
            reasoning = f"R:{rule_result.get(primary_intent, 0):.2f}, L:{llm_result.get(primary_intent, 0):.2f}, F:{fewshot_result.get(primary_intent, 0):.2f}"

        # 3. 后置边界校验矩阵（最后的安全网）
        clarification_msg = None
        is_project_active = current_state != "no_project"
        is_outline_confirmed = current_state in ["in_progress", "completed"]
        _outline_exists = current_state in ["outline_generated", "in_progress", "completed"]
        
        boundary_matrix = {
            "outline_generation": (is_project_active, "chat", "⚠️ 请先创建项目工程，再生成大纲。"),
            "outline_modification": (is_project_active and _outline_exists, "chat", "⚠️ 当前没有可修改的大纲。"),
            "outline_confirmation": (current_state == 'outline_generated', "chat", "⚠️ 当前没有待确认的大纲。"),
            "chapter_writing": (is_outline_confirmed, "chat", "⚠️ 请先确认大纲，然后才能开始写正文。"),
            "batch_generation": (is_outline_confirmed, "chat", "⚠️ 请先确认大纲，然后才能批量生成。"),
            "continue_generation": (is_outline_confirmed, "chat", "⚠️ 请先确认大纲，然后才能继续生成。"),
            "chapter_rewrite": (is_outline_confirmed, "chapter_writing", "⚠️ 大纲未确认，已降级为普通写作。"),
            "chapter_evaluation": (is_outline_confirmed, "chat", "⚠️ 请先完成项目创建和大纲确认，再评估章节。"),
        }

        if primary_intent in boundary_matrix:
            condition, fallback_intent, msg = boundary_matrix[primary_intent]
            if not condition:
                self._log(f"🛡️ [边界拦截] {primary_intent} 前提不满足，降级为 {fallback_intent}。", level="WARN")
                primary_intent = fallback_intent
                clarification_msg = msg
                confidence = 1.0 

        # 4. 条件触发的高级参数提取
        extracted_params = {}
        writing_intents = ["chapter_writing", "batch_generation", "continue_generation", "chapter_rewrite", "chapter_evaluation", "outline_modification"]
        
        if primary_intent in writing_intents:
            regex_params = self._extract_advanced_params(user_prompt)
            extracted_params = self._merge_params(regex_params, llm_params, primary_intent)

        # 5. 生成模式推断
        generation_mode = self._infer_generation_mode(user_prompt, primary_intent, extracted_params)
        needs_clarification = (confidence < INTENT_CONFIDENCE_THRESHOLD) or bool(clarification_msg)

        return self._build_result(primary_intent, confidence, reasoning, generation_mode, extracted_params, needs_clarification, clarification_msg)

    def _build_result(self, intent, conf, reason, mode, params, clarify=False, clarify_msg=None) -> Dict[str, Any]:
        return {
            "primary_intent": intent, "confidence": conf, "reasoning": reason,
            "generation_mode": mode, "extracted_params": params,
            "needs_clarification": clarify, "clarification_message": clarify_msg
        }

    # -----------------------------------------------------------------
    # 🌟 工业级高级参数提取引擎（子句隔离 + 动词绑定）
    # -----------------------------------------------------------------
    def _extract_advanced_params(self, user_prompt: str) -> Dict[str, Any]:
        params = {}
        text = user_prompt.lower()
        write_verbs = r'(?:写|生成|创作|续写|出|来|搞|弄|编写|产出)'
        rewrite_verbs = r'(?:重写|重新写|改写|覆盖|推翻|修改|调整|改|重做)'
        eval_verbs = r'(?:打分|评分|评估|评价|检查|审查|提取|总结|摘要|复盘|分析)'
        continue_verbs = r'(?:继续|接着|往下|恢复|接上)'
        all_action_verbs = f"(?:{write_verbs}|{rewrite_verbs}|{eval_verbs}|{continue_verbs})"
        num_pattern = r'([一二两三四五六七八九十百千\d]+)'
        clauses = re.split(r'[，,。.！!？?；;、\n]', text)
        
        target_chapters, batch_size, action_type = [], 0, None
        
        for clause in clauses:
            clause = clause.strip()
            if not clause: continue
            has_action = re.search(all_action_verbs, clause)
            if not has_action: continue 
            
            range_match = re.search(rf'(?:{all_action_verbs}.{{0,15}}?第?{num_pattern}[到至\-~]第?{num_pattern}章)|(?:第?{num_pattern}[到至\-~]第?{num_pattern}章.{{0,15}}?{all_action_verbs})', clause)
            if range_match:
                nums = [g for g in range_match.groups() if g]
                if len(nums) >= 2:
                    start, end = _cn_to_int(nums[0]), _cn_to_int(nums[1])
                    if start <= end: target_chapters.extend(range(start, end + 1))
                continue

            single_match = re.search(rf'(?:{all_action_verbs}.{{0,15}}?第?{num_pattern}章)|(?:第?{num_pattern}章.{{0,15}}?{all_action_verbs})', clause)
            if single_match:
                nums = [g for g in single_match.groups() if g]
                if nums: target_chapters.append(_cn_to_int(nums[0]))
                continue
                
            batch_match = re.search(rf'{write_verbs}.{{0,5}}?{num_pattern}[章个]', clause)
            if batch_match: batch_size = max(batch_size, _cn_to_int(batch_match.group(1)))
                
            if re.search(continue_verbs, clause) and not action_type: action_type = "continue"

        if re.search(rewrite_verbs, text): action_type = "rewrite"; params["force_overwrite"] = True
        elif re.search(eval_verbs, text): action_type = "evaluate" if any(kw in text for kw in ["打分", "评分", "评估", "评价", "检查"]) else "extract"
        elif target_chapters and not action_type: action_type = "write"
            
        if target_chapters: params["target_chapters"] = sorted(list(set(target_chapters)))
        if batch_size > 0: params["batch_size"] = batch_size
        if action_type: params["action_type"] = action_type
        return params

    def _merge_params(self, regex_params: Dict, llm_params: Dict, primary_intent: str) -> Dict[str, Any]:
        merged = {}
        for key in ["target_chapters", "batch_size", "action_type"]:
            if key in regex_params: merged[key] = regex_params[key]
            elif key in llm_params: merged[key] = llm_params[key]
            
        if "action_type" not in merged:
            if primary_intent == "chapter_rewrite": merged["action_type"] = "rewrite"
            elif primary_intent == "chapter_evaluation": merged["action_type"] = "evaluate"
            elif primary_intent == "continue_generation": merged["action_type"] = "continue"
            
        if merged.get("action_type") == "rewrite": merged["force_overwrite"] = True
        return merged

    # -----------------------------------------------------------------
    # 规则基础分类器 (受白名单限制)
    # -----------------------------------------------------------------
    class _RuleBasedClassifier:
        def __init__(self):
            self.intent_features = {
                "project_creation": {"keywords": ["新建项目", "创建项目", "建立项目", "初始化项目", "小说项目", "故事项目", "写作项目", "创作项目", "项目设置", "项目初始化", "项目框架", "项目结构", "创建工程", "创建工程文件", "建立工程", "新建工程"], "negative_keywords": ["先别", "不用", "不必", "只是聊聊", "探讨", "灵感", "脑洞", "构思", "先不建", "别急着", "帮我完善", "设定一下", "你觉得", "怎么样"], "weight": 1.0, "context_boost": {"no_active_project": 0.3, "planned_state": 0.2}},
                "file_generation": {"keywords": ["生成文件", "创建文件", "写入文件", "保存为", "输出文件", "生成.*文件", "写.*文件", "保存.*文件", "导出.*文件", "文本文件", "图片文件", "视频文件", "音频文件", "压缩文件", "脚本文件", "文档文件", "代码文件"], "negative_keywords": ["工程", "项目", "小说", "大纲", "世界观"], "weight": 1.0, "context_boost": {"no_active_project": 0.2, "file_operation": 0.3}},
                "outline_generation": {"keywords": ["生成大纲", "创建大纲", "写大纲", "大纲制定", "故事大纲", "情节大纲", "章节大纲", "框架大纲", "outline", "大纲生成"], "weight": 1.0, "context_boost": {"project_created": 0.3, "no_outline": 0.4}},
                "outline_modification": {"keywords": ["修改大纲", "调整大纲", "改大纲", "重写大纲", "大纲不行", "大纲太", "把大纲", "改一下大纲"], "weight": 1.2, "context_boost": {"outline_generated": 0.5}},
                "outline_confirmation": {"keywords": ["确认大纲", "大纲没问题", "就按这个写", "开始写正文", "确认", "没问题", "可以", "好的", "开始", "ok", "yes", "y", "行"], "weight": 1.5, "context_boost": {"outline_generated": 0.8}},
                "chapter_writing": {"keywords": ["写章节", "生成章节", "章节生成", "正文创作", "章节写作", "故事创作", "情节发展", "chapter writing", "写第.*章", "生成第.*章"], "weight": 1.0, "context_boost": {"outline_confirmed": 0.5, "in_progress": 0.3}},
                "chapter_rewrite": {"keywords": ["重写第.*章", "改写第.*章", "重新写第.*章", "覆盖第.*章", "推翻第.*章", "重写章节", "重新生成第"], "weight": 1.5, "context_boost": {"in_progress": 0.4}},
                "chapter_evaluation": {"keywords": ["给第.*章打分", "评估第.*章", "检查第.*章", "评价第.*章", "章节打分", "质量评估", "提取第.*章"], "weight": 1.2, "context_boost": {"in_progress": 0.3}},
                "batch_generation": {"keywords": ["批量生成", "一次性生成", "全量生成", "全部章节", "批量写作", "一键生成", "全文生成", "生成.*章", "写.*章"], "weight": 1.0, "context_boost": {"outline_confirmed": 0.4}},
                "continue_generation": {"keywords": ["继续生成", "恢复写作", "从中断点", "接上写", "断点续写", "继续创作", "恢复生成", "接着写", "往下写"], "weight": 1.0, "context_boost": {"in_progress": 0.5, "paused": 0.4}},
                "setting_discussion": {"keywords": ["讨论设定", "设定讨论", "世界观讨论", "角色讨论", "情节讨论", "背景讨论", "设定完善", "世界观建设", "setting discussion", "世界观讨论"], "boost_keywords": ["灵感", "脑洞", "构思", "探讨", "你觉得", "怎么样", "完善设定", "帮我看看", "评价一下", "有什么建议"], "weight": 1.0, "context_boost": {"project_created": 0.2, "no_outline": 0.1}},
                "custom_setting": {"keywords": ["自定义设定", "定制设定", "自定义项目", "定制项目", "自定义小说", "定制小说", "custom setting", "自定义需求", "定制需求"], "weight": 1.0, "context_boost": {"no_active_project": 0.3}},
                "chat": {"keywords": ["聊天", "对话", "交谈", "闲聊", "询问", "咨询", "了解", "解释", "说明", "建议", "意见", "看法", "hello", "hi", "hey", "我在", "我在吗", "测试"], "weight": 0.5, "context_boost": {}}
            }
        
        def classify(self, user_prompt: str, context: dict, allowed_intents: List[str]) -> Dict[str, float]:
            scores = {}
            user_prompt_lower = user_prompt.lower()
            context = context or {}
            for intent_name, features in self.intent_features.items():
                # 🌟 框架限制：如果不在白名单中，直接跳过
                if intent_name not in allowed_intents:
                    scores[intent_name] = 0.0
                    continue
                    
                score = 0.0
                if any(nk in user_prompt_lower for nk in features.get("negative_keywords", [])):
                    scores[intent_name] = 0.0; continue
                keyword_hits = 0
                for keyword in features["keywords"]:
                    if "*" in keyword or "." in keyword:
                        if re.search(keyword.replace("*", ".*").replace(".", "\\."), user_prompt_lower): keyword_hits += 1
                    else:
                        if keyword in user_prompt_lower: keyword_hits += 1
                keyword_score = (keyword_hits / len(features["keywords"])) if features["keywords"] else 0.0
                boost_hits = sum(1 for bk in features.get("boost_keywords", []) if bk in user_prompt_lower)
                if boost_hits > 0: keyword_score = min(1.0, keyword_score + boost_hits * 0.4)
                context_bonus = sum(bonus for ctx_key, bonus in features.get("context_boost", {}).items() if self._check_context(ctx_key, context, user_prompt))
                base_score = keyword_score * features["weight"] + context_bonus
                if keyword_hits > 0: base_score += 1.0 + (keyword_hits * 0.5)
                scores[intent_name] = max(0.0, min(4.0, base_score))
            return scores

        def _check_context(self, context_key: str, context: dict, user_prompt: str) -> bool:
            if context_key == "no_active_project": return not context.get("active_project_id", False)
            elif context_key == "planned_state": return context.get("project_status") == "planned"
            elif context_key == "project_created": return bool(context.get("project_id"))
            elif context_key == "no_outline": return not context.get("outline_exists", False)
            elif context_key == "outline_confirmed": return context.get("outline_status") == "confirmed" or context.get("project_status") == "in_progress"
            elif context_key == "outline_generated": return context.get("task_status") == "outline_generated"
            elif context_key == "in_progress": return context.get("project_status") == "in_progress"
            elif context_key == "paused": return context.get("project_status") == "paused"
            elif context_key == "file_operation": return any(op in user_prompt for op in ["读取", "写入", "保存", "导出", "生成", "创建", "文件"])
            return False

    # -----------------------------------------------------------------
    # LLM语义理解分类器 (受白名单严格限制)
    # -----------------------------------------------------------------
    class _LLMClassifier:
        def __init__(self, log_callback=None):
            self._log = log_callback if callable(log_callback) else print
            
        def classify(self, user_prompt: str, context: dict, current_state: str, allowed_intents: List[str]) -> tuple:
            intent_list = IntentAnalyzer.INTENTS
            allowed_str = ", ".join(allowed_intents)
            
            system_prompt = f"""你是一个严谨的意图分类专家。
【当前系统状态】：{current_state}
【允许的意图白名单】：[{allowed_str}]

【绝对铁律】：
1. 你**只能且必须**从【允许的意图白名单】中选择意图！绝对禁止输出白名单之外的任何意图！
2. 如果用户的输入是闲聊、抱怨、或者与白名单无关的操作，必须分类为 `chat` 或 `setting_discussion`（如果在白名单中）。
3. 如果用户要求写作，但当前状态不允许（如大纲未确认），你必须分类为 `chat`。

【意图定义】：
- project_creation: 创建新项目/工程。
- outline_generation: 生成大纲。
- outline_confirmation: 确认大纲，准备开始写作（如“确认”、“没问题”、“开始写”）。
- outline_modification: 修改/调整已生成的大纲。
- chapter_writing: 写作指定的章节（如“写第3章”）。
- batch_generation: 批量生成多章（如“写3章”、“一次性写两章”）。
- continue_generation: 从断点继续生成。
- chapter_rewrite: 强制重写已存在的章节。
- chapter_evaluation: 对章节进行打分/评估。
- setting_discussion: 讨论世界观/设定/灵感/伏笔。
- chat: 纯闲聊或无法归类的操作。

请返回 JSON：{{"intent_type": "意图", "confidence": 0.95, "extracted_params": {{"target_chapters": [], "batch_size": null, "action_type": ""}}}}
不要返回任何其他文字！"""

            user_content = f"用户输入：{user_prompt}"
            messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_content}]
            default_scores = {intent: 1.0 / len(intent_list) for intent in intent_list}
            
            try:
                response = call_ollama_safe(messages, timeout=REQUEST_TIMEOUT, options={"temperature": 0.1, "num_predict": 300})
                if not response.get("success"): return default_scores, {}
                raw = re.sub(r'^```json\s*', '', response.get("content", "").strip(), flags=re.IGNORECASE)
                raw = re.sub(r'\s*```$', '', raw).strip()
                match = re.search(r'\{.*\}', raw, re.DOTALL)
                if match:
                    result = json.loads(match.group())
                    scores = {intent: 0.0 for intent in intent_list}
                    identified_intent = result.get("intent_type", "chat")
                    
                    # 🌟 框架限制：如果 LLM 幻觉输出了白名单外的意图，强制降级为 chat
                    if identified_intent not in allowed_intents:
                        self._log(f"⚠️ LLM 输出了白名单外的意图 {identified_intent}，强制降级为 chat。", level="WARN")
                        identified_intent = "chat" if "chat" in allowed_intents else allowed_intents[0]
                        
                    scores[identified_intent] = float(result.get("confidence", 0.9))
                    total = sum(scores.values())
                    if total > 0: scores = {k: v/total for k, v in scores.items()}
                    return scores, result.get("extracted_params", {}) if isinstance(result.get("extracted_params"), dict) else {}
            except Exception as e:
                self._log(f"⚠️ LLM 意图分类异常: {e}", level="WARN")
            return default_scores, {}

    # -----------------------------------------------------------------
    # 生成模式推断
    # -----------------------------------------------------------------
    def _infer_generation_mode(self, user_prompt: str, primary_intent: str, params: Dict) -> str:
        if params.get("batch_size") and params["batch_size"] > 1: return "batch"
        if params.get("target_chapters") and len(params["target_chapters"]) > 1: return "batch"
        if primary_intent == "batch_generation" or any(kw in user_prompt for kw in ["批量", "一次性", "全部", "全量", "一键", "所有"]): return "batch"
        if primary_intent == "continue_generation" or any(kw in user_prompt for kw in ["继续", "恢复", "断点", "接上", "后续"]): return "continue"
        return "all"

def analyze_intent(user_prompt: str, context: dict = None) -> Dict[str, Any]:
    return IntentAnalyzer().analyze_intent(user_prompt, context)
