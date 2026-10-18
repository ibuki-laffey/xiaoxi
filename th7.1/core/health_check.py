import json
from datetime import datetime
from typing import Dict, Any, List

from .config import (
    HEALTH_CHECK_AUTO_REGENERATE, HEALTH_CHECK_STRICT_MODE,
    CHARACTER_GROWTH_ENABLED, INTERNAL_LOG_ENABLED,
)

class HealthChecker:
    """HealthChecker：负责所有章节健康检查的业务逻辑。"""
    def __init__(self, controller):
        self.controller = controller
        self._log = controller._log

    def run(self, task_id: str, current_chapter: int) -> Dict[str, Any]:
        """执行一次完整的健康检查，并在必要时自动触发大纲重建。"""
        result: Dict[str, Any] = {
            "success": True, "errors": [], "warnings": [],
            "checked_at": datetime.now().isoformat(), "chapter": current_chapter,
            "error_type": "", "error_message": "", "auto_regenerated": False,
        }

        # 🌟 核心修复：增加 hasattr 防弹检查，防止子模块未注入时崩溃
        outline_manager = getattr(self.controller, 'outline_manager', None)
        outline = outline_manager.get_outline(task_id) if outline_manager and hasattr(outline_manager, 'get_outline') else None
        
        if not outline:
            result["success"] = False
            result["errors"].append({"type": "outline_missing", "message": "大纲文件丢失"})
            result["error_type"] = "outline_missing"
            result["error_message"] = "未能在磁盘或数据库中读取到对应的大纲文件"
            self._log("❌ 健康检查：大纲文件缺失", level="WARN")

            if HEALTH_CHECK_AUTO_REGENERATE:
                self._log("🧠 健康检查：尝试自动重建大纲（AUTO_REGENERATE 开启）", level="INFO")
                task_planner = getattr(self.controller, 'task_planner', None)
                plan = task_planner.get_plan(task_id) if task_planner and hasattr(task_planner, 'get_plan') else {}
                
                if hasattr(self.controller, '_auto_generate_outline'):
                    auto_res = self.controller._auto_generate_outline(plan, f"健康检查自动重建（章节 {current_chapter}）")
                    if auto_res.get("action") == "outline_generated":
                        self._log("✅ 健康检查：自动重建大纲成功", level="INFO")
                        result["warnings"].append({"type": "outline_regenerated", "message": "已自动重建大纲"})
                        result["success"] = True
                        result["auto_regenerated"] = True
                    else:
                        err_msg = auto_res.get("explanation", "未知错误")
                        self._log(f"⚠️ 健康检查：自动重建大纲失败 – {err_msg}", level="WARN")
                        result["error_message"] = f"自动重建失败: {err_msg}"

            if HEALTH_CHECK_STRICT_MODE:
                return result

        if CHARACTER_GROWTH_ENABLED:
            world_state = getattr(self.controller, 'world_state', None)
            if world_state and hasattr(world_state, 'get_characters'):
                for char in world_state.get_characters():
                    cid = char.get('id')
                    cur_status = world_state.get_character_status(cid, current_chapter) if hasattr(world_state, 'get_character_status') else None
                    exp_status = world_state.get_expected_character_status(cid, current_chapter) if hasattr(world_state, 'get_expected_character_status') else None
                    if cur_status and exp_status and cur_status != exp_status:
                        result["warnings"].append({
                            "type": "character_state_mismatch", "character": char.get('name'),
                            "current": cur_status, "expected": exp_status,
                        })
                        self._log(f"⚠️ 健康检查：角色 {char.get('name')} 状态不一致", level="WARN")

        if not result["success"] or result["warnings"]:
            self._log("🧠 健康检查发现问题，调用 ReasoningEngine 生成修复方案", level="INFO")
            reasoning_engine = getattr(self.controller, 'reasoning_engine', None)
            fix_plan = {}
            if reasoning_engine and hasattr(reasoning_engine, 'generate_health_check_fix_plan'):
                fix_plan = reasoning_engine.generate_health_check_fix_plan(task_id, current_chapter, result)
            result["fix_plan"] = fix_plan

            if not result["success"]:
                learning_engine = getattr(self.controller, 'learning_engine', None)
                if learning_engine and hasattr(learning_engine, 'save_cross_project_experience'):
                    learning_engine.save_cross_project_experience(
                        project_id=getattr(self.controller, 'current_project_id', None),
                        topic="健康检查错误", solution=json.dumps(fix_plan, ensure_ascii=False),
                        tags=["health_check", "error_recovery"],
                    )

        if INTERNAL_LOG_ENABLED:
            self._log("📝 健康检查日志已写入（INTERNAL_LOG_ENABLED）", level="DEBUG")

        return result
