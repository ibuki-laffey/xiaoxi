import os
import json
import requests
import hashlib
from datetime import datetime
from typing import Dict, Any, List, Optional
import re

from .config import (
    OLLAMA_URL, MODEL_NAME, SAFE_TIMEOUT, UNEXPECTED_DEMAND_ENABLED,
    DEMAND_IMPACT_THRESHOLD, KNOWLEDGE_BASE_DIR, TASK_TRACKING_DIR,
    PROJECT_BLUEPRINT_VALIDATION_ENABLED, INTERNAL_LOG_ENABLED,
)

class ReasoningEngine:
    """思考引擎：让 AI 在每个步骤前进行思考，处理意外需求与蓝图校验。"""
    
    def __init__(self, log_callback=None, controller=None):
        self.log_callback = log_callback
        self.controller = controller
        self.reasoning_log_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "reasoning_logs")
        os.makedirs(self.reasoning_log_dir, exist_ok=True)

        self.unexpected_demand_enabled = UNEXPECTED_DEMAND_ENABLED
        self.impact_threshold = DEMAND_IMPACT_THRESHOLD
        self.setting_priority_cache = {}
        self._log("✅ 思考引擎已初始化（完整修复版 + 意外需求处理）")

    @staticmethod
    def lenient_json_load(text: str) -> Dict[str, Any]:
        """
        🌟 宽容 JSON 解析器（安全版）。
        废弃危险的单引号替换，改用首尾括号定位和尾部逗号清理。
        """
        if not text: raise ValueError("Empty text")
        
        json_block = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if json_block: text = json_block.group(1)
            
        start_idx = text.find('{')
        end_idx = text.rfind('}')
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            text = text[start_idx:end_idx + 1]
            
        text = re.sub(r",\s*([\]}])", r"\1", text)
        return json.loads(text)

    def _log(self, msg, level: str = "INFO"):
        if self.log_callback:
            try: self.log_callback(msg, level=level)
            except TypeError: self.log_callback(msg)

    def _atomic_write_json(self, filepath: str, data: Any) -> None:
        """🌟 工业级原子写入 JSON，防止断电数据丢失。"""
        tmp_path = filepath + ".tmp"
        try:
            dir_name = os.path.dirname(filepath)
            if dir_name: os.makedirs(dir_name, exist_ok=True)
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, filepath)
        except Exception:
            if os.path.exists(tmp_path):
                try: os.remove(tmp_path)
                except Exception: pass
            raise

    def analyze_request(self, user_request: str, project_id: str = None) -> Dict[str, Any]:
        """分析用户请求，生成思考日志。"""
        reasoning_id = f"reason_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        thinking_prompt = f"""你是一个智能助手，请分析用户的请求。
用户请求：{user_request}
当前项目：{project_id or '无'}

请思考并回答以下问题（JSON 格式）：
{{"action_type": "动作类型（project_creation/auto_generation/chapter_writing/other）", "requires_research": true/false, "requires_planning": true/false, "complexity_level": "low/medium/high", "estimated_time_minutes": 预计耗时, "potential_risks": [], "recommended_approach": "推荐处理方法"}}"""
        try:
            result = self._call_model(thinking_prompt)
            self._save_reasoning_log(reasoning_id, {"user_request": user_request, "project_id": project_id, "thinking_result": result, "timestamp": datetime.now().isoformat()})
            self._log(f"🧠 思考完成：{result.get('action_type', 'unknown')}")
            return result
        except Exception as e:
            self._log(f"⚠️ 思考失败：{e}", level="WARN")
            return {"action_type": "other", "error": str(e)}

    def analyze_project_type(self, user_request: str) -> Dict[str, Any]:
        """分析项目类型（单世界/多世界/无限流）。"""
        analysis_prompt = f"""分析用户的小说创作需求，确定项目类型。
用户请求：{user_request}

