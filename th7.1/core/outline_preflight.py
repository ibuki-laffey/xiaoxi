# core/outline_preflight.py
# --------------------------------------------------------------
# 大纲前置检查模块（Outline Pre‑Flight）
# 负责在正式调用 OutlineManager.generate_outline 前完成：
#   1️⃣ 元信息抽取（标题、题材、主角、字数、章节数…）
#   2️⃣ 任务计划完整性校验 & 必要字段自动补全
#   3️⃣ project_id & 项目目录合法性检查 / 自动创建
#   4️⃣ 向量记忆库相似大纲检索（为 LLM 提供参考示例）
#   5️⃣ 生成 “结构化的大纲生成 Prompt”
#   6️⃣ 统一日志写入（支持结构化日志）
# --------------------------------------------------------------

import os
import re
import json
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple, Union

# 项目内部模块引用（均已实现）
from .task_planner import TaskPlanner
from .outline_manager import OutlineManager
from .reasoning_engine import ReasoningEngine
from .vector_memory import VectorMemory
from .intent_analyzer import IntentAnalyzer
from .config import (
    PROJECT_BLUEPRINT_VALIDATION_ENABLED,
    INTERNAL_LOG_ENABLED,
    MAX_OUTLINE_SIMILARITY,
    TOPIC_STORAGE_DIR,
    LOG_DEDUPLICATION,
)
from .log_mixin import LogMixin
from .paths import NOVEL_PROJECTS_DIR
from .path_utils import safe_join, ensure_dir


# ----------------------------------------------------------------------
# 常量区 – 如需统一管理，请在 config 中统一设置
# ----------------------------------------------------------------------
DEFAULTS = {
    "task_type": "novel",
    "genre": "通用",
    "total_words": 0,
    "total_chapters": 1,
    "words_per_chapter": 2500,
    "style": "自然流畅",
    "special_requirements": [],
    "prohibitions": [],
    "file_naming_preference": "每章一个文件，按章节号排序",
    "generation_mode": "逐章",
    "batch_size": 1,
    "title": "未命名",
    "main_character": "未知",
}
# LLM 生成大纲时使用的温度范围（从低到高尝试）
OUTLINE_TEMPERATURES = [0.3, 0.5, 0.7, 0.85, 0.95, 1.0]


# ----------------------------------------------------------------------
# 辅助函数 – 正则抽取（在 TaskPlanner 里也有类似实现，但这里用于
#            前置检查的“快速抽取”，避免依赖模型）
# ----------------------------------------------------------------------
def _regex_extract_metadata(user_prompt: str) -> Dict[str, Any]:
    """
    使用轻量正则从原始提示中抓取可能缺失的核心字段。
    返回的 dict 仅包含成功匹配的键，后续会与默认值合并。
    """
    meta: Dict[str, Any] = {}

    # 标题（《…》或 “...”）
    title_match = re.search(r'《([^》]+)》|\"([^\"]+)\"', user_prompt)
    if title_match:
        meta["title"] = title_match.group(1) or title_match.group(2)

    # 题材（“题材：”后直到逗号/句号/空格）
    genre_match = re.search(r'题材[:：]\s*([^\，,.]+)', user_prompt)
    if genre_match:
        meta["genre"] = genre_match.group(1).strip()

    # 世界类型（可选，用于后续蓝图校验）
    world_type_match = re.search(r'世界类型[:：]\s*([^\，,.]+)', user_prompt)
    if world_type_match:
        meta["world_type"] = world_type_match.group(1).strip()

    # 主角名
    char_match = re.search(r'主角(?:名字)?叫?([\w]+)', user_prompt)
    if char_match:
        meta["main_character"] = char_match.group(1).strip()

    # 总字数（支持 “x 万字”）
    total_words_match = re.search(r'总字数[:：]\s*([\d,.]+)\s*万字', user_prompt)
    if total_words_match:
        num = total_words_match.group(1).replace(',', '').replace('.', '')
        try:
            meta["total_words"] = int(num) * 10000
        except Exception:
            pass

    # 每章字数
    wpc_match = re.search(r'每章字数[:：]\s*([\d,]+)', user_prompt)
    if wpc_match:
        try:
            meta["words_per_chapter"] = int(wpc_match.group(1).replace(',', ''))
        except Exception:
            pass

    # 自动推算章节数（若只给了总字数或每章字数但未给章节数）
    if (
        "total_words" in meta
        and "words_per_chapter" in meta
        and "total_chapters" not in meta
    ):
        try:
            meta["total_chapters"] = max(
                1, meta["total_words"] // meta["words_per_chapter"]
            )
        except Exception:
            pass

    return meta


