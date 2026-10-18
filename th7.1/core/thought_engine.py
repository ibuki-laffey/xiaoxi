import os
import json
import re
import hashlib
import threading
from datetime import datetime
from typing import Dict, Any, List, Optional

from .config import (
    OLLAMA_URL, MODEL_NAME, SAFE_TIMEOUT, UNEXPECTED_DEMAND_ENABLED,
    DEMAND_IMPACT_THRESHOLD, INTERNAL_LOG_ENABLED, PROFESSIONAL_LOG_ENABLED,
    AI_AUTONOMY_LEVEL, call_ollama_safe
)

class _BaseLogger:
    """内部统一日志实现（支持结构化 JSONL）。"""
    def __init__(self, log_callback=None, module_name: str = "ThoughtEngine"):
        self.log_callback = log_callback
        self.module_name = module_name

    def _log(self, msg: str, level: str = "INFO") -> None:
        if not self.log_callback: return
        ts = datetime.now().isoformat()
        if PROFESSIONAL_LOG_ENABLED:
            entry = {"timestamp": ts, "level": level, "module": self.module_name, "message": msg}
            msg = json.dumps(entry, ensure_ascii=False)
        else:
            msg = f"[{ts}] [{level}] {msg}"
        try: self.log_callback(msg, level=level)
        except TypeError: self.log_callback(msg)

    def info(self, msg): self._log(msg, "INFO")
    def warn(self, msg): self._log(msg, "WARN")
    def error(self, msg): self._log(msg, "ERROR")
    def debug(self, msg): self._log(msg, "DEBUG")

def _robust_extract_json(text: str) -> Optional[dict]:
    """🌟 工业级 JSON 提取器。"""
    if not text: return None
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE).strip()
    start_idx = text.find('{')
    end_idx = text.rfind('}')
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        json_str = text[start_idx:end_idx + 1]
        json_str = re.sub(r",\s*([}\]])", r"\1", json_str)
        try: return json.loads(json_str)
        except json.JSONDecodeError: pass
    try: return json.loads(text)
    except json.JSONDecodeError: return None

def _call_model(prompt: str, temperature: float = 0.3, max_predict: int = 4096, require_json: bool = True) -> Dict[str, Any]:
    """统一调用 Ollama，强制 JSON 模式 + 防弹提取。"""
    try:
        sys_content = "你是一个底层的 JSON 数据审查 API。你只输出合法的、结构严谨的纯 JSON 字符串，绝不输出任何废话、解释或 Markdown 标记。" if require_json else "你是一个专业的 AI 助手。"
        options = {"temperature": temperature, "num_predict": max_predict}
        if require_json: options["format"] = "json"
        
        resp = call_ollama_safe(
            [{"role": "system", "content": sys_content}, {"role": "user", "content": prompt}],
            timeout=SAFE_TIMEOUT, options=options
        )
        if not resp.get("success"): return {"error": resp.get("error", "Unknown error")}
        content = resp.get("content", "").strip()
    except Exception as e:
        return {"error": str(e)}

    if not require_json: return {"raw_response": content}
    parsed = _robust_extract_json(content)
    if parsed is not None: return parsed
    return {"raw_response": content, "error": "JSON 解析彻底失败"}