请分析并返回（JSON 格式）：
{{"world_type": "single/multi/infinite", "genre": "题材", "total_chapters": 数字, "words_per_chapter": 数字, "style": "写作风格", "special_requirements": [], "potential_world_settings": []}}"""
        try:
            result = self._call_model(analysis_prompt)
            self._log(f"📋 项目类型分析完成：{result.get('world_type', 'single')}")
            return result
        except Exception as e:
            self._log(f"⚠️ 项目分析失败：{e}", level="WARN")
            return {"world_type": "single", "genre": "通用"}

    def _safe_truncate(self, text: str, max_len: int = 500) -> str:
        """安全截断文本，保证不会从句子中间腰斩。"""
        if not text or len(text) <= max_len: return text or "未知"
        truncated = text[:max_len]
        for sep in ['。', '！', '？', '\n', '.']:
            last_idx = truncated.rfind(sep)
            if last_idx > max_len * 0.5: return truncated[:last_idx + 1]
        return truncated + "..."

    def generate_outline(self, task_plan: Dict[str, Any]) -> Dict[str, Any]:
        """为给定的任务计划生成完整的大纲（JSON）。"""
        task_id = task_plan.get("task_id")
        if not task_id: return {"success": False, "error": "task_plan 中缺少 task_id"}

        title = task_plan.get("title") or "未命名项目"
        genre = task_plan.get("genre") or "通用"
        total_chapters = task_plan.get("total_chapters", 10)
        words_per = task_plan.get("words_per_chapter", 2500)
        user_prompt = task_plan.get("user_prompt", "")

        outline_prompt = f"""请为下面的小说项目生成完整的大纲，**仅返回合法的 JSON**（不包含任何额外文字）。
{{"title": "{title}", "genre": "{genre}", "total_chapters": {total_chapters}, "words_per_chapter": {words_per}, "chapters": [{{"title": "第 N 章标题", "summary": "本章概要，100-150 字左右", "keywords": ["关键词1","关键词2","关键词3"]}}]}}

约束：每章的 summary 必须围绕整体情节展开。如有 user_prompt，请在大纲中体现。
项目描述：{user_prompt}"""
        
        self._log(f"🔧 调用模型生成大纲（task_id={task_id})")
        try:
            model_res = self._call_model(outline_prompt)

            if isinstance(model_res, dict) and "title" in model_res and "chapters" in model_res:
                outline = model_res
            elif isinstance(model_res, dict) and "outline" in model_res:
                outline = model_res["outline"]
            elif isinstance(model_res, dict) and "raw_response" in model_res:
                # 🌟 核心修复：使用 super_guardian 进行工业级 JSON 自愈
                from .super_guardian import guardian
                try:
                    outline = guardian.heal_json(model_res["raw_response"], expected_type=dict)
                except ValueError:
                    return {"success": False, "error": "模型返回非结构化内容，无法解析为大纲"}
            else:
                return {"success": False, "error": "模型返回非结构化内容，无法解析为大纲"}

            if not isinstance(outline, dict) or "chapters" not in outline:
                return {"success": False, "error": "大纲缺少章节信息"}

            chapters = outline.get("chapters", [])
            if len(chapters) != total_chapters:
                self._log(f"🔧 调整章节数：期望 {total_chapters}，实际 {len(chapters)}")
                if len(chapters) > total_chapters:
                    chapters = chapters[:total_chapters]
                else:
                    for i in range(len(chapters) + 1, total_chapters + 1):
                        chapters.append({"title": f"第 {i} 章（待完善）", "summary": "待完善的章节概要", "keywords": []})
                outline["chapters"] = chapters
                outline["total_chapters"] = total_chapters

            outline.setdefault("title", title)
            outline.setdefault("genre", genre)
            outline.setdefault("words_per_chapter", words_per)

            self._log(f"✅ 大纲生成成功（task_id={task_id})")
            return {"success": True, "outline": outline}
        except Exception as e:
            self._log(f"❌ 调用模型生成大纲异常：{e}", level="ERROR")
            return {"success": False, "error": str(e)}

    def validate_project_blueprint(self, user_request: str, project_analysis: Dict[str, Any]) -> Dict[str, Any]:
        """项目蓝图验证（静默，用户无感）。"""
        if not PROJECT_BLUEPRINT_VALIDATION_ENABLED:
            return {"status": "disabled", "reason": "蓝图校验已在 config 中关闭", "validated_at": datetime.now().isoformat(), "stored_for_learning": False}

        validation_result = {"status": "valid", "issues": [], "warnings": [], "suggestions": [], "validated_at": datetime.now().isoformat(), "stored_for_learning": True}

        total_chapters = project_analysis.get("total_chapters", 100)
        words_per_chapter = project_analysis.get("words_per_chapter", 3000)
        total_words = total_chapters * words_per_chapter

        if total_chapters > 500:
            validation_result["warnings"].append(f"章节数过多（{total_chapters}章），可能导致剧情注水")
            validation_result["suggestions"].append("建议控制在 200-300 章之间")
        if words_per_chapter < 1000:
            validation_result["warnings"].append(f"每章字数过少（{words_per_chapter}字）")
            validation_result["suggestions"].append("建议每章 2000-4000 字")
        if total_words > 5_000_000:
            validation_result["warnings"].append(f"总字数过多（{total_words/10_000:.1f} 万字）")
            validation_result["suggestions"].append("建议分卷创作")

        conflict_prompt = f"""检查以下小说设定是否存在题材冲突。