# ----------------------------------------------------------------------
# 主类 – OutlinePreflight
# ----------------------------------------------------------------------
class OutlinePreflight(LogMixin):
    """
    大纲生成前置检查的核心类。

    使用方式（在 AIController 中）::

        preflight = OutlinePreflight(self)        # self 为 AIController 实例
        result = preflight.run(user_prompt)      # 返回 dict，包含
                                                #   * task_plan（已完整、已校验）
                                                #   * outline_prompt（用于 LLM）
                                                #   * similar_outlines（向量记忆检索结果）
                                                #   * metadata（抽取的元信息）
    """

    def __init__(
        self,
        controller_or_log: Union[Any, None] = None,
        log_callback: Optional[Any] = None,
    ) -> None:
        """
        参数
        ----
        controller_or_log – 可以是完整的 ``AIController`` 实例，也可以是
                            仅提供日志回调的 ``log_callback``（兼容旧调用）。
        log_callback – 可选的日志回调；若 ``controller_or_log`` 为
                      ``AIController``，则默认使用其 ``log_callback``。
        """
        # -----------------------------------------------------------------
        # 兼容两种构造方式（老代码：OutlinePreflight(log_callback)）
        # -----------------------------------------------------------------
        if controller_or_log is not None and hasattr(controller_or_log, "task_planner"):
            # 传入的是完整的 controller 实例
            self.controller = controller_or_log
            self.log_callback = log_callback or getattr(self.controller, "log_callback", None)
        else:
            # 仅传入了日志回调（旧调用方式），controller 需要后续手动注入
            self.controller = None
            self.log_callback = (
                controller_or_log
                if callable(controller_or_log) and log_callback is None
                else log_callback
            )

        # LogMixin 本身可能在未来实现 __init__，安全调用 super()
        super().__init__()   # 只传递 self，避免 TypeError

        # 若已拿到 controller，立即绑定子组件的快捷引用
        if self.controller:
            self.task_planner: TaskPlanner = self.controller.task_planner
            self.outline_manager: OutlineManager = self.controller.outline_manager
            self.reasoning_engine: ReasoningEngine = self.controller.reasoning_engine
            self.vector_memory: VectorMemory = self.controller.vector_memory
            self.intent_analyzer: IntentAnalyzer = self.controller.intent_analyzer
        else:
            # 延迟绑定的占位属性，后续使用前会检查并抛出明确错误
            self.task_planner = None  # type: ignore
            self.outline_manager = None  # type: ignore
            self.reasoning_engine = None  # type: ignore
            self.vector_memory = None  # type: ignore
            self.intent_analyzer = None  # type: ignore

    # ------------------------------------------------------------------
    # 1️⃣ 抽取并合并元信息（正则 + IntentAnalyzer 双保险）
    # ------------------------------------------------------------------
    def _gather_metadata(self, user_prompt: str) -> Dict[str, Any]:
        """
        先使用轻量正则抽取，再交给 IntentAnalyzer 完整解析。
        两者的结果深度合并（IntentAnalyzer 的结果拥有更高优先级）。
        """
        # ① 正则抽取（速度快，覆盖常见字段）
        regex_meta = _regex_extract_metadata(user_prompt)

        # ② IntentAnalyzer 完整抽取（可能返回更多细节）
        intent = (
            self.intent_analyzer.analyze_intent(
                user_prompt,
                context={},  # 大纲前置不需要外部上下文
            )
            or {}
        )
        intent_meta = intent.get("extracted_metadata", {})
        if not isinstance(intent_meta, dict):
            intent_meta = {}

        # 合并：IntentAnalyzer > 正则 > 默认
        merged = {**regex_meta, **intent_meta}
        return merged

    # ------------------------------------------------------------------
    # 2️⃣ 完整任务计划校验 & 自动补全（核心）
    # ------------------------------------------------------------------
    def _ensure_task_plan(self, user_prompt: str) -> Dict[str, Any]:
        """
        返回 **完整、合法** 的任务计划 dict。
        - 若已有 pending 任务则直接返回，防止重复创建；
        - 若没有则调用 ``TaskPlanner.parse_user_request`` 生成新计划；
        - 合并元信息（标题、题材、字数等）；
        - 确保关键字段都有默认值，自动计算章节数；
        - 最后写回磁盘（若是新建则安全写入，已有的已经在前面读取）。
        """
        # ① 检查是否已有 pending 任务（防止重复创建）
        pending = self.task_planner.list_pending_tasks()
        if pending:
            plan = pending[0]  # 测试环境仅会有一个 pending
            self._log(f"✅ 检测到已存在的 pending 任务 → task_id={plan.get('task_id')}")
        else:
            # ② 生成全新计划
            plan = self.task_planner.parse_user_request(user_prompt)
            self._log(f"✅ 已生成全新任务计划 → task_id={plan.get('task_id')}")

        # ③ 合并元信息（title、genre、字数等）
        meta = self._gather_metadata(user_prompt)
        for k, v in meta.items():
            if v is not None:
                plan[k] = v

        # ④ 确保关键字段的默认值（防止 KeyError）
        for k, v in DEFAULTS.items():
            plan.setdefault(k, v)

        # ⑤ 计算章节数（若只给了总字数或每章字数）
        if not plan.get("total_chapters"):
            try:
                total = int(plan.get("total_words", 0))
                per = int(plan.get("words_per_chapter", 2500))
                plan["total_chapters"] = max(1, total // per) if per else 1
            except Exception:
                plan["total_chapters"] = 1

        # ⑥ 确保 project_id 已写入计划文件（若仍是占位则写入真实值）
        task_id = plan.get("task_id")
        if task_id:
            real_project_id = (
                getattr(self.controller, "current_project_id", None) or ""
            )
            self.task_planner.ensure_project_id(task_id, real_project_id)

        # ⑦ 最终写回（若是新建则安全写入，已有的已经在前面读取）
        self.task_planner._save_plan(plan)

        self._log("✅ 任务计划已完整校验并写回磁盘")
        return plan

    # ------------------------------------------------------------------
    # 3️⃣ 项目目录合法性检查（自动创建或纠正）
    # ------------------------------------------------------------------
    def _ensure_project_directory(self, project_id: str) -> str:
        """
        基于 ``project_id`` 创建（或验证）项目根目录结构。
        返回项目根目录的 **绝对路径**，供后续 Prompt 使用。
        """
        # 项目根目录 = NOVEL_PROJECTS_DIR / <project_id>
        project_dir = safe_join(str(NOVEL_PROJECTS_DIR), project_id)
        ensure_dir(project_dir)  # 若不存在自动创建

        # 自动创建常用子目录（仅首次创建时会生效）
        sub_dirs = ["01_大纲", "02_正文", "system"]
        for sub in sub_dirs:
            ensure_dir(safe_join(project_dir, sub))

        self._log(f"✅ 项目目录已准备好 → {project_dir}")
        return project_dir

    # ------------------------------------------------------------------
    # 4️⃣ 向量记忆相似大纲检索（为 LLM 提供参考示例）
    # ------------------------------------------------------------------
    def _retrieve_similar_outlines(self, plan: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        根据 ``genre``、``title``、``main_character`` 等字段在
        ``VectorMemory`` 中检索相似的大纲模板（最多 3 条）。
        若 VectorMemory 未实现对应方法，则安全降级返回空列表。
        """
        if not hasattr(self.vector_memory, "search_similar_outline"):
            self._log(
                "⚠️ VectorMemory 未实现 search_similar_outline，跳过相似大纲检索",
                level="WARN",
            )
            return []

        query = {
            "genre": plan.get("genre", ""),
            "title": plan.get("title", ""),
            "main_character": plan.get("main_character", ""),
        }
        try:
            results = self.vector_memory.search_similar_outline(query, top_k=3)
            outlines: List[Dict[str, Any]] = []
            for r in results:
                if isinstance(r, dict):
                    outlines.append(r)
                elif isinstance(r, str) and os.path.isfile(r):
                    with open(r, "r", encoding="utf-8") as f:
                        outlines.append(json.load(f))
            self._log(f"✅ 检索到 {len(outlines)} 条相似大纲用于参考")
            return outlines
        except Exception as e:
            self._log(f"⚠️ 相似大纲检索异常: {e}", level="WARN")
            return []

    # ------------------------------------------------------------------
    # 5️⃣ 组装用于 LLM 的大纲生成 Prompt（最高质量提示）
    # ------------------------------------------------------------------
    def _build_outline_prompt(
        self,
        plan: Dict[str, Any],
        similar_outlines: List[Dict[str, Any]],
    ) -> str:
        """
        返回 **完整的 Prompt 文本**，包含：
        - 任务的全部结构化元信息（title、genre、章节数、字数、主角、风格、特殊需求、禁止事项）；
        - 若开启蓝图校验且提供 ``world_type``，则在 Prompt 中加入蓝图校验约束；
        - 若有相似大纲，则在 Prompt 中提供示例（使用 JSON 包装，方便 LLM 直接解析）；
        - 明确的 “不使用 AI 常用套话” 约束（提升语言质量）。
        """
        # 基础信息块
        base_info = {
            "title": plan.get("title"),
            "genre": plan.get("genre"),
            "total_words": plan.get("total_words"),
            "total_chapters": plan.get("total_chapters"),
            "words_per_chapter": plan.get("words_per_chapter"),
            "main_character": plan.get("main_character"),
            "style": plan.get("style"),
            "special_requirements": plan.get("special_requirements"),
            "prohibitions": plan.get("prohibitions"),
        }

        # 蓝图验证（若开启且提供 world_type）
        blueprint_section = ""
        if PROJECT_BLUEPRINT_VALIDATION_ENABLED and plan.get("world_type"):
            blueprint_section = (
                f"\n--- 项目蓝图验证 ---\n"
                f"世界类型: {plan.get('world_type')}\n"
                "请在生成大纲前对蓝图进行一致性校验，若发现冲突请在返回的 JSON 中添加字段 "
                "`\"blueprint_issues\": [...]` 并给出具体问题说明。\n"
            )

        # 相似大纲示例（如果有检索结果）
        similar_section = ""
        if similar_outlines:
            similar_json = json.dumps(similar_outlines, ensure_ascii=False, indent=2)
            similar_section = (
                "\n--- 参考相似大纲（仅供参考，必须原创）---\n"
                f"{similar_json}\n"
            )

        # 禁止套话约束
        cliche_section = (
            "请务必 **避免** 使用以下套话词汇，每句话必须保持自然、流畅、原创：\n"
            "仿佛、似乎、无疑、值得注意的是、总而言之、综上所述、可以看到、"
            "不难发现\n"
        )

        # 完整 Prompt
        prompt = (
            "请基于以下任务信息生成完整的小说大纲（返回 JSON，字段见下方示例）。\n"
            f"{json.dumps(base_info, ensure_ascii=False, indent=2)}\n"
            f"{blueprint_section}"
            f"{similar_section}"
            f"{cliche_section}"
            "**生成要求**：\n"
            "1. 返回 JSON，顶层键必须为 `outline`，其值为对象，包含 `title`、`genre`、`chapters`（列表）等。\n"
            "2. `chapters` 列表每项必须包含 `title`、`summary`（不少于 150 字）以及 `keywords`（列表）。\n"
            "3. 严格遵守 `words_per_chapter` 目标字数范围。\n"
            "4. 如发现蓝图冲突，请在返回中加入 `blueprint_issues` 字段并说明。\n"
            "5. 使用 **中文**，保持人类作者般的表达风格。\n"
        )
        self._log("✅ 大纲生成 Prompt 已构建完毕")
        return prompt

    # ------------------------------------------------------------------
    # 6️⃣ 主入口 – 完整的前置检查 & Prompt 返回
    # ------------------------------------------------------------------
    def run(self, user_prompt: str) -> Dict[str, Any]:
        """
        对外唯一公开的方法。

        返回值结构示例::

            {
                "task_plan": {...},          # 完整、已补全的任务计划
                "project_dir": "/abs/path",  # 项目根目录（已确保存在）
                "outline_prompt": "...",    # 用于 LLM 的最终 Prompt
                "similar_outlines": [...],  # 向量记忆检索的相似示例
                "metadata": {...}            # 正则+Intent 抽取的原始元信息
            }

        若任何一步出现致命错误会抛出异常，调用方可捕获并执行
        ``TaskPlanner.create_fallback_plan`` 等回退策略。
        """
        try:
            # 1️⃣ 任务计划（新建或使用已有 pending）
            plan = self._ensure_task_plan(user_prompt)

            # 2️⃣ 项目目录（确保存在且合法）
            project_id = plan.get("project_id") or getattr(
                self.controller, "current_project_id", ""
            )
            if not project_id:
                raise RuntimeError("project_id 仍为空，无法继续")
            project_dir = self._ensure_project_directory(project_id)

            # 3️⃣ 向量记忆相似大纲检索（可选）
            similar = self._retrieve_similar_outlines(plan)

            # 4️⃣ 最终 Prompt 组装（包含所有质量约束）
            outline_prompt = self._build_outline_prompt(plan, similar)

            # 5️⃣ 返回完整结果
            result = {
                "task_plan": plan,
                "project_dir": project_dir,
                "outline_prompt": outline_prompt,
                "similar_outlines": similar,
                "metadata": self._gather_metadata(user_prompt),
            }
            self._log("✅ 大纲前置检查全部通过，返回完整结果")
            return result

        except Exception as exc:
            # 统一记录日志后向上抛出，让 AIController 决定 fallback 策略
            self._log(f"❌ 大纲前置检查失败: {exc}", level="ERROR")
            raise
