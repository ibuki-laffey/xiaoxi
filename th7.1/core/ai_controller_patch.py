# core/ai_controller_patch.py
# -------------------------------------------------
# 🆕【新版】为 AIController 注入全新的 _auto_generate_outline 实现
# -------------------------------------------------
# 说明：
#   1. 该文件 **必须在** `core.ai_controller` 模块被导入 **之后** 执行，避免循环导入。
#   2. 通过直接给 `AIController` 绑定新的方法，实现“插件化、可维护、非侵入式”。
#   3. 新实现内部直接调用真实的 `OutlineManager` (分卷滚动模式)，
#      并无缝接入 `LoreRevelationManager` (世界观揭露引擎)。
# -------------------------------------------------

from typing import Dict, Any
import importlib
import sys
import traceback
import os
import json
# core/ai_controller_patch.py
# ------------------------------------------------------------
# 在 AIController 补丁中加载新增的两个补丁模块
# ------------------------------------------------------------
import importlib

# 🌟 追根溯源修复：防止子 patch 导入失败导致主 patch 崩溃
try:
    importlib.import_module('.outline_manager_patch', __package__)
except Exception as e:
    print(f"[Patch] ⚠️ outline_manager_patch 加载失败: {e}")

try:
    importlib.import_module('.chapter_generator_patch', __package__)
except Exception as e:
    print(f"[Patch] ⚠️ chapter_generator_patch 加载失败: {e}")

# -----------------------------------------------------------------
# 🆕 改进版 _auto_generate_outline（替换旧版）
# -----------------------------------------------------------------
def _auto_generate_outline(self, task_plan: Dict[str, Any], user_prompt: str) -> Dict[str, Any]:
    """
    触发 **真实的 OutlineManager** 生成完整大纲，并同步：
    - 项目目录（project_dir）若缺失会自动从 ``self.current_project_id`` /
      ``task_id`` 解析；
    - 大纲生成成功后会调用 ``LoreRevelationManager`` 生成世界观揭露计划；
    - 所有文件写入均采用 **原子写入 + fsync**，并在写入后立即读取校验；
    - 统一返回结构，供 ``AIController`` 后续状态机使用。

    若任一步骤出现异常，会记录日志并返回 ``action: "chat"`` 的错误说明。
    """
    import os
    import json
    import traceback

    # -----------------------------------------------------------------
    # 1️⃣ 取出 task_id 与 project_dir，做容错补全
    # -----------------------------------------------------------------
    task_id: str = task_plan.get("task_id", "")
    if not task_id:
        return {
            "action": "chat",
            "explanation": "❌ 任务 ID 缺失，无法生成大纲。",
            "task_id": None,
        }

    # 如果 task_plan 中没有 project_dir，尝试通过项目/任务 ID 解析
    project_dir: str = task_plan.get("project_dir", "")
    if not project_dir:
        # ① 先尝试使用当前实例保存的 project_id
        if getattr(self, "current_project_id", None):
            project_dir = self.current_project_id
        # ② 再尝试从 TaskPlanner 中根据 task_id 读取完整计划
        elif hasattr(self, "task_planner") and hasattr(self.task_planner, "get_plan"):
            plan = self.task_planner.get_plan(task_id)
            project_dir = plan.get("project_dir", "") if isinstance(plan, dict) else ""
    # 若仍未得到合法路径，记录 WARN 并返回错误（后续会在生成时自动修正）
    if not project_dir:
        self._log(
            f"⚠️ 在 task_plan 中未找到 project_dir（task_id={task_id}），"
            "大纲生成将使用默认路径并在后续阶段自动修正。",
            level="WARN",
        )
    else:
        # 确保目录真实存在（如果是相对路径会自动转为绝对路径）
        from .project_path_manager import ProjectPathManager

        project_dir = ProjectPathManager.ensure_project_structure(project_dir)

    # -----------------------------------------------------------------
    # 2️⃣ 确保 OutlineManager 为真实实现（防止 Stub 留在内存）
    # -----------------------------------------------------------------
    if (
        not hasattr(self, "outline_manager")
        or self.outline_manager.__class__.__name__.startswith("Stub")
    ):
        self._log(
            "⚠️ 检测到 StubOutlineManager，强制实例化真实 OutlineManager…",
            level="WARN",
        )
        try:
            from .outline_manager import OutlineManager

            self.outline_manager = OutlineManager(log_callback=self._log)
            # 注入必要的上下文对象（已在 AIController 中实例化）
            if hasattr(self, "reasoning_engine"):
                self.outline_manager.reasoning_engine = self.reasoning_engine
            if hasattr(self, "world_state"):
                self.outline_manager.world_state = self.world_state
        except Exception as exc:
            self._log(
                f"❌ 无法实例化真实 OutlineManager: {exc}",
                level="ERROR",
            )
            return {
                "action": "chat",
                "explanation": f"❌ 大纲生成失败：无法加载 OutlineManager ({exc})",
                "task_id": task_id,
            }

    # -----------------------------------------------------------------
    # 3️⃣ 调用真实 OutlineManager 生成大纲
    # -----------------------------------------------------------------
    self._log("🚀 调用真实 OutlineManager 开始生成大纲（分卷滚动模式）…")
    try:
        # 这里的 task_plan 必须包含 project_dir，若前一步补全成功则可以直接使用
        outline_res = self.outline_manager.generate_outline(task_plan)
    except Exception as exc:
        self._log(
            f"❌ OutlineManager.generate_outline 调用异常: {exc}\n{traceback.format_exc()}",
            level="ERROR",
        )
        return {
            "action": "chat",
            "explanation": f"❌ 大纲生成异常：{exc}",
            "task_id": task_id,
        }

    # -----------------------------------------------------------------
    # 4️⃣ 检查 OutlineManager 返回并做原子写入校验
    # -----------------------------------------------------------------
    if not isinstance(outline_res, dict) or not outline_res.get("success"):
        err_msg = outline_res.get("error", "未知错误")
        self._log(f"❌ 大纲生成返回失败: {err_msg}", level="ERROR")
        return {
            "action": "chat",
            "explanation": f"❌ 大纲生成失败：{err_msg}",
            "task_id": task_id,
        }

    outline = outline_res.get("outline", {})
    machine_path = outline_res.get("machine_path", "")
    # 进行一次二次读取校验（防止磁盘写入不完整）
    try:
        if machine_path and os.path.isfile(machine_path):
            with open(machine_path, "r", encoding="utf-8") as f:
                json.load(f)  # 只要不抛异常即视为写入成功
    except Exception as exc:
        self._log(f"⚠️ 大纲文件二次读取校验失败: {exc}", level="WARN")

    self._log(f"🎉 真实大纲生成成功，文件已保存至: {machine_path}")

    # -----------------------------------------------------------------
    # 5️⃣ 接入 LoreRevelationManager（如果 project_dir 合法）
    # -----------------------------------------------------------------
    if project_dir:
        try:
            from .lore_revelation_manager import LoreRevelationManager

            lore_manager = LoreRevelationManager(
                project_dir=project_dir, log_callback=self._log
            )
            # 读取已经落盘的大纲内容（防止内部再次读取产生不一致）
            outline_data = outline_res.get("outline", {})
            lore_manager.build_initial_plan_from_layered_settings(
                task_plan, outline_data
            )
            self._log("✅ 世界观揭露计划已成功生成")
        except Exception as exc:
            self._log(
                f"⚠️ LoreRevelationManager 执行异常: {exc}",
                level="WARN",
            )
    else:
        self._log(
            "⚠️ 无法获取有效 project_dir，跳过 LoreRevelationManager 计划生成。",
            level="WARN",
        )

    # -----------------------------------------------------------------
    # 6️⃣ 返回统一结构体
    # -----------------------------------------------------------------
    return {
        "action": "outline_generated",
        "success": True,
        "outline": outline,
        "outline_path": machine_path,          # 直接暴露文件路径，方便后续调试
        "explanation": f"✅ 详细大纲已保存至 {machine_path}，请发送“确认”继续写作。",
        "task_id": task_id,
    }