用户请求：{user_request}
项目分析：{json.dumps(project_analysis, ensure_ascii=False, indent=2)}

请返回 JSON：
{{"has_conflicts": true/false, "conflicts": [], "feasibility_score": 0-100, "suggestions": []}}"""
        try:
            ai_result = self._call_model(conflict_prompt)
            if ai_result.get("has_conflicts"):
                validation_result["issues"].extend(ai_result.get("conflicts", []))
                validation_result["status"] = "invalid"
            validation_result["feasibility_score"] = ai_result.get("feasibility_score", 100)
            validation_result["suggestions"].extend(ai_result.get("suggestions", []))
        except Exception as e:
            self._log(f"⚠️ 蓝图校验 AI 分析失败: {e}", level="WARN")

        if INTERNAL_LOG_ENABLED:
            self._save_blueprint_validation_log(user_request, project_analysis, validation_result)
        return validation_result

    def _save_blueprint_validation_log(self, user_request: str, project_analysis: Dict[str, Any], validation_result: Dict[str, Any]) -> None:
        """将蓝图校验结果写入内部学习日志（JSONL）。"""
        try:
            log_file = os.path.join(self.reasoning_log_dir, "blueprint_validations.jsonl")
            entry = {"timestamp": datetime.now().isoformat(), "user_request": user_request[:500], "project_analysis": project_analysis, "validation_result": validation_result}
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
            self._log("📝 蓝图验证已写入内部日志", level="DEBUG")
        except Exception as e:
            self._log(f"⚠️ 保存蓝图验证日志失败：{e}", level="WARN")

    def handle_unexpected_demand(self, user_request: str, current_project_state: Dict[str, Any]) -> Dict[str, Any]:
        """处理意外需求（世界观反转、设定变更等）。"""
        if not self.unexpected_demand_enabled:
            return {"success": False, "error": "意外需求处理已禁用"}

        self._log("🔍 开始处理意外需求...")
        analysis = self._analyze_new_demand(user_request, current_project_state)
        impact_assessment = self._assess_demand_impact(analysis, current_project_state)
        coordination_plans = self._generate_coordination_plans(analysis, impact_assessment)
        requires_confirmation = impact_assessment.get("impact_score", 0) > self.impact_threshold
        handling_log = self._log_demand_handling(user_request, analysis, impact_assessment, coordination_plans)

        self._log(f"✅ 意外需求处理完成：{analysis.get('demand_type', 'unknown')}")
        return {"success": True, "analysis": analysis, "impact_assessment": impact_assessment, "coordination_plans": coordination_plans, "requires_confirmation": requires_confirmation, "handling_log_id": handling_log}

    def _analyze_new_demand(self, user_request: str, current_project_state: Dict[str, Any]) -> Dict[str, Any]:
        """分析新需求的类型和性质。"""
        # 🌟 核心修复：统一使用 _safe_truncate，防止硬截断 [:200] 导致语义腰斩
        world_setting = self._safe_truncate(current_project_state.get('world_setting', '未知'), max_len=800)
        
        analysis_prompt = f"""分析用户提出的新设定需求。
用户新设定：{user_request}
当前工程状态：
- 已完成章节：{current_project_state.get('completed_chapters', 0)}
- 总章节数：{current_project_state.get('total_chapters', 0)}
- 当前世界观：{world_setting}

返回 JSON：
{{"demand_type": "supplement/reverse/contradiction/expansion", "relation_to_existing": "关系", "impact_scope": "early/mid/late/all", "is_world_reversal": true/false, "confidence": 0-1}}"""
        try:
            return self._call_model(analysis_prompt)
        except Exception as e:
            self._log(f"⚠️ 需求分析失败：{e}", level="WARN")
            return {"demand_type": "unknown", "relation_to_existing": "unknown", "impact_scope": "all", "is_world_reversal": False, "confidence": 0.5}

    def _assess_demand_impact(self, analysis: Dict[str, Any], current_project_state: Dict[str, Any]) -> Dict[str, Any]:
        """评估需求影响范围。"""
        impact_prompt = f"""评估新设定对工程的影响。
