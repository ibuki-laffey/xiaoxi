import os
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
from .constants import LOG_DEDUPLICATION, MAX_CHAPTER_GENERATION_RETRIES
from . import config

class AIControllerHelperMixin:
    def _log(self, msg: str, level: str = "INFO") -> None:
        if getattr(config, "PROFESSIONAL_LOG_ENABLED", False) and self.log_callback:
            entry = {"timestamp": datetime.now().isoformat(), "level": level, "module": self.__class__.__name__, "project_id": getattr(self, "project_id", None), "message": msg}
            self.log_callback(json.dumps(entry, ensure_ascii=False))
            return

        if not self.log_callback:
            from .paths import DEFAULT_SAVE_DIR, ensure_path
            log_dir = ensure_path(Path(DEFAULT_SAVE_DIR, "logs"))
            fallback_path = log_dir / "ai_controller_fallback.log"
            ts = datetime.now().isoformat()
            # 🌟 性能修复：日志追加不需要原子 rename，直接 append 避免 I/O 阻塞
            with open(fallback_path, "a", encoding="utf-8") as f:
                f.write(f"[{ts}] [{level}] {msg}\n")
            return

        if LOG_DEDUPLICATION and level in ("WARN", "ERROR"):
            if not hasattr(self, "_logged_warnings"): self._logged_warnings = set()
            key = (level, msg)
            if key in self._logged_warnings: return
            self._logged_warnings.add(key)

        self.log_callback(f"[{datetime.now().isoformat()}] [{level}] {msg}")

    def _render_prompt(self, template_name: str, **kwargs) -> str:
        from .prompt_manager import PromptManager
        from .config import PROMPT_TEMPLATES_DIR
        try:
            return PromptManager.render(template_name, **kwargs)
        except Exception as first_err:
            self._log(f"⚠️ Prompt 首次渲染失败: {first_err}", level="WARN")
            tmpl_path = os.path.join(PROMPT_TEMPLATES_DIR, f"{template_name}.txt")
            try:
                with open(tmpl_path, "r", encoding="utf-8") as f: tmpl_content = f.read()
            except Exception as e:
                raise RuntimeError(f"无法读取模板文件 {tmpl_path}") from e
            
            missing_vars = {part.split("|")[0].strip() for part in re.findall(r"{{\s*([^}]+?)\s*}}", tmpl_content) if part.split("|")[0].strip() and part.split("|")[0].strip() not in kwargs}
            for var in missing_vars: kwargs[var] = ""
            
            try:
                return PromptManager.render(template_name, **kwargs)
            except Exception as second_err:
                raise RuntimeError(f"Prompt 渲染两次均失败（模板: {template_name}）") from second_err

    def _detect_intent_context(self, context: Dict[str, Any]) -> str:
        if not context: return "当前状态：无活跃任务（设定讨论阶段）"
        lines = []
        pending = context.get("pending_tasks", [])
        if not pending:
            lines.append("当前状态：无活跃任务（设定讨论阶段）")
        else:
            status_map = {"planned": "项目已创建，等待生成大纲", "outline_generated": "大纲已生成，待确认", "outline_confirmed": "大纲已确认，准备写作", "in_progress": "正在进行章节写作", "completed": "项目已完成", "paused": "项目已暂停"}
            lines.append(f"当前状态：{status_map.get(context.get('task_status', 'unknown'), context.get('task_status', 'unknown'))}")
            if context.get("current_project_id"): lines.append(f"项目 ID：{context['current_project_id']}")
            task_id = pending[0].get("task_id") if pending else None
            outline_exists = bool(self.outline_manager.get_outline(task_id)) if task_id and hasattr(self, 'outline_manager') else False
            lines.append(f"大纲文件：{'已存在' if outline_exists else '未生成'}")
        return "\n".join(lines)

    def _detect_intent(self, user_prompt: str, context: Dict[str, Any] = None) -> Dict[str, Any]:
        return {"primary_intent": "chat", "confidence": 0.7, "reasoning": "默认聊天"}

    def _detect_actions(self, user_prompt: str) -> List[str]:
        txt = user_prompt.lower()
        actions = []
        deny_words = ['不', '没', '别', '非', '无']
        has_deny = any(d in txt for d in deny_words)
        
        if not has_deny:
            if any(w in txt for w in ['时间', '几点', '日期']): actions.append('get_time')
            if any(w in txt for w in ['天气', '气温', '下雨', '预报']): actions.append('get_weather')
        
        if any(w in txt for w in ['创建', '生成', '保存', '写']): actions.append('create_file')
        if any(w in txt for w in ['打开', '访问', '浏览']) and any(w in txt for w in ['网页', '网站', '浏览器']): actions.append('open_url')
        if any(w in txt for w in ['读取', '查看', '打开文件']) and any(w in txt for w in ['文件', 'txt', '文档']): actions.append('read_file')
        if any(w in txt for w in ['统计', 'cpu', '内存', '系统信息']): actions.append('get_system_stats')
        if any(w in txt for w in ['学习', '去学', '查一下']): actions.append('start_learning')
        return actions if actions else ['chat']

    def _fallback_intent_analysis(self, user_prompt: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        txt = user_prompt.lower()
        has_pending = bool(context and context.get("pending_tasks"))
        task_status = context.get("task_status", "none") if context else "none"
        outline_exists = bool(context.get("outline_exists")) if context else False
        
        # 🌟 解决关键词化死板问题：引入否定词过滤，防止“不可以”被误判为“可以”
        deny_words = ['不', '没', '别', '非', '未', '勿']
        has_deny = any(d in txt for d in deny_words)

        creation_kw = ["创建", "新建", "生成", "写", "启动", "工程", "项目", "文件"]
        if any(kw in txt for kw in creation_kw) and not has_deny:
            return {"primary_intent": "project_creation", "confidence": 0.88, "generation_mode": "all", "needs_clarification": False, "reasoning": "关键字触发：项目创建意图"}

        if has_pending and task_status == "planned":
            if any(kw in txt for kw in ["生成大纲", "写大纲", "做大纲", "规划一下"]) and not has_deny:
                return {"primary_intent": "outline_generation", "confidence": 0.84, "generation_mode": "all", "needs_clarification": False, "reasoning": "项目已创建，用户请求生成大纲"}
            if any(kw in txt for kw in ["确认", "可以", "好的", "行", "没问题"]):
                return {"primary_intent": "chat", "confidence": 0.80, "generation_mode": "all", "needs_clarification": False, "reasoning": "确认指令在未生成大纲阶段无效"}
            return {"primary_intent": "chat", "confidence": 0.86, "generation_mode": "all", "needs_clarification": False, "reasoning": "项目已创建，仍在设定讨论"}

        if has_pending and task_status == "outline_generated" and outline_exists:
            confirm_kws = ["确认", "可以", "好的", "行", "没问题", "开始写", "写正文", "生成章节"]
            if any(kw in txt for kw in confirm_kws) and not has_deny:
                return {"primary_intent": "outline_confirmation", "confidence": 0.85, "generation_mode": "all", "needs_clarification": False, "reasoning": "大纲已生成，用户确认进入写作阶段"}
            if any(kw in txt for kw in ["修改", "调整", "改一下", "不对"]):
                return {"primary_intent": "chat", "confidence": 0.84, "generation_mode": "all", "needs_clarification": False, "reasoning": "用户希望修改已生成的大纲"}
            return {"primary_intent": "chat", "confidence": 0.80, "generation_mode": "all", "needs_clarification": False, "reasoning": "大纲已生成但未确认，继续讨论"}

        if has_pending and task_status in ("in_progress", "outline_confirmed"):
            continue_kws = ["继续", "接着写", "下一章", "继续生成", "写下一章"]
            if any(kw in txt for kw in continue_kws) and not has_deny:
                return {"primary_intent": "continue_generation", "confidence": 0.86, "generation_mode": "all", "needs_clarification": False, "reasoning": "写作进行中，用户请求继续章节"}
            if any(kw in txt for kw in ["暂停", "停一下", "先不写"]):
                return {"primary_intent": "chat", "confidence": 0.85, "generation_mode": "all", "needs_clarification": False, "reasoning": "用户请求暂停写作"}
            return {"primary_intent": "chat", "confidence": 0.78, "generation_mode": "all", "needs_clarification": False, "reasoning": "写作中但用户可能在剧情讨论"}

        return {"primary_intent": "chat", "confidence": 0.70, "generation_mode": "all", "needs_clarification": False, "reasoning": "未匹配到任何关键字，返回普通聊天"}

    def _load_last_active_topic(self) -> None:
        try:
            topic = self.topic_manager.get_current()
            if not topic:
                topic = "default"
                self.topic_manager.save(topic, [])
        except Exception as e:
            self._log(f"⚠️ 加载活跃话题失败，回退为 default: {e}", level="WARN")
            topic = "default"
        self.current_topic = topic

    def _save_last_active_topic(self) -> None:
        try:
            self.topic_manager.save(self.current_topic, [])
        except Exception as e:
            self._log(f"⚠️ 保存活跃话题失败: {e}", level="ERROR")

    def _load_conversation_history(self) -> None:
        try:
            if getattr(self, 'disk_cache', None):
                from .disk_cache_manager import load_conversation_from_disk
                self.conversation_history = load_conversation_from_disk(self.current_topic, self.disk_cache, default=[])
            else:
                from .config import KNOWLEDGE_BASE_DIR
                file_path = os.path.join(KNOWLEDGE_BASE_DIR, f"conversation_{self.current_topic}.json")
                if os.path.isfile(file_path):
                    with open(file_path, "r", encoding="utf-8") as f: data = json.load(f)
                    self.conversation_history = data.get("messages", [])
                    self.current_project_id = data.get("project_id", getattr(self, 'current_project_id', None))
                else:
                    self.conversation_history = []
        except Exception as e:
            self._log(f"❌ 加载对话历史时异常: {e}", level="ERROR")
            self.conversation_history = []
        self._ensure_conversation_size()

    def _save_conversation_history(self) -> None:
        if not getattr(self, 'conversation_history', None): return
        now = time.time()
        if now - getattr(self, "_last_save_ts", 0) < 15: return
        self._last_save_ts = now

        try:
            if getattr(self, 'disk_cache', None):
                from .disk_cache_manager import save_conversation_to_disk
                save_conversation_to_disk(self.current_topic, self.conversation_history, self.disk_cache)
            else:
                from .config import KNOWLEDGE_BASE_DIR
                file_path = os.path.join(KNOWLEDGE_BASE_DIR, f"conversation_{self.current_topic}.json")
                tmp_path = file_path + ".tmp"
                with open(tmp_path, "w", encoding="utf-8") as tmp:
                    json.dump({"messages": self.conversation_history, "project_id": getattr(self, 'current_project_id', None), "task_status": getattr(self, "current_task_status", None)}, tmp, ensure_ascii=False, indent=2)
                    tmp.flush()
                    os.fsync(tmp.fileno())
                os.replace(tmp_path, file_path)
        except Exception as e:
            self._log(f"⚠️ 保存对话历史失败: {e}", level="WARN")

    def _ensure_conversation_size(self) -> None:
        max_len = getattr(self, 'max_history_length', 500)
        if len(self.conversation_history) > max_len:
            excess = len(self.conversation_history) - max_len
            self.conversation_history = self.conversation_history[-max_len:]
            self._log(f"⚠️ 对话历史超出上限，已截断 {excess} 条", level="WARN")

    def _safe_pending_tasks(self) -> List[Dict[str, Any]]:
        tasks = []
        try:
            tasks = getattr(self.task_planner, "list_pending_tasks", lambda: [])()
        except Exception as e:
            self._log(f"⚠️ list_pending_tasks 调用异常，回退至 list_all_tasks: {e}", level="WARN")

        if not tasks:
            try:
                all_tasks = getattr(self.task_planner, "list_all_tasks", lambda: [])()
                tasks = [t for t in all_tasks if t.get("status") != "completed"]
            except Exception as e:
                self._log(f"⚠️ list_all_tasks 也不可用: {e}", level="WARN")

        for task in tasks:
            if not task.get("task_id"):
                task["task_id"] = self.task_planner._generate_task_id(task.get("title", "fallback_task"))
            self._ensure_int_fields(task)
        return tasks

    def _get_current_task_id(self) -> Optional[str]:
        mem_id = getattr(self, "current_task_id", None)
        if mem_id and isinstance(mem_id, str) and mem_id.startswith("task_"): return mem_id

        pending = self._safe_pending_tasks()
        if pending:
            pid = pending[0].get("task_id")
            if pid:
                self.current_task_id = pid
                return pid

        proj_id = getattr(self, "current_project_id", None)
        if proj_id:
            try:
                all_tasks = getattr(self.task_planner, "list_all_tasks", lambda: [])()
                for t in all_tasks:
                    if t.get("project_id") == proj_id and t.get("task_id"):
                        self.current_task_id = t["task_id"]
                        return t["task_id"]
            except Exception as e:
                self._log(f"⚠️ 通过 project_id 搜索 task_id 失败: {e}", level="WARN")

        if hasattr(self, "progress_tracker") and hasattr(self.progress_tracker, "get_task_by_project"):
            try:
                task = self.progress_tracker.get_task_by_project(proj_id)
                if task and task.get("task_id"):
                    self.current_task_id = task["task_id"]
                    return task["task_id"]
            except Exception as e:
                self._log(f"⚠️ 进度追踪器获取 task_id 失败: {e}", level="WARN")

        self._log("⚠️ 无法定位当前 task_id（返回 None）", level="WARN")
        return None

    def _is_valid_task_id(self, task_id: Optional[str]) -> bool:
        if not task_id or not isinstance(task_id, str): return False
        try:
            plan = self.task_planner.get_plan(task_id)
            return bool(plan)
        except Exception as e:
            self._log(f"⚠️ 检查 task_id 有效性时异常: {e}", level="WARN")
            return False

    def _generate_chapters_batch(self, task_plan: Dict[str, Any], start_chapter: int, end_chapter: int) -> Dict[str, Any]:
        task_id = task_plan.get("task_id")
        if not task_id:
            return {"action": "chat", "explanation": "❌ 任务 ID 缺失，无法进行章节生成。", "task_id": None, "status": "error"}

        results = []
        self._log(f"📚 开始批量生成章节：{start_chapter}-{end_chapter}（任务 {task_id}）")

        for chap_num in range(start_chapter, end_chapter + 1):
            if self._chapter_exists(task_plan, chap_num):
                self._log(f"⚡ 第 {chap_num} 章已存在（磁盘检测），直接跳过。", level="INFO")
                self.progress_tracker.update_progress(task_id=task_id, chapter_num=chap_num, success=True, chapter_summary="已存在（跳过）", filepath=self._first_nonempty_file(os.path.join(task_plan.get("project_dir", ""), "02_章节", f"第{chap_num:03d}章")))
                results.append({"chapter": chap_num, "success": True, "skipped": True})
                continue

            if hasattr(self, "interruption_handler") and self.interruption_handler.is_active():
                return {"action": "chat", "explanation": "🛑 已检测到用户中断请求，章节生成已暂停。", "task_id": task_id, "status": "paused", "interrupted_at": chap_num}

            if chap_num > 1 and chap_num % getattr(config, "HEALTH_CHECK_INTERVAL_CHAPTERS", 10) == 0:
                self._log(f"🏥 第 {chap_num} 章触发健康检查…")
                health_res = self._run_health_check(task_id, chap_num)
                if not health_res.get("success"):
                    return {"action": "chat", "explanation": f"⚠️ 健康检查阻塞章节生成（第 {chap_num} 章）\n原因：{health_res.get('error')}", "task_id": task_id, "status": "blocked"}

            generation_res = self.chapter_generator.generate_chapter(task_id=task_id, chapter_num=chap_num, task_plan=task_plan, continuation_guide=None)

            if not generation_res.get("success"):
                err_id = self.error_handler.record_error(task_id=task_id, chapter_num=chap_num, error_type="generation_failed", error_message=generation_res.get("error", "未知错误"))
                self.progress_tracker.update_progress(task_id=task_id, chapter_num=chap_num, success=False, error=generation_res.get("error", "未知错误"))
                results.append({"chapter": chap_num, "success": False, "error_id": err_id})
                continue

            inspection = self.quality_inspector.inspect_chapter(content=generation_res.get("content", ""), chapter_num=chap_num, quality_rules=task_plan.get("quality_rules", {}), task_plan=task_plan)
            rewrite_cnt = 0
            while not inspection.get("passed") and rewrite_cnt < MAX_CHAPTER_GENERATION_RETRIES:
                self._log(f"⚠️ 第 {chap_num} 章质量检查未通过（得分 {inspection.get('score',0)}），重新生成（尝试 {rewrite_cnt+1}/{MAX_CHAPTER_GENERATION_RETRIES}）", level="WARN")
                
                # 🌟 防弹修复：安全获取重写反馈，防止 AttributeError
                feedback = ""
                if hasattr(self, '_extract_rewrite_feedback'):
                    feedback = self._extract_rewrite_feedback(inspection, inspection.get("score", 0))
                else:
                    issues = inspection.get('issues', ['质量不佳'])
                    feedback = f"当前得分 {inspection.get('score', 0)}。主要问题：{'; '.join(str(i) for i in issues[:3])}。请针对这些问题进行实质性修改！"
                    
                generation_res = self.chapter_generator.generate_chapter(task_id=task_id, chapter_num=chap_num, task_plan=task_plan, continuation_guide={"rewrite_feedback": feedback})
                if not generation_res.get("success"): break
                inspection = self.quality_inspector.inspect_chapter(content=generation_res.get("content", ""), chapter_num=chap_num, quality_rules=task_plan.get("quality_rules", {}), task_plan=task_plan)
                rewrite_cnt += 1

            try:
                file_path = self.chapter_generator._save_chapter_content_atomic(task_id=task_id, chapter_num=chap_num, content=generation_res.get("content", ""), task_plan=task_plan)
            except Exception as e:
                self._log(f"❌ 第 {chap_num} 章写入磁盘失败: {e}", level="ERROR")
                self.progress_tracker.update_progress(task_id=task_id, chapter_num=chap_num, success=False, error=str(e))
                results.append({"chapter": chap_num, "success": False, "error": str(e)})
                continue

            self.progress_tracker.update_progress(task_id=task_id, chapter_num=chap_num, success=True, chapter_summary=generation_res.get("summary", ""), filepath=file_path, quality_score=inspection.get("score", 0), quality_passed=inspection.get("passed", False))
            results.append({"chapter": chap_num, "success": True})

        total = len(results)
        success_cnt = sum(1 for r in results if r.get("success"))
        if end_chapter >= task_plan.get("total_chapters", 1):
            self.progress_tracker.pause_task(task_id)
            return {"action": "chat", "explanation": f"🎉 全部章节生成完毕！共 {total} 章，成功 {success_cnt} 章，失败 {total - success_cnt} 章。", "task_id": task_id, "status": "completed"}

        next_chap = end_chapter + 1
        return {"action": "chat", "explanation": f"✅ 已生成第 {start_chapter}-{end_chapter} 章（成功 {success_cnt} 章），请回复“继续生成”写第 {next_chap} 章。", "task_id": task_id, "status": "in_progress", "next_chapter": next_chap}


    def _run_health_check(self, task_id: str, current_chapter: int) -> Dict[str, Any]:
        """执行健康检查。若检测到大纲缺失且开启 AUTO_REGENERATE，则自动重新生成大纲。"""
        check_result = {"success": True, "errors": [], "warnings": [], "checked_at": datetime.now().isoformat(), "chapter": current_chapter, "error_type": ""}

        if not task_id:
            check_result.update({"success": False, "error": "task_id missing", "error_type": "task_missing"})
            return check_result

        SILENT = getattr(config, 'SILENT_HEALTH_CHECK', False)
        AUTO_REGEN = getattr(config, 'HEALTH_CHECK_AUTO_REGENERATE', False)
        STRICT = getattr(config, 'HEALTH_CHECK_STRICT_MODE', False)

        outline_data = self.outline_manager.get_outline(task_id) if hasattr(self, 'outline_manager') else None
        if not outline_data:
            check_result["success"] = False
            check_result["errors"].append({"type": "outline_missing", "message": "大纲文件丢失"})
            check_result["error_type"] = "outline_missing"
            if not SILENT: self._log("❌ 健康检查：大纲文件丢失")

            if AUTO_REGEN and hasattr(self, '_auto_generate_outline') and hasattr(self, 'task_planner'):
                self._log("🧠 检测到大纲缺失，尝试自动重新生成…")
                plan = self.task_planner.get_plan(task_id) if hasattr(self.task_planner, 'get_plan') else {}
                auto_res = self._auto_generate_outline(plan, f"健康检查自动重建（章节 {current_chapter})")
                if auto_res.get("action") == "outline_generated":
                    self._log("✅ 自动重建大纲成功")
                    check_result["success"] = True
                    check_result["warnings"].append({"type": "outline_regenerated", "message": "已自动重建大纲"})
                else:
                    self._log(f"⚠️ 自动重建失败：{auto_res.get('explanation','未知')}", level="WARN")
            if STRICT: return check_result

        if getattr(config, 'CHARACTER_GROWTH_ENABLED', False) and hasattr(self, 'world_state') and hasattr(self.world_state, 'get_characters'):
            for char in self.world_state.get_characters():
                cid = char.get('id')
                cur_status = self.world_state.get_character_status(cid, current_chapter) if hasattr(self.world_state, 'get_character_status') else None
                exp_status = self.world_state.get_expected_character_status(cid, current_chapter) if hasattr(self.world_state, 'get_expected_character_status') else None
                if cur_status and exp_status and cur_status != exp_status:
                    check_result["warnings"].append({"type": "character_state_mismatch", "character": char.get('name'), "current": cur_status, "expected": exp_status})
                    if not SILENT: self._log(f"⚠️ 角色 {char.get('name')} 状态不一致", level="WARN")

        if not check_result["success"] or check_result["warnings"]:
            self._log("🧠 健康检查发现问题，调用 AI 生成修复方案…")
            fix_plan = {}
            if hasattr(self, 'reasoning_engine') and hasattr(self.reasoning_engine, 'generate_health_check_fix_plan'):
                fix_plan = self.reasoning_engine.generate_health_check_fix_plan(task_id, current_chapter, check_result)
            check_result["fix_plan"] = fix_plan
            
            if not check_result["success"] and hasattr(self, 'learning_engine') and hasattr(self.learning_engine, 'save_cross_project_experience'):
                self.learning_engine.save_cross_project_experience(
                    project_id=getattr(self, 'current_project_id', None),
                    topic="健康检查错误",
                    solution=json.dumps(fix_plan, ensure_ascii=False),
                    tags=["health_check", "error_recovery"],
                )
                
        if getattr(config, 'INTERNAL_LOG_ENABLED', False):
            self._log("📝 健康检查日志已保存（内部）")
        return check_result

    def _record_outline_failure(self, task_id: str, reason: str, extra: dict = None) -> None:
        """当大纲生成全部手段均失败时，统一写入跨项目经验库。"""
        self._log(f"⚠️ 大纲生成失败 (task_id={task_id}) → {reason}", level="WARN")
        try:
            if hasattr(self, "learning_engine") and hasattr(self.learning_engine, 'save_cross_project_experience'):
                payload = {"task_id": task_id, "reason": reason, "extra": extra or {}, "timestamp": datetime.now().isoformat()}
                self.learning_engine.save_cross_project_experience(
                    project_id=getattr(self, 'current_project_id', None),
                    topic="outline_generation_failure",
                    solution=json.dumps(payload, ensure_ascii=False),
                    tags=["outline", "failure"],
                )
        except Exception as e:
            self._log(f"❌ 记录大纲失败经验时异常: {e}", level="ERROR")

    def _load_state_files(self) -> Dict[str, Any]:
        """加载并校验所有必需的状态文件。任一文件缺失或解析失败均抛出 ValueError。"""
        state_dir = getattr(config, 'STATE_DIR', 'state')
        char_file = getattr(config, 'CHARACTER_FILE', 'character_state.json')
        world_file = getattr(config, 'WORLD_FILE', 'world_state.json')
        conflict_file = getattr(config, 'CONFLICT_FILE', 'conflict_state.json')
        thinking_file = getattr(config, 'THINKING_LOG_FILE', 'thought_log.json')
        result = {}

        char_path = os.path.join(state_dir, char_file)
        try:
            with open(char_path, 'r', encoding='utf-8') as f: char_data = json.load(f)
            if not isinstance(char_data, dict): raise ValueError("角色状态文件根对象必须为 dict")
            for req in ("id", "name", "health", "belief"):
                if req not in char_data: raise ValueError(f"角色状态缺少必填字段：{req}")
            result["character"] = char_data
        except FileNotFoundError: raise ValueError(f"角色状态文件未找到：{char_path}")
        except json.JSONDecodeError as e: raise ValueError(f"角色状态文件 JSON 解析失败：{e}")
        except Exception as e: raise ValueError(f"读取角色状态时发生未知错误：{e}")

        world_path = os.path.join(state_dir, world_file)
        try:
            with open(world_path, 'r', encoding='utf-8') as f: world_data = json.load(f)
            if not isinstance(world_data, dict): raise ValueError("世界状态文件根对象必须为 dict")
            for req in ("start_time", "current_time", "location", "world_rules"):
                if req not in world_data: raise ValueError(f"世界状态缺少必填字段：{req}")
            result["world"] = world_data
        except FileNotFoundError: raise ValueError(f"世界状态文件未找到：{world_path}")
        except json.JSONDecodeError as e: raise ValueError(f"世界状态文件 JSON 解析失败：{e}")
        except Exception as e: raise ValueError(f"读取世界状态时发生未知错误：{e}")

        conflict_path = os.path.join(state_dir, conflict_file)
        try:
            with open(conflict_path, 'r', encoding='utf-8') as f: conflict_data = json.load(f)
            if not isinstance(conflict_data, dict): raise ValueError("冲突文件根对象必须为 dict")
            if "statement" not in conflict_data: raise ValueError("冲突文件缺少必填字段：statement")
            result["conflict"] = conflict_data
        except FileNotFoundError: raise ValueError(f"冲突文件未找到：{conflict_path}")
        except json.JSONDecodeError as e: raise ValueError(f"冲突文件 JSON 解析失败：{e}")
        except Exception as e: raise ValueError(f"读取冲突描述时发生未知错误：{e}")

        thinking_path = os.path.join(state_dir, thinking_file)
        thinking_list = []
        try:
            if os.path.isfile(thinking_path):
                with open(thinking_path, 'r', encoding='utf-8') as f:
                    for line_num, line in enumerate(f, 1):
                        line = line.strip()
                        if not line: continue
                        try:
                            entry = json.loads(line)
                            if not isinstance(entry, dict): raise ValueError(f"思考日志第{line_num}行不是 dict")
                            thinking_list.append(entry)
                        except json.JSONDecodeError as e: raise ValueError(f"思考日志第{line_num}行 JSON 解析失败：{e}")
                        except Exception as e: raise ValueError(f"读取思考日志第{line_num}行时发生错误：{e}")
            result["thinking_log"] = thinking_list
        except Exception as e: raise ValueError(f"读取思考日志时发生错误：{e}")

        return result

    def _save_state_files(self, role_state: Dict[str, Any], world_state: Dict[str, Any]) -> None:
        """原子写入角色和世界状态文件，并在写入后立即做 schema 校验。"""
        state_dir = getattr(config, 'STATE_DIR', 'state')
        char_file = getattr(config, 'CHARACTER_FILE', 'character_state.json')
        world_file = getattr(config, 'WORLD_FILE', 'world_state.json')

        char_tmp = os.path.join(state_dir, f"{char_file}.tmp")
        char_final = os.path.join(state_dir, char_file)
        try:
            with open(char_tmp, 'w', encoding='utf-8') as f:
                json.dump(role_state, f, ensure_ascii=False, indent=2)
                f.flush(); os.fsync(f.fileno())
            os.replace(char_tmp, char_final)
            with open(char_final, 'r', encoding='utf-8') as f: verify_char = json.load(f)
            if not isinstance(verify_char, dict): raise ValueError("角色状态写入后校验失败：根对象不是 dict")
            for req in ("id", "name", "health", "belief"):
                if req not in verify_char: raise ValueError(f"角色状态写入后校验失败：缺少字段 {req}")
        except Exception as e:
            if os.path.isfile(char_tmp):
                try: os.remove(char_tmp)
                except Exception: pass
            raise ValueError(f"保存角色状态失败：{e}")

        world_tmp = os.path.join(state_dir, f"{world_file}.tmp")
        world_final = os.path.join(state_dir, world_file)
        try:
            with open(world_tmp, 'w', encoding='utf-8') as f:
                json.dump(world_state, f, ensure_ascii=False, indent=2)
                f.flush(); os.fsync(f.fileno())
            os.replace(world_tmp, world_final)
            with open(world_final, 'r', encoding='utf-8') as f: verify_world = json.load(f)
            if not isinstance(verify_world, dict): raise ValueError("世界状态写入后校验失败：根对象不是 dict")
            for req in ("start_time", "current_time", "location", "world_rules"):
                if req not in verify_world: raise ValueError(f"世界状态写入后校验失败：缺少字段 {req}")
        except Exception as e:
            if os.path.isfile(world_tmp):
                try: os.remove(world_tmp)
                except Exception: pass
            raise ValueError(f"保存世界状态失败：{e}")

        state_changes = getattr(self, '_last_state_changes', {})
        chapter_number = getattr(self, '_last_chapter_number', 0)
        current_pid = getattr(self, 'current_project_id', None)
        
        if state_changes and chapter_number > 0 and current_pid:
            illegal_chars = '/\\:*?"<>|'
            char_dir = os.path.join("novel_projects", str(current_pid), "03_设定", "角色卡")
            if os.path.isdir(char_dir):
                timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                for char_name, change_desc in state_changes.items():
                    safe_char_name = char_name
                    for ch in illegal_chars: safe_char_name = safe_char_name.replace(ch, '')
                    if not safe_char_name or not change_desc: continue
                    
                    char_card_path = os.path.join(char_dir, f"角色卡_{safe_char_name}.md")
                    if os.path.isfile(char_card_path):
                        with open(char_card_path, 'a', encoding='utf-8') as f:
                            f.write(f"=== [{timestamp}] 第{chapter_number}章 ===\n{change_desc}\n\n")

    def _append_thinking_log(self, entry: Dict[str, Any]) -> None:
        """以 JSON Lines 形式追加一条思考日志条目。采用原子替换方式写入。"""
        state_dir = getattr(config, 'STATE_DIR', 'state')
        thinking_file = getattr(config, 'THINKING_LOG_FILE', 'thought_log.json')
        thinking_path = os.path.join(state_dir, thinking_file)
        tmp_path = thinking_path + ".tmp"

        try:
            existing = []
            if os.path.isfile(thinking_path):
                with open(thinking_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line: existing.append(line)

            new_line = json.dumps(entry, ensure_ascii=False)
            with open(tmp_path, "w", encoding="utf-8") as f:
                for line in existing: f.write(line + "\n")
                f.write(new_line + "\n")
                f.flush(); os.fsync(f.fileno())

            os.replace(tmp_path, thinking_path)

            with open(thinking_path, "r", encoding="utf-8") as f:
                lines = [l.strip() for l in f if l.strip()]
            if not lines: raise ValueError("思考日志写入后验证失败：文件为空")
            json.loads(lines[-1])
        except Exception as e:
            if os.path.isfile(tmp_path):
                try: os.remove(tmp_path)
                except Exception: pass
            raise ValueError(f"追加思考日志失败：{e}")

    def _build_precond_summary(self, role_state: Dict[str, Any], world_state: Dict[str, Any], conflict: Dict[str, Any], thinking_count: int) -> str:
        """根据角色、世界、冲突和思考日志条目数生成人类可读的确认摘要。"""
        char_name = role_state.get("name", "未知")
        char_health = role_state.get("health", 0)
        char_belief = role_state.get("belief", "")
        char_injury = role_state.get("injury", "无")
        world_time = world_state.get("current_time", "未知")
        world_loc = world_state.get("location", "未知")
        world_season = world_state.get("season", "未知")
        world_rules = world_state.get("world_rules", "无特殊规则")
        conflict_statement = conflict.get("statement", "未定义")
        think_line = f"思考过程日志已记录（共 {thinking_count} 条），可随时查询。"

        return (
            f"✅ 前置准备已完成：\n"
            f"• 主角：{char_name}（体力：{char_health}/100，伤势：{char_injury}，初始信念：{char_belief}）\n"
            f"• 世界：当前时间点 {world_time}，地点 {world_loc}，季节 {world_season}，世界规则：{world_rules}\n"
            f"• 核心冲突：{conflict_statement}\n"
            f"• {think_line}"
        )

    def _set_precond_ready(self) -> None:
        """创建前置完成标志文件（仅含时间戳）。"""
        try:
            state_dir = getattr(config, 'STATE_DIR', 'state')
            precond_flag = getattr(config, 'PRECOND_FLAG', 'precond_ready.flag')
            flag_path = os.path.join(state_dir, precond_flag)
            os.makedirs(os.path.dirname(flag_path), exist_ok=True)
            with open(flag_path, 'w', encoding='utf-8') as f:
                f.write(str(time.time()))
                f.flush(); os.fsync(f.fileno())
            self._log("✅ 前置完成标志文件已创建", level="INFO")
        except Exception as e:
            self._log(f"❌ 创建前置完成标志失败：{e}", level="ERROR")
            raise

    def _precond_ready_exists(self) -> bool:
        """仅检查前置完成标志文件是否存在。"""
        try:
            state_dir = getattr(config, 'STATE_DIR', 'state')
            precond_flag = getattr(config, 'PRECOND_FLAG', 'precond_ready.flag')
            flag_path = os.path.join(state_dir, precond_flag)
            return os.path.isfile(flag_path)
        except Exception:
            return False