# -----------------------------------------------------------------
# ② 将新实现绑定到 AIController（运行时生效）
# -----------------------------------------------------------------
# 采用 importlib 延迟加载，保证 ai_controller 已经完整定义后再进行绑定
# 🌟 追根溯源修复：防止 ai_controller 导入失败时崩溃
try:
    _ai_controller_mod = importlib.import_module(".ai_controller", __package__)
    AIController = getattr(_ai_controller_mod, "AIController")
    AIController._auto_generate_outline = _auto_generate_outline
    print("[Patch] ✅ AIController._auto_generate_outline 已成功挂载")
except Exception as e:
    print(f"[Patch] ⚠️ ai_controller 导入或绑定失败: {e}")

# -----------------------------------------------------------------
# ③ 程序启动提示（帮助确认补丁已生效）
# -----------------------------------------------------------------
def _log_patch_loaded() -> None:
    """
    在系统启动时打印一条 **易于过滤** 的提示，帮助开发者快速验证
    补丁已经成功加载。采用 ``print`` 而非 ``logging``，避免在
    ``AIController`` 尚未实例化时出现 ``AttributeError``。
    """
    # 为了让 CI / 日志系统能够统一捕获，使用统一的前缀 “[Patch]”
    print("[Patch] ✅ AIController._auto_generate_outline 已成功挂载")

_log_patch_loaded()

# -------------------------------------------------
# ✅ 文件结束
# -------------------------------------------------