需求分析：{json.dumps(analysis, ensure_ascii=False, indent=2)}
当前工程状态：
- 已完成章节：{current_project_state.get('completed_chapters', 0)}
- 总章节数：{current_project_state.get('total_chapters', 0)}
- 大纲状态：{current_project_state.get('outline_status', 'unknown')}

返回 JSON：
{{"chapters_to_modify": [], "character_impact": "low/medium/high", "world_impact": "low/medium/high", "plot_impact": "low/medium/high", "impact_score": 0-1, "requires_regeneration": true/false}}"""
        try:
            return self._call_model(impact_prompt)
        except Exception as e:
            self._log(f"⚠️ 影响评估失败：{e}", level="WARN")
            return {"chapters_to_modify": [], "character_impact": "medium", "world_impact": "medium", "plot_impact": "medium", "impact_score": 0.5, "requires_regeneration": False}

    def _generate_coordination_plans(self, analysis: Dict[str, Any], impact_assessment: Dict[str, Any]) -> List[Dict[str, Any]]:
        """生成协调方案。"""
        return [
            {"plan_id": "A", "name": "保持前文，后续铺垫", "description": "已完成章节保持不变，从当前章节开始逐步铺垫新设定", "pros": ["不修改已完成内容", "节省时间"], "cons": ["可能存在前后不一致", "需要巧妙过渡"], "effort": "low", "recommended_for": ["影响范围小的需求", "补充类需求"]},
            {"plan_id": "B", "name": "微调前文，埋下伏笔", "description": "对已完成章节进行微调，埋下新设定的伏笔", "pros": ["前后连贯性更好", "阅读体验流畅"], "cons": ["需要修改已发布内容", "可能影响读者体验"], "effort": "medium", "recommended_for": ["中等影响需求", "反转类需求"]},
            {"plan_id": "C", "name": "重新生成前文", "description": "根据新设定重新生成受影响的章节", "pros": ["完全一致", "质量最高"], "cons": ["耗时最长", "可能丢失原有亮点"], "effort": "high", "recommended_for": ["高影响需求", "矛盾类需求"]}
        ]

    def _log_demand_handling(self, user_request: str, analysis: Dict[str, Any], impact_assessment: Dict[str, Any], coordination_plans: List[Dict[str, Any]]) -> str:
        """记录需求处理过程。"""
        log_id = hashlib.md5(f"{user_request}{datetime.now()}".encode()).hexdigest()[:16]
        log_data = {"log_id": log_id, "user_request": user_request, "analysis": analysis, "impact_assessment": impact_assessment, "coordination_plans": coordination_plans, "timestamp": datetime.now().isoformat()}
        log_file = os.path.join(self.reasoning_log_dir, f"demand_{log_id}.json")
        self._atomic_write_json(log_file, log_data)
        return log_id

    def set_setting_priority(self, setting_id: str, priority: int, reason: str = ""):
        self.setting_priority_cache[setting_id] = {"priority": priority, "reason": reason, "set_at": datetime.now().isoformat()}
        self._log(f"📊 设定优先级已设置：{setting_id} → {priority}")

    def get_setting_priority(self, setting_id: str) -> int:
        if setting_id in self.setting_priority_cache: return self.setting_priority_cache[setting_id]["priority"]
        return 5

    def check_setting_conflict(self, new_setting: Dict[str, Any], existing_settings: List[Dict[str, Any]]) -> Dict[str, Any]:
        conflicts = []
        for existing in existing_settings:
            if existing.get("name") == new_setting.get("name") and existing.get("world_id") == new_setting.get("world_id"):
                conflicts.append({"type": "name_conflict", "setting_id": existing.get("id"), "severity": "high"})
            if self._check_logical_conflict(existing, new_setting):
                conflicts.append({"type": "logical_conflict", "setting_id": existing.get("id"), "severity": "medium"})
            existing_priority = self.get_setting_priority(existing.get("id", ""))
            new_priority = new_setting.get("priority", 5)
            if existing_priority > new_priority and self._is_incompatible(existing, new_setting):
                conflicts.append({"type": "priority_conflict", "setting_id": existing.get("id"), "severity": "low", "message": f"高优先级设定（{existing_priority}）与新设定（{new_priority}）冲突"})
        return {"has_conflicts": len(conflicts) > 0, "conflicts": conflicts, "conflict_count": len(conflicts)}

    def _check_logical_conflict(self, existing: Dict, new: Dict) -> bool:
        forbidden_combinations = [(["低武", "武侠"], ["核武器", "高科技", "机甲"]), (["古代", "封建"], ["手机", "电脑", "互联网"]), (["魔法世界"], ["科学解释", "物理定律"])]
        existing_tags = existing.get("tags", [])
        new_tags = new.get("tags", [])
        for group1, group2 in forbidden_combinations:
            if (any(t in existing_tags for t in group1) or any(t in new_tags for t in group1)) and (any(t in existing_tags for t in group2) or any(t in new_tags for t in group2)):
                return True
        return False

    def _is_incompatible(self, existing: Dict, new: Dict) -> bool:
        contradiction_pairs = [("死亡", "复活"), ("消失", "出现"), ("破坏", "修复"), ("开始", "结束")]
        existing_content = existing.get("content", "") + " " + existing.get("name", "")
        new_content = new.get("content", "") + " " + new.get("name", "")
        for a, b in contradiction_pairs:
            if (a in existing_content and b in new_content) or (b in existing_content and a in new_content): return True
        return False

    def _call_model(self, prompt: str, require_json: bool = True) -> Dict[str, Any]:
        """🌟 核心修复：调用本地 Ollama，强制开启 format: json 从根源约束 LLM。"""
        try:
            sys_content = "你是一个底层的 JSON 数据审查 API。你只输出合法的、结构严谨的纯 JSON 字符串，绝不输出任何废话、解释或 Markdown 标记。" if require_json else "你是一个专业的 AI 助手。"
            options = {"temperature": 0.3, "num_predict": 2048}
            if require_json: options["format"] = "json"
            
            resp = requests.post(
                OLLAMA_URL,
                json={"model": MODEL_NAME, "messages": [{"role": "system", "content": sys_content}, {"role": "user", "content": prompt}], "stream": False, "options": options},
                timeout=SAFE_TIMEOUT,
            )
            resp.raise_for_status()
            content = resp.json().get("message", {}).get("content", "").strip()
        except Exception as e:
            return {"error": str(e)}

        from .super_guardian import guardian
        try:
            return guardian.heal_json(content, expected_type=dict)
        except ValueError:
            return {"raw_response": content}

    def _save_reasoning_log(self, reasoning_id: str, data: Dict[str, Any]) -> None:
        log_file = os.path.join(self.reasoning_log_dir, f"{reasoning_id}.json")
        self._atomic_write_json(log_file, data)

    def get_reasoning_history(self, project_id: str = None, limit: int = 50) -> List[Dict[str, Any]]:
        history = []
        for filename in os.listdir(self.reasoning_log_dir):
            if filename.endswith(".json") and not filename.startswith("demand_"):
                filepath = os.path.join(self.reasoning_log_dir, filename)
                try:
                    with open(filepath, "r", encoding="utf-8") as f: data = json.load(f)
                    if project_id is None or data.get("project_id") == project_id: history.append(data)
                except Exception as e:
                    self._log(f"⚠️ 读取历史日志失败 {filepath}: {e}", level="WARN")
                    continue
        history.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return history[:limit]

    def get_demand_handling_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        history = []
        for filename in os.listdir(self.reasoning_log_dir):
            if filename.startswith("demand_") and filename.endswith(".json"):
                filepath = os.path.join(self.reasoning_log_dir, filename)
                try:
                    with open(filepath, "r", encoding="utf-8") as f: data = json.load(f)
                    history.append(data)
                except Exception as e:
                    self._log(f"⚠️ 读取需求日志失败 {filepath}: {e}", level="WARN")
                    continue
        history.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return history[:limit]

    def export_reasoning_logs(self, output_dir: str = None) -> Dict[str, Any]:
        if output_dir is None: output_dir = os.path.join(self.reasoning_log_dir, "exports")
        os.makedirs(output_dir, exist_ok=True)
        exported = []
        for filename in os.listdir(self.reasoning_log_dir):
            if filename.endswith(".json"):
                src_path = os.path.join(self.reasoning_log_dir, filename)
                dst_path = os.path.join(output_dir, filename)
                try:
                    with open(src_path, "r", encoding="utf-8") as f: data = json.load(f)
                    self._atomic_write_json(dst_path, data)
                    exported.append(dst_path)
                except Exception: continue
        return {"success": True, "exported": exported, "count": len(exported)}