class ThoughtEngine:
    """统一思考引擎：集中实现 ReasoningEngine 与 ReconciliationEngine 的全部功能。"""
    def __init__(self, log_callback=None, controller=None):
        self.logger = _BaseLogger(log_callback, module_name="ThoughtEngine")
        self.controller = controller

        self.unexpected_demand_enabled = UNEXPECTED_DEMAND_ENABLED
        self.demand_impact_threshold = DEMAND_IMPACT_THRESHOLD
        self.ai_autonomy_level = AI_AUTONOMY_LEVEL
        self.internal_log_enabled = INTERNAL_LOG_ENABLED

        self.setting_priority_cache: Dict[str, Dict[str, Any]] = {}
        self.reasoning_log_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "reasoning_logs")
        os.makedirs(self.reasoning_log_dir, exist_ok=True)
        
        self._jsonl_lock = threading.Lock()
        self.logger.info("✅ ThoughtEngine 已初始化（统一版）")

    def _safe_truncate(self, text: str, max_len: int = 500) -> str:
        """安全截断文本，保证不会从句子中间腰斩。"""
        if not text or len(text) <= max_len: return text or "未知"
        truncated = text[:max_len]
        for sep in ['。', '！', '？', '\n', '.']:
            last_idx = truncated.rfind(sep)
            if last_idx > max_len * 0.5: return truncated[:last_idx + 1]
        return truncated + "..."

    def _atomic_write_json(self, filepath: str, data: Any) -> None:
        """🌟 工业级原子写入 JSON。"""
        tmp_path = filepath + ".tmp"
        try:
            dir_name = os.path.dirname(filepath)
            if dir_name: os.makedirs(dir_name, exist_ok=True)
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, filepath)
        except Exception as e:
            if os.path.exists(tmp_path):
                try: os.remove(tmp_path)
                except Exception: pass
            self.logger.warn(f"原子写入失败 ({filepath}): {e}")

    def _save_reasoning_log(self, reasoning_id: str, data: Dict[str, Any]) -> None:
        log_file = os.path.join(self.reasoning_log_dir, f"{reasoning_id}.json")
        self._atomic_write_json(log_file, data)

    def analyze_request(self, user_request: str, project_id: Optional[str] = None) -> Dict[str, Any]:
        reasoning_id = f"reason_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
        prompt = f"""你是一个智能助手，请分析用户的请求。
用户请求：{user_request} | 当前项目：{project_id or '无'}
返回 JSON：{{"action_type": "project_creation/auto_generation/chapter_writing/health_check/other", "requires_research": false, "requires_planning": false, "complexity_level": "low", "estimated_time_minutes": 0, "potential_risks": [], "recommended_approach": "方案", "autonomy_decision": false}}"""
        result = _call_model(prompt)
        self._save_reasoning_log(reasoning_id, {"user_request": user_request, "project_id": project_id, "thinking_result": result, "timestamp": datetime.now().isoformat()})
        if "error" in result: return {"action_type": "other", "error": result["error"]}
        return result

    def analyze_project_type(self, user_request: str) -> Dict[str, Any]:
        prompt = f"""分析小说创作需求。用户请求：{user_prompt}
返回 JSON：{{"world_type": "single", "genre": "题材", "total_chapters": 0, "words_per_chapter": 0, "style": "风格", "special_requirements": [], "potential_world_settings": [], "blueprint_issues": []}}"""
        result = _call_model(prompt)
        if "error" in result: return {"world_type": "single", "genre": "通用"}
        return result

    def validate_project_blueprint(self, user_request: str, project_analysis: Dict[str, Any]) -> Dict[str, Any]:
        validation = {"status": "valid", "issues": [], "warnings": [], "suggestions": [], "validated_at": datetime.now().isoformat(), "stored_for_learning": True}
        total = project_analysis.get("total_chapters", 0)
        words = project_analysis.get("words_per_chapter", 0)

        if total > 5000:
            validation["warnings"].append(f"章节数({total}) 极多")
            validation["suggestions"].append("建议按卷拆分管理")
        if words < 200:
            validation["warnings"].append(f"每章字数({words}) 极少")
            validation["suggestions"].append("建议合并为'每节'生成")

        conflict_prompt = f"""判断小说设定是否存在冲突。用户请求：{user_request} | 项目分析：{json.dumps(project_analysis, ensure_ascii=False)}
返回 JSON：{{"has_conflicts": false, "conflicts": [], "feasibility_score": 100, "suggestions": []}}"""
        try:
            ai_res = _call_model(conflict_prompt)
            if ai_res.get("has_conflicts"):
                validation["issues"].extend(ai_res.get("conflicts", []))
                validation["status"] = "invalid"
            validation["feasibility_score"] = ai_res.get("feasibility_score", 100)
            validation["suggestions"].extend(ai_res.get("suggestions", []))
        except Exception: pass

        if self.internal_log_enabled: self._save_blueprint_validation_log(user_request, project_analysis, validation)
        return validation

    def _save_blueprint_validation_log(self, user_request: str, project_analysis: Dict[str, Any], validation: Dict[str, Any]) -> None:
        file_path = os.path.join(self.reasoning_log_dir, "blueprint_validations.jsonl")
        entry = {"timestamp": datetime.now().isoformat(), "user_request": user_request[:500], "project_analysis": project_analysis, "validation": validation}
        with self._jsonl_lock:
            try:
                with open(file_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                    f.flush(); os.fsync(f.fileno())
            except Exception as e: self.logger.warn(f"写入蓝图日志失败: {e}")

    def generate_health_check_fix_plan(self, task_id: str, current_chapter: int, check_result: Dict[str, Any]) -> Dict[str, Any]:
        if not check_result.get("errors") and not check_result.get("warnings"): return {"success": True, "message": "无需修复"}
        prompt = f"""健康检查结果：{json.dumps(check_result, ensure_ascii=False, indent=2)}
返回修复方案 JSON：{{"problem_analysis": [], "fix_plans": [{{"plan_id": "A", "name": "名称", "description": "描述", "pros": [], "cons": [], "effort": "low", "recommended": true}}], "recommended_plan": "A", "requires_user_confirmation": false, "auto_execute_possible": true, "reasoning": "依据"}}"""
        result = _call_model(prompt)
        if self.internal_log_enabled: self._save_health_check_fix_log(task_id, current_chapter, check_result, result)
        if "error" in result: return {"success": False, "error": result["error"]}
        return result

    def _save_health_check_fix_log(self, task_id: str, chapter: int, check_result: Dict[str, Any], fix_plan: Dict[str, Any]) -> None:
        file_path = os.path.join(self.reasoning_log_dir, "health_check_fixes.jsonl")
        entry = {"timestamp": datetime.now().isoformat(), "task_id": task_id, "chapter": chapter, "check_result": check_result, "fix_plan": fix_plan}
        with self._jsonl_lock:
            try:
                with open(file_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                    f.flush(); os.fsync(f.fileno())
            except Exception as e: self.logger.warn(f"写入健康检查修复日志失败: {e}")

    def handle_unexpected_demand(self, user_request: str, current_state: Dict[str, Any]) -> Dict[str, Any]:
        if not self.unexpected_demand_enabled: return {"success": False, "error": "意外需求处理已关闭"}
        analysis = self._analyze_new_demand(user_request, current_state)
        impact = self._assess_demand_impact(analysis, current_state)
        plans = self._generate_coordination_plans(analysis, impact)
        requires_confirm = impact.get("impact_score", 0) > self.demand_impact_threshold
        log_id = self._log_demand_handling(user_request, analysis, impact, plans)
        return {"success": True, "analysis": analysis, "impact_assessment": impact, "coordination_plans": plans, "requires_confirmation": requires_confirm, "handling_log_id": log_id}

    def _analyze_new_demand(self, user_request: str, current_state: Dict[str, Any]) -> Dict[str, Any]:
        world_setting = self._safe_truncate(str(current_state.get('world_setting', '')), max_len=800)
        prompt = f"""分析新设定需求。用户需求：{user_request} | 当前状态：已完成{current_state.get('completed_chapters',0)}章，总{current_state.get('total_chapters',0)}章，世界观：{world_setting}
返回 JSON：{{"demand_type": "supplement", "relation_to_existing": "关系", "impact_scope": "all", "is_world_reversal": false, "confidence": 0.5}}"""
        res = _call_model(prompt)
        if "error" in res: return {"demand_type": "unknown", "relation_to_existing": "unknown", "impact_scope": "all", "is_world_reversal": False, "confidence": 0.5}
        return res

    def _assess_demand_impact(self, analysis: Dict[str, Any], current_state: Dict[str, Any]) -> Dict[str, Any]:
        prompt = f"""评估影响。需求分析：{json.dumps(analysis, ensure_ascii=False)} | 状态：已完成{current_state.get('completed_chapters',0)}章，大纲：{current_state.get('outline_status','unknown')}
返回 JSON：{{"chapters_to_modify": [], "character_impact": "medium", "world_impact": "medium", "plot_impact": "medium", "impact_score": 0.5, "requires_regeneration": false}}"""
        res = _call_model(prompt)
        if "error" in res: return {"chapters_to_modify": [], "character_impact": "medium", "world_impact": "medium", "plot_impact": "medium", "impact_score": 0.5, "requires_regeneration": False}
        return res

    def _generate_coordination_plans(self, analysis: Dict[str, Any], impact: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [
            {"plan_id": "A", "name": "保持前文，后续铺垫", "description": "已完成章节不改", "pros": ["省时"], "cons": ["可能不一致"], "effort": "low", "recommended_for": ["影响小"]},
            {"plan_id": "B", "name": "微调前文，埋伏笔", "description": "微调已完成章节", "pros": ["连贯"], "cons": ["需修改"], "effort": "medium", "recommended_for": ["中等影响"]},
            {"plan_id": "C", "name": "重新生成前文", "description": "重新生成受影响章节", "pros": ["一致"], "cons": ["耗时"], "effort": "high", "recommended_for": ["高影响"]}
        ]

    def _log_demand_handling(self, user_request: str, analysis: Dict[str, Any], impact: Dict[str, Any], plans: List[Dict[str, Any]]) -> str:
        log_id = hashlib.md5(f"{user_request}{datetime.now()}".encode()).hexdigest()[:16]
        entry = {"log_id": log_id, "user_request": user_request, "analysis": analysis, "impact_assessment": impact, "coordination_plans": plans, "timestamp": datetime.now().isoformat()}
        file_path = os.path.join(self.reasoning_log_dir, f"demand_{log_id}.json")
        self._atomic_write_json(file_path, entry)
        return log_id

    def set_setting_priority(self, setting_id: str, priority: int, reason: str = "") -> None:
        self.setting_priority_cache[setting_id] = {"priority": priority, "reason": reason, "set_at": datetime.now().isoformat()}

    def get_setting_priority(self, setting_id: str) -> int:
        return self.setting_priority_cache.get(setting_id, {"priority": 5})["priority"]

    def check_setting_conflict(self, new_setting: Dict[str, Any], existing_settings: List[Dict[str, Any]]) -> Dict[str, Any]:
        conflicts = []
        for existing in existing_settings:
            if existing.get("name") == new_setting.get("name") and existing.get("world_id") == new_setting.get("world_id"):
                conflicts.append({"type": "name_conflict", "setting_id": existing.get("id"), "severity": "high"})
            if self._check_logical_conflict(existing, new_setting):
                conflicts.append({"type": "logical_conflict", "setting_id": existing.get("id"), "severity": "medium"})
            existing_pri = self.get_setting_priority(existing.get("id", ""))
            new_pri = new_setting.get("priority", 5)
            if existing_pri > new_pri and self._is_incompatible(existing, new_setting):
                conflicts.append({"type": "priority_conflict", "setting_id": existing.get("id"), "severity": "low", "message": f"高优先级({existing_pri}) 与低优先级({new_pri}) 冲突"})
        return {"has_conflicts": len(conflicts) > 0, "conflicts": conflicts, "conflict_count": len(conflicts)}

    def _check_logical_conflict(self, existing: Dict, new: Dict) -> bool:
        forbidden = [(["低武", "武侠"], ["核武器", "高科技"]), (["古代", "封建"], ["手机", "电脑"]), (["魔法世界"], ["科学解释"])]
        e_tags, n_tags = existing.get("tags", []), new.get("tags", [])
        for g1, g2 in forbidden:
            if (any(t in e_tags for t in g1) or any(t in n_tags for t in g1)) and (any(t in e_tags for t in g2) or any(t in n_tags for t in g2)): return True
        return False

    def _is_incompatible(self, existing: Dict, new: Dict) -> bool:
        contradictions = [("死亡", "复活"), ("消失", "出现"), ("破坏", "修复"), ("开始", "结束")]
        e_text = (existing.get("content", "") + " " + existing.get("name", "")).lower()
        n_text = (new.get("content", "") + " " + new.get("name", "")).lower()
        for a, b in contradictions:
            if (a in e_text and b in n_text) or (b in e_text and a in n_text): return True
        return False

class ReasoningEngine(ThoughtEngine): pass
class ReconciliationEngine(ThoughtEngine): pass
