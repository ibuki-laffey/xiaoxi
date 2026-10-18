# core/ai_controller.py
# ==================== AI 核心控制器（完整增强版 - 质量优先）====================
# 功能：AI 核心交互控制，协调所有子模块，支持中断、修改、恢复
# 新增：中断处理集成、章节卡片、状态对账、用户编辑优先
# 新增：健康检查触发器（每 10 章）、AI 自主决策引擎、工程蓝图验证集成

# -*- coding: utf-8 -*-
# core/ai_controller.py
# ==============================================================================
# AI 核心控制器（完整增强版 - 质量优先）
# ==============================================================================

# ---------------------- 1. 标准库导入 ----------------------
import os
import sys
import json
import time
import glob
import hashlib
import gc
import re
import threading
import pickle
import subprocess
import traceback
from datetime import datetime
from typing import Dict, Any, List, Optional
from pathlib import Path

# ---------------------- 2. 第三方库导入 ----------------------
import requests

# ---------------------- 3. 本地配置与常量导入 ----------------------
from . import config as config
from .config import (
    OLLAMA_URL, MODEL_NAME, KNOWLEDGE_BASE_DIR, DEFAULT_SAVE_DIR,
    DATA_CONFIG_DIR, TASK_TRACKING_DIR, NOVEL_PROJECTS_DIR,
    REQUEST_TIMEOUT, SAFE_TIMEOUT, PROFESSIONAL_LOG_ENABLED,
    PROJECT_LOG_ISOLATION, ATOMIC_OPERATION_ENABLED,
    HEALTH_CHECK_INTERVAL_CHAPTERS, SILENT_HEALTH_CHECK,
    AUTO_RECONSTRUCT_OUTLINE_ENABLED, AI_AUTONOMY_LEVEL,
    PROJECT_BLUEPRINT_VALIDATION_ENABLED, INTERNAL_LOG_ENABLED,
    get_db_connection, close_db_connection,
    CHARACTER_GROWTH_ENABLED, WORLD_TRUTH_LEVELS,
    USE_ONLY_AI_INTENT, AUTO_OUTLINE_AFTER_CREATION,
    call_ollama_safe, MAX_OUTPUT_TOKENS,
    TOPIC_STORAGE_DIR, TOPIC_BACKEND, 
)
from .constants import (
    LOG_DEDUPLICATION,
    INTENT_CONFIDENCE_THRESHOLD,
    MAX_CHAPTER_GENERATION_RETRIES,
)

# ---------------------- 4. 本地核心子模块导入 ----------------------
# 注意：为了防止循环导入，部分重型模块（如 OutlineManager）将在 __init__ 中延迟导入
from .health_check import HealthChecker
from .timeline_manager import TimelineManager
from .scene_graph_manager import SceneGraphManager
from .code_executor import CodeExecutor
from .logger import action_logger, project_logger
from .personality import PersonalityManager
from .training import TrainingManager
from .task_tracker import TaskTracker
from .permanent_memory import PermanentMemory
from .knowledge_manager import KnowledgeManager
from .self_learning_engine import SelfLearningEngine
from .plugin_auto_loader import PluginAutoLoader
from .file_backup import FileBackupManager
from .startup_scanner import StartupScanner
from .action_executor import ActionExecutor
from .time_service import TimeService
from .disk_cache_manager import DiskCacheManager, save_conversation_to_disk, load_conversation_from_disk
from .prompt_manager import PromptManager
from .task_planner import TaskPlanner
from .chapter_generator import ChapterGenerator
from .quality_inspector import QualityInspector
from .file_manager import FileManager
from .progress_tracker import ProgressTracker
from .error_handler import ErrorHandler
from .preference_learner import PreferenceLearner
from .context_builder import ContextBuilder
from .continuation_analyzer import ContinuationAnalyzer
from .world_state_manager import WorldStateManager
from .project_template_generator import ProjectTemplateGenerator
from .reasoning_engine import ReasoningEngine
from .vector_memory import VectorMemory
from .file_cleanup_manager import FileCleanupManager
from .chapter_card_manager import ChapterCardManager
from .reconciliation_engine import ReconciliationEngine
from .interruption_handler import InterruptionHandler
from .state_manager import StateManager
from .log_mixin import LogMixin
from .ai_controller_helpers import AIControllerHelperMixin
from .topic_manager import TopicManager as _TopicManager
from .topic_manager_sqlite import SQLiteTopicManager
from .task_definition import Task, NovelWritingTask, ContentModificationTask
from .self_check_and_autofix import self_check_and_autofix
from core.prompt_builder import get_prompt_builder


# -------------------------------------------------------------
# AIController 主类（继承 LogMixin，获得 _log 方法）
# -------------------------------------------------------------
class AIController(LogMixin, AIControllerHelperMixin):
    """
    AI 核心控制器（完整增强版 - 质量优先）
    """

    # -----------------------------------------------------------------
    # ① 兼容 IntentAnalyzer 的日志回调
    # -----------------------------------------------------------------
    def _intent_log_wrapper(self, msg: str, level: str = "INFO", *args, **kwargs) -> None:
        """Wrapper for the log callback passed to IntentAnalyzer."""
        try:
            if self.log_callback is not None:
                self.log_callback(msg, level=level, *args, **kwargs)
            return
        except TypeError:
            pass
        except Exception as exc:
            print(f"[IntentLog][{level}] {msg} (fallback, original error: {exc})", flush=True)
            return

        try:
            if self.log_callback is not None:
                self.log_callback(msg)
        except Exception as exc:
            print(f"[IntentLog][{level}] {msg} (fallback, secondary error: {exc})", flush=True)

    # -----------------------------------------------------------------
    # ② 核心初始化入口
    # -----------------------------------------------------------------
    def __init__(self,
                 log_callback=None,
                 macro_recorder=None,
                 plugin_manager=None,
                 project_id: str = None):
        """AIController 初始化（去大纲、前置确认版）。"""
        
        # 1️⃣ 基础属性 & 回调
        self.log_callback      = log_callback
        self.macro_recorder    = macro_recorder
        self.plugin_manager    = plugin_manager
        self.project_id        = project_id

        # 2️⃣ 线程安全锁
        self._conversation_lock = threading.RLock()
        self._task_lock         = threading.Lock()
        self._state_lock        = threading.Lock()

        # 3️⃣ 状态与前置标志
        self.creation_allowed = False

        # 4️⃣ TopicManager
        if getattr(config, "TOPIC_BACKEND", "json") == "sqlite":
            self.topic_manager = SQLiteTopicManager(log_callback=self.log_callback)
        else:
            self.topic_manager = _TopicManager(log_callback=self.log_callback)

        if not hasattr(self.topic_manager, "log"):
            self.topic_manager.log = lambda *args, **kwargs: None

        # 5️⃣ 基础服务子模块
        self.executor          = CodeExecutor(log_callback)
        self.time_service      = TimeService(log_callback)
        self.action_executor   = ActionExecutor(plugin_manager, self.time_service, log_callback)
        self.personality       = PersonalityManager(log_callback)
        self.training          = TrainingManager(log_callback)
        self.task_tracker      = TaskTracker(log_callback)
        self.permanent_memory  = PermanentMemory(log_callback)
        self.knowledge_manager = KnowledgeManager(log_callback)
        self.learning_engine   = SelfLearningEngine(log_callback, self.knowledge_manager, plugin_manager)
        self.plugin_loader     = PluginAutoLoader(log_callback, plugin_manager)
        self.backup_manager    = FileBackupManager(log_callback)
        self.startup_scanner   = StartupScanner(log_callback, self.knowledge_manager, self.learning_engine, self.permanent_memory)
        self.timeline_manager  = TimelineManager(log_callback)
        self.scene_graph_manager = SceneGraphManager(log_callback)

        # 6️⃣ 增强器 & 其它子模块 (使用顶部已导入的模块，避免重复 import)
        from .enhanced_outline_generator import EnhancedOutlineGenerator
        self.enhanced_outline_generator = EnhancedOutlineGenerator(log_callback)

        from .outline_preflight import OutlinePreflight
        self.outline_preflight = OutlinePreflight(self, log_callback)

        self.llm_utils = call_ollama_json if 'call_ollama_json' in globals() else None
        self.code_executor = CodeExecutor(log_callback)
        self.action_logger = action_logger
        self.project_logger = project_logger

        # 7️⃣ 磁盘缓存
        self.disk_cache = DiskCacheManager(log_callback) if getattr(config, "DISK_CACHE_ENABLED", False) else None

        # 8️⃣ 任务规划器
        self.task_planner = TaskPlanner(log_callback, self)

        # ==================================================================
        # 🌟 核心修复：调整初始化顺序！
        # 必须先初始化 reasoning_engine 和 world_state，再初始化 outline_manager，
        # 确保依赖注入成功，彻底消灭“组件丢失”的隐患。
        # ==================================================================
        
        # 9️⃣ 提前初始化核心引擎（供后续组件依赖）
        self.context_builder    = ContextBuilder(log_callback)
        self.world_state        = WorldStateManager(log_callback)
        self.reasoning_engine   = ReasoningEngine(log_callback, self)
        self.state_manager      = StateManager(log_callback, self)

        # 🔟 大纲管理（依据配置动态选择真实或存根实现，并注入已初始化的引擎）
        try:
            from .config import ENABLE_OUTLINE_GENERATION
            _enable_outline = ENABLE_OUTLINE_GENERATION
        except Exception:
            _enable_outline = False

        if _enable_outline:
            try:
                from .outline_manager import OutlineManager
                self.outline_manager = OutlineManager(log_callback=self.log_callback, ai_controller=self)
                # 🌟 完美注入：此时 reasoning_engine 和 world_state 已经存在
                self.outline_manager.reasoning_engine = self.reasoning_engine
                self.outline_manager.world_state = self.world_state
                self._log("✅ 大纲管理器：已加载真实 OutlineManager (分卷滚动模式)")
            except Exception as e:
                self._log(f"⚠️ 真实 OutlineManager 加载失败 ({e})，降级为 StubOutlineManager", level="WARN")
                from .outline_stub import StubOutlineManager
                self.outline_manager = StubOutlineManager(log_callback=self.log_callback)
        else:
            from .outline_stub import StubOutlineManager
            self.outline_manager = StubOutlineManager(log_callback=self.log_callback)
            self._log("✅ 大纲管理器：已加载 StubOutlineManager (去大纲模式)")

        # 1️⃣1️⃣ 其余核心子模块
        self.chapter_generator  = ChapterGenerator(log_callback=log_callback, model_client=self, context_builder=self.context_builder, project_id=project_id)
        self.quality_inspector  = QualityInspector(log_callback, self, project_id=project_id)
        self.file_manager       = FileManager(log_callback, self)
        self.progress_tracker   = ProgressTracker(log_callback)
        self.error_handler      = ErrorHandler(log_callback)
        self.preference_learner = PreferenceLearner(log_callback, self)
        self.continuation_analyzer = ContinuationAnalyzer(log_callback, self)
        self.project_generator  = ProjectTemplateGenerator(log_callback)
        self.vector_memory      = VectorMemory(log_callback)
        self.cleanup_manager    = FileCleanupManager(log_callback, self)
        self.chapter_card_manager = ChapterCardManager(log_callback)
        self.reconciliation_engine = ReconciliationEngine(log_callback, project_id)
        self.interruption_handler = InterruptionHandler(log_callback, project_id)

        # 1️⃣2️⃣ 对话分析、计划、健康检查、意图分析
        from .dialog_analyzer import DialogAnalyzer
        self.dialog_analyzer = DialogAnalyzer(log_callback=self.log_callback, vector_memory=self.vector_memory)
        
        from .project_plan_builder import ProjectPlanBuilder
        self.project_plan_builder = ProjectPlanBuilder(log_callback=self.log_callback)

        self.health_checker = HealthChecker(self)

        from .intent_analyzer import IntentAnalyzer
        self.intent_analyzer = IntentAnalyzer(log_callback=self._intent_log_wrapper)

        # 1️⃣3️⃣ 配置、插件、学习历史加载
        try:
            self._load_robot_config()
        except Exception as e:
            self._log(f"⚠️ 调用 _load_robot_config 失败：{e}", level="WARN")

        self._load_plugin_capabilities()
        self._load_learning_history()

        # 1️⃣4️⃣ 对话历史 & 会话状态
        self.conversation_history: List[Dict[str, str]] = []
        self.last_raw_response = ""
        self.task_history: List[Dict[str, Any]] = []
        self.max_history_length = 500
        self.current_topic = "default"
        self._last_save_ts = 0.0

        # 1️⃣5️⃣ 辅助状态变量
        self.current_novel_project = None
        self.current_project_id = project_id
        self.world_context: Dict[str, Any] = {}
        self.character_database: Dict[str, Any] = {}
        self.plot_development: List[Dict[str, Any]] = []
        self.auto_generation_mode = True
        self.generation_checkpoint = None

        # 1️⃣6️⃣ 中断监听 & 话题恢复
        self.interruption_handler.start_listening()
        self._load_last_active_topic()
        self._load_conversation_history()

        # 1️⃣7️⃣ 核心状态变量
        self.current_task_id = None

        # 1️⃣8️⃣ 统一话题管理器
        self._init_topic_manager()
        self._log("✅ AI 控制器已初始化（去大纲、前置确认）")

        # 1️⃣9️⃣ 启动时清理残留的无效任务计划
        cleaned = self.task_planner.cleanup_invalid_plans()
        if cleaned:
            self._log(f"🧹 启动清理完成，移除 {cleaned} 份无效任务计划")

        # 2️⃣0️⃣ 将 llm 调用入口注入守护进程
        from .super_guardian import guardian
        guardian.set_llm_caller(call_ollama_safe)

        # ==================================================================
        # 🌟 终极兜底补丁：强制将所有初始化失败的 None 组件替换为 Dummy 对象
        # ==================================================================
        try:
            from .null_objects import (
                DummyTaskPlanner, DummyOutlineManager, DummyProgressTracker,
                DummyProjectGenerator, DummyChapterGenerator, DummyQualityInspector,
                DummyDialogAnalyzer, DummyProjectPlanBuilder, DummyLoreRevelationManager,
                DummyDeepLoreExpander, DummyEventTracker, DummyMemoryGraph
            )
            if not getattr(self, 'task_planner', None): self.task_planner = DummyTaskPlanner(self._log)
            if not getattr(self, 'outline_manager', None): self.outline_manager = DummyOutlineManager(self._log)
            if not getattr(self, 'progress_tracker', None): self.progress_tracker = DummyProgressTracker(self._log)
            if not getattr(self, 'project_generator', None): self.project_generator = DummyProjectGenerator(self._log)
            if not getattr(self, 'chapter_generator', None): self.chapter_generator = DummyChapterGenerator(self._log)
            if not getattr(self, 'quality_inspector', None): self.quality_inspector = DummyQualityInspector(self._log)
            if not getattr(self, 'dialog_analyzer', None): self.dialog_analyzer = DummyDialogAnalyzer(self._log)
            if not getattr(self, 'project_plan_builder', None): self.project_plan_builder = DummyProjectPlanBuilder(self._log)
            if not getattr(self, 'lore_revelation_manager', None): self.lore_revelation_manager = DummyLoreRevelationManager(self._log)
            if not getattr(self, 'deep_lore_expander', None): self.deep_lore_expander = DummyDeepLoreExpander(self._log)
            if not getattr(self, 'event_tracker', None): self.event_tracker = DummyEventTracker(self._log)
            if not getattr(self, 'memory_graph', None): self.memory_graph = DummyMemoryGraph(self._log)
            self._log("🛡️ [架构防御] 所有核心组件已完成 Dummy 兜底注入，系统免疫 AttributeError。", level="INFO")
        except Exception as e:
            self._log(f"⚠️ 注入 Dummy 兜底对象失败: {e}", level="WARN")

        # 🌟 核心架构创新：初始化“动作注册表”
        self._action_registry = {}
        self._register_default_actions()

    # -----------------------------------------------------------------
    # ③ 动作注册表与 Handler
    # -----------------------------------------------------------------
    def _register_default_actions(self):
        """注册所有默认的动作处理器。"""
        def _safe_get(method_name):
            return getattr(self, method_name, self._handle_not_implemented)

        self._action_registry = {
            "project_creation": (_safe_get('_handle_project_creation'), False),
            "outline_generation": (_safe_get('_handle_outline_generation'), False),
            "outline_confirmation": (_safe_get('_handle_outline_confirmed'), False),
            "outline_modification": (_safe_get('_handle_outline_modification'), True), 
            "chapter_writing": (_safe_get('_handle_chapter_writing'), True),
            "chapter_rewrite": (_safe_get('_handle_chapter_rewrite'), True),        
            "chapter_evaluation": (_safe_get('_handle_chapter_evaluation'), True),  
            "batch_generation": (_safe_get('_handle_batch_generation'), False),
            "continue_generation": (_safe_get('_handle_resume_task'), True),
            "custom_setting": (_safe_get('_handle_custom_setting'), True),
            "file_generation": (_safe_get('_handle_file_generation'), True),
            "setting_discussion": (_safe_get('_handle_chat'), False),
            "chat": (_safe_get('_handle_chat'), False),
        }

    def _handle_not_implemented(self, user_prompt: str, **kwargs) -> Dict[str, Any]:
        return {"action": "chat", "explanation": "⚠️ 该高级功能正在开发中或尚未实现，请使用常规指令。"}

    def _handle_outline_modification(self, user_prompt: str, params: Dict = None, **kwargs) -> Dict[str, Any]:
        import os
        task_id = self._get_current_task_id() if hasattr(self, '_get_current_task_id') else self.current_task_id
        if not task_id:
            return {"action": "chat", "explanation": "❌ 无法获取当前任务 ID，请先创建项目。"}
            
        outline_mgr = getattr(self, 'outline_manager', None)
        if not outline_mgr:
            return {"action": "chat", "explanation": "❌ 大纲管理器未就绪。"}
            
        machine_path = outline_mgr._machine_outline_path(task_id) if hasattr(outline_mgr, '_machine_outline_path') else ""
        if machine_path and os.path.exists(machine_path):
            try:
                os.remove(machine_path)
                self._log(f"🗑️ 已删除旧大纲文件: {machine_path}")
            except Exception as e:
                self._log(f"⚠️ 删除旧大纲失败: {e}", level="WARN")
                
        task_planner = getattr(self, 'task_planner', None)
        if task_planner and hasattr(task_planner, 'update_plan_status'):
            task_planner.update_plan_status(task_id, 'planned')
            
        return {"action": "outline_modified", "explanation": f"✅ 已清空旧大纲并重置状态。\n您的修改意见已记录：【{user_prompt[:50]}...】\n\n👉 请重新发送 **“生成大纲”**。"}

    def _handle_chapter_rewrite(self, user_prompt: str, params: Dict = None, **kwargs) -> Dict[str, Any]:
        import os, shutil
        params = params or getattr(self, 'current_intent_params', {})
        target_chapters = params.get('target_chapters', [])
        
        if not target_chapters:
            return {"action": "chat", "explanation": "❌ 未指定需要重写的章节。请明确说明，例如：“重写第3章”。"}
            
        task_id = self._get_current_task_id() if hasattr(self, '_get_current_task_id') else self.current_task_id
        
        # 🌟 防弹修复：增加 try-except 防止 get_plan 抛出异常导致崩溃
        task_plan = {}
        try:
            if hasattr(self, 'task_planner'):
                task_plan = self.task_planner.get_plan(task_id) or {}
        except Exception as e:
            self._log(f"⚠️ 获取 task_plan 失败: {e}", level="WARN")
            
        project_dir = task_plan.get('project_dir', '')
        if not project_dir:
            return {"action": "chat", "explanation": "❌ 无法获取项目目录，请确保项目已创建。"}
            
        deleted_count = 0
        for chap_num in target_chapters:
            chap_dir = os.path.join(project_dir, "02_章节", f"第{chap_num:03d}章")
            if os.path.exists(chap_dir):
                try:
                    shutil.rmtree(chap_dir)
                    deleted_count += 1
                except Exception as e:
                    self._log(f"⚠️ 清空第 {chap_num} 章目录失败: {e}", level="WARN")
                    
        if deleted_count == 0:
            return {"action": "chat", "explanation": f"⚠️ 目标章节 {target_chapters} 不存在或已被清空，无需重写。"}
            
        self._log(f"🔄 章节重写准备完毕，开始重新生成第 {target_chapters} 章...")
        return self._handle_chapter_writing(user_prompt, generation_mode="all")

    def _handle_chapter_evaluation(self, user_prompt: str, params: Dict = None, **kwargs) -> Dict[str, Any]:
        import os
        params = params or getattr(self, 'current_intent_params', {})
        target_chapters = params.get('target_chapters', [])
        
        if not target_chapters:
            return {"action": "chat", "explanation": "❌ 未指定需要评估的章节。"}
            
        task_id = self._get_current_task_id() if hasattr(self, '_get_current_task_id') else self.current_task_id
        
        # 🌟 防弹修复
        task_plan = {}
        try:
            if hasattr(self, 'task_planner'):
                task_plan = self.task_planner.get_plan(task_id) or {}
        except Exception: pass
            
        project_dir = task_plan.get('project_dir', '')
        results = []
        for chap_num in target_chapters:
            chap_file = os.path.join(project_dir, "02_章节", f"第{chap_num:03d}章", f"第{chap_num:03d}章.txt")
            if not os.path.exists(chap_file):
                results.append(f"第 {chap_num} 章：文件不存在，跳过。")
                continue
                
            with open(chap_file, 'r', encoding='utf-8') as f:
                content = f.read()
                
            inspector = getattr(self, 'quality_inspector', None)
            if inspector and hasattr(inspector, 'inspect_chapter'):
                try:
                    eval_res = inspector.inspect_chapter(content, task_plan)
                    score = eval_res.get('score', 0)
                    issues = eval_res.get('issues', [])
                    results.append(f"第 {chap_num} 章：得分 {score}/100。主要问题：{', '.join(issues[:3]) if issues else '无明显问题'}。")
                except Exception as e:
                    results.append(f"第 {chap_num} 章：评估失败 ({e})。")
            else:
                results.append(f"第 {chap_num} 章：质量检查器未就绪。")
                
        return {"action": "chapter_evaluated", "explanation": "📊 章节评估结果：\n" + "\n".join(results)}

    def register_action(self, action_name: str, handler: callable, requires_params: bool = False):
        self._action_registry[action_name] = (handler, requires_params)
        self._log(f"✅ 动态注册新动作: {action_name}", level="INFO")

    # -----------------------------------------------------------------
    # ④ 配置加载辅助方法
    # -----------------------------------------------------------------
    def _load_robot_config(self) -> None:
        self.robot_name = "筱溪"
        self.robot_icon = "❤️"
        try:
            cfg_path = os.path.join(KNOWLEDGE_BASE_DIR, "robot_config.json")
            if os.path.exists(cfg_path):
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                self.robot_name = cfg.get("name", self.robot_name)
                self.robot_icon = cfg.get("icon", self.robot_icon)
        except Exception as e:
            self._log(f"⚠️ 加载机器人配置失败: {e}", level="WARN")

    def _load_plugin_capabilities(self) -> None:
        self.plugin_capabilities = []
        if self.plugin_manager:
            try:
                self.plugin_capabilities = self.plugin_manager.get_all_capabilities()
            except Exception as e:
                self._log(f"⚠️ 加载插件能力失败: {e}", level="WARN")


    # -----------------------------------------------------------------
    # ✅ 将任务计划中可能为 str 的章节数字强制转换为 int
    # -----------------------------------------------------------------
    def _ensure_int_fields(self, task_plan: Dict[str, Any]) -> None:
        """，
        把 ``total_chapters``、``words_per_chapter`` 强制转换为 int；
        若转换异常则回退为安全默认值（1 / 2500），并记录 WARN。
        """
        try:
            task_plan["total_chapters"] = int(task_plan.get("total_chapters", 1))
        except (ValueError, TypeError):
            task_plan["total_chapters"] = 1
            self._log(
                "⚠️ total_chapters 不是合法整数，已回退为 1",
                level="WARN",
            )

        try:
            task_plan["words_per_chapter"] = int(task_plan.get("words_per_chapter", 2500))
        except (ValueError, TypeError):
            task_plan["words_per_chapter"] = 2500
            self._log(
                "⚠️ words_per_chapter 不是合法整数，已回退为 2500",
                level="WARN",
            )

    # 🔑【依赖说明】：此方法仅被__init__调用，无其他依赖，故置于_load_plugin_capabilities之后
    # 📌 调用核心：self._load_learning_history() → 因此_load_learning_history必须在此方法之前定义
    def _load_learning_history(self) -> None:
        """从磁盘读取学习历史（若存在）"""
        if self.learning_engine:
            self.learning_engine.load_learning_history()

    # 🔑【依赖说明】：此方法仅被__init__调用，无其他依赖，故置于_load_learning_history之后
    # 📌 调用核心：self._save_learning_history()将在退出时调用，但定义顺序无硬性要求（因仅在运行时调用）
    def _save_learning_history(self) -> None:
        """将学习历史写回磁盘（在关闭时调用）"""
        if self.learning_engine:
            self.learning_engine.save_learning_history()

    # 🔑【依赖说明】：此方法仅被__init__调用，无其他依赖，故置于_save_learning_history之后
    # 📌 调用核心：self._init_topic_manager() → 因此_init_topic_manager必须在此方法之前定义
    def _init_topic_manager(self) -> None:
        """
        根据 config.TOPIC_BACKEND 创建对应的 TopicManager。
        只在 ``__init__`` 结束时调用一次，防止后续代码再次实例化导致状态不一致。
        """
        if getattr(self, "topic_manager", None):
            return  # 已经初始化，直接返回

        # 🌟 防弹修复：使用 getattr 防止 config 中未定义 TOPIC_BACKEND 导致 NameError
        if getattr(config, "TOPIC_BACKEND", "json") == "sqlite":
            from .topic_manager_sqlite import SQLiteTopicManager
            self.topic_manager = SQLiteTopicManager(log_callback=self.log_callback)
            self._log("✅ 使用 SQLite 话题后端", level="INFO")
        else:
            from .topic_manager import TopicManager
            self.topic_manager = TopicManager(log_callback=self.log_callback)
            self._log("✅ 使用 JSON 话题后端", level="INFO")

    # 🔑【依赖说明】：此方法仅被__init__调用，无其他依赖，故置于_init_topic_manager之后
    # 📌 调用核心：self._load_last_active_topic() → 因此_load_last_active_topic必须在此方法之前定义
    def _load_last_active_topic(self) -> None:
        """
        读取 ``current_topic.txt``，如果文件缺失或读取异常则回退为 ``default``。
        同时会触发一次话题迁移（把旧的 conversation_*.json 移动到新目录）。
        
        🌟 核心优化：
        1. 精准捕获底层 Backend 属性缺失异常（如缺少 'log'），降级日志级别，不报 WARN。
        2. 拆分迁移逻辑至独立 try-except，防止迁移组件报错阻断主流程。
        3. 增加对 topic_manager 的防御性检查。
        """
        # 1. 防御性检查：确保 topic_manager 已正确初始化
        if not hasattr(self, 'topic_manager') or self.topic_manager is None:
            self.current_topic = "default"
            self._log("ℹ️ topic_manager 未初始化，安全降级使用默认话题 [default]", level="DEBUG")
            return

        # 2. 核心逻辑：获取当前活跃话题
        try:
            topic = self.topic_manager.get_current()
            if not topic:
                topic = "default"
            self.current_topic = topic
            self._log(f"✅ 当前活跃话题恢复为 [{self.current_topic}]")
        except AttributeError as ae:
            # 🌟 精准捕获：底层 _JSONTopicBackend 缺少 'log' 等属性导致的异常
            # 这种属于已知的底层组件瑕疵，降级为 DEBUG，绝不报 WARN 吓唬用户
            self._log(f"ℹ️ 话题管理器底层属性缺失 ({ae})，已安全降级使用 [default] 话题。", level="DEBUG")
            self.current_topic = "default"
        except Exception as e:
            # 其他未知 IO 或逻辑异常，保留 WARN 级别
            self._log(f"⚠️ _load_last_active_topic 读取异常，回退使用 [default]: {e}", level="WARN")
            self.current_topic = "default"

        # 3. 附加逻辑：自动迁移旧版文件（独立 try-except，防止迁移失败影响核心对话）
        # 仅在第一次调用时执行一次
        try:
            if hasattr(self.topic_manager, 'migrate_legacy'):
                self.topic_manager.migrate_legacy()
        except AttributeError as ae:
            # 同样静默底层 Backend 的属性缺失问题，不影响主流程
            self._log(f"ℹ️ 话题迁移组件底层属性缺失 ({ae})，跳过旧版迁移。", level="DEBUG")
        except Exception as e:
            # 迁移失败属于非致命错误，仅记录 DEBUG，保持控制台干净
            self._log(f"ℹ️ 旧版话题文件迁移跳过 (非致命): {e}", level="DEBUG")


    # -----------------------------------------------------------------
    # ① 检测单章是否已经生成（文件大小 > 10 bytes）
    # -----------------------------------------------------------------
    def _chapter_exists(self, task_plan: Dict[str, Any], chapter_num: int) -> bool:
        """
        根据 task_plan 中的 ``project_dir`` 判断指定章节目录是否已经
        生成且其中至少有一个文件大小 > 10 bytes（与测试脚本的
        MIN_FILE_SIZE_BYTES 保持一致）。只要满足条件即视为章节已完成，
        用于跳过重复写入。
        """
        project_dir = task_plan.get("project_dir")
        if not project_dir:
            # 没有 project_dir 时只能返回 False，交给后续生成处理
            self._log(
                "⚠️ _chapter_exists: task_plan 缺失 project_dir，无法进行章节重复性检测",
                level="WARN",
            )
            return False

        chapter_dir = os.path.join(
            project_dir, "02_章节", f"第{chapter_num:03d}章"
        )
        if not os.path.isdir(chapter_dir):
            return False

        # 任意一个非空文件即认定章节已完成
        try:
            for fname in os.listdir(chapter_dir):
                fpath = os.path.join(chapter_dir, fname)
                if os.path.isfile(fpath) and os.path.getsize(fpath) > 10:
                    return True
        except Exception as exc:  # pragma: no cover
            self._log(
                f"⚠️ _chapter_exists 检测异常（章节 {chapter_num}）：{exc}",
                level="WARN",
            )
        return False


    # -----------------------------------------------------------------
    # ② 返回章节目录中第一个非空文件的完整路径（用于进度记录）
    # -----------------------------------------------------------------
    def _first_nonempty_file(self, dir_path: str) -> Optional[str]:
        """
        在 ``dir_path`` 中返回第一条满足大小 > 10 bytes 的文件完整路径。
        若目录不存在或没有满足条件的文件返回 ``None``。
        """
        if not os.path.isdir(dir_path):
            return None
        try:
            for fname in os.listdir(dir_path):
                fpath = os.path.join(dir_path, fname)
                if os.path.isfile(fpath) and os.path.getsize(fpath) > 10:
                    return fpath
        except Exception as exc:  # pragma: no cover
            self._log(
                f"⚠️ _first_nonempty_file 检测异常（目录 {dir_path}）：{exc}",
                level="WARN",
            )
        return None

    # -----------------------------------------------------------------
    # ③ 读取已完成章节的最高编号（进度兜底）
    #     • 首先尝试 ProgressTracker.get_completed_chapters(task_id)
    #     • 若返回空或异常，回退到磁盘扫描得到的最大章节号
    # -----------------------------------------------------------------
    def _get_last_completed_chapter(self, task_plan: Dict[str, Any]) -> int:
        """
        统一获取已完成章节的最大编号。返回值 0 表示还没有章节完成。
        兼容两种情况：
          1️⃣ ProgressTracker 正常返回已完成章节列表；
          2️⃣ ProgressTracker 失效时，通过磁盘目录（02_章节）逐章检查。
        """
        task_id = task_plan.get("task_id")
        if not task_id:
            return 0

        # ----- ① 先查进度追踪器 -----
        try:
            completed = self.progress_tracker.get_completed_chapters(task_id)  # 期望返回 List[int]
            if completed:
                return max(completed)
        except Exception:  # pragma: no cover
            pass  # 进度追踪器异常，走后面的文件系统兜底

        # ----- ② 文件系统兜底 -----
        project_dir = task_plan.get("project_dir")
        if not project_dir:
            return 0
        chapter_root = os.path.join(project_dir, "02_章节")
        if not os.path.isdir(chapter_root):
            return 0

        max_num = 0
        for entry in os.listdir(chapter_root):
            if entry.startswith("第") and entry.endswith("章"):
                try:
                    num = int(entry[1:-1])          # “第001章” → 1
                except Exception:
                    continue
                if self._chapter_exists(task_plan, num):
                    max_num = max(max_num, num)
        return max_num


    # 🔑【依赖说明】：此方法仅被__init__调用，无其他依赖，故置于_load_last_active_topic之后
    # 📌 调用核心：self._load_conversation_history() → 因此_load_conversation_history必须在此方法之前定义
    def _load_conversation_history(self) -> None:
        """
        从当前话题后端读取会话历史。
        - SQLite 后端使用 ``load_recent``（最多读取 300 条）；
        - JSON 后端直接 ``load``。
        """
        try:
            with self._conversation_lock:
                if isinstance(self.topic_manager, SQLiteTopicManager):
                    # 读取最近 300 条（防止一次性读取过大）
                    self.conversation_history = self.topic_manager.load_recent(
                        self.current_topic, limit=300
                    )
                    self._log(
                        f"✅ SQLite 方式已加载最近 300 条对话（共 {len(self.conversation_history)} 条）"
                    )
                else:
                    # JSON 方式一次性读取全部
                    self.conversation_history = self.topic_manager.load(self.current_topic)
                    self._log(
                        f"✅ JSON 方式已加载全部对话（共 {len(self.conversation_history)} 条）"
                    )
        except Exception as e:  # pragma: no cover
            self._log(
                f"❌ _load_conversation_history 异常: {e}",
                level="ERROR",
            )
            self.conversation_history = []

    def _save_conversation_history(self) -> None:
        """将会话历史持久化。🌟 致命修复：增加防空气覆盖机制，内存为空时绝不覆盖数据库！"""
        try:
            with self._conversation_lock:
                current_topic = getattr(self, 'current_topic', 'default') or 'default'
                
                # 🌟🌟🌟 致命修复：如果内存为空，绝对不允许执行 SQLite 的覆盖写！
                if not self.conversation_history:
                    if hasattr(self, 'topic_manager') and hasattr(self.topic_manager, 'load'):
                        try:
                            db_history = self.topic_manager.load(current_topic)
                            if db_history:
                                self.conversation_history = db_history
                                self._log(f"📥 内存为空，已从数据库恢复话题 [{current_topic}] 的 {len(db_history)} 条记录，跳过保存。")
                                return
                        except Exception: pass
                    self._log(f"ℹ️ 内存与数据库均为空，跳过保存话题 [{current_topic}]。")
                    return

                if hasattr(self, 'topic_manager') and self.topic_manager:
                    self.topic_manager.save(current_topic, self.conversation_history)
                    self._log(f"✅ 已写入全部对话（全量覆盖 {len(self.conversation_history)} 条）")
        except Exception as e:
            self._log(f"❌ _save_conversation_history 异常: {e}", level="ERROR")

    def get_conversation_history(self) -> List[Dict[str, Any]]:
        """
        公共接口：返回当前会话的完整历史记录（列表形式）。
        - 若 `self.conversation_history` 为空（例如 GUI 第一次请求），会主动调用
          ``_load_conversation_history`` 进行一次懒加载，确保返回有效数据。
        - 返回值与 ``_load_conversation_history`` 保存的结构保持一致：
          每条记录形如 ``{'role': 'user'/'assistant', 'content': '...'}``。
        """
        if not self.conversation_history:
            # 懒加载一次，确保即使在未显式调用过 ``_load_conversation_history`` 的情形下也能正常工作
            self._load_conversation_history()
        return self.conversation_history

    def get_personality_stats(self) -> Dict[str, Any]:
        """
        公共接口：获取人格（Personality）统计信息。

        返回的字典示例（供 GUI 使用）：
        {
            "name": "筱溪",
            "conversations": 123,
            "tasks": 45,
            "success_rate": 96.5,
            # 其它自定义字段可自由扩展
        }

        若底层 ``PersonalityManager`` 未实现 ``get_stats``，会回退为最小化的占位字典，
        这样不会因为缺失字段导致 UI 崩溃。
        """
        try:
            # 大多数实现会提供 ``get_stats`` 方法返回完整统计
            if hasattr(self.personality, "get_stats"):
                return self.personality.get_stats()
        except Exception as e:
            self._log(f"⚠️ 获取人格统计失败，回退占位信息：{e}", level="WARN")

        # 回退占位（保证一定有必需字段）
        return {
            "name": getattr(self, "robot_name", "筱溪"),
            "conversations": 0,
            "tasks": 0,
            "success_rate": 0.0,
        }

    # 🔑【依赖说明】：此方法被_save_conversation_history调用（通过磁盘缓存路径），但磁盘缓存是可选项
    # 📌 为避免潜在未定义风险，置于磁盘缓存初始化之后（虽然在__init__中已初始化）
    def _ensure_conversation_size(self) -> None:
        """保证对话历史长度不超过 max_history_length。"""
        if len(self.conversation_history) > self.max_history_length:
            self.conversation_history = self.conversation_history[-self.max_history_length:]

    def _safe_extract_llm_json(self, raw_response) -> dict:
        """
        🛡️ 防弹级 JSON 提取器：
        兼容 str、dict、Ollama 原生格式，自动剥离 Markdown，绝不在 .strip() 处崩溃。
        """
        import json, re
        
        text = ""
        # 1. 处理字典类型（call_ollama_safe 可能已经解析过了）
        if isinstance(raw_response, dict):
            if "character" in raw_response or "world" in raw_response:
                return raw_response  # 已经是目标数据，直接返回
            if "message" in raw_response and isinstance(raw_response["message"], dict):
                text = raw_response["message"].get("content", "")
            else:
                text = json.dumps(raw_response)
        else:
            text = str(raw_response)
            
        # 2. 安全清理字符串
        text = text.strip() if hasattr(text, 'strip') else str(text)
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            
        # 3. 尝试解析
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # 兜底：用正则强行提取最外层的 {}
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                return json.loads(match.group())
            raise ValueError("无法从 LLM 响应中解析出合法 JSON")

    # 🔑【依赖说明】：此方法被_ask_ai等调用，用于读取状态文件
    # 📌 调用核心：self._load_state_files() → 因此_load_state_files必须在此方法之前定义
    # 📌 同时被_save_state_files调用，但_save_state_files定义在之后（因调用时两者均已存在）
    def _load_state_files(self) -> Dict[str, Any]:
        """
        纯粹的状态文件读取器。
        🏗️ 架构级重构：彻底移除 LLM 调用。如果文件缺失，直接报错要求先创建项目。
        """
        result = {}
        
        # 🌟 防弹修复：安全获取 state_dir，防止 config 中未定义导致 AttributeError
        state_dir = getattr(config, 'STATE_DIR', 'state')
        
        world_file = getattr(config, 'WORLD_FILE', os.path.join(state_dir, "world_state.json"))
        char_file = getattr(config, 'CHARACTER_FILE', os.path.join(state_dir, "character_state.json"))

        # 1. 严格检查核心状态文件是否存在
        if not os.path.exists(world_file) or not os.path.exists(char_file):
            raise FileNotFoundError(
                f"核心状态文件缺失！请先发送“创建项目”指令以初始化状态。\n"
                f"期望路径: {world_file} 和 {char_file}"
            )

        # 2. 读取角色和世界状态
        try:
            with open(char_file, 'r', encoding='utf-8') as f: 
                result["character"] = json.load(f)
            with open(world_file, 'r', encoding='utf-8') as f: 
                result["world"] = json.load(f)
        except Exception as e:
            raise ValueError(f"读取核心状态文件失败: {e}")

        # 3. 读取冲突描述（允许缺失）
        conflict_file = getattr(config, 'CONFLICT_FILE', os.path.join(state_dir, "conflict_state.json"))
        result["conflict"] = {"statement": "核心冲突待定义", "status": "pending"}
        if os.path.exists(conflict_file):
            try:
                with open(conflict_file, 'r', encoding='utf-8') as f:
                    result["conflict"] = json.load(f)
            except Exception:
                pass

        # 4. 读取思考日志（允许缺失）
        thinking_file = getattr(config, 'THOUGHT_LOG_FILE', getattr(config, 'THINKING_LOG_FILE', os.path.join(state_dir, "thought_log.json")))
        result["thinking_log"] = []
        if os.path.isfile(thinking_file):
            try:
                with open(thinking_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try: result["thinking_log"].append(json.loads(line))
                            except json.JSONDecodeError: pass 
            except Exception:
                pass

        return result

    def _persist_initial_states(self, merged_spec: Dict[str, Any],
                                states: Optional[Dict[str, Any]]) -> None:
        """落盘初始 world/character 状态文件（含兜底）。
        states 为 None 或缺键时，用 merged_spec 构造默认骨架落盘（兜底路径，显式标注），
        否则 LLM 输出失败会让 _load_state_files 的严格校验永远拒绝写章，整个项目报废。
        """
        default_character = {
            "name": merged_spec.get("main_character") or "主角",
            "role": "主角",
            "personality": "待细化",
            "status": "初始状态",
        }
        default_world = {
            "title": merged_spec.get("title") or "未命名项目",
            "genre": merged_spec.get("genre") or "通用",
            "world_type": merged_spec.get("world_type") or "默认世界",
            "description": "待细化",
        }
        char_state = states.get("character") if isinstance(states, dict) and isinstance(states.get("character"), dict) else default_character
        world_state = states.get("world") if isinstance(states, dict) and isinstance(states.get("world"), dict) else default_world

        state_dir = getattr(config, 'STATE_DIR', 'state')
        world_file = getattr(config, 'WORLD_FILE', os.path.join(state_dir, "world_state.json"))
        char_file = getattr(config, 'CHARACTER_FILE', os.path.join(state_dir, "character_state.json"))
        os.makedirs(os.path.dirname(world_file), exist_ok=True)
        os.makedirs(os.path.dirname(char_file), exist_ok=True)

        with open(char_file, "w", encoding="utf-8") as f:
            json.dump(char_state, f, ensure_ascii=False, indent=2)

        now_iso = datetime.now().isoformat()
        world_state.setdefault("start_time", now_iso)
        world_state.setdefault("current_time", now_iso)
        with open(world_file, "w", encoding="utf-8") as f:
            json.dump(world_state, f, ensure_ascii=False, indent=2)

    # 🔑【依赖说明】：此方法被_load_state_files调用（通过状态文件写入路径）
    # 📌 调用核心：self._save_state_files() → 因此_save_state_files必须在此方法之前定义
    # 📌 但因_load_state_files仅在运行时调用（非__init__中），实际定义顺序可互换
    # 📌 为严格遵循依赖图（调用方必须在被调用方之后），此处置于_load_state_files之后
    def _save_state_files(self, role_state: Dict[str, Any],
                          world_state: Dict[str, Any]) -> None:
        """原子写入角色和世界状态文件，并在写入后立即做 schema 校验。
        必须采用「临时文件 + 强制刷新 + 原子替换」方式，
        写入完成后必须立即再次读取正式文件并做完整的 schema 校验，
        若不通过则抛出异常。
        不得添加任何后备方案（如写入失败则使用内存状态）。
        """
        import os
        import json
        from datetime import datetime
        from . import config

        state_dir = getattr(config, 'STATE_DIR', 'state')
        os.makedirs(state_dir, exist_ok=True)

        required_char_keys = ("id", "name", "health", "belief")
        for req in required_char_keys:
            if req not in role_state:
                role_state[req] = "default_char" if req in ("id", "name") else 100 if req == "health" else "default_belief"

        # 角色状态（保持原实现）
        char_tmp = os.path.join(state_dir, f"{getattr(config, 'CHARACTER_FILE', 'character_state.json')}.tmp")
        char_final = os.path.join(state_dir, getattr(config, 'CHARACTER_FILE', 'character_state.json'))
        try:
            with open(char_tmp, 'w', encoding='utf-8') as f:
                json.dump(role_state, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(char_tmp, char_final)
            with open(char_final, 'r', encoding='utf-8') as f:
                verify_char = json.load(f)
            if not isinstance(verify_char, dict):
                raise ValueError("角色状态写入后校验失败：根对象不是 dict")
            for req in required_char_keys:
                if req not in verify_char:
                    raise ValueError(f"角色状态写入后校验失败：缺少字段 {req}")
        except Exception as e:
            if os.path.isfile(char_tmp):
                try:
                    os.remove(char_tmp)
                except Exception:
                    pass
            raise ValueError(f"保存角色状态失败：{e}")

        required_world_keys = ("start_time", "current_time", "location", "world_rules")
        
        # 🌟 致命修复：底层增量合并兜底，绝不用“未知”覆盖旧状态！
        old_world_state = {}
        if os.path.exists(char_final.replace("character_state.json", "world_state.json")):
            try:
                with open(char_final.replace("character_state.json", "world_state.json"), 'r', encoding='utf-8') as f:
                    old_world_state = json.load(f)
                    if not isinstance(old_world_state, dict):
                        old_world_state = {}
            except Exception:
                old_world_state = {}

        for key in required_world_keys:
            new_val = world_state.get(key)
            old_val = old_world_state.get(key)
            # 如果新值为空、None、或字面量"未知"，且旧值有效，则强制保留旧值
            if (not new_val or str(new_val).strip() in ("未知", "null", "无特殊规则", "")) and old_val and str(old_val).strip() not in ("未知", "null", "无特殊规则", ""):
                world_state[key] = old_val
            elif not new_val or str(new_val).strip() in ("未知", "null", ""):
                now_iso = datetime.now().isoformat()
                defaults = {
                    "start_time": now_iso,
                    "current_time": now_iso,
                    "location": "未知",
                    "world_rules": "无特殊规则",
                }
                world_state[key] = defaults.get(key, f"默认_{key}")
                if hasattr(self, '_log'):
                    self._log(f"⚠️ 自动补全 world_state 缺失字段 {key}，已使用默认值填充", level="WARN")

        # 世界状态（保持原子写入 + 完整校验）
        world_tmp = os.path.join(state_dir, f"{getattr(config, 'WORLD_FILE', 'world_state.json')}.tmp")
        world_final = os.path.join(state_dir, getattr(config, 'WORLD_FILE', 'world_state.json'))
        try:
            with open(world_tmp, 'w', encoding='utf-8') as f:
                json.dump(world_state, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(world_tmp, world_final)
            with open(world_final, 'r', encoding='utf-8') as f:
                verify_world = json.load(f)
            if not isinstance(verify_world, dict):
                raise ValueError("世界状态写入后校验失败：根对象不是 dict")
            for req in required_world_keys:
                if req not in verify_world:
                    raise ValueError(f"世界状态写入后校验失败：缺少字段 {req}")
        except Exception as e:
            if os.path.isfile(world_tmp):
                try:
                    os.remove(world_tmp)
                except Exception:
                    pass
            raise ValueError(f"保存世界状态失败：{e}")
    def ask_ai(self, user_prompt: str, plugin_result=None,
               is_long_task=False, is_learning_mode=False) -> Dict[str, Any]:
        """
        核心交互入口（架构级重构版 - 基于动作注册表的纯粹分发引擎）
        🌟 彻底消灭所有 if/elif 打地鼠代码，符合开闭原则（对扩展开放，对修改关闭）。
        """
        # 🌟🌟🌟 全局状态拦截器：如果当前处于“设定确认阶段”，强制接管所有路由 🌟🌟🌟
        if getattr(self, '_pending_stage', None) == 'spec_confirm' or getattr(self, '_pending_final_spec', None):
            self._log("🛡️ [全局拦截] 检测到处于设定确认阶段，强制路由至项目创建处理器，跳过意图识别...")
            return self._handle_project_creation(user_prompt)

        # 🌟 核心修复：将当前 prompt 挂载到实例，供 _build_intent_context 使用
        self._last_user_prompt = user_prompt

        # 1️⃣ 前置处理：保存历史与 ID 净化
        if hasattr(self, '_save_conversation_history'):
            self._save_conversation_history()
            
        self._purify_current_task_id() 

        # 2️⃣ 思考引擎与偏好检测
        reasoning_engine = getattr(self, 'reasoning_engine', None)
        if reasoning_engine:
            reasoning_result = reasoning_engine.analyze_request(user_prompt, self.current_project_id) or {}
            self._log(f"🧠 思考完成：{reasoning_result.get('action_type', 'unknown')}")

        preference_learner = getattr(self, 'preference_learner', None)
        if preference_learner:
            preference = preference_learner.analyze_user_feedback(user_prompt) or {}
            if isinstance(preference, dict) and preference.get('is_preference'):
                preference_learner.save_preference(preference, self._get_current_task_id() if hasattr(self, '_get_current_task_id') else None)
                if preference.get('is_premanent'):
                    return {"action": "chat", "explanation": "✅ 偏好已永久保存"}

        # 3️⃣ 智能意图分析与参数提取
        intent_context = self._build_intent_context()
        intent_analyzer = getattr(self, 'intent_analyzer', None)
        intent_result = intent_analyzer.analyze_intent(user_prompt, context=intent_context) if intent_analyzer else {}
        
        self._calibrate_confidence(intent_result)

        # 4️⃣ 提取核心动作与参数
        action = intent_result.get('primary_intent', 'chat')
        params = intent_result.get('extracted_params', {})
        generation_mode = intent_result.get('generation_mode', 'all')
        
        self._log(f"🎯 路由分发：Action={action}, Mode={generation_mode}, Params={params}")
        
        # 🌟 核心架构：将参数挂载到实例，供下游无感读取
        self.current_intent_params = params

        # 🚨🚨🚨 致命清理：彻底删除了上一版残留的 outline_confirmation 拦截硬编码（打地鼠元凶） 🚨🚨🚨
        # 所有的状态流转和 task_plan 获取，全部下放到了 _handle_outline_confirmation 和 _handle_batch_generation 中！

        # 5️⃣ 🚀 纯粹的动作分发（彻底消灭 if/elif，符合开闭原则）
        handler, requires_params = self._action_registry.get(action, (None, False))
        
        # 🌟 架构级优雅兜底：如果注册表中未显式注册，但系统内置了该 Handler，则动态绑定
        if handler is None:
            if action == 'outline_confirmation' and hasattr(self, '_handle_outline_confirmation'):
                handler = self._handle_outline_confirmation
            elif action in ['batch_generation', 'continue_generation', 'chapter_writing'] and hasattr(self, '_handle_batch_generation'):
                handler = self._handle_batch_generation
            else:
                handler = self._handle_chat # 终极兜底
                
        self._log(f"🎯 最终路由：Action={action}, Handler={handler.__name__}")

        # 🌟 核心修复：完美兼容旧版 Handler 签名
        try:
            if requires_params:
                return handler(user_prompt, params=params, generation_mode=generation_mode)
            else:
                return handler(user_prompt)
        except TypeError as e:
            # 兼容旧版 Handler：如果报错缺少 'intent' 参数，或者不认识 'params' 关键字
            if "intent" in str(e) or "unexpected keyword argument" in str(e):
                try:
                    return handler(user_prompt, intent_result)
                except TypeError:
                    try:
                        return handler(user_prompt, intent=intent_result)
                    except Exception:
                        pass
            
            # 终极兜底：使用 inspect 探测方法签名，只传它认识的参数
            try:
                import inspect
                sig = inspect.signature(handler)
                valid_kwargs = {}
                if 'generation_mode' in sig.parameters: valid_kwargs['generation_mode'] = generation_mode
                if 'params' in sig.parameters: valid_kwargs['params'] = params
                if 'intent' in sig.parameters: valid_kwargs['intent'] = intent_result
                return handler(user_prompt, **valid_kwargs)
            except Exception as fallback_e:
                self._log(f"⚠️ Handler {handler.__name__} 调用异常，降级为聊天: {fallback_e}", level="WARN")
                return self._handle_chat(user_prompt, plugin_result, is_learning_mode)

    def _get_active_task_plan(self) -> tuple:
        """🌟 架构级基座：安全、统一地获取当前活跃的 task_id 和 task_plan，供所有 Handler 调用。"""
        task_id = getattr(self, 'current_task_id', None)
        if not task_id and hasattr(self, '_get_current_task_id'):
            task_id = self._get_current_task_id()
        if not task_id and hasattr(self, 'task_planner') and hasattr(self.task_planner, 'get_active_task_id'):
            task_id = self.task_planner.get_active_task_id()
            
        task_plan = {}
        if task_id and hasattr(self, 'task_planner') and self.task_planner:
            task_plan = self.task_planner.get_plan(task_id) or {}
            task_plan['task_id'] = task_id
        return task_id, task_plan

    def _handle_outline_confirmation(self, user_prompt: str, **kwargs) -> Dict[str, Any]:
        """🌟 内聚 Handler：处理大纲确认，流转状态并生成第1章。"""
        task_id, task_plan = self._get_active_task_plan()
        if not task_id or not task_plan:
            return {"action": "chat", "explanation": "⚠️ 未找到当前任务计划，请确保已生成大纲。"}
            
        self._log("✅ [状态流转] 确认大纲，强制更新状态为 in_progress...")
        self.current_task_status = 'in_progress'
        task_plan['status'] = 'in_progress'
        if hasattr(self, 'task_planner') and hasattr(self.task_planner, '_save_plan'):
            self.task_planner._save_plan(task_plan)
            
        result = self._generate_chapters_batch(task_plan, start_chapter=1, end_chapter=1)
        # 修复：同 _handle_outline_confirmed，补回“大纲已确认”的动作语义
        if isinstance(result, dict):
            result["action"] = "outline_confirmed"
        return result

    def _get_last_generated_chapter(self, task_plan: Dict) -> int:
        """🌟 留有余裕的接口：精准扫描文件系统，获取当前项目已生成的最大章节数。"""
        import os
        project_dir = task_plan.get("project_dir", "") if isinstance(task_plan, dict) else ""
        if not project_dir: return 0
        chap_dir = os.path.join(project_dir, "02_章节")
        if not os.path.isdir(chap_dir): return 0
        
        max_chap = 0
        for d in os.listdir(chap_dir):
            if d.startswith("第") and d.endswith("章"):
                try:
                    num = int(d.replace("第", "").replace("章", ""))
                    if num > max_chap: max_chap = num
                except ValueError: pass
        return max_chap

    def _handle_batch_generation(self, user_prompt: str, **kwargs) -> Dict[str, Any]:
        """🌟 内聚 Handler：处理批量生成。智能解析章节范围、流转状态，并完美兼容旧版字段。"""
        task_id, task_plan = self._get_active_task_plan()
        if not task_id or not task_plan:
            return {"action": "chat", "explanation": "⚠️ 未找到当前任务计划，请先创建项目并生成大纲。"}
            
        # 🌟 留有余裕：兼容旧版字段设置，防止下游 OutlineManager/ChapterGenerator 依赖这些字段报错
        task_plan.setdefault('generation_mode', '分批')
        task_plan.setdefault('batch_size', task_plan.get('total_chapters', 1))
        task_plan.setdefault('health_check_interval', getattr(config, 'HEALTH_CHECK_INTERVAL_CHAPTERS', 10))
        task_plan.setdefault('silent_health_check', getattr(config, 'SILENT_HEALTH_CHECK', False))
        
        # 状态流转
        if getattr(self, 'current_task_status', '') in ['outline_generated', 'planned']:
            self._log("✅ [状态流转] 批量生成触发，自动流转状态为 in_progress...")
            self.current_task_status = 'in_progress'
            task_plan['status'] = 'in_progress'
            
        if hasattr(self, 'task_planner') and hasattr(self.task_planner, '_save_plan'):
            self.task_planner._save_plan(task_plan)

        total_chapters = self._safe_int(task_plan.get("total_chapters"), 1)
        last_chap = self._get_last_generated_chapter(task_plan)
        
        # 🌟 智能解析用户意图与强制重写参数
        start_chap = 1
        end_chap = total_chapters
        force_overwrite = kwargs.get('force_overwrite', False)
        
        if "继续" in user_prompt:
            start_chap = last_chap + 1
            end_chap = min(total_chapters, start_chap + 9) # 默认继续生成10章
        elif any(kw in user_prompt for kw in ["全部", "所有", "批量", "全本"]):
            start_chap = 1
            end_chap = total_chapters
        else:
            start_chap = last_chap + 1
            end_chap = min(total_chapters, start_chap + 9)
            
        if start_chap > total_chapters:
            return {"action": "chat", "explanation": f"🎉 全书 {total_chapters} 章已全部生成完毕！"}
            
        self._log(f"🚀 [批量生成] 准备生成第 {start_chap} 至 {end_chap} 章 (强制重写: {force_overwrite})。")
        return self._generate_chapters_batch(task_plan, start_chapter=start_chap, end_chapter=end_chapter, force_overwrite=force_overwrite)

    # -----------------------------------------------------------------
    # 辅助方法（从 ask_ai 中抽离，保持主流程极致干净）
    # -----------------------------------------------------------------
    def _purify_current_task_id(self):
        """全局 ID 净化与反查"""
        _current_tid = getattr(self, 'current_task_id', None)
        if _current_tid and isinstance(_current_tid, str) and not _current_tid.startswith("task_") and len(_current_tid) <= 16:
            self._log(f"🚨 [全局拦截] task_id ({_current_tid}) 格式异常，启动反查...", level="WARN")
            _real_task_id = None
            _current_pid = getattr(self, 'current_project_id', None)
            if _current_pid and hasattr(self, 'task_planner') and hasattr(self.task_planner, 'list_all_tasks'):
                for t in self.task_planner.list_all_tasks():
                    if t.get('project_id') == _current_pid:
                        _real_task_id = t.get('task_id')
                        break
            self.current_task_id = _real_task_id if _real_task_id else None

    def _build_intent_context(self) -> dict:
        """
        构建意图识别上下文。
        🌟 核心重构：彻底抛弃对 pending_tasks 的依赖（防止 in_progress 状态丢失）。
        直接通过 current_task_id 或 project_id 穿透到 TaskPlanner 获取真实的、全生命周期的任务状态。
        """
        task_status = 'none'
        project_status = 'none'
        outline_exists = False
        outline_status = 'none'
        active_project_id = getattr(self, 'current_project_id', None) or getattr(self, 'active_project_id', None)
        current_task_id = getattr(self, 'current_task_id', None)
        
        task_plan = None
        
        # 1. 优先通过 current_task_id 获取真实的 task_plan (全生命周期)
        if current_task_id and hasattr(self, 'task_planner') and hasattr(self.task_planner, 'get_plan'):
            try:
                task_plan = self.task_planner.get_plan(current_task_id)
            except Exception as e:
                self._log(f"⚠️ 通过 task_id 获取 plan 失败: {e}", level="WARN")
                
        # 2. 如果 current_task_id 拿不到，尝试从 active_project_id 反查最新任务
        if not task_plan and active_project_id:
            if hasattr(self, 'task_planner') and hasattr(self.task_planner, 'list_all_tasks'):
                try:
                    all_tasks = self.task_planner.list_all_tasks()
                    # 找到匹配 project_id 且状态最新的任务
                    for t in reversed(all_tasks): 
                        if t.get('project_id') == active_project_id:
                            task_plan = t
                            break
                except Exception as e:
                    self._log(f"⚠️ 通过 project_id 反查 plan 失败: {e}", level="WARN")

        # 3. 提取真实状态（穿透式获取，绝不漏掉 in_progress 或 completed）
        if task_plan and isinstance(task_plan, dict):
            task_status = task_plan.get('status', 'none')
            project_status = task_plan.get('project_status', task_status) 
            if task_plan.get('outline_confirmed') or task_status in ['in_progress', 'completed']:
                outline_status = 'confirmed'
            if not active_project_id:
                active_project_id = task_plan.get('project_id')

        # 4. 检查大纲是否存在 (物理校验)
        real_task_id = task_plan.get('task_id') if task_plan else current_task_id
        if real_task_id and hasattr(self, 'outline_manager'):
            try:
                outline_data = self.outline_manager.get_outline(real_task_id)
                outline_exists = bool(outline_data)
                # 如果大纲物理存在，但状态没更新，强制修正 outline_exists 为 True
                if outline_exists and outline_status != 'confirmed' and task_status == 'outline_generated':
                    outline_status = 'generated'
            except Exception:
                pass

        context = {
            'user_prompt': getattr(self, '_last_user_prompt', ''), 
            'task_status': task_status,
            'project_status': project_status,
            'outline_exists': outline_exists,
            'outline_status': outline_status,
            'active_project_id': active_project_id,
            'project_id': task_plan.get('project_id') if task_plan else active_project_id
        }
        
        self._log(f"📊 [Context] 穿透获取真实状态 -> task_status: {task_status}, outline_exists: {outline_exists}", level="DEBUG")
        return context
    # ==================================================================
    # 🌟 核心架构创新：自主代理兜底机制 (Autonomous Agent Fallback)
    # ==================================================================
    def _is_action_oriented_prompt(self, prompt: str) -> bool:
        """快速判断用户的输入是否包含“操作/动作”意图"""
        action_keywords = [
            "翻译", "合并", "拆分", "删除", "提取", "总结", "分析", "对比", 
            "改成", "换成", "润色", "精简", "扩写", "检查", "找出", "统计"
        ]
        return any(kw in prompt for kw in action_keywords)

    def _handle_autonomous_agent(self, user_prompt: str, intent_result: dict) -> Dict[str, Any]:
        """自主代理处理器：让 LLM 扮演系统管理员，分析未定义指令并生成执行计划。"""
        from .config import call_ollama_safe
        self._log("🤖 [Agent 模式] 检测到未注册的操作指令，启动自主思考与规划...", level="INFO")
        
        project_dir = ""
        if hasattr(self, 'task_planner') and self.current_task_id:
            plan = self.task_planner.get_plan(self.current_task_id)
            project_dir = plan.get("project_dir", "") if isinstance(plan, dict) else ""
            
        agent_prompt = f"""你是一个高级 AI 小说系统的自主代理（Agent）。用户向你下达了一个系统未预设的指令。
请分析用户的意图，并决定如何完成它。
当前系统环境：
- 项目目录: {project_dir or '未知'}
- 当前任务ID: {self.current_task_id or '未知'}
用户指令: "{user_prompt}"
请输出一个 JSON 对象，包含你的思考和执行计划：
{{
  "thought_process": "你的思考过程",
  "action_type": "direct_llm" | "file_operation" | "out_of_scope",
  "execution_plan": "具体的执行步骤描述",
  "requires_confirmation": true/false
}}
规则：
1. direct_llm: 如果只是需要 LLM 生成文本，选此项。
2. file_operation: 如果涉及读取、修改、删除项目目录下的文件，选此项。
3. out_of_scope: 如果完全超出文本处理范畴，选此项。
只输出纯 JSON，不要任何废话！"""

        try:
            resp = call_ollama_safe(
                [{"role": "system", "content": "你是一个严谨的系统 Agent，只输出合法 JSON。"}, 
                 {"role": "user", "content": agent_prompt}],
                options={"temperature": 0.2, "num_predict": 32000}
            )
            raw_text = resp.get("content", "") if isinstance(resp, dict) else str(resp)
            import re, json
            match = re.search(r'\{.*\}', raw_text, re.DOTALL)
            plan = json.loads(match.group()) if match else {}
            
            thought = plan.get("thought_process", "无法解析")
            action_type = plan.get("action_type", "out_of_scope")
            exec_plan = plan.get("execution_plan", "")
            needs_confirm = plan.get("requires_confirmation", False)
            
            self._log(f"🧠 [Agent 思考] {thought}", level="INFO")
            self._log(f"📋 [Agent 计划] 类型: {action_type}, 计划: {exec_plan}", level="INFO")
            
            if action_type == "out_of_scope":
                return {"action": "chat", "explanation": f"🤖 Agent 思考：{thought}\n\n⚠️ 抱歉，该指令超出了当前小说创作系统的能力范围。"}
            elif action_type == "direct_llm":
                self._log("🚀 [Agent 执行] 调用 LLM 直接处理文本任务...", level="INFO")
                task_resp = call_ollama_safe([{"role": "user", "content": f"请执行以下任务：{user_prompt}"}])
                result_text = task_resp.get("content", "执行失败") if isinstance(task_resp, dict) else str(task_resp)
                return {"action": "agent_direct_llm", "explanation": f"🤖 Agent 已完成任务：\n\n{result_text}"}
            elif action_type == "file_operation":
                if needs_confirm:
                    return {
                        "action": "agent_requires_confirm",
                        "explanation": f"🤖 Agent 思考：{thought}\n\n⚠️ **安全拦截**：Agent 计划执行以下文件操作：\n`{exec_plan}`\n\n该操作可能覆盖或删除现有数据。如果您确认执行，请回复：**“确认执行”**。",
                        "pending_agent_plan": exec_plan
                    }
                else:
                    return {"action": "agent_file_op", "explanation": f"🤖 Agent 思考：{thought}\n\n✅ Agent 已记录您的文件操作需求：`{exec_plan}`。"}
        except Exception as e:
            self._log(f"❌ [Agent 模式] 规划失败: {e}", level="ERROR")
            
        return self._handle_chat(user_prompt)

    def _calibrate_confidence(self, intent_result: dict):
        """动态置信度校准"""
        state_reward = 0.0
        current_task_state = self._get_current_task_state() if hasattr(self, '_get_current_task_state') else 'none'
        if current_task_state == 'planned' and intent_result.get('primary_intent') in ['outline_generation', 'chapter_writing']:
            state_reward = 0.2
        ambiguity_penalty = 0.1 if intent_result.get('confidence', 0) < 0.5 else 0.0
        raw_confidence = intent_result.get('confidence', 0)
        intent_result['confidence'] = raw_confidence + state_reward - ambiguity_penalty

    def _append_thinking_log(self, entry: Dict[str, Any]) -> None:
        """以 JSON Lines 形式追加一条思考日志条目。采用原子写入。"""
        # 🌟 防弹修复：安全获取 config 属性
        state_dir = getattr(config, 'STATE_DIR', 'state')
        thinking_file = getattr(config, 'THINKING_LOG_FILE', getattr(config, 'THOUGHT_LOG_FILE', 'thought_log.json'))
        thinking_path = os.path.join(state_dir, thinking_file)
        tmp_path = thinking_path + ".tmp"
        try:
            existing = []
            if os.path.isfile(thinking_path):
                with open(thinking_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line: existing.append(line)
            new_line = json.dumps(entry, ensure_ascii=False)
            with open(tmp_path, 'w', encoding='utf-8') as f:
                for line in existing: f.write(line + "\n")
                f.write(new_line + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, thinking_path)
            with open(thinking_path, 'r', encoding='utf-8') as f:
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
            # 🌟 防弹修复：安全获取 config 属性
            state_dir = getattr(config, 'STATE_DIR', 'state')
            precond_flag = getattr(config, 'PRECOND_FLAG', 'precond_ready.flag')
            flag_path = os.path.join(state_dir, precond_flag)
            os.makedirs(os.path.dirname(flag_path), exist_ok=True)
            with open(flag_path, 'w', encoding='utf-8') as f:
                f.write(str(time.time()))
                f.flush()
                os.fsync(f.fileno())
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

    def _check_precond_ready(self) -> bool:
        """检查前置完成标志是否存在且未过期（有效期 25 分钟）。"""
        try:
            state_dir = getattr(config, 'STATE_DIR', 'state')
            precond_flag = getattr(config, 'PRECOND_FLAG', 'precond_ready.flag')
            flag_path = os.path.join(state_dir, precond_flag)
            if not os.path.isfile(flag_path): return False
            mtime = os.path.getmtime(flag_path)
            if (time.time() - mtime) > 1500:
                self._log(f"⚠️ 前置标志已过期，要求用户重新完成前置", level="WARN")
                return False
            return True
        except Exception as e:
            self._log(f"❌ 检查前置标志异常：{e}", level="ERROR")
            return False

    def _handle_precond_confirm(self, user_prompt: str) -> Dict[str, Any]:
        """处理用户的确认指令（仅一次确认）。执行三层验证。"""
        if not self._check_precond_ready():
            return {"action": "chat", "explanation": "❌ 前置尚未完成，请先完成角色、世界、冲突和思考日志的初始化。", "need_confirm": False}

        try:
            state_data = self._load_state_files()
        except Exception as e:
            self._log(f"❌ 读取状态文件失败：{e}", level="ERROR")
            return {"action": "chat", "explanation": f"❌ 状态文件读取或校验失败：{str(e)}。请检查 state/ 目录下的 JSON 文件是否完整。", "need_confirm": False}

        summary = self._build_precond_summary(
            role_state=state_data.get("character", {}),
            world_state=state_data.get("world", {}),
            conflict=state_data.get("conflict", {}),
            thinking_count=len(state_data.get("thinking_log", []))
        )

        confirm_keywords = {"确认", "开始", "我准备好了"}
        if user_prompt.strip() not in confirm_keywords:
            return {
                "action": "chat",
                "explanation": f"{summary}\n\n请仅回复以下任意一项以完成确认：\n“确认”、“开始”、“我准备好了”。\n其他任何内容都将被视为未确认。",
                "need_confirm": False,
            }

        try:
            state_data_second = self._load_state_files()
            for key in ("character", "world", "conflict"):
                if not state_data_second.get(key): raise ValueError(f"第二次读取时 {key} 为空或缺失")
            if not isinstance(state_data_second.get("thinking_log", []), list): raise ValueError("思考日志不是列表类型")
        except Exception as e:
            self._log(f"❌ 第二次状态校验失败：{e}", level="ERROR")
            return {"action": "chat", "explanation": f"❌ 确认后状态校验失败：{str(e)}。可能是状态文件在确认过程中被意外修改，请重新完成前置。", "need_confirm": False}

        self.creation_allowed = True
        self._log("✅ 用户完成前置确认，creation_allowed 已置为 True", level="INFO")
        return {
            "action": "chat",
            "explanation": f"{summary}\n\n✅ 前置确认成功！系统现在将进入全自动章节生成模式。\n请直接输入章节需求（例如：'生成第 1 章' 或 '继续生成'）。",
            "need_confirm": False,
        }

    def _safe_parse_json(self, raw_text: str) -> Optional[Dict]:
        """🌟 防弹 JSON 解析，自动处理 Markdown 标记和尾部逗号"""
        import re
        import json
        if not raw_text: return None
        text = re.sub(r"^```(?:json)?\s*", "", raw_text, flags=re.MULTILINE)
        text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE).strip()
        
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                json_str = match.group()
                json_str = re.sub(r",\s*([}\]])", r"\1", json_str) # 修复尾部逗号
                try:
                    return json.loads(json_str)
                except Exception:
                    pass
        return None

    def _robust_extract_and_validate_spec(self, user_prompt: str, history: list) -> Dict[str, Any]:
        """
        🌟 核心创新：双重提取交叉校验引擎 + 复杂设定（马甲）探测。
        解决 AI 无视要求、乱加设定、无法处理动态身份的问题。
        """
        import json
        from .config import call_ollama_safe

        # 基础提取 Prompt (强调约束，禁止脑补，支持马甲)
        base_prompt = f"""你是极其严谨的小说设定提取器。请从用户的输入中提取小说设定。
【⚠️ 最高铁律：绝对忠实与禁止脑补】：
1. 必须 100% 提取用户明确要求的设定（如“必须写xxx”）。
2. 绝对禁止额外增加用户未提及的设定（如用户没说力量体系，就留空）。
3. 如果用户提到“马甲”、“多重身份”、“伪装”、“不同性别”，必须将其提取到 `dynamic_identities` 数组中。

用户输入：“{user_prompt}”
历史对话：{json.dumps(history[-5:], ensure_ascii=False) if history else "无"}

输出严格的 JSON：
{{
  "title": "书名(无则空)", "genre": "题材(无则空)", "world_type": "世界类型(无则空)",
  "main_character": "主角本名(无则空)", "main_character_pronoun": "主角本体代词(他/她/它)",
  "total_word_count": 0, "words_per_chapter": 0,
  "mandatory_settings": ["用户强调必须写的设定1", "设定2"],
  "forbidden_settings": ["用户强调不要写的设定1", "设定2"],
  "dynamic_identities": [
    {{"identity_name": "马甲名", "gender_pronoun": "该马甲的性别/代词", "switch_condition": "切换该马甲的条件/时机", "core_traits": "该马甲的核心特征/性格"}}
  ]
}}
只输出纯 JSON！"""

        # 视角转换 Prompt (用于第二次交叉提取，防止单次注意力丢失)
        alt_prompt = f"""你是小说主编，正在审核作者的大纲。请从以下输入中反向提取核心设定，特别注意作者“强调必须写”和“强调不要写”的内容，以及任何关于“主角伪装/马甲/多重身份”的描述。
输入：“{user_prompt}”
输出 JSON 格式同上，只输出纯 JSON！"""

        def _call_extract(prompt):
            try:
                resp = call_ollama_safe(
                    [{"role": "user", "content": prompt}],
                    options={"temperature": 0.2, "num_predict": 4000, "format": "json"}
                )
                raw = resp.get("content", "") if isinstance(resp, dict) else str(resp)
                if hasattr(self, '_safe_parse_json'):
                    return self._safe_parse_json(raw) or {}
                else:
                    import json_repair
                    return json_repair.repair_json(raw, return_objects=True) or {}
            except Exception as e:
                self._log(f"⚠️ 设定提取 LLM 调用失败: {e}", level="WARN")
                return {}

        self._log("🔍 [双重校验] 正在进行第一次严格提取...")
        spec1 = _call_extract(base_prompt)
        
        self._log("🔍 [双重校验] 正在进行第二次交叉提取...")
        spec2 = _call_extract(alt_prompt)

        # 🌟 交叉比对与合并逻辑
        final_spec = {}
        conflicts = []
        
        # 1. 比对核心字段
        core_fields = ["title", "genre", "world_type", "main_character", "main_character_pronoun"]
        for field in core_fields:
            v1 = spec1.get(field, "")
            v2 = spec2.get(field, "")
            if v1 and v2 and str(v1).strip() != str(v2).strip():
                conflicts.append(f"【{field}】提取一为 '{v1}'，提取二为 '{v2}'")
                final_spec[field] = v1 
            else:
                final_spec[field] = v1 or v2

        # 2. 合并强制与禁止设定 (取并集，确保不遗漏)
        for list_field in ["mandatory_settings", "forbidden_settings"]:
            s1 = set(spec1.get(list_field, []) or [])
            s2 = set(spec2.get(list_field, []) or [])
            final_spec[list_field] = list(s1.union(s2))
            
        # 3. 合并动态身份 (马甲) - 核心创新
        ids1 = spec1.get("dynamic_identities", []) or []
        ids2 = spec2.get("dynamic_identities", []) or []
        merged_ids = {id.get("identity_name"): id for id in ids1 if isinstance(id, dict) and id.get("identity_name")}
        for id2 in ids2:
            if isinstance(id2, dict) and id2.get("identity_name"):
                if id2["identity_name"] not in merged_ids:
                    merged_ids[id2["identity_name"]] = id2
        final_spec["dynamic_identities"] = list(merged_ids.values())
        
        # 4. 数值字段
        final_spec["total_word_count"] = max(spec1.get("total_word_count", 0) or 0, spec2.get("total_word_count", 0) or 0)
        final_spec["words_per_chapter"] = max(spec1.get("words_per_chapter", 0) or 0, spec2.get("words_per_chapter", 0) or 0)

        # 🌟 复杂设定探测与疑问生成
        questions = []
        if conflicts:
            questions.append("⚠️ 两次提取存在差异：\n" + "\n".join(conflicts))
            
        if final_spec.get("dynamic_identities"):
            questions.append(f"🎭 检测到主角拥有 {len(final_spec['dynamic_identities'])} 个马甲/动态身份。系统已记录切换条件和代词，将在正文生成时启用【身份状态机】进行精准控制。")
            
        if final_spec.get("forbidden_settings"):
            questions.append(f"🚫 已锁定禁止设定：{', '.join(final_spec['forbidden_settings'])}。质检引擎将对此进行严厉扣分。")

        # 判断是否需要暂停询问 (有冲突、文本过长、或有复杂马甲设定时触发)
        need_confirm = bool(conflicts) or len(user_prompt) > 150 or bool(final_spec.get("dynamic_identities"))
        
        return {
            "spec": final_spec,
            "need_confirm": need_confirm,
            "questions": questions
        }

    def _find_similar_key(self, new_val: str, existing_dict: dict, threshold: float = 0.65) -> tuple:
        """
        🌟 留有余裕的接口：在字典中查找相似的 key。
        当前使用 difflib 文本相似度，后续可无缝替换为 FAISS/ChromaDB 向量相似度(Embedding)。
        返回: (匹配的已有 key, 相似度) 或 (None, 0.0)
        """
        import difflib
        if not isinstance(new_val, str) or not new_val.strip():
            return None, 0.0
            
        new_val_clean = new_val.strip()
        best_match = None
        max_sim = 0.0
        
        for exist_key in existing_dict.keys():
            if not isinstance(exist_key, str): continue
            # 计算文本相似度 (0.0 到 1.0)
            sim = difflib.SequenceMatcher(None, new_val_clean, exist_key.strip()).ratio()
            if sim > max_sim:
                max_sim = sim
                best_match = exist_key
                
        if max_sim >= threshold:
            return best_match, max_sim
        return None, 0.0

    def _is_similar_to_fixed(self, new_val: str, fixed_val: str, threshold: float = 0.65) -> bool:
        """🌟 留有余裕的接口：判断新值是否与已固定的值语义相似。"""
        import difflib
        if not isinstance(new_val, str) or not isinstance(fixed_val, str): return False
        return difflib.SequenceMatcher(None, new_val.strip(), fixed_val.strip()).ratio() >= threshold

    def _llm_semantic_retrieve_settings(self, current_context: str, all_settings: list, top_k: int = 25) -> list:
        """
        🌟 终极重构：纯 LLM 语义召回引擎。
        彻底废除 2-gram 和硬编码关键词，让 LLM 深度阅读大纲，精准挑选本章需要的设定。
        """
        if not all_settings or not current_context:
            return all_settings[:top_k] if all_settings else []
            
        # 为全量设定建立索引
        indexed_settings = [{"id": i, "content": s} for i, s in enumerate(all_settings) if isinstance(s, str)]
        if not indexed_settings: return []
        
        # 将全量设定序列化（73条设定通常在 4k-8k token 内，现代 LLM 完全可以一次性读完）
        settings_json = json.dumps(indexed_settings, ensure_ascii=False)
        
        prompt = f"""你是极其精准的小说设定调度专家。
【当前章节上下文/大纲】：
{current_context}

【全量设定池（共 {len(indexed_settings)} 条）】：
{settings_json}

【任务】：
请深度分析当前章节上下文，从全量设定池中挑选出本章写作时**必须用到**、**高度相关**或**需要特别注意避免违规**的设定。
1. 语义匹配：即使正文用了同义词（如设定是“附身”，大纲是“控制身体”），也要将其召回。
2. 宁多勿少：不要遗漏任何与本章剧情、人物、场景、规则相关的设定。
3. 过滤废话：不要挑选与本章完全无关的设定。

输出你挑选出的设定的 ID 列表（最多 {top_k} 个）。
输出严格的 JSON 格式：{{"selected_ids": [0, 2, 5, 12]}}
只输出纯 JSON！"""

        try:
            from .config import call_ollama_safe
            resp = call_ollama_safe(
                [{"role": "user", "content": prompt}], 
                options={"temperature": 0.1, "num_predict": 2000, "format": "json"}
            )
            raw = resp.get("content", "") if isinstance(resp, dict) else str(resp)
            parsed = self._safe_parse_json(raw) if hasattr(self, '_safe_parse_json') else {}
            
            if parsed and isinstance(parsed.get("selected_ids"), list):
                selected_ids = set(parsed["selected_ids"])
                result = [s["content"] for s in indexed_settings if s["id"] in selected_ids]
                self._log(f"🧠 [语义召回] LLM 从 {len(indexed_settings)} 条设定中精准召回了 {len(result)} 条本章专属设定。")
                return result
        except Exception as e:
            self._log(f"⚠️ [语义召回] LLM 调用异常: {e}，降级为截断返回。", level="WARN")
            
        return all_settings[:top_k]

    def _llm_audit_setting_coverage(self, generated_text: str, task_plan: Dict, phase: str = "正文"):
        """
        🌟 终极重构：纯 LLM 语义消耗审计。
        彻底废除硬编码关键词，让 LLM 深度阅读正文，判断哪些设定已被隐式或显式消耗。
        """
        if not isinstance(task_plan, dict) or not generated_text: return
        
        all_settings = task_plan.get("core_details", [])
        if not all_settings: return
        
        used_settings = task_plan.setdefault("used_settings", [])
        unused_settings = task_plan.setdefault("unused_settings", list(all_settings))
        
        if not unused_settings:
            self._log("🎉 [设定审计] 所有核心设定均已消耗完毕！")
            return

        indexed_settings = [{"id": i, "content": s} for i, s in enumerate(unused_settings) if isinstance(s, str)]
        if not indexed_settings: return
        
        settings_json = json.dumps(indexed_settings, ensure_ascii=False)
        
        prompt = f"""你是极其严苛的小说设定审计专家。
【生成的{phase}片段】：
{generated_text[:4000]}

【待审计的未使用设定池（共 {len(indexed_settings)} 条）】：
{settings_json}

【任务】：
请仔细阅读{phase}，判断“未使用设定池”中，有哪些设定在{phase}中**已经得到了体现**。
注意：体现包括直接提及、隐式体现、换词表达、剧情推进等。
例如：设定是“主角能附身”，{phase}写了“他占据了她的身体”，这算作已体现。

输出已经体现的设定的 ID 列表。
输出严格的 JSON 格式：{{"consumed_ids": [1, 4, 8]}}
只输出纯 JSON！"""

        try:
            from .config import call_ollama_safe
            resp = call_ollama_safe(
                [{"role": "user", "content": prompt}], 
                options={"temperature": 0.1, "num_predict": 2000, "format": "json"}
            )
            raw = resp.get("content", "") if isinstance(resp, dict) else str(resp)
            parsed = self._safe_parse_json(raw) if hasattr(self, '_safe_parse_json') else {}
            
            if parsed and isinstance(parsed.get("consumed_ids"), list):
                consumed_ids = set(parsed["consumed_ids"])
                newly_used = [s["content"] for s in indexed_settings if s["id"] in consumed_ids]
                
                if newly_used:
                    for s in newly_used:
                        if s in unused_settings: unused_settings.remove(s)
                        if s not in used_settings: used_settings.append(s)
                    self._log(f"📊 [语义审计] {phase}阶段 LLM 确认新消耗了 {len(newly_used)} 条设定。进度：已用 {len(used_settings)}/{len(all_settings)}，未用 {len(unused_settings)}。")
                    
                    # 将未使用的设定提权，下次召回时作为高优先级
                    if unused_settings:
                        task_plan["high_priority_settings"] = unused_settings[:15]
                        
                    if hasattr(self, 'task_planner') and self.task_planner:
                        try: self.task_planner._save_plan(task_plan)
                        except Exception: pass
        except Exception as e:
            self._log(f"⚠️ [语义审计] LLM 调用异常: {e}", level="WARN")

    def _iterative_extract_spec(self, user_prompt: str, history: list) -> Dict[str, Any]:
        """🌟 核心重构：动态排除与注意力聚焦引擎。已固定的设定将被排除，逼迫 LLM 专心提取新细节。"""
        import json
        
        forbid_keywords = ["不要脑补", "不要额外", "严格提取", "只提取", "不要增加", "禁止脑补", "不要加戏"]
        allow_brainstorm = not any(kw in user_prompt for kw in forbid_keywords) and len(user_prompt) < 300
        
        # 🌟 核心修改：在 Prompt 中加入 {already_fixed_placeholder} 占位符
        base_prompt = f"""你是极其严谨且富有创造力的小说设定、写作要求与剧情大纲约束提取器。
【⚠️ 核心铁律】：
1. 必须 100% 提取用户明确要求的“世界观设定”、“写作要求”以及“剧情/大纲约束”。
2. {"鼓励你根据题材和灵感，自主补充合理的商业级设定和剧情建议。" if allow_brainstorm else "绝对禁止额外增加用户未提及的设定和要求！"}
3. 🌟 遵循 MECE 原则（相互独立、完全穷尽）：每个设定条目应聚焦一个完整的主题（例如：将手环的道具描述、附身机制、记忆规则合并为一条完整的“手环附身能力设定”），**不要过度拆碎**，也不要换着花样重复同一件事。将所有细节统一放入 `core_details` 列表中。

{{already_fixed_placeholder}}

用户输入：“{user_prompt}”
历史对话：{json.dumps(history[-3:], ensure_ascii=False) if history else "无"}

输出严格的 JSON（只输出尚未固定的新内容，已固定的绝对不要输出）：
{{
  "title": "书名", "genre": "题材", "world_type": "世界类型",
  "main_character": "主角本名", "main_character_pronoun": "他/她",
  "total_word_count": 0, "words_per_chapter": 0,
  "writing_style": "写作风格/文风",
  "narrative_pov": "叙事视角",
  "plot_tone_progression": "剧情基调/风格演变",
  
  "core_details": ["所有必须写的设定、剧情里程碑、角色弧光、写作限制、其他重要细节（合并为一个列表，遵循MECE原则，不拆碎不重复）"],
  "forbidden_settings": ["绝对禁止写的设定/元素/毒点"],
  "brainstormed_details": ["你自主补充的细节(若无则空)"],
  "dynamic_identities": [{{"identity_name": "马甲名", "gender_pronoun": "代词", "switch_condition": "切换条件"}}]
}}
只输出纯 JSON！"""

        def _call_llm(prompt):
            try:
                from .config import call_ollama_safe
                resp = call_ollama_safe([{"role": "user", "content": prompt}], options={"temperature": 0.5, "num_predict": 4000, "format": "json"})
                raw = resp.get("content", "") if isinstance(resp, dict) else str(resp)
                if hasattr(self, '_safe_parse_json'): return self._safe_parse_json(raw) or {}
                import json_repair
                return json_repair.repair_json(raw, return_objects=True) or {}
            except Exception: return {}

        max_iterations = 24
        target_count = 2
        
        # 投票计数器 (只统计未固定的部分)
        str_field_counts = {} 
        list_field_counts = {} 
        
        # 🌟 核心新增：已固定设定池 (用于从后续 Prompt 中排除，聚焦注意力)
        fixed_str_fields = {} # {field: value}
        fixed_list_fields = {} # {field: [item1, item2]}
        
        self._log(f"🔄 [收敛提取] 启动动态排除提取 (最高{max_iterations}次, {target_count}次固定, 允许脑补: {allow_brainstorm})...")
        
        no_new_fix_count = 0 # 连续没有新固定的次数
        
        for i in range(max_iterations):
            # 1. 构建动态排除提示词
            already_fixed_prompt = ""
            if fixed_str_fields or fixed_list_fields:
                already_fixed_prompt = "【⚠️ 以下设定已提取并固定，绝对禁止在 JSON 中重复输出它们！请专心寻找未提取的新细节！】\n"
                for k, v in fixed_str_fields.items():
                    already_fixed_prompt += f"- {k}: {v}\n"
                for k, v_list in fixed_list_fields.items():
                    already_fixed_prompt += f"- {k}: {json.dumps(v_list, ensure_ascii=False)}\n"
                    
            current_prompt = base_prompt.replace("{already_fixed_placeholder}", already_fixed_prompt)
            
            self._log(f"🔄 [收敛提取] 第 {i+1} 次提取 (已固定 {len(fixed_str_fields)} 个基础项, {sum(len(v) for v in fixed_list_fields.values())} 个列表项)...")
            current_spec = _call_llm(current_prompt)
            
            # 2. 统计本次提取的结果 (排除已固定的，并合并语义相似的)
            has_new_fix = False
            for k, v in current_spec.items():
                # 如果是字符串/数字，且未被固定
                if isinstance(v, (str, int, float)) and str(v).strip():
                    if k in fixed_str_fields: 
                        # 如果与已固定的值相似，直接跳过，不浪费统计
                        if self._is_similar_to_fixed(str(v), fixed_str_fields[k]): continue 
                        
                    val_str = str(v).strip()
                    str_field_counts.setdefault(k, {})
                    
                    # 🌟 核心新增：在统计池中查找相似的 key，合并票数
                    similar_key, _ = self._find_similar_key(val_str, str_field_counts[k])
                    if similar_key:
                        str_field_counts[k][similar_key] += 1
                        # 核心逻辑：保留较长（信息量更大、细节更多）的那个作为主 key
                        if len(val_str) > len(similar_key):
                            count = str_field_counts[k].pop(similar_key)
                            str_field_counts[k][val_str] = count
                    else:
                        str_field_counts[k][val_str] = str_field_counts[k].get(val_str, 0) + 1
                        
                # 如果是列表，排除已固定的元素，并合并相似的
                elif isinstance(v, list):
                    list_field_counts.setdefault(k, {})
                    fixed_items_str = [json.dumps(item, ensure_ascii=False) if isinstance(item, dict) else str(item) for item in fixed_list_fields.get(k, [])]
                    
                    for item in v:
                        item_str = json.dumps(item, ensure_ascii=False) if isinstance(item, dict) else str(item)
                        
                        # 检查是否与已固定的元素相似
                        is_fixed_similar = False
                        for fixed_item_str in fixed_items_str:
                            if self._is_similar_to_fixed(item_str, fixed_item_str):
                                is_fixed_similar = True
                                break
                        if is_fixed_similar: continue
                        
                        # 🌟 核心新增：在统计池中查找相似的元素，合并票数
                        similar_key, _ = self._find_similar_key(item_str, list_field_counts[k])
                        if similar_key:
                            list_field_counts[k][similar_key] += 1
                            if len(item_str) > len(similar_key):
                                count = list_field_counts[k].pop(similar_key)
                                list_field_counts[k][item_str] = count
                        else:
                            list_field_counts[k][item_str] = list_field_counts[k].get(item_str, 0) + 1

            # 3. 检查是否有达到阈值的新设定，移入固定池
            # 检查字符串字段
            for k, counts in list(str_field_counts.items()):
                if k in fixed_str_fields: continue # 整个字段已固定，跳过
                for val_str, count in counts.items():
                    if count >= target_count:
                        fixed_str_fields[k] = val_str
                        has_new_fix = True
                        self._log(f"✅ [固定] 字段 '{k}' 的值 '{val_str}' 已达到 {target_count} 次，锁定并从后续提取中排除！")
                        del str_field_counts[k] # 从统计池中移除，避免重复判断
                        break 
                        
            # 检查列表字段
            for k, counts in list(list_field_counts.items()):
                for item_str, count in list(counts.items()):
                    if count >= target_count:
                        fixed_list_fields.setdefault(k, [])
                        
                        # 🌟 核心新增：检查是否已经存在相似的固定项
                        is_already_fixed = False
                        for fixed_item in fixed_list_fields[k]:
                            fixed_item_str = json.dumps(fixed_item, ensure_ascii=False) if isinstance(fixed_item, dict) else str(fixed_item)
                            if self._is_similar_to_fixed(item_str, fixed_item_str):
                                # 如果已固定的较短，则替换为更长的（保留更完整的）
                                if len(item_str) > len(fixed_item_str):
                                    try: new_item = json.loads(item_str)
                                    except: new_item = item_str
                                    fixed_list_fields[k].remove(fixed_item)
                                    fixed_list_fields[k].append(new_item)
                                is_already_fixed = True
                                break
                        
                        if not is_already_fixed:
                            try: fixed_list_fields[k].append(json.loads(item_str))
                            except: fixed_list_fields[k].append(item_str)
                            has_new_fix = True
                            self._log(f"✅ [固定] 列表 '{k}' 新增元素达到 {target_count} 次，锁定并从后续提取中排除！")
                            
                        # 从统计池中移除该元素
                        if item_str in list_field_counts[k]:
                            del list_field_counts[k][item_str]

                        
            # 4. 提前结束判断
            if has_new_fix:
                no_new_fix_count = 0
            else:
                no_new_fix_count += 1
                
            # 如果连续 3 次没有新固定，提前结束，避免无意义的 24 次循环
            if no_new_fix_count >= 3:
                self._log("✅ [收敛提取] 连续 3 次未发现新设定，提前结束迭代。")
                break

        # 5. 合并最终结果
        final_spec = {}
        # 字符串字段：优先取固定值，其次取出现次数最多的值
        all_str_keys = set(str_field_counts.keys()) | set(fixed_str_fields.keys())
        for k in all_str_keys:
            if k in fixed_str_fields:
                val_str = fixed_str_fields[k]
                try: final_spec[k] = int(val_str)
                except: final_spec[k] = val_str
            elif str_field_counts.get(k):
                best_val = max(str_field_counts[k], key=str_field_counts[k].get)
                try: final_spec[k] = int(best_val)
                except: final_spec[k] = best_val
                
        # 列表字段：合并固定值和出现次数 >= 1 的值 (宽容度，防止遗漏)
        all_list_keys = set(list_field_counts.keys()) | set(fixed_list_fields.keys())
        for k in all_list_keys:
            final_list = list(fixed_list_fields.get(k, []))
            fixed_items_str = set(json.dumps(item, ensure_ascii=False) for item in final_list)
            
            for item_str, count in list_field_counts.get(k, {}).items():
                if item_str not in fixed_items_str and count >= 1: # 未达到2次但出现过的也保留
                    try: final_list.append(json.loads(item_str))
                    except: final_list.append(item_str)
                    
            if final_list: final_spec[k] = final_list
        # 🌟 核心接入：在返回最终结果前，进行语义去重与矛盾排除
        final_spec = self._consolidate_final_spec(final_spec)

        return {
            "spec": final_spec,
            "need_confirm": True,
            "questions": [],
            "allow_brainstorm": allow_brainstorm
        }

    def _deduplicate_string_list(self, lst: list) -> list:
        """🌟 核心新增：基于 difflib 的字符串相似度去重，彻底解决字面量不同但语义重复的问题。"""
        import difflib
        if not lst: return []
        
        unique_items = []
        for item in lst:
            if not isinstance(item, str): 
                unique_items.append(item)
                continue
                
            item_clean = item.strip()
            is_duplicate = False
            
            for i, unique_item in enumerate(unique_items):
                if not isinstance(unique_item, str): continue
                # 计算文本相似度 (0.0 到 1.0)
                similarity = difflib.SequenceMatcher(None, item_clean, unique_item.strip()).ratio()
                
                # 🌟 阈值设定：相似度超过 60% 即视为语义重复
                if similarity > 0.60: 
                    # 核心逻辑：保留较长（信息量更大、细节更多）的那个
                    if len(item_clean) > len(unique_item.strip()):
                        unique_items[i] = item_clean
                    is_duplicate = True
                    break
                    
            if not is_duplicate:
                unique_items.append(item_clean)
                
        return unique_items

    def _consolidate_final_spec(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        """🌟 核心重构：引入分批聚合机制，彻底解决超长列表导致的 LLM 截断与合并失效。"""
        import json
        
        # 1. 🌟 核心处理：对 core_details 进行“分批主题聚合与 MECE 重写”
        core_details = spec.get("core_details", [])
        if isinstance(core_details, list) and len(core_details) > 5:
            self._log(f"🧹 [最终整合] 启动分批主题聚合与 MECE 重写 (当前 {len(core_details)} 条)...")
            
            # 先进行一遍代码级的严格去重
            unique_details = []
            seen = set()
            for item in core_details:
                if isinstance(item, str):
                    clean_item = item.strip()
                    if clean_item and clean_item not in seen:
                        seen.add(clean_item)
                        unique_details.append(clean_item)
                else:
                    unique_details.append(item)
            
            # 🌟 致命修复：分批聚合（每批 75 条），防止 Token 溢出和 LLM 输出截断
            chunk_size = 75
            aggregated_pool = []
            
            for i in range(0, len(unique_details), chunk_size):
                chunk = unique_details[i:i + chunk_size]
                if len(chunk) < 3:
                    aggregated_pool.extend(chunk)
                    continue
                    
                prompt = f"""你是极其严谨的小说设定整合专家。
以下列表包含了提取出的部分核心细节。目前存在“碎片化”和“语义重复”。
【任务】：将描述同一个“实体/机制/概念”的碎片合并为一条完整设定；去除语义重复项。遵循 MECE 原则。
【禁令】：绝对禁止新增设定！绝对禁止遗漏核心细节（如年龄、规则、限制等必须保留）！
输入列表：{json.dumps(chunk, ensure_ascii=False)}
输出 JSON：{{"aggregated_details": ["合并后的完整设定1", "合并后的完整设定2"]}}
只输出纯 JSON！"""
                try:
                    from .config import call_ollama_safe
                    resp = call_ollama_safe([{"role": "user", "content": prompt}], options={"temperature": 0.2, "num_predict": 4000, "format": "json"})
                    raw = resp.get("content", "") if isinstance(resp, dict) else str(resp)
                    parsed = self._safe_parse_json(raw) if hasattr(self, '_safe_parse_json') else {}
                    if parsed and isinstance(parsed.get("aggregated_details"), list):
                        aggregated_pool.extend(parsed["aggregated_details"])
                    else:
                        aggregated_pool.extend(chunk) # 解析失败则保留原批次
                except Exception:
                    aggregated_pool.extend(chunk)
            
            # 🌟 全局二次去重（使用代码级相似度去重，合并各批次结果）
            final_details = self._deduplicate_string_list(aggregated_pool)
            self._log(f"✅ [主题聚合] core_details 从 {len(core_details)} 条精简聚合为 {len(final_details)} 条。")
            spec["core_details"] = final_details

        # 2. 处理其他纯字符串列表 (如 forbidden_settings) - 代码级相似度去重
        for k, v in list(spec.items()):
            if k == "core_details": continue
            if isinstance(v, list) and v and all(isinstance(item, str) for item in v):
                original_len = len(v)
                spec[k] = self._deduplicate_string_list(v)
                if len(spec[k]) < original_len:
                    self._log(f"✅ [代码去重] 字段 '{k}' 从 {original_len} 项精简为 {len(spec[k])} 项。")
                        
        # 3. 处理复杂字典列表和核心字符串
        complex_list_fields = {k: v for k, v in spec.items() if isinstance(v, list) and v and isinstance(v[0], dict) and k != "core_details"}
        core_str_fields = {k: v for k, v in spec.items() if isinstance(v, str) and v and k in ["narrative_pov", "writing_style", "plot_tone_progression", "world_type", "genre"]}
        
        if not complex_list_fields and not core_str_fields:
            return spec
            
        self._log("🧹 [最终整合] 启动 LLM 语义去重与矛盾修复 (复杂结构)...")
        payload = {}
        if complex_list_fields: payload["lists"] = complex_list_fields
        if core_str_fields: payload["strings"] = core_str_fields
        
        payload_json_str = json.dumps(payload, ensure_ascii=False, indent=2)
        prompt2 = f"""你是逻辑校验专家。处理以下 JSON 中的 "lists"（复杂字典）和 "strings"（核心字符串）。
任务：删除 lists 中的重复/矛盾项；修复 strings 中的内部逻辑矛盾。
禁令：禁止新增设定，禁止改变 Key 和结构，无问题则原样返回。
输入：{payload_json_str}
输出纯 JSON！"""

        try:
            from .config import call_ollama_safe
            resp = call_ollama_safe([{"role": "user", "content": prompt2}], options={"temperature": 0.1, "num_predict": 4000, "format": "json"})
            raw = resp.get("content", "") if isinstance(resp, dict) else str(resp)
            cleaned = self._safe_parse_json(raw) if hasattr(self, '_safe_parse_json') else {}
            if isinstance(cleaned, dict):
                if "lists" in cleaned and isinstance(cleaned["lists"], dict):
                    for k, v in cleaned["lists"].items():
                        if isinstance(v, list): spec[k] = v
                if "strings" in cleaned and isinstance(cleaned["strings"], dict):
                    for k, v in cleaned["strings"].items():
                        if isinstance(v, str): spec[k] = v
        except Exception as e:
            self._log(f"⚠️ [最终整合] 复杂结构清洗异常: {e}", level="WARN")
            
        return spec
        
    def _check_readiness_and_backtrack(self, task_plan: Dict, spec: Dict, project_dir: str, user_prompt: str) -> Dict[str, Any]:
        """
        🌟 核心创新：全局准备度检查。
        检查设定、大纲、状态是否完备。如果欠缺，仅回退单个步骤进行重写。
        """
        import os
        self._log("🔍 [准备度检查] 正在全方位扫描工程准备状态...")
        
        missing_components = []
        
        # 1. 检查大纲是否生成且合法
        outline_path = os.path.join(project_dir, "01_大纲", "machine_outline.json")
        outline_data = None
        if os.path.exists(outline_path):
            try:
                import json
                with open(outline_path, 'r', encoding='utf-8') as f:
                    outline_data = json.load(f)
                if not outline_data or not outline_data.get("chapters"):
                    missing_components.append("outline")
            except Exception:
                missing_components.append("outline")
        else:
            missing_components.append("outline")
            
        # 2. 检查核心设定是否缺失
        if not spec.get("genre") and not spec.get("world_type"):
            missing_components.append("spec_core")

        # 🌟 单步回退重写机制
        if missing_components:
            self._log(f"⚠️ [准备度检查] 发现缺失组件: {missing_components}，触发单步回退重写...")
            
            if "outline" in missing_components:
                self._log("🔄 [单步回退] 仅重新生成大纲...")
                try:
                    self._auto_generate_outline(task_plan, user_prompt)
                except Exception as e:
                    self._log(f"❌ 单步回退生成大纲失败: {e}", level="ERROR")
                    
            if "spec_core" in missing_components:
                self._log("🔄 [单步回退] 仅重新补全核心设定...")
                # 这里可以调用灵感扩写引擎补全
                if hasattr(self, '_expand_inspiration'):
                    self._expand_inspiration(spec, user_prompt)
                    
        return {"ready": len(missing_components) == 0, "missing": missing_components}

    def _final_reflection_and_patch(self, task_plan: Dict, spec: Dict, project_dir: str):
        """
        🌟 核心创新：最终独立思考与弥补。
        AI 审视全局，发现逻辑漏洞或未闭环的伏笔，生成动态规则补丁。
        """
        import os, json
        self._log("🧠 [最终思考] AI 正在进行全局审视与逻辑弥补...")
        
        reflection_prompt = f"""你是顶级的小说架构师与代码逻辑审计员。
请审视以下小说的核心设定与工程计划，寻找：
1. 逻辑漏洞（如：力量体系与世界观冲突）。
2. 未闭环的伏笔或马甲切换条件不明确。
3. 后续正文生成时，AI 可能会犯的错误（如：代词混乱、设定遗忘）。

【核心设定】：{json.dumps(spec, ensure_ascii=False)}
【工程计划】：{json.dumps(task_plan, ensure_ascii=False)}

如果发现问题，请输出“弥补规则（Patch）”，这些规则将在后续每一章生成时强制注入给 AI。
如果没有问题，请返回空对象 {{}}。

输出严格的 JSON：
{{
  "has_issues": true/false,
  "patches": [
    {{"rule_type": "character_logic", "description": "弥补规则描述", "prompt_injection": "注入给生成AI的具体指令"}}
  ]
}}
只输出纯 JSON！"""

        try:
            from .config import call_ollama_safe
            resp = call_ollama_safe([{"role": "user", "content": reflection_prompt}], options={"temperature": 0.5, "num_predict": 2000, "format": "json"})
            raw = resp.get("content", "") if isinstance(resp, dict) else str(resp)
            
            parsed = self._safe_parse_json(raw) if hasattr(self, '_safe_parse_json') else {}
            
            if parsed and parsed.get("has_issues") and parsed.get("patches"):
                self._log(f"💡 [最终思考] 发现 {len(parsed['patches'])} 个逻辑漏洞，正在生成弥补补丁...")
                
                # 将补丁保存到独立的 reflections 文件夹
                reflections_dir = os.path.join(project_dir, "system", "reflections")
                os.makedirs(reflections_dir, exist_ok=True)
                patch_file = os.path.join(reflections_dir, "logic_patch.json")
                
                with open(patch_file, "w", encoding="utf-8") as f:
                    json.dump(parsed["patches"], f, ensure_ascii=False, indent=2)
                    
                self._log(f"✅ [最终思考] 弥补补丁已保存至: {patch_file}")
            else:
                self._log("✅ [最终思考] 全局逻辑完美，无需弥补。")
        except Exception as e:
            self._log(f"⚠️ [最终思考] 独立思考异常(已跳过): {e}", level="WARN")

    def _safe_int(self, val, default=0):
        """🌟 防弹整数转换器：完美处理 '10w', '10万', '80,000-95,000', '字' 等脏数据"""
        if val is None: return default
        if isinstance(val, (int, float)): return int(val)
        if isinstance(val, str):
            val = val.replace(',', '').replace('字', '').replace(' ', '').strip()
            if not val: return default
            # 处理 "10w", "10万"
            if 'w' in val.lower():
                try: return int(float(val.lower().replace('w', '')) * 10000)
                except: pass
            if '万' in val:
                try: return int(float(val.replace('万', '')) * 10000)
                except: pass
            # 处理 "80000-95000" 取第一个值
            if '-' in val or '~' in val:
                parts = val.replace('~', '-').split('-')
                try: return int(float(parts[0].strip()))
                except: pass
            try: return int(float(val))
            except: pass
        return default

    def _handle_project_creation(self, user_prompt: str) -> Dict[str, Any]:
        """处理项目创建请求。🌟 地毯式重构：修复意图误判、原始灵感丢失、字数硬编码及状态清理问题。"""
        import os, json, re
        from datetime import datetime
        from . import config

        # 🌟🌟🌟 核心新增：防暴走与显式确认拦截器 (Guardrail) 🌟🌟🌟
        brainstorm_keywords = ["先别", "不用", "不必", "只是聊聊", "探讨", "灵感", "脑洞", "构思", "先不建", "别急着", "帮我完善", "设定一下", "先不创建", "别建", "先不生成", "先不写"]
        # 🌟 修复：将弱确认词（好的/可以）从强动作词中剥离，防止闲聊误触发
        strong_action_keywords = ["创建工程", "生成工程", "开始建", "建项目", "生成大纲", "开始写", "正式创建", "立项", "建工程", "开始创建", "生成项目", "确认创建", "就按这个创建", "那就这么定了", "没问题开始吧"]
        weak_confirm_keywords = ["确认", "没问题", "可以", "好的", "行", "就这个"]
        modify_keywords = ["修改", "改成", "换成", "新增", "加一个", "不要这个", "删除", "改一下", "调整", "不对", "补充", "加上", "去掉"]
        cancel_keywords = ["取消", "放弃", "不建了", "重新开始", "算了"]
        
        has_brainstorm = any(kw in user_prompt for kw in brainstorm_keywords)
        has_strong_action = any(kw in user_prompt for kw in strong_action_keywords)
        has_weak_confirm = any(kw in user_prompt for kw in weak_confirm_keywords)
        has_modify = any(kw in user_prompt for kw in modify_keywords)
        has_cancel = any(kw in user_prompt for kw in cancel_keywords)
        
        if has_strong_action:
            has_brainstorm = False
            
        # 🌟 致命修复 1：霸道状态锁定与意图精准判定
        pending_final_spec = getattr(self, '_pending_final_spec', None)
        
        if pending_final_spec:
            # 1.1 如果用户要求取消
            if has_cancel:
                self._pending_final_spec = None
                self._pending_stage = None
                self._pending_user_prompt = None
                return {"action": "chat", "explanation": "✅ 已取消当前工程创建，您可以重新输入灵感。"}
                
            # 1.2 如果用户要求修改
            if has_modify:
                self._log("🔄 [设定修改循环] 检测到修改意图，更新最终方案并再次暂停询问...")
                try:
                    from .config import call_ollama_safe
                    update_prompt = f"""你是严谨的设定更新器。
【当前最终设定】：{json.dumps(pending_final_spec, ensure_ascii=False)}
【用户修改要求】："{user_prompt}"
请根据用户的要求，修改、新增或删除设定。保持原有的 JSON 结构，只输出更新后的纯 JSON！"""
                    resp = call_ollama_safe([{"role": "user", "content": update_prompt}], options={"temperature": 0.3, "num_predict": 4000, "format": "json"})
                    raw = resp.get("content", "") if isinstance(resp, dict) else str(resp)
                    updated_spec = self._safe_parse_json(raw) if hasattr(self, '_safe_parse_json') else {}
                    if isinstance(updated_spec, dict) and updated_spec:
                        for k, v in updated_spec.items():
                            if v: pending_final_spec[k] = v
                        self._pending_final_spec = pending_final_spec
                except Exception as e:
                    self._log(f"⚠️ 设定更新 LLM 调用失败: {e}", level="WARN")
                return {"action": "chat", "explanation": self._build_spec_confirm_msg(pending_final_spec, "🔄 最终方案已根据您的要求更新，请再次确认："), "need_confirm": True}
                
            # 1.3 否则，一律视为确认（即使意图识别器把它路由到了其他分支）
            self._log("✅ 检测到暂存的最终设定，强制锁定并进入建工程流程。")
            is_confirming = True
        else:
            # 🌟 致命修复：没有暂存状态时，只有“强动作词”才视为 is_confirming，“好的/可以”等弱确认词不触发！
            is_confirming = has_strong_action

        # 拦截探讨模式 (只有在没有暂存状态且不确认时才生效)
        if not pending_final_spec and not is_confirming and (has_brainstorm or (not has_strong_action and getattr(self, 'current_task_status', 'none') != 'in_progress')):
            self._log(f"🛡️ [Guardian] 拦截到创建意图，触发前置确认/探讨机制...")
            if hasattr(self, '_handle_setting_discussion'):
                return self._handle_setting_discussion(user_prompt)
            elif hasattr(self, '_handle_chat'):
                return self._handle_chat(user_prompt, plugin_result=None, is_learning_mode=False)
            else:
                return {"action": "chat", "explanation": f"💡 我注意到您现在似乎只是在分享灵感。等设定完善了，请明确对我说‘确认创建工程’即可。"}

        self._log("📁 准备启动项目创建流水线...")

        # ================= 🌟 核心流水线重构：提取 -> 扩写 -> 最终确认 =================
        if is_confirming:
            self._log("✅ 用户已确认最终设定方案，锁定设定，禁止任何后续脑补覆盖。")
            spec = pending_final_spec
            # 🌟 致命修复：使用暂存的原始 prompt，防止底层把“确认创建”当成小说灵感！
            original_prompt = getattr(self, '_pending_user_prompt', user_prompt) 
            
            # 彻底清理所有 pending 状态
            self._pending_final_spec = None 
            self._pending_stage = None
            self._pending_user_prompt = None
            
            if not spec:
                self._log("⚠️ [状态丢失] 确认时发现暂存设定为空，强制回退到重新提取阶段...", level="WARN")
                is_confirming = False 
        else:
            original_prompt = user_prompt

        if not is_confirming:
            # 🌟 致命修复：暂存原始 prompt，供确认阶段使用
            self._pending_user_prompt = user_prompt 
            
            # 1️⃣ 对话元信息提取 (初步提取)
            try:
                history = getattr(self, 'conversation_history', [])
                if hasattr(self, '_iterative_extract_spec'):
                    validation_result = self._iterative_extract_spec(user_prompt, history)
                    spec = validation_result["spec"]
                elif hasattr(self, '_robust_extract_and_validate_spec'):
                    validation_result = self._robust_extract_and_validate_spec(user_prompt, history)
                    spec = validation_result["spec"]
                else:
                    spec = self.dialog_analyzer.extract_spec(conversation=history, user_prompt=user_prompt)
            except Exception as e:
                self._log(f"⚠️ 设定提取异常：{e}", level="WARN")
                spec = {}
                
            # 🌟 致命修复：从 core_details 中反向提取丢失的基础字段
            core_details = spec.get("core_details", [])
            if isinstance(core_details, list):
                for item in core_details:
                    if isinstance(item, str):
                        if not spec.get("title") and ("书名" in item or "《" in item):
                            match = re.search(r'[《](.*?)[》]', item)
                            if match: spec["title"] = match.group(1)
                        if not spec.get("main_character") and ("主角" in item or "主人公" in item):
                            match = re.search(r'(?:主角|主人公)[是为：:\s]+([^\s,，。、]+)', item)
                            if match: spec["main_character"] = match.group(1)
                        if not spec.get("total_word_count") and ("字数" in item or "万字" in item or "w字" in item.lower()):
                            match = re.search(r'(\d+[\d,.]*)\s*(?:万|w)', item, re.IGNORECASE)
                            if match: spec["total_word_count"] = int(float(match.group(1)) * 10000)

            # 2️⃣ 灵感扩写引擎
            current_word_count = self._safe_int(spec.get("total_word_count", 0))
            
            # 🌟 致命修复：丰富度熔断机制 & 移除 == 50000 的死板判断
            core_details_count = len(spec.get("core_details", []))
            is_rich_spec = core_details_count >= 5 
            
            needs_expansion = (
                not is_rich_spec and (
                    not spec.get("title") or spec.get("title") in ["未命名项目", ""] or
                    current_word_count <= 0 or # 修复：只要大于0就不算缺失
                    not spec.get("main_character") or spec.get("main_character") == "主角"
                )
            )
            
            # 扩大 forbid_brainstorm 检测范围
            forbid_keywords = ["不要脑补", "不要额外", "严格提取", "不要增加", "禁止脑补", "不要加戏", "不需要额外", "只写我提供的", "不要自动生成"]
            forbid_brainstorm = any(kw in user_prompt for kw in forbid_keywords)
            if not forbid_brainstorm:
                all_text = str(spec.get("forbidden_settings", [])) + str(spec.get("core_details", []))
                forbid_brainstorm = any(kw in all_text for kw in forbid_keywords)
                
            if needs_expansion and not forbid_brainstorm:
                self._log("💡 检测到设定缺失，启动【灵感扩写引擎】自动补全商业级设定...")
                try:
                    from .config import call_ollama_safe
                    expand_prompt = f"""你是资深网文主编。用户提供了一个小说灵感，请为其脑补并补全缺失的商业级小说设定。
已有设定：{json.dumps(spec, ensure_ascii=False)}
用户原话："{user_prompt}"
请输出包含以下字段的 JSON：title, genre, world_type, main_character, total_word_count, words_per_chapter。只输出纯 JSON！"""
                    resp = call_ollama_safe([{"role": "user", "content": expand_prompt}], options={"num_predict": 32000, "format": "json"})
                    raw = resp.get("content", "") if isinstance(resp, dict) else str(resp)
                    expanded_data = self._safe_parse_json(raw) if hasattr(self, '_safe_parse_json') else {}
                        
                    if isinstance(expanded_data, dict):
                        for key in ["title", "genre", "world_type", "main_character", "total_word_count", "words_per_chapter"]:
                            if expanded_data.get(key) and (not spec.get(key) or spec.get(key) in ["未命名项目", "通用", "默认世界", "主角", 50000, 2500, ""]):
                                spec[key] = expanded_data[key]
                        self._log(f"✨ 灵感扩写成功：书名《{spec.get('title')}》，主角：{spec.get('main_character')}")
                except Exception as exp_e:
                    self._log(f"⚠️ 灵感扩写失败: {exp_e}", level="WARN")

            # 3️⃣ 🌟 最终方案确认拦截
            # 修复：仅在用户未下达强动作指令（如“创建工程”）时才暂停等待确认；
            # 强动作词代表用户已明确下令，直接落入下方建工程流程，消除“双重确认”断链
            if not has_strong_action:
                self._log("🛑 [最终确认] 设定提取与扩写已完成，暂停工程创建，等待用户最终确认...")
                self._pending_final_spec = spec 
                self._pending_stage = 'spec_confirm' 
                
                confirm_msg = self._build_spec_confirm_msg(spec, "📋 【最终设定方案确认（含自动补充）】")
                return {"action": "chat", "explanation": confirm_msg, "need_confirm": True}
            self._log("🚀 [强动作直通] 检测到明确创建指令，跳过最终确认，直接创建工程...")
            self._pending_user_prompt = None

        # 🌟 终极防弹：确保进入建工程流程时，spec 绝对是一个字典
        spec = spec or {}

        # 🌟 致命修复：统一字数与章节计算，消除重复计算与覆盖
        total_words = self._safe_int(spec.get("total_word_count", 0))
        words_per_chapter = self._safe_int(spec.get("words_per_chapter", 2500), 2500)
        if total_words <= 0: total_words = 100000 
        if words_per_chapter <= 0: words_per_chapter = 2500
        total_chapters = max(1, int(total_words / words_per_chapter))
        
        # 回写到 spec，确保后续所有模块读取到的是清洗后的统一数据
        spec["total_word_count"] = total_words
        spec["words_per_chapter"] = words_per_chapter
        spec["total_chapters"] = total_chapters

        # 2️⃣ 生成唯一的 task_id 并持久化计划
        try:
            # 🌟 修复：传入 original_prompt，防止把“确认创建”当成需求解析
            task_plan = self.task_planner.parse_user_request(original_prompt)
            task_id = task_plan.get("task_id")
            if not task_id: raise RuntimeError("TaskPlanner 未返回 task_id")
                
            if spec:
                for k in ["title", "genre", "world_type", "main_character", "total_word_count", "words_per_chapter", "total_chapters",
                          "writing_style_profile", "writing_style", "narrative_pov", "plot_tone_progression",
                          "core_details", "forbidden_settings", "brainstormed_details", "dynamic_identities", "other"]:
                    if spec.get(k): task_plan[k] = spec[k]
                    
                if hasattr(self.task_planner, '_save_plan'):
                    self.task_planner._save_plan(task_plan)
                self._log(f"🛡️ [Guardian] 已强制纠偏 task_plan：主角={task_plan.get('main_character')}，题材={task_plan.get('genre')}")
        except Exception as e:
            self._log(f"❌ 创建任务计划失败：{e}", level="ERROR")
            return {"action": "chat", "explanation": f"❌ 项目创建失败（任务计划错误）: {e}"}

        # 3️⃣ 占位 project_id
        self.current_project_id = task_plan.get("project_id")
        if not self.current_project_id:
            self.current_project_id = f"proj_{task_id[:8]}"
            task_plan["project_id"] = self.current_project_id
            self._log(f"⚠️ 使用占位 project_id={self.current_project_id}")

        # 4️⃣ 项目模板创建 (🌟 修复：传入 original_prompt)
        result = self.project_generator.create_novel_project(original_prompt, spec)
        if not result.get("success"):
            return {"action": "chat", "explanation": f"❌ 项目创建失败：{result.get('error', '未知错误')}"}

        self.current_project_id = result.get("project_id")
        real_project_dir = result.get("project_dir")
        task_plan["project_dir"] = real_project_dir
        task_plan["project_id"] = self.current_project_id
        
        self.current_project_dir_path = real_project_dir
        self.current_project_dir = real_project_dir

        # 5️⃣ 写回 project_id
        try:
            if hasattr(self.task_planner, 'ensure_project_id'):
                self.task_planner.ensure_project_id(task_id, self.current_project_id)
        except Exception as e:
            self._log(f"⚠️ 写回 project_id 失败: {e}", level="WARN")

        # 6️⃣ 同步状态 (字数已在上方统一计算，此处仅更新状态)
        if hasattr(self.task_planner, '_save_plan'):
            self.task_planner._save_plan(task_plan)
        self.current_task_id = task_id
        self.current_task_status = 'in_progress' 
        self._log(f"🗂️ 任务计划已保存（task_id={task_id}，total_chapters={total_chapters}）")

        # 7️⃣ 生成工程计划 & 初始状态
        try:
            merged_spec = {
                "title": spec.get("title") or "未命名项目",
                "genre": spec.get("genre") or "通用",
                "world_type": spec.get("world_type") or "默认世界",
                "main_character": spec.get("main_character") or "主角",
                "total_word_count": total_words,
                "words_per_chapter": words_per_chapter,
                "total_chapters": total_chapters,
                "style": spec.get("style") or "自然流畅",
                "writing_style_profile": spec.get("writing_style_profile", "默认平铺直叙，关键剧情采用高光微操"),
                "writing_style": spec.get("writing_style") or "自然流畅",
                "narrative_pov": spec.get("narrative_pov") or "第三人称",
                "plot_tone_progression": spec.get("plot_tone_progression") or "",
                "core_details": spec.get("core_details") or [],
                "forbidden_settings": spec.get("forbidden_settings") or [],
                "brainstormed_details": spec.get("brainstormed_details") or [],
                "dynamic_identities": spec.get("dynamic_identities") or [],
            }

            self._log("⚙️ 正在调用 LLM 初始化真实世界与角色状态...")
            try:
                from .config import call_ollama_safe
                prompt = f"""你是一个严格的 JSON 数据生成器。请为小说《{merged_spec['title']}》(题材:{merged_spec['genre']}, 主角:{merged_spec['main_character']})生成初始状态。
必须且只能输出一个合法的 JSON 对象，包含 "character" 和 "world" 两个键。
【⚠️ 绝对禁令】：绝对禁止输出 ```json 或 ``` 等任何 Markdown 标记！直接以 {{ 开头，以 }} 结尾！"""

                messages = [{"role": "system", "content": "你是一个没有感情的 JSON 输出机器。"}, {"role": "user", "content": prompt}]
                raw_response = call_ollama_safe(messages, options={"format": "json", "num_predict": 32000})
                raw_text = raw_response.get("content", "") if isinstance(raw_response, dict) else str(raw_response)
                
                states = self._safe_parse_json(raw_text)
                
                if not states or not isinstance(states, dict):
                    self._log("⚠️ 初始状态 JSON 解析失败，触发 LLM 自动修复语法...", level="WARN")
                    fix_prompt = f"修复以下 JSON 的语法错误，只输出修复后的纯 JSON：\n{raw_text[:2000]}"
                    fix_resp = call_ollama_safe([{"role": "user", "content": fix_prompt}], options={"format": "json", "num_predict": 32000})
                    states = self._safe_parse_json(fix_resp.get("content", "") if isinstance(fix_resp, dict) else str(fix_resp))

                if isinstance(states, dict) and ("character" in states or "world" in states):
                    self._persist_initial_states(merged_spec, states)
                    self._log("✅ 初始真实状态已成功生成并保存！")
                else:
                    # 兜底（显式标注）：LLM 两次均未产出合法状态 JSON 时，用 merged_spec 构造默认骨架落盘。
                    # 若不落盘，_load_state_files 严格校验会因状态文件缺失让整个项目永远无法写章。
                    self._log("⚠️ LLM 初始状态生成彻底失败，写入兜底默认初始状态（非 LLM 真实产出）", level="WARN")
                    self._persist_initial_states(merged_spec, None)
            except Exception as state_e:
                self._log(f"⚠️ 初始状态生成异常: {state_e}，写入兜底默认初始状态（非 LLM 真实产出）", level="WARN")
                try:
                    self._persist_initial_states(merged_spec, None)
                except Exception as persist_e:
                    self._log(f"❌ 兜底默认初始状态落盘失败: {persist_e}", level="ERROR")

            detailed_plan = self.project_plan_builder.elaborate_plan(merged_spec)
            self.project_plan_builder.save_plan(detailed_plan, result["project_dir"])
            self._log("✅ 详细工程计划已生成并写入项目目录")
        except Exception as e:
            self._log(f"⚠️ 项目计划生成失败（非致命错误）：{e}", level="WARN")

        # 7.5️⃣ 动态多元化深度设定展开
        enable_deep_lore = getattr(config, 'ENABLE_DEEP_LORE_GENERATION', True)
        
        # 🌟 致命修复：精确遍历列表匹配，修复 str() 转换导致的漏判
        forbidden_settings = spec.get("forbidden_settings", [])
        skip_lore_keywords = ["不要额外", "不要脑补", "禁止脑补", "不要增加设定", "不需要额外设定", "只写我提供的", "不要自动生成", "禁止额外"]
        
        should_skip_lore = False
        if isinstance(forbidden_settings, list):
            for item in forbidden_settings:
                if isinstance(item, str) and any(kw in item for kw in skip_lore_keywords):
                    should_skip_lore = True
                    break
        elif isinstance(forbidden_settings, str):
            should_skip_lore = any(kw in forbidden_settings for kw in skip_lore_keywords)
            
        if should_skip_lore:
            self._log("🛑 [Guardian] 检测到用户明确禁止额外设定，强制关闭设定展开引擎。")
            enable_deep_lore = False

        # 世界观复杂度评估
        lore_mode = "deep" 
        genre_str = str(spec.get("genre", "")).lower()
        core_details_str = str(spec.get("core_details", [])).lower()
        
        flat_genres = ["日常", "都市", "现代", "言情", "恋爱", "青春", "现实", "普通悬疑", "生活"]
        deep_keywords = ["深度世界观", "复杂力量体系", "宏大叙事", "多维宇宙", "克苏鲁", "修仙", "玄幻", "科幻"]
        
        is_flat_genre = any(kw in genre_str for kw in flat_genres)
        has_deep_requirement = any(kw in core_details_str for kw in deep_keywords)
        
        if is_flat_genre and not has_deep_requirement:
            lore_mode = "flat"
            self._log("📉 [宏观路由] 检测到日常/都市题材且无深度世界观要求，设定引擎降级为【Flat（扁平细节）模式】。")

        if enable_deep_lore:
            try:
                from .deep_lore_expander import DeepLoreExpander
                self._log(f"🧠 启动设定展开引擎 (当前模式: {lore_mode})...")
                if "forbidden_settings" not in task_plan:
                    task_plan["forbidden_settings"] = forbidden_settings
                    
                expander = DeepLoreExpander(
                    project_dir=result["project_dir"], 
                    task_plan=task_plan, 
                    log_callback=self._log,
                    mode=lore_mode 
                )
                expander.execute()
                self.deep_lore_expander = expander 
            except Exception as lore_e:
                self._log(f"⚠️ 设定展开异常（非致命）：{lore_e}", level="WARN")

        # 7.6️⃣ 🌟 全局准备度检查与单步回退 (确保大纲和设定完备，缺失则单步重写)
        if hasattr(self, '_check_readiness_and_backtrack'):
            self._log("🔍 [准备度检查] 启动全局状态扫描...")
            self._check_readiness_and_backtrack(task_plan, spec, real_project_dir, original_prompt)

        # 7.7️⃣ 🌟 最终独立思考与动态弥补 (生成逻辑补丁，解决代码未考虑到的漏洞)
        if hasattr(self, '_final_reflection_and_patch'):
            self._log("🧠 [最终思考] 启动 AI 全局逻辑审计与补丁生成...")
            self._final_reflection_and_patch(task_plan, spec, real_project_dir)

        # 8️⃣ 自动生成大纲
        auto_outline = getattr(config, 'AUTO_OUTLINE_AFTER_CREATION', False)
        if auto_outline:
            self._log("⚙️ 创建成功后自动触发真实大纲生成…")
            # 🌟 修复：传入 original_prompt
            outline_res = self._auto_generate_outline(task_plan, original_prompt)
            if outline_res.get("action") == "outline_generated":
                return {"action": "project_created_and_outline_generated", "explanation": outline_res.get("explanation", ""), "project_dir": result["project_dir"], "task_id": task_id}

        # 9️⃣ 返回创建成功信息
        return {
            "action": "project_created",
            "explanation": f"✅ 项目模板已创建完成！\n\n📁 项目路径：{result['project_dir']}\n\n💡 下一步建议：回复 “生成大纲” 让系统自动生成正式大纲。",
            "project_dir": result["project_dir"], "task_id": task_id,
        }


    def _build_spec_confirm_msg(self, spec: Dict, title: str) -> str:
        """🌟 核心重构：适配聚合后的分类，全景无死角展示。"""
        import json
        msg = f"{title}\n\n"
        
        # 1. 核心基础字段
        core_keys = ["title", "genre", "world_type", "main_character", "main_character_pronoun", "total_word_count", "words_per_chapter"]
        msg += "📌 **【核心基础设定】**\n"
        for k in core_keys:
            if k in spec and spec[k]:
                display_k = k.replace('_', ' ').title()
                if k == "total_word_count": display_k = "预计总字数"
                if k == "words_per_chapter": display_k = "每章字数"
                msg += f"- **{display_k}**: {spec[k]}\n"
                
        # 2. 写作风格与基调
        msg += "\n🎨 **【写作风格与基调】**\n"
        if spec.get("writing_style"): msg += f"- **写作风格/文风**: {spec['writing_style']}\n"
        if spec.get("narrative_pov"): msg += f"- **叙事视角**: {spec['narrative_pov']}\n"
        if spec.get("plot_tone_progression"): msg += f"- **剧情基调演变**: {spec['plot_tone_progression']}\n"
        if not any([spec.get("writing_style"), spec.get("narrative_pov"), spec.get("plot_tone_progression")]):
            msg += "- (未指定，将采用默认自然流畅风格与第三人称视角)\n"

        # 3. 🌟 核心细节与全局要求 (聚合后的主列表)
        core_details = spec.get("core_details", [])
        if core_details:
            msg += "\n📦 **【核心细节与全局要求（含设定/剧情/角色/限制）】**\n"
            for idx, item in enumerate(core_details):
                msg += f"  {idx+1}. {item}\n"
        else:
            msg += "\n📦 **【核心细节与全局要求】**\n  - (暂无额外细节要求)\n"

        # 4. 绝对禁止项
        forbidden = spec.get("forbidden_settings", [])
        if forbidden:
            msg += "\n🚫 **【绝对禁止项（毒点/避雷）】**\n"
            for item in forbidden: msg += f"  - {item}\n"
            
        # 5. AI 补充细节
        brainstormed = spec.get("brainstormed_details", [])
        if brainstormed:
            msg += "\n💡 **【AI 补充细节】**\n"
            for item in brainstormed: msg += f"  - {item}\n"

        # 6. 动态马甲
        identities = spec.get("dynamic_identities", [])
        if identities:
            msg += "\n🎭 **【主角动态身份（马甲）】**\n"
            for idx, ident in enumerate(identities):
                if isinstance(ident, dict):
                    name = ident.get("identity_name", f"马甲{idx+1}")
                    pronoun = ident.get("gender_pronoun", "他")
                    condition = ident.get("switch_condition", "特定剧情需要")
                    msg += f"  - **{name}** (代词:{pronoun}) | 切换条件: {condition}\n"

        # 7. 遍历所有其他未知字段 (兜底)
        known_keys = core_keys + ["writing_style", "narrative_pov", "plot_tone_progression", 
                                  "core_details", "forbidden_settings", "brainstormed_details", "dynamic_identities"]
        other_keys = [k for k in spec.keys() if k not in known_keys and spec[k]]
        if other_keys:
            msg += "\n📊 **【其他扩展字段】**\n"
            for k in other_keys:
                v = spec[k]
                display_k = k.replace('_', ' ').title()
                if isinstance(v, list):
                    for item in v: msg += f"- **{display_k}**: {item}\n"
                else:
                    msg += f"- **{display_k}**: {v}\n"

        msg += "\n💡 **请检查上述所有设定与要求是否准确。**\n"
        msg += "如果无误，请回复 **“确认创建”** 正式启动工程。\n"
        msg += "如果需要修改，请直接告诉我（例如：“改成第一人称”、“主角在第20章必须黑化”、“不要XX设定”）。"
        return msg

    def _handle_setting_discussion(self, user_prompt: str) -> Dict[str, Any]:
        """处理设定探讨、脑洞发散与灵感引导（非强制创建阶段）。🌟 核心重构：废除死板键值对校验，引入 LLM 灵感引导。"""
        import json
        
        # 1. 尝试轻量级提取（不强制要求键值对，提取不到也不报错）
        spec = {}
        try:
            history = getattr(self, 'conversation_history', [])
            if hasattr(self, 'dialog_analyzer') and self.dialog_analyzer:
                try:
                    spec = self.dialog_analyzer.extract_spec(conversation=history, user_prompt=user_prompt) or {}
                except Exception as inner_e:
                    # 🌟 致命修复：静默吞掉 JSON-Schema 校验失败的报错（如 0 is less than the minimum of 1）
                    err_str = str(inner_e)
                    if "should be non-empty" in err_str or "Failed validating" in err_str or "minimum of 1" in err_str or "less than the minimum" in err_str:
                        self._log("⚠️ 探讨模式提取触发 Schema 校验失败（字数为0），已静默忽略，视为无有效设定。", level="WARN")
                        spec = {} 
                    else:
                        self._log(f"⚠️ 探讨模式提取内部异常: {inner_e}", level="WARN")
                        spec = {}
        except Exception as e:
            self._log(f"⚠️ 探讨模式轻量级提取异常(非致命): {e}", level="WARN")
            spec = {}

        # 2. 动态构建引导 Prompt
        if spec and any(k for k in ["title", "genre", "main_character", "world_type"] if spec.get(k)):
            extracted_info = []
            if spec.get("title"): extracted_info.append(f"书名《{spec['title']}》")
            if spec.get("genre"): extracted_info.append(f"题材[{spec['genre']}]")
            if spec.get("main_character"): extracted_info.append(f"主角[{spec['main_character']}]")
            if spec.get("world_type"): extracted_info.append(f"世界类型[{spec['world_type']}]")
            
            guide_prompt = f"""用户想写小说，目前处于探讨阶段（明确表示先不创建工程）。
用户原话：“{user_prompt}”
目前已确定的灵感碎片：{', '.join(extracted_info)}。
请你作为资深网文主编，用亲切、专业的语气回复用户：
1. 肯定目前的灵感，给予正向情绪价值。
2. 针对缺失的核心要素（如：核心卖点、力量体系、主线冲突、金手指等）提出 2-3 个启发性的问题，引导用户继续发散思维。
3. 提醒用户，等脑洞完善后，可以说“确认创建工程”来正式立项。
限制：回复控制在 200 字以内，语气自然，像人类编辑一样交流，绝对不要使用“键:值”这种机器格式！"""
        else:
            # 3. 如果完全没提取到设定，进入“纯脑洞盲盒引导模式”
            guide_prompt = f"""用户想写小说，但目前只有一个模糊的想法，处于探讨阶段（明确表示先不创建工程）。
用户原话：“{user_prompt}”
请你作为资深网文主编，用亲切、专业的语气回复用户：
1. 表达热情，表示非常乐意一起探讨和构思。
2. 提供 3 个不同方向的“脑洞盲盒”（例如：一个反套路的设定、一个极具张力的开局、一个新奇的世界观），供用户选择或激发灵感。
3. 提醒用户，可以随时把想到的点子用大白话告诉我，等设定完善了再说“确认创建工程”。
限制：回复控制在 250 字以内，语气自然，充满启发性，绝对不要使用“键:值”这种机器格式！"""

        # 4. 调用 LLM 生成引导回复
        try:
            from .config import call_ollama_safe
            resp = call_ollama_safe(
                [{"role": "system", "content": "你是一个懂网文市场、善于引导作者发散思维的资深主编。"}, {"role": "user", "content": guide_prompt}],
                options={"temperature": 0.85, "num_predict": 1000}
            )
            reply = resp.get("content", "") if isinstance(resp, dict) else str(resp)
            if not reply or len(reply) < 10:
                reply = "没问题！写小说最重要的是灵感。你现在有什么初步的想法吗？比如想写什么题材（仙侠、科幻、都市），或者主角有什么特别的金手指？我们可以先随便聊聊，等你觉得设定差不多了，再告诉我‘确认创建工程’。"
        except Exception as e:
            self._log(f"⚠️ 设定探讨 LLM 引导失败: {e}", level="WARN")
            reply = "没问题！写小说最重要的是灵感。你现在有什么初步的想法吗？比如想写什么题材，或者主角有什么特别的设定？我们可以先随便聊聊，等你觉得设定差不多了，再告诉我‘确认创建工程’。"

        return {"action": "chat", "explanation": reply}


    def _handle_custom_setting(self, user_prompt: str, intent: Dict[str, Any] = None) -> Dict[str, Any]:
        """处理用户的自定义设定请求。🌟 核心重构：防弹 JSON 解析 + 语境感知智能重定向。"""
        # 🌟 全局拦截：如果处于最终确认阶段，强制重定向回项目创建，防止意图识别器误路由
        if getattr(self, '_pending_final_spec', None):
            self._log("🛡️ [全局拦截] 处于设定确认阶段，将 custom_setting 重定向回 project_creation...")
            return self._handle_project_creation(user_prompt)
            
        self._log("⚙️ 处理自定义设定请求…")
        import re
        import json
        import os
        from pathlib import Path
        
        # 🌟 核心重构：语境感知重定向（彻底废弃旧版 DialogAnalyzer，统一收口到新流水线）
        current_status = getattr(self, 'current_task_status', 'none')
        if current_status == 'none' and len(user_prompt) > 30:
            creation_keywords = ["写小说", "写书", "主角", "题材", "世界观", "大纲", "故事", "设定一下", "现代都市", "超凡", "能力"]
            if any(kw in user_prompt for kw in creation_keywords):
                self._log("💡 检测到强建书意图但被误路由为 custom_setting，智能重定向至【项目创建流水线】。")
                # 🌟 致命修复：直接重定向到 _handle_project_creation，使用最新的迭代提取引擎！
                if hasattr(self, '_handle_project_creation'):
                    return self._handle_project_creation(user_prompt)
    
        settings = {}
        # 1. 优先尝试正则提取“键: 值”格式 (支持中文键名)
        for line in user_prompt.splitlines():
            m = re.match(r"\s*([\w\u4e00-\u9fa5\-]+)\s*[:：]\s*(.+)", line)
            if m:
                settings[m.group(1).strip()] = m.group(2).strip()

        # 2. 🌟 核心容错：如果正则没匹配到，启用 LLM 从自然语言中提取设定
        if not settings:
            self._log("💡 未检测到标准键值对，启用 LLM 自然语言梳理模式...")
            try:
                from .config import call_ollama_safe
                parse_prompt = f"""用户输入了一段关于小说的自然语言描述，请将其梳理为结构化的设定键值对。
用户原话：“{user_prompt}”
请提取其中的设定要素（如：书名、题材、主角、世界观、力量体系、金手指等）。
如果用户的输入只是普通的闲聊、模糊的写书意愿（如“帮我写篇小说”），没有任何具体设定，请返回空字典 {{}}。
输出严格的 JSON 对象（键为设定名，值为设定内容），只输出纯 JSON！"""
                
                resp = call_ollama_safe(
                    [{"role": "user", "content": parse_prompt}],
                    options={"temperature": 0.3, "num_predict": 1000, "format": "json"}
                )
                raw = resp.get("content", "") if isinstance(resp, dict) else str(resp)
                
                # 🌟 致命修复：废弃简陋的 json.loads，使用系统级的 _safe_parse_json 或 json_repair 进行防弹解析
                parsed = None
                if hasattr(self, '_safe_parse_json'):
                    parsed = self._safe_parse_json(raw)
                else:
                    try:
                        import json_repair
                        parsed = json_repair.repair_json(raw, return_objects=True)
                    except ImportError:
                        match = re.search(r'\{.*\}', raw, re.DOTALL)
                        if match:
                            try: parsed = json.loads(match.group())
                            except Exception: pass
                            
                if isinstance(parsed, dict):
                    settings = parsed
            except Exception as e:
                self._log(f"⚠️ LLM 自然语言梳理失败: {e}", level="WARN")

        # 3. 🌟 终极容错：如果经过 LLM 提取后依然没有有效设定，平滑降级
        if not settings:
            self._log("ℹ️ 确认用户未提供具体设定，平滑降级为设定探讨/闲聊模式。")
            if hasattr(self, '_handle_setting_discussion'):
                return self._handle_setting_discussion(user_prompt)
            elif hasattr(self, '_handle_chat'):
                return self._handle_chat(user_prompt, plugin_result=None, is_learning_mode=False)
            else:
                return {"action": "chat", "explanation": "没问题！写小说最重要的是灵感。你现在有什么初步的想法吗？我们可以先随便聊聊，等你觉得设定差不多了，再告诉我‘确认创建工程’。", "need_confirm": False}

        # 4. 原有业务逻辑：将提取到的 settings 写入 custom_settings.json
        task_plan = {}
        if hasattr(self, 'task_planner') and getattr(self, 'current_task_id', None):
            try:
                task_plan = self.task_planner.get_plan(self.current_task_id) or {}
            except Exception:
                pass
                
        project_dir = getattr(self, "test_project_dir", None) or task_plan.get("project_dir")
        if not project_dir:
            self._log("⚠️ 尚未创建项目目录，将自定义设定暂存至全局上下文。", level="WARN")
            if hasattr(self, '_update_context_state'):
                self._update_context_state({'pending_custom_settings': settings})
            return {"action": "chat", "explanation": f"✅ 我已经记录下您的 {len(settings)} 条自定义设定！不过目前还没有正式创建项目工程，您可以继续补充，或者对我说“确认创建工程”来正式立项。", "need_confirm": False}

        custom_path = Path(project_dir) / "03_设定" / "custom_settings.json"
        try:
            existing = {}
            if custom_path.is_file():
                with open(custom_path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
            existing.update(settings)

            tmp_path = custom_path.with_suffix(".tmp")
            with open(tmp_path, "w", encoding="utf-8") as tmp:
                json.dump(existing, tmp, ensure_ascii=False, indent=2)
                tmp.flush()
                os.fsync(tmp.fileno())
            os.replace(tmp_path, custom_path)

            self._log(f"✅ 自定义设定写入成功：{custom_path}", level="INFO")
            return {"action": "chat", "explanation": f"✅ 自定义设定已保存（共 {len(settings)} 条）。您可以继续补充，或者对我说“确认创建工程”来正式立项。", "need_confirm": False}
        except Exception as e:
            self._log(f"❌ 写入自定义设定失败: {e}", level="ERROR")
            return {"action": "chat", "explanation": f"⚠️ 写入自定义设定文件时遇到点小问题({e})，但我已经把它们记在脑子里啦！您可以继续补充设定。", "need_confirm": False}


    def _handle_file_generation(self, user_prompt: str, intent: Dict[str, Any] = None) -> Dict[str, Any]:
        """处理文件生成请求（防御性兜底）。"""
        current_state = self._get_current_task_state() if hasattr(self, '_get_current_task_state') else 'no_task'
        if current_state == 'no_task':
            self._log("💡 [意图纠偏] 当前无活跃任务，将 'file_generation' 自动路由为 'project_creation'。", level="INFO")
            return self._handle_project_creation(user_prompt)
        self._log("📁 处理文件生成请求...")
        return self._handle_chat(user_prompt, plugin_result=None, is_learning_mode=False)

    def _extract_project_meta(self, user_prompt: str) -> Dict[str, Any]:
        """从 “创建项目” 的自然语言指令中精准抽取元信息。"""
        meta = {"title": "", "genre": "", "world_type": "", "main_character": "", "total_word_count": 0, "words_per_chapter": 0}
        m = re.search(r"《([^》]+)》", user_prompt)
        if m: meta["title"] = m.group(1).strip()
        m = re.search(r"题材[:：]\s*([^\，,.]+)", user_prompt)
        if m: meta["genre"] = m.group(1).strip()
        m = re.search(r"世界类型[:：]\s*([^\，,.]+)", user_prompt)
        if m: meta["world_type"] = m.group(1).strip()
        m = re.search(r"主角名[:：]\s*([^\，,.]+)", user_prompt)
        if m: meta["main_character"] = m.group(1).strip()
        m = re.search(r"总字数[:：]\s*([\d,]+)", user_prompt)
        if m: meta["total_word_count"] = int(m.group(1).replace(",", ""))
        m = re.search(r"每章字数[:：]\s*([\d,]+)", user_prompt)
        if m: meta["words_per_chapter"] = int(m.group(1).replace(",", ""))
        return meta

    def _handle_outline_confirmed(self, user_prompt: str) -> Dict[str, Any]:
        """处理大纲确认（包括自检、恢复、健康检查预检）"""
        import time
        from datetime import datetime
        self._log("✅ 大纲已确认，开始生成正文...")
        self.creation_allowed = True
        pending_tasks = self.task_planner.list_pending_tasks() if hasattr(self.task_planner, 'list_pending_tasks') else []

        if not pending_tasks:
            pending_progress = self.progress_tracker.list_pending_tasks() if hasattr(self, 'progress_tracker') and hasattr(self.progress_tracker, 'list_pending_tasks') else []
            if pending_progress:
                self._log("📋 从进度追踪器恢复任务...")
                return self._handle_resume_task(user_prompt)
            recovery = self._recover_task_from_project_directory() if hasattr(self, '_recover_task_from_project_directory') else {'success': False}
            if recovery.get('success'):
                self._log(f"✅ 任务恢复成功：{recovery.get('task_id')}") 
                pending_tasks = self.task_planner.list_pending_tasks() if hasattr(self.task_planner, 'list_pending_tasks') else []

        if not pending_tasks:
            self._log("⚠️ 未找到任务，创建默认任务计划...")
            if hasattr(self.task_planner, "create_fallback_plan"):
                default_plan = self.task_planner.create_fallback_plan("继续创作")
            else:
                default_plan = {"task_id": f"fallback_{int(time.time())}", "status": "planned", "total_chapters": 1, "words_per_chapter": 2500, "generation_mode": "all", "batch_size": 1}
            if hasattr(self.task_planner, '_save_plan'):
                self.task_planner._save_plan(default_plan)
            return {"action": "chat", "explanation": "⚠️ 未找到待确认的任务，已创建默认任务计划。\n请回复\"生成大纲\"重新开始流程。"}

        task_plan = None
        for t in pending_tasks:
            if t.get('status') == 'outline_generated':
                task_plan = t
                break

        if not task_plan:
            for t in pending_tasks:
                if t.get('status') == 'in_progress' and hasattr(self, 'outline_manager') and self.outline_manager.get_outline(t['task_id']):
                    task_plan = t
                    if hasattr(self.task_planner, 'update_plan_status'):
                        self.task_planner.update_plan_status(t['task_id'], 'outline_generated', {'auto_fixed': True, 'fixed_at': datetime.now().isoformat()})
                    break

        if not task_plan:
            recovery = self._recover_task_from_outline_files() if hasattr(self, '_recover_task_from_outline_files') else {'success': False}
            if recovery.get('success'):
                task_plan = recovery.get('task_plan')
            else:
                return {"action": "chat", "explanation": "❌ 未找到待确认的大纲（状态应为 outline_generated）\n建议：回复\"生成大纲\"重新创建大纲。"}

        outline_data = self.outline_manager.get_outline(task_plan['task_id']) if hasattr(self, 'outline_manager') else None
        if not outline_data and hasattr(self, 'state_manager') and hasattr(self.state_manager, 'load_outline_from_backup'):
            self._log("⚠️ 主大纲文件不存在，尝试从备用位置加载...")
            outline_data = self.state_manager.load_outline_from_backup(task_plan['task_id'])

        if not outline_data:
            self._log("⚠️ 大纲不存在，尝试自动生成（包括世界模型）...")
            if hasattr(self, '_auto_generate_world_models'):
                self._auto_generate_world_models(task_plan.get('task_id', ''), user_prompt)
            if hasattr(self, 'outline_manager'):
                outline_res = self.outline_manager.generate_outline(task_plan)
                if not outline_res.get('success'):
                    return {"action": "chat", "explanation": f"❌ 大纲生成失败，请先生成大纲。\n错误信息：{outline_res.get('error', '未知')}"}
                outline_data = outline_res

        task_id = task_plan['task_id']
        if hasattr(self.task_planner, 'update_plan_status'):
            self.task_planner.update_plan_status(task_id, 'in_progress', {'outline_confirmed_at': datetime.now().isoformat()})
        if hasattr(self, 'outline_manager') and hasattr(self.outline_manager, 'confirm_outline'):
            self.outline_manager.confirm_outline(task_id)

        generation_mode = task_plan.get('generation_mode', '逐章')
        batch_size = task_plan.get('batch_size', 1)
        if generation_mode == '分批':
            end_chapter = min(batch_size, task_plan.get('total_chapters', 1))
            result = self._generate_chapters_batch(task_plan, 1, end_chapter)
        else:
            result = self._generate_chapters_batch(task_plan, 1, 1)
        # 修复：批量生成返回值原本恒为 action="chat"，丢失“大纲已确认”的动作语义；
        # 这里包装为 outline_confirmed（保留 explanation/status/next_chapter），供状态机识别
        if isinstance(result, dict):
            result["action"] = "outline_confirmed"
        return result

    def _recover_task_from_project_directory(self) -> Dict[str, Any]:
        """从项目目录中恢复任务计划。"""
        try:
            # 🌟 防弹修复：安全获取 NOVEL_PROJECTS_DIR
            novel_projects_dir = getattr(config, 'NOVEL_PROJECTS_DIR', 'novel_projects')
            if not os.path.isdir(novel_projects_dir):
                return {'success': False, 'error': '项目目录不存在'}
            
            for project_dir in os.listdir(novel_projects_dir):
                project_path = os.path.join(novel_projects_dir, project_dir)
                if not os.path.isdir(project_path): continue
                task_plan_path = os.path.join(project_path, "task_plan.json")
                if os.path.exists(task_plan_path):
                    try:
                        with open(task_plan_path, 'r', encoding='utf-8') as f:
                            task_plan = json.load(f)
                        if not isinstance(task_plan, dict): continue
                        task_id = task_plan.get('task_id')
                        if not task_id: continue
                        status = task_plan.get('status', '')
                        if status in ['planned', 'outline_generated', 'outline_confirmed', 'in_progress']:
                            if not self.current_project_id:
                                self.current_project_id = project_dir
                            self._log(f"🔍 从项目目录恢复任务：{task_id}（状态：{status}）")
                            return {'success': True, 'task_id': task_id, 'task_plan': task_plan, 'project_dir': project_path}
                    except Exception as e:
                        self._log(f"⚠️ 读取任务计划失败 {task_plan_path}: {e}", level="WARN")
                        continue
            return {'success': False, 'error': '未找到可恢复的任务计划'}
        except Exception as e:
            self._log(f"❌ 从项目目录恢复任务失败: {e}", level="ERROR")
            return {'success': False, 'error': str(e)}

    def _recover_task_from_outline_files(self) -> Dict[str, Any]:
        """从大纲文件中逆向恢复任务计划。"""
        import glob, os, json
        try:
            novel_projects_dir = getattr(config, 'NOVEL_PROJECTS_DIR', 'novel_projects')
            outline_base_dir = os.path.join(novel_projects_dir, "*", "outline")
            if not os.path.isdir(os.path.dirname(outline_base_dir)):
                return {'success': False, 'error': '大纲目录不存在'}
            
            for outline_dir in glob.glob(outline_base_dir):
                if not os.path.isdir(outline_dir): continue
                outline_files = glob.glob(os.path.join(outline_dir, "*.json"))
                for outline_file in outline_files:
                    try:
                        with open(outline_file, 'r', encoding='utf-8') as f:
                            outline_data = json.load(f)
                        title = outline_data.get('title', '')
                        if not title: continue
                        project_dir = os.path.dirname(os.path.dirname(outline_dir))
                        task_plan_path = os.path.join(project_dir, "task_plan.json")
                        if os.path.exists(task_plan_path):
                            try:
                                with open(task_plan_path, 'r', encoding='utf-8') as f:
                                    task_plan = json.load(f)
                                if not isinstance(task_plan, dict): continue
                                task_id = task_plan.get('task_id')
                                if not task_id: continue
                                status = task_plan.get('status', '')
                                if status in ['planned', 'outline_generated', 'outline_confirmed', 'in_progress']:
                                    if not self.current_project_id:
                                        self.current_project_id = os.path.basename(project_dir)
                                    self._log(f"🔍 从大纲文件恢复任务：{task_id}（状态：{status}）")
                                    return {'success': True, 'task_id': task_id, 'task_plan': task_plan, 'outline_file': outline_file}
                            except Exception as e:
                                self._log(f"⚠️ 读取任务计划失败 {task_plan_path}: {e}", level="WARN")
                                continue
                    except Exception as e:
                        self._log(f"⚠️ 读取大纲文件失败 {outline_file}: {e}", level="WARN")
                        continue
            return {'success': False, 'error': '未找到可恢复的大纲文件'}
        except Exception as e:
            self._log(f"❌ 从大纲文件恢复任务失败: {e}", level="ERROR")
            return {'success': False, 'error': str(e)}

    def _auto_generate_outline(self, task_plan: Dict[str, Any], user_prompt: str) -> Dict[str, Any]:
        """触发真实的 OutlineManager 生成完整大纲。"""
        import os, json, traceback
        task_id = task_plan.get("task_id", "")
        if not task_id:
            return {"action": "chat", "explanation": "❌ 任务 ID 缺失，无法生成大纲。", "task_id": None}

        project_dir = task_plan.get("project_dir", "")
        if not project_dir:
            if getattr(self, "current_project_id", None):
                project_dir = self.current_project_id
            elif hasattr(self, "task_planner") and hasattr(self.task_planner, "get_plan"):
                plan = self.task_planner.get_plan(task_id)
                project_dir = plan.get("project_dir", "") if isinstance(plan, dict) else ""
                
        # 🌟 致命修复 3：将解析到的 project_dir 强制写回 task_plan，防止下游丢失
        if project_dir and isinstance(task_plan, dict):
            task_plan["project_dir"] = project_dir

        if not project_dir:
            self._log(f"⚠️ 在 task_plan 中未找到 project_dir（task_id={task_id}），大纲生成将使用默认路径。", level="WARN")
        else:
            try:
                from .project_path_manager import ProjectPathManager
                project_dir = ProjectPathManager.ensure_project_structure(project_dir)
                task_plan["project_dir"] = project_dir # 再次确保写回绝对路径
            except Exception:
                pass

        if not hasattr(self, "outline_manager") or self.outline_manager.__class__.__name__.startswith("Stub"):
            self._log("⚠️ 检测到 StubOutlineManager，强制实例化真实 OutlineManager…", level="WARN")
            try:
                from .outline_manager import OutlineManager
                self.outline_manager = OutlineManager(log_callback=self._log)
                if hasattr(self, "reasoning_engine"): self.outline_manager.reasoning_engine = self.reasoning_engine
                if hasattr(self, "world_state"): self.outline_manager.world_state = self.world_state
            except Exception as exc:
                self._log(f"❌ 无法实例化真实 OutlineManager: {exc}", level="ERROR")
                return {"action": "chat", "explanation": f"❌ 大纲生成失败：无法加载 OutlineManager ({exc})", "task_id": task_id}

        self._log("🚀 调用真实 OutlineManager 开始生成大纲（分卷滚动模式）…")
        try:
            outline_res = self.outline_manager.generate_outline(task_plan)
        except Exception as exc:
            self._log(f"❌ OutlineManager.generate_outline 调用异常: {exc}\n{traceback.format_exc()}", level="ERROR")
            return {"action": "chat", "explanation": f"❌ 大纲生成异常：{exc}", "task_id": task_id}

        if not isinstance(outline_res, dict) or not outline_res.get("success"):
            err_msg = outline_res.get("error", "未知错误")
            self._log(f"❌ 大纲生成返回失败: {err_msg}", level="ERROR")
            return {"action": "chat", "explanation": f"❌ 大纲生成失败：{err_msg}", "task_id": task_id}

        machine_path = outline_res.get("machine_path", "")
        outline_data = {}
        try:
            if machine_path and os.path.isfile(machine_path):
                with open(machine_path, "r", encoding="utf-8") as f:
                    outline_data = json.load(f)
        except Exception as exc:
            self._log(f"⚠️ 大纲文件二次读取校验失败: {exc}", level="WARN")
            outline_data = outline_res.get("outline", {"chapters": outline_res.get("chapter_outlines", [])})

        self._log(f"🎉 真实大纲生成成功，文件已保存至: {machine_path}")

        if project_dir:
            try:
                from .lore_revelation_manager import LoreRevelationManager
                lore_manager = LoreRevelationManager(project_dir=project_dir, log_callback=self._log)
                lore_manager.build_initial_plan_from_layered_settings(task_plan, outline_data)
                self._log("✅ 世界观揭露计划已成功生成")
            except Exception as exc:
                self._log(f"⚠️ LoreRevelationManager 执行异常: {exc}", level="WARN")
        else:
            self._log("⚠️ 无法获取有效 project_dir，跳过 LoreRevelationManager 计划生成。", level="WARN")

        return {
            "action": "outline_generated", "success": True, "outline": outline_data,
            "outline_path": machine_path, "explanation": f"✅ 详细大纲已保存至 {machine_path}，请发送“确认”继续写作。", "task_id": task_id,
        }

    def _safe_pending_tasks(self) -> List[Dict[str, Any]]:
        """统一获取当前「待处理」任务列表，防止 TaskPlanner 抛异常。"""
        import time
        try:
            tasks = self.task_planner.list_pending_tasks()
        except Exception as e:
            self._log(f"⚠️ 获取待处理任务失败: {e}", level="WARN")
            tasks = []

        for task in tasks:
            if not task.get('task_id'):
                # 🌟 防弹修复：安全获取 _generate_task_id，防止方法不存在导致崩溃
                gen_func = getattr(self.task_planner, '_generate_task_id', lambda x: f"task_{int(time.time())}")
                task['task_id'] = gen_func(task.get('title', 'fallback_task'))
            self._ensure_int_fields(task)
        return tasks

    def _request_outline_clarification(self, missing_fields: List[str]) -> bool:
        """当大纲缺少关键字段时向用户请求补全。"""
        field_list = "、".join(missing_fields)
        clarification_prompt = (
            f"生成的大纲缺少以下关键字段：{field_list}。\n"
            "您可以回复：\n"
            "1️⃣ “使用默认值” – 系统将自动填充默认章节数/字数；\n"
            "2️⃣ 手动提供数值（例如：“章节数 15，字数 3000”）\n"
            "请在 30 秒内回复，如未回复系统将采用默认值。"
        )
        self._log(f"🗨️ 向用户请求大纲补全：{field_list}", level="INFO")
        response = self.ask_ai(clarification_prompt, is_learning_mode=False)
        if response.get("action") == "chat" and "默认值" in response.get("explanation", ""):
            self._log("✅ 用户选择使用默认值进行大纲补全", level="INFO")
            return True
        self._log("ℹ️ 用户提供了自定义大纲信息（交由后续流程自行处理）", level="INFO")
        return False

    def _get_active_task_id(self) -> Optional[str]:
        """Robust 获取当前活跃的 task_id。"""
        task_id = getattr(self, 'current_task_id', None)
        if task_id and str(task_id).startswith("task_"):
            return str(task_id)
            
        if hasattr(self, 'task_planner') and hasattr(self.task_planner, 'get_current_active_task'):
            active = self.task_planner.get_current_active_task()
            if active and 'task_id' in active:
                return str(active['task_id'])
                
        project_id = getattr(self, 'current_project_id', None)
        if project_id:
            tasks = []
            if hasattr(self.task_planner, 'list_all_tasks'):
                tasks = self.task_planner.list_all_tasks()
            elif hasattr(self.task_planner, 'list_pending_tasks'):
                tasks = self.task_planner.list_pending_tasks()
            for t in tasks:
                if t.get('project_id') == project_id or t.get('task_id', '').startswith(project_id):
                    return str(t.get('task_id'))
                    
        if hasattr(self, 'progress_tracker') and hasattr(self.progress_tracker, 'get_task_by_project'):
            task = self.progress_tracker.get_task_by_project(project_id)
            if task and 'task_id' in task:
                return str(task['task_id'])
        return None


    def _handle_outline_generation(self, user_prompt: str) -> Dict[str, Any]:
        """处理用户请求生成大纲的入口。"""
        from . import config
        from datetime import datetime
        from typing import List
        
        task_id = getattr(self, 'current_task_id', None)
        if not task_id and hasattr(self, '_get_current_task_id'):
            task_id = self._get_current_task_id()
            
        if not task_id and getattr(self, 'current_project_id', None):
            all_tasks = []
            if hasattr(self.task_planner, 'list_all_tasks'):
                all_tasks = self.task_planner.list_all_tasks()
            elif hasattr(self.task_planner, 'list_pending_tasks'):
                all_tasks = self.task_planner.list_pending_tasks()
            for t in all_tasks:
                if t.get('project_id') == self.current_project_id:
                    task_id = t.get('task_id')
                    break

        if not getattr(config, 'ENABLE_OUTLINE_GENERATION', False):
            self._log("⚙️ [开关: OFF] 已禁用大纲生成，启用去大纲/状态驱动模式...")
            if not task_id:
                self._log("❌ 去大纲模式下无法获取真实 task_id，拒绝使用 project_id 污染数据！", level="ERROR")
                return {"action": "chat", "explanation": "❌ 系统状态异常：无法获取当前任务 ID (task_id)。为防止数据错乱，请重新发送“创建项目”。"}
                
            if hasattr(self, 'outline_manager') and hasattr(self.outline_manager, 'create_stub_outline'):
                self.outline_manager.create_stub_outline(task_id)
            elif hasattr(self, 'stub_outline_manager'):
                self.stub_outline_manager.create_stub_outline(task_id)
                
            if hasattr(self, '_update_task_status'):
                self._update_task_status(task_id, 'outline_generated')
            elif hasattr(self.task_planner, 'update_plan_status'):
                self.task_planner.update_plan_status(task_id, 'outline_generated')
                
            return {"action": "outline_generated", "success": True, "message": "✅ 已跳过大纲生成（去大纲模式）。请回复'确认'以激活状态驱动写作。"}

        self._log("📋 [开关: ON] 已启用大纲生成，正在调用 LLM 生成详细大纲...")
        task_plan = None
        if task_id and hasattr(self.task_planner, 'get_plan'):
            task_plan = self.task_planner.get_plan(task_id)
            
        if not task_plan and task_id:
            pending = self.task_planner.list_pending_tasks() if hasattr(self.task_planner, 'list_pending_tasks') else []
            for p in pending:
                if p.get('task_id') == task_id:
                    task_plan = p
                    break
                    
        if not task_plan:
            self._log(f"❌ 无法通过 task_id ({task_id}) 精准匹配到任务计划，拒绝使用旧数据污染！", level="ERROR")
            return {"action": "chat", "explanation": f"❌ 无法读取当前项目的任务计划 (task_id={task_id})。为防止设定丢失，请重新发送“创建项目”。"}

        self._log(f"✅ 成功锁定任务计划 (task_id={task_plan.get('task_id')})，主角：{task_plan.get('main_character', '未知')}，题材：{task_plan.get('genre', '未知')}")
        if not task_plan.get("project_id") and getattr(self, 'current_project_id', None):
            task_plan["project_id"] = self.current_project_id
            self._log(f"🔒 [Guardian] 已强制为 task_plan 注入 project_id: {self.current_project_id}")

        outline_missing_fields: List[str] = []   
        if outline_missing_fields:
            use_default = self._request_outline_clarification(outline_missing_fields) if hasattr(self, '_request_outline_clarification') else True
            if use_default:
                task_plan.setdefault("total_chapters", 20)
                task_plan.setdefault("words_per_chapter", 2500)

        outline_res = self._auto_generate_outline(task_plan, user_prompt)

        if outline_res.get("action") == "outline_generated":
            final_task_id = task_plan.get("task_id")
            if final_task_id and hasattr(self.task_planner, 'update_plan_status'):
                self.task_planner.update_plan_status(
                    final_task_id, "outline_generated",
                    {"outline_generated_at": datetime.now().isoformat(), "outline_id": outline_res.get("outline", {}).get("title", "")}
                )
        return outline_res

    def _get_current_task_id(self) -> Optional[str]:
        """安全获取当前 task_id"""
        return getattr(self, 'current_task_id', None)

    def _get_current_task_state(self) -> str:
        """安全获取当前任务状态"""
        task_id = self._get_current_task_id()
        if not task_id:
            pid = getattr(self, 'current_project_id', None)
            if pid and hasattr(self, 'task_planner') and hasattr(self.task_planner, 'list_pending_tasks'):
                for t in self.task_planner.list_pending_tasks():
                    if t.get('project_id') == pid:
                        return t.get('status', 'no_task')
            return 'no_task'
        
        if hasattr(self, 'task_planner'):
            if hasattr(self.task_planner, 'get_plan'):
                plan = self.task_planner.get_plan(task_id)
                if plan: return plan.get('status', 'no_task')
            elif hasattr(self.task_planner, 'list_pending_tasks'):
                for t in self.task_planner.list_pending_tasks():
                    if t.get('task_id') == task_id:
                        return t.get('status', 'no_task')
        return 'no_task'

    def _ensure_world_start_time(self, new_world: Dict[str, Any], old_world: Dict[str, Any]) -> Dict[str, Any]:
        """确保 new_world 至少包含四个必填字段。"""
        from datetime import datetime
        required_keys = ("start_time", "current_time", "location", "world_rules")
        for key in required_keys:
            if key not in new_world or new_world[key] in (None, "", []):
                fallback = old_world.get(key)
                if fallback:
                    new_world[key] = fallback
                    self._log(f"⚠️ 自动从旧 world_state 中恢复缺失键 '{key}'（值：{fallback})", level="WARN")
                else:
                    default_val = datetime.now().isoformat() if "time" in key else "未知"
                    new_world[key] = default_val
                    self._log(f"⚠️ 关键字段 '{key}' 均不存在，使用默认值 '{default_val}' 进行补全", level="WARN")
        return new_world

    def _handle_chat(self, user_prompt: str, plugin_result=None, is_learning_mode=False, intent: Dict[str, Any] = None) -> Dict[str, Any]:
        """普通聊天（含情绪、知识、人格等上下文）。"""
        import json
        import time
        from . import config
        from .config import call_ollama_safe
        
        emotion = self.detect_user_emotion(user_prompt)
        beijing = self._get_current_beijing_time()
        perm_rules = self.permanent_memory.get_context_string()
        knowledge = self.knowledge_manager.get_knowledge_for_context(user_prompt)
        knowledge_ctx = self._format_knowledge_context(knowledge)
        personality_ctx = self.personality.get_context()
        training_ctx = self.training.get_learning_context()
        task_ctx = self.task_tracker.get_task_context()
        plugin_caps = "\n".join(
            [f"- {c.get('action','未知')}: {c.get('description','')} (插件：{c.get('plugin','系统')})"
             for c in self.plugin_capabilities[:10]]
        )

        intent_hint = ""
        if intent:
            primary = intent.get("primary_intent", "chat")
            conf = intent.get("confidence", 0.0)
            if primary != "chat" and conf >= 0.3:
                intent_hint = f"，我检测到您可能想要执行 **{primary}**（置信度 {conf:.0%}）"
            elif primary == "chat" and conf < 0.5:
                intent_hint = "，当前为普通聊天，如有创作需求请明确说明"

        system_prompt = f"""你是"{self.robot_name}"{self.robot_icon}，运行在用户本地的智能电脑助手。
【当前北京时间】{beijing}
{perm_rules}
{knowledge_ctx}
【人格设定】{personality_ctx}
【学习状态】{training_ctx}
【当前任务】{task_ctx}
【可用插件能力】{plugin_caps}
【情绪检测】用户情绪：{emotion}
【意图倾向】{intent_hint}
"""

        msgs = [{"role": "system", "content": system_prompt}]
        msgs.extend(self.conversation_history[-self.max_history_length :])
        if plugin_result:
            msgs.append({"role": "user", "content": f"[系统执行结果] {json.dumps(plugin_result, ensure_ascii=False)}"})
        else:
            msgs.append({"role": "user", "content": user_prompt})

        num_predict = getattr(config, "MAX_OUTPUT_TOKENS", 16000) if is_learning_mode else 16000
        timeout = getattr(config, "REQUEST_TIMEOUT", 120) if is_learning_mode else getattr(config, "SAFE_TIMEOUT", 30)

        last_err = None
        for _ in range(3):
            try:
                result = call_ollama_safe(msgs, timeout=timeout, options={"num_predict": num_predict})
                if result.get("success"):
                    ai_text = result.get("content", "")
                    self.last_raw_response = ai_text
                    with self._conversation_lock:
                        self.conversation_history.append({"role": "user", "content": user_prompt})
                        self.conversation_history.append({"role": "assistant", "content": ai_text})
                        self._ensure_conversation_size()
                    self._save_conversation_history()
                    self.training.record_session(user_prompt, ai_text, emotion)
                    return {"action": "chat", "explanation": ai_text, "need_confirm": False}
                last_err = result.get("error", "未知错误")
                time.sleep(2)
            except Exception as e:
                last_err = str(e)
                time.sleep(2)

        return {"action": "chat", "explanation": f"思考超时：{last_err}", "need_confirm": False}

    def _detect_action(self, user_prompt: str, context: Dict[str, Any] = None) -> Dict[str, Any]:
        """旧版保留接口，直接返回 chat"""
        return {"primary_intent": "chat", "confidence": 0.7, "reasoning": "默认聊天"}

    def _detect_actions(self, user_prompt: str) -> List[str]:
        """基于关键词的动作检测"""
        txt = user_prompt.lower()
        actions = []
        if any(w in txt for w in ['时间', '几点', '日期']): actions.append('get_time')
        if any(w in txt for w in ['天气', '气温', '下雨', '预报']): actions.append('get_weather')
        if any(w in txt for w in ['创建', '生成', '保存', '写']): actions.append('create_file')
        if any(w in txt for w in ['打开', '访问', '浏览']) and any(w in txt for w in ['网页', '网站', '浏览器']): actions.append('open_url')
        if any(w in txt for w in ['读取', '查看', '打开文件']) and any(w in txt for w in ['文件', 'txt', '文档']): actions.append('read_file')
        if any(w in txt for w in ['统计', 'cpu', '内存', '系统信息']): actions.append('get_system_stats')
        if any(w in txt for w in ['学习', '去学', '查一下']): actions.append('start_learning')
        return actions if actions else ['chat']

    def _format_knowledge_context(self, knowledge) -> str:
        """将 KnowledgeManager 返回的结构化知识转换为文字块。"""
        if not knowledge: return ""
        if isinstance(knowledge, dict): items = [knowledge]
        elif isinstance(knowledge, (list, tuple)): items = knowledge
        else: return str(knowledge)

        formatted_lines = []
        for idx, item in enumerate(items, 1):
            title = item.get("title") or item.get("name") or f"知识 {idx}"
            summary = item.get("summary") or item.get("content") or ""
            source = item.get("source") or item.get("url") or "未知"
            formatted_lines.append(f"- {title}: {summary} (来源: {source})")
        return "\n".join(formatted_lines)

    def detect_user_emotion(self, text: str) -> str:
        """简单情感分析"""
        txt = text.lower()
        if any(w in txt for w in ["！！！！！", "！！！！", "！！！", "烦", "生气", "滚"]): emotion = "angry"
        elif any(w in txt for w in ["谢谢", "太好了", "很好", "不错", "厉害"]): emotion = "happy"
        elif any(w in txt for w in ["失望", "不行", "不对", "错误", "错了"]): emotion = "disappointed"
        elif any(w in txt for w in ["？", "为什么", "怎么", "什么"]): emotion = "confused"
        else: emotion = "neutral"
        self.personality.update_emotion(emotion)
        self._log(f"[情绪] 检测到用户情绪: {emotion}", level="DEBUG")
        return emotion

    def _get_current_beijing_time(self) -> str:
        """统一获取北京时间"""
        from datetime import datetime
        try:
            return self.time_service.get_beijing_time()
        except Exception as e:
            self._log(f"⚠️ 获取北京时间失败，回退本地时间：{e}", level="WARN")
            return datetime.now().strftime("%Y 年%m 月%d 日 %H 时%M 分%S 秒")

    def _run_health_check(self, task_id: str, current_chapter: int) -> Dict[str, Any]:
        """调用统一的 HealthChecker 完成健康检查。"""
        from . import config
        if not getattr(config, "HEALTH_CHECK_INTERVAL_CHAPTERS", 0):
            self._log("ℹ️ 配置关闭健康检查，直接放行。", level="INFO")
            return {"success": True, "message": "Health check disabled"}

        try:
            from .health_check import HealthChecker
            checker = HealthChecker(self) if not hasattr(self, "health_checker") else self.health_checker
            result = checker.run(task_id, current_chapter)
        except Exception as e:
            self._log(f"⚠️ 健康检查执行异常: {e}", level="ERROR")
            result = {"success": False, "error": str(e), "message": "Health check exception"}

        if result.get("success"):
            self._log(f"✅ 健康检查通过（第 {current_chapter} 章）", level="INFO")
        else:
            self._log(f"⚠️ 健康检查未通过（第 {current_chapter} 章）: {result.get('error', '未知')}", level="WARN")
        return result

    def _build_chapter_prompt(self, role_state: Dict[str, Any], world_state: Dict[str, Any], conflict: Dict[str, Any], chapter_num: int, thinking_slice: List[Dict[str, Any]] = None) -> str:
        """根据当前状态信息生成章节 Prompt。"""
        thinking_slice = thinking_slice or []
        char_desc = f"主角：{role_state.get('name', '未知')}，体力：{role_state.get('health', 0)}/100，伤势：{role_state.get('injury', '无')}，初始信念：{role_state.get('belief', '')}"
        world_desc = f"世界：当前时间点 {world_state.get('current_time', '未知')}，地点 {world_state.get('location', '未知')}，季节 {world_state.get('season', '未知')}，世界规则：{world_state.get('world_rules', '无特殊规则')}"
        conflict_desc = f"核心冲突：{conflict.get('statement', '未定义')}"
        
        thinking_desc = "暂无思考日志记录"
        if thinking_slice:
            thinking_entries = [f"第{entry.get('chapter', '?')}章：{entry.get('inspection', {}).get('feedback', '无具体反馈')}" for entry in thinking_slice[-3:]]
            thinking_desc = "最近思考：\n" + "\n".join(thinking_entries)
        
        prompt = f"""你是资深小说创作助手，请根据以下状态信息生成第{chapter_num}章内容：
{char_desc}
{world_desc}
{conflict_desc}
{thinking_desc}

要求：
1. 严格符合角色当前状态和世界规则
2. 推进核心冲突发展
3. 保持故事连贯性和可读性
4. 本章字数控制在合理范围内
5. 不要重复之前章节内容
6. 如果是第一章，需要吸引人的开头；如果是中间章节，需要承上启下；如果是结尾章节，需要有结论感

请直接输出章节正文内容，不要包含任何解释或元信息。"""
        return prompt

    def _generate_live_rewrite_feedback(self, inspection: Dict[str, Any], current_score: int, task_plan: Dict[str, Any]) -> str:
        """现场实时调用 LLM 生成“毒舌主编批语”。"""
        import json
        import re
        try:
            from .config import call_ollama_safe
            issues = inspection.get("issues", [])
            reader_exp = inspection.get("reader_experience", {})
            complaints = reader_exp.get("reader_complaints", []) if isinstance(reader_exp, dict) else []
            red_lines = inspection.get("red_lines_violations", [])

            eval_data = {
                "score": current_score,
                "red_lines": [r.get("issue", r.get("rule_id", "")) for r in red_lines[:2]] if isinstance(red_lines, list) else [],
                "reader_complaints": complaints[:2],
                "deductions": [i for i in issues if "[读者吐槽]" not in i][:3]
            }
            
            prompt = f"""你是一位极其严苛、目光如炬的网文主编。你的作者刚交了一章初稿，质量很差（得分仅 {current_score} 分）。
请根据以下质检反馈，用【严厉、毒舌、一针见血】的口吻，给作者写一段 100-150 字的【重写指导批语】。
质检反馈（罪证）：
{json.dumps(eval_data, ensure_ascii=False)}

要求：
1. 绝对不要说“文笔不错但...”这种废话！直接开骂/痛批核心毒点！
2. 必须明确指出“哪里写得像屎”、“读者为什么想弃书”。
3. 给出极其具体的修改动作。
4. 只输出批语文本，不要输出任何前缀和后缀！"""

            messages = [
                {"role": "system", "content": "你是一个没有感情的毒舌主编，只输出批语文本，绝不输出任何废话。"},
                {"role": "user", "content": prompt}
            ]
            
            self._log("🧠 [教鞭引擎] 正在现场实时生成主编批语...", level="INFO")
            resp = call_ollama_safe(messages, options={"num_predict": 32000, "temperature": 0.6})
            feedback = resp.get("content", "").strip() if isinstance(resp, dict) else str(resp).strip()
            feedback = re.sub(r'^(好的|批语如下|主编批语：|重写指导：|批语)\s*', '', feedback, flags=re.IGNORECASE)
            
            if feedback and len(feedback) > 15:
                return feedback
        except Exception as e:
            self._log(f"⚠️ 现场生成主编批语失败: {e}，降级为动态提取", level="WARN")
            
        # 降级逻辑
        issues = inspection.get('issues', [])
        reader_complaints = [i.replace("[读者吐槽] ", "") for i in issues if "[读者吐槽]" in i]
        specific_issues = [i for i in issues if "[读者吐槽]" not in i and len(i) > 10][:2]
        
        parts = []
        if reader_complaints: parts.append(f"读者痛批：{'; '.join(reader_complaints[:2])}")
        if specific_issues: parts.append(f"核心毒点：{'; '.join(specific_issues)}")
        if not parts: parts.append(f"得分仅 {current_score}，剧情像流水账，缺乏张力，重写！")
        return "\n".join(parts)
  
    def _generate_chapters_batch(self, task_plan: Dict[str, Any], start_chapter: int, end_chapter: int, force_overwrite: bool = False) -> Dict[str, Any]:
        """批量生成章节。🌟 架构优化：提取路径穿透至循环外，支持 force_overwrite 强制重写。"""
        import time, os, json, shutil
        from . import config

        task_id = task_plan.get('task_id')
        if not task_id:
            return {"action": "chat", "explanation": "❌ 任务 ID 缺失，无法进行章节生成。", "task_id": None, "status": "error"}

        # 🌟 架构优化：将 project_dir 穿透逻辑提取到循环外部，只执行一次，彻底告别性能浪费
        real_dir = task_plan.get("project_dir", "")
        if not real_dir:
            real_dir = getattr(self, 'current_project_dir_path', '') or getattr(self, 'current_project_dir', '')
        if not real_dir and hasattr(self, 'task_planner') and self.task_planner:
            plan = self.task_planner.get_plan(task_id) if hasattr(self.task_planner, 'get_plan') else {}
            if isinstance(plan, dict): real_dir = plan.get("project_dir", "")
        if not real_dir and hasattr(self, 'task_planner') and self.task_planner:
            plan = self.task_planner.get_plan(task_id) if hasattr(self.task_planner, 'get_plan') else {}
            if isinstance(plan, dict):
                pid = plan.get("project_id", "")
                if pid and not str(pid).startswith("proj_task_"):
                    try:
                        from .project_path_manager import ProjectPathManager
                        resolved = ProjectPathManager.resolve_project_dir(str(pid))
                        if resolved and resolved != "invalid_project":
                            base_dir = getattr(self, 'base_dir', None) or getattr(config, 'BASE_DIR', os.getcwd())
                            full_path = os.path.join(base_dir, "novel_projects", resolved) if not os.path.isabs(resolved) else resolved
                            if os.path.isdir(full_path): real_dir = full_path
                    except Exception: pass
                    
        if real_dir and isinstance(task_plan, dict):
            task_plan["project_dir"] = real_dir
            self._log(f"💉 [物理注入] 已从 AIController 实例强制注入 project_dir: {real_dir}")
            if hasattr(self, 'context_builder') and self.context_builder:
                self.context_builder.task_plan = task_plan
                if hasattr(self.context_builder, 'set_project_dir'): self.context_builder.set_project_dir(real_dir)

        results = []
        self._log(f"📚 开始生成章节：第{start_chapter}章 至 第{end_chapter}章 (强制重写: {force_overwrite})", level="INFO")

        for chap_num in range(start_chapter, end_chapter + 1):
            # 🌟 致命修复：支持 force_overwrite，如果开启则无视文件存在检测并删除旧文件
            if self._chapter_exists(task_plan, chap_num):
                if force_overwrite:
                    self._log(f"🔄 [强制重写] 检测到第 {chap_num} 章已存在，正在删除旧文件...", level="INFO")
                    existing_dir = os.path.join(task_plan.get("project_dir", ""), "02_章节", f"第{chap_num:03d}章")
                    if os.path.isdir(existing_dir):
                        try: shutil.rmtree(existing_dir)
                        except Exception: pass
                else:
                    existing_dir = os.path.join(task_plan.get("project_dir", ""), "02_章节", f"第{chap_num:03d}章")
                    existing_fp = self._first_nonempty_file(existing_dir) or ""
                    self._log(f"⚡ 第 {chap_num} 章已存在，跳过生成", level="INFO")
                    if hasattr(self, 'progress_tracker'):
                        self.progress_tracker.update_progress(task_id=task_id, chapter_num=chap_num, success=True, chapter_summary="已存在（跳过）", filepath=existing_fp)
                    results.append({'chapter': chap_num, 'success': True, 'skipped': True})
                    continue

            try:
                if hasattr(self, 'interruption_handler') and self.interruption_handler.is_active():
                    return {"action": "chat", "explanation": "🛑 用户中断请求已被接收。", "task_id": task_id, "status": "paused", "interrupted_at": chap_num}
            except Exception: pass

            if chap_num > 1 and chap_num % getattr(config, "HEALTH_CHECK_INTERVAL_CHAPTERS", 10) == 0:
                health_res = self._run_health_check(task_id, chap_num) if hasattr(self, '_run_health_check') else {"success": True}
                if not health_res.get("success", False):
                    return {"action": "chat", "explanation": f"⚠️ 健康检查错误：{health_res.get('error','未知')}", "task_id": task_id, "status": "blocked"}

            try:
                state_data = self._load_state_files()
            except Exception as e:
                return {"action": "chat", "explanation": f"❌ 状态读取失败：{str(e)}。", "task_id": task_id, "status": "error"}

            role_state = state_data.get("character", {})
            world_state = state_data.get("world", {})
            conflict = state_data.get("conflict", {})
            thinking_log = state_data.get("thinking_log", [])

            prompt = ""
            if hasattr(self, '_build_chapter_prompt'):
                prompt = self._build_chapter_prompt(role_state=role_state, world_state=world_state, conflict=conflict, chapter_num=chap_num, thinking_slice=thinking_log[-5:] if thinking_log else [])

            last_rewrite_feedback = None
            max_retries = getattr(config, 'MAX_CHAPTER_GENERATION_RETRIES', 2)
            attempt = 0
            best_gen_res = None
            best_inspection = None
            best_score = -1
            continuation_guide = {}

            while attempt <= max_retries:
                current_guide = dict(continuation_guide)
                if last_rewrite_feedback:
                    current_guide["rewrite_feedback"] = last_rewrite_feedback

                # 🌟 核心接入：生成前，调用 LLM 语义召回本章专属设定
                if hasattr(self, '_llm_semantic_retrieve_settings'):
                    chapter_context = f"第{chap_num}章。" + json.dumps(continuation_guide.get('chapter_outline', {}), ensure_ascii=False)
                    recalled_settings = self._llm_semantic_retrieve_settings(chapter_context, task_plan.get("core_details", []), top_k=25)
                    continuation_guide["recalled_settings"] = recalled_settings
                    high_priority = task_plan.get("high_priority_settings", [])
                    if high_priority: continuation_guide["high_priority_settings"] = high_priority[:10]

                gen_res = self.chapter_generator.generate_chapter(chapter_num=chap_num, task_id=task_id, task_plan=task_plan, continuation_guide=current_guide)

                # 🌟🌟🌟 核心接入：接管 generate_chapter 返回的 Critic 审查结果，决定是否重试 🌟🌟🌟
                critic_result = gen_res.get("critic_result", {})
                if isinstance(critic_result, dict) and not critic_result.get("is_valid", True):
                    feedback = critic_result.get("feedback", "正文偏离设定或存在幻觉")
                    self._log(f"⚠️ [Critic拦截] 第{chap_num}章正文审查未通过：{feedback}，触发外层重写...", level="WARN")
                    continuation_guide["rewrite_feedback"] = f"【主编严厉批语】：{feedback}。请彻底修正上述问题，严格遵循核心设定和大纲重写本章！"
                    attempt += 1
                    continue 

                if not gen_res.get("success", False):
                    if gen_res.get("is_refusal"):
                        continuation_guide["is_refusal"] = True
                        self._log("🔓 已标记越狱状态，下次重试将强制解除模型限制。", level="WARN")
                    attempt += 1
                    if attempt > max_retries and best_gen_res is None:
                        err_id = self.error_handler.record_error(task_id=task_id, chapter_num=chap_num, error_type='generation_failed', error_message=gen_res.get('error','')) if hasattr(self, 'error_handler') else None
                        if hasattr(self, 'progress_tracker'):
                            self.progress_tracker.update_progress(task_id=task_id, chapter_num=chap_num, success=False, error=gen_res.get('error',''))
                        results.append({'chapter': chap_num, 'success': False, 'error_id': err_id})
                        break
                    continue
                
                inspection = gen_res.get("quality_details", {})
                current_score = inspection.get("score", 80) or inspection.get("overall_score", 80)
                if current_score > best_score:
                    best_score = current_score
                    best_gen_res = gen_res
                    best_inspection = inspection

                if inspection.get('passed', False) or current_score >= 70:
                    break
                    
                attempt += 1
                if attempt > max_retries:
                    self._log(f"⚠️ 第{chap_num}章重试已达上限({max_retries+1}次)，强制启用【最高分保留机制】(保留最高分: {best_score})，立刻放行！", level="WARN")
                    break
                    
                if hasattr(self, '_generate_live_rewrite_feedback'):
                    last_rewrite_feedback = self._generate_live_rewrite_feedback(inspection, current_score, task_plan)
                elif hasattr(self, '_extract_rewrite_feedback'):
                    last_rewrite_feedback = self._extract_rewrite_feedback(inspection, current_score)
                else:
                    issues = inspection.get('issues', ['质量不佳'])
                    last_rewrite_feedback = f"当前得分 {current_score}。主要问题：{'; '.join(str(i) for i in issues[:3])}。请针对这些问题进行实质性修改！"
                self._log(f"⚠️ 第{chap_num}章底层质检未通过（得分 {current_score}），触发现场生成批语，准备第 {attempt} 次重写。", level="WARN")

            if best_gen_res and best_gen_res.get("success", False):
                inspection = best_inspection
                gen_res = best_gen_res
                new_role = inspection.get('new_role_state', role_state)
                new_world = inspection.get('new_world_state', world_state)
                
                if isinstance(new_world, dict) and isinstance(world_state, dict):
                    for key in ['location', 'world_rules', 'start_time', 'current_time']:
                        old_val = world_state.get(key)
                        new_val = new_world.get(key)
                        if (not new_val or str(new_val).strip() in ("未知", "null", "无特殊规则", "")) and old_val and str(old_val).strip() not in ("未知", "null", "无特殊规则", ""):
                            new_world[key] = old_val

                if isinstance(new_world, dict) and 'start_time' not in new_world and isinstance(world_state, dict):
                    new_world['start_time'] = world_state.get('start_time')
                if isinstance(new_role, dict) and 'id' not in new_role: new_role['id'] = task_id
                if isinstance(new_world, dict) and 'id' not in new_world: new_world['id'] = task_id

                self._last_state_changes = inspection.get('state_changes', {})
                self._last_chapter_number = chap_num
                if hasattr(self, '_ensure_world_start_time'):
                    new_world = self._ensure_world_start_time(new_world, world_state)

                try: self._save_state_files(new_role, new_world)
                except Exception as e: self._log(f"⚠️ 第{chap_num}章状态写入失败（已忽略）：{e}", level="WARN")

                thinking_entry = {"timestamp": time.time(), "chapter": chap_num, "prompt": prompt, "draft": gen_res.get('content', ''), "inspection": inspection}
                try:
                    if hasattr(self, '_append_thinking_log'): self._append_thinking_log(thinking_entry)
                except Exception: pass
                    
                if hasattr(self, 'progress_tracker'):
                    self.progress_tracker.update_progress(task_id=task_id, chapter_num=chap_num, success=True, chapter_summary=gen_res.get('summary',''), filepath=gen_res.get('filepath',''))
                    
                if hasattr(self, '_llm_audit_setting_coverage'):
                    self._log(f"🔍 [正文审计] 启动 LLM 校验第{chap_num}章正文消耗了哪些核心设定...")
                    self._llm_audit_setting_coverage(gen_res.get('content', ''), task_plan, phase=f"第{chap_num}章正文")

                results.append({'chapter': chap_num, 'success': True, 'final_score': best_score})
            else:
                err_id = self.error_handler.record_error(task_id=task_id, chapter_num=chap_num, error_type='fatal_generation_failed', error_message='底层彻底失败') if hasattr(self, 'error_handler') else None
                if hasattr(self, 'progress_tracker'):
                    self.progress_tracker.update_progress(task_id=task_id, chapter_num=chap_num, success=False, error='Fatal error')
                results.append({'chapter': chap_num, 'success': False, 'error_id': err_id})

        # 🌟 批次宏观验收与全局打分
        success_cnt = sum(1 for r in results if r.get('success'))
        total_cnt = len(results)
        macro_score = 100
        macro_feedback = ""
        
        if success_cnt > 0:
            valid_scores = [r.get('final_score', 80) for r in results if r.get('success') and r.get('final_score') is not None]
            if valid_scores:
                macro_score = sum(valid_scores) / len(valid_scores)
            if macro_score < 75:
                macro_feedback = f"\n\n⚠️ **【宏观质量警告】**：本批次平均得分仅为 {macro_score:.1f} 分（低于 75 分阈值）。可能存在剧情推进过快、设定遗漏或文笔重复等宏观问题。建议回复“回退重写第X章”或调整核心设定后重新生成。"
                self._log(f"⚠️ [宏观验收] 批次平均分 {macro_score:.1f}，未达优良标准，已附加警告提示。", level="WARN")
            else:
                self._log(f"✅ [宏观验收] 批次平均分 {macro_score:.1f}，质量达标。")

        if end_chapter >= int(task_plan.get('total_chapters', 1)):
            if hasattr(self, 'progress_tracker') and hasattr(self.progress_tracker, 'pause_task'): 
                self.progress_tracker.pause_task(task_id)
            return {
                "action": "chat", 
                "explanation": f"🎉 小说生成完成！\n总章节：{task_plan.get('total_chapters')} 成功：{success_cnt} 失败：{total_cnt - success_cnt}\n📊 最终宏观得分：{macro_score:.1f} 分{macro_feedback}", 
                "task_id": task_id, 
                "status": "completed"
            }

        next_chap = end_chapter + 1
        return {
            "action": "chat", 
            "explanation": f"✅ 已生成第{start_chapter}-{end_chapter}章（成功 {success_cnt} 章）\n📊 本批次宏观得分：{macro_score:.1f} 分{macro_feedback}\n\n回复'继续生成'写第 {next_chap} 章。", 
            "task_id": task_id, 
            "status": "in_progress", 
            "next_chapter": next_chap
        }

    def _extract_rewrite_feedback(self, inspection: Dict[str, Any], score: int) -> str:
        """从质检结果中提取核心低分原因，构建‘主编批语’用于指导下一次重写。"""
        feedback_parts = [f"上一版得分：{score}分（未达标）。"]
        
        name_check = inspection.get("protagonist_name_check", {})
        if not name_check.get("passed", True):
            violations = name_check.get("violations", [])
            feedback_parts.append(f"🚨 致命错误：主角名字写错！出现了 {violations}。必须全部替换为正确的主角名！")
            
        dim_scores = inspection.get("dimension_scores", {})
        for dim_id, dim_data in dim_scores.items():
            dim_score = dim_data.get("score", 100)
            if dim_score < 80:  
                dim_name = dim_data.get("name", dim_id)
                deductions = dim_data.get("deductions", [])
                deduc_details = []
                for d in deductions:
                    if isinstance(d, dict) and d.get("reason"):
                        quote = d.get("quote", "")
                        detail = d["reason"]
                        if quote: detail += f" (原文：'{quote[:30]}...')"
                        deduc_details.append(detail)
                if deduc_details:
                    feedback_parts.append(f"- [{dim_name}] 得分仅 {dim_score} 分。具体问题：{'; '.join(deduc_details)}。")
                    
        suggestions = inspection.get("modification_suggestions", "") or inspection.get("core_issues_summary", "")
        if suggestions:
            feedback_parts.append(f"主编总体修改建议：{suggestions}")
            
        feedback = "\n".join(feedback_parts)
        self._log(f"[重写反馈] 生成的批语：{feedback}", level="DEBUG")
        return feedback

    def _handle_resume_task(self, user_prompt: str, generation_mode: str = "all", **kwargs) -> Dict[str, Any]:
        """处理继续写作的请求（包括自动生成缺失的大纲）。🌟 完美兼容原版逻辑，增加进度兜底与重写透传。"""
        import re
        from . import config
        
        self._log("▶️ 检测到继续生成指令...")
        pending = []
        if hasattr(self, 'task_planner') and hasattr(self.task_planner, 'list_pending_tasks'):
            pending = self.task_planner.list_pending_tasks()
        if not pending and hasattr(self, 'progress_tracker') and hasattr(self.progress_tracker, 'list_pending_tasks'):
            pending = self.progress_tracker.list_pending_tasks()
            
        if not pending:
            return {"action": "chat", "explanation": "❌ 未找到未完成的任务"}

        task_plan = pending[0]
        task_id = task_plan.get('task_id', '')
        status = task_plan.get('status', 'unknown')

        outline_exists = False
        if hasattr(self, 'outline_manager'):
            outline_exists = bool(self.outline_manager.get_outline(task_id))
        self._log(f"📋 任务状态：{status}，大纲存在：{outline_exists}")

        # 🌟 核心新增：提取强制重写参数，为后续透传做准备
        force_overwrite = kwargs.get('force_overwrite', False)

        if not outline_exists:
            self._log("⚠️ 大纲不存在，先生成大纲...")
            return self._handle_outline_generation(user_prompt)

        if status in ['planned', 'outline_generated', 'outline_confirmed']:
            self._log("✅ 大纲已就绪，直接进入写作章节生成（零用户确认）")
            # 🌟 致命修复：透传 force_overwrite
            return self._generate_chapters_batch(task_plan, 1, 1, force_overwrite=force_overwrite)

        if status == 'in_progress':
            next_chap = 1
            if hasattr(self, 'progress_tracker') and hasattr(self.progress_tracker, 'get_next_chapter'):
                next_chap = self.progress_tracker.get_next_chapter(task_id)
                
            # 🌟🌟🌟 致命修复：进度兜底机制。如果 progress_tracker 获取失败或返回 1，使用文件系统扫描兜底，防止永远从第1章开始！
            if next_chap <= 1 and hasattr(self, '_get_last_generated_chapter'):
                last_gen = self._get_last_generated_chapter(task_plan)
                if last_gen > 0:
                    next_chap = last_gen + 1
                    self._log(f"💡 [进度兜底] progress_tracker 未返回有效进度，通过文件系统扫描确认下一章为：第 {next_chap} 章")

            try:
                if hasattr(self, 'interruption_handler') and self.interruption_handler.is_active():
                    self._log("🛑 检测到用户中断请求，终止继续生成", level="WARN")
                    return {"action": "chat", "explanation": "🛑 用户中断请求已被接收，继续生成已安全终止。", "task_id": task_id, "status": "paused", "interrupted_at": next_chap}
            except Exception as e:
                self._log(f"🛑 中断状态检查异常：{e}", level="WARN")

            health_interval = getattr(config, "HEALTH_CHECK_INTERVAL_CHAPTERS", 10)
            if next_chap > 1 and (next_chap - 1) % health_interval == 0:
                self._log(f"🏥 继续生成前触发健康检查（第{next_chap-1}章）...")
                health_res = self._run_health_check(task_id, next_chap - 1) if hasattr(self, '_run_health_check') else {"success": True}
                if not health_res.get('success'):
                    return {"action": "chat", "explanation": f"⚠️ 健康检查失败：{health_res.get('error','未知')}。\n\n请先修复工程状态。", "task_id": task_id, "status": "blocked"}

            batch_match = re.search(r'生成\s?(\d+)\s?章', user_prompt)
            if batch_match:
                try: batch = int(batch_match.group(1))
                except ValueError: batch = 1
                if batch <= 0: batch = 1
                try: total_chapters = int(task_plan.get('total_chapters', 1))
                except (ValueError, TypeError): total_chapters = 1
                end = min(next_chap + batch - 1, total_chapters)
            else:
                end = next_chap

            # 🌟 致命修复：透传 force_overwrite
            return self._generate_chapters_batch(task_plan, next_chap, end, force_overwrite=force_overwrite)

        return {"action": "chat", "explanation": f"⚠️ 任务状态异常：{status}\n\n建议：回复\"生成大纲\"重新开始流程。"}

    def _handle_chapter_writing(self, user_prompt: str, generation_mode: str = "all", **kwargs) -> Dict[str, Any]:
        """章节写作入口（强制消费 IntentAnalyzer 提取的高级参数）。"""
        import os
        
        task_plan = None
        if getattr(self, 'current_task_id', None) and hasattr(self, 'task_planner') and hasattr(self.task_planner, 'get_plan'):
            task_plan = self.task_planner.get_plan(self.current_task_id)

        if not task_plan and hasattr(self, 'task_planner') and hasattr(self.task_planner, 'list_pending_tasks'):
            pending = self.task_planner.list_pending_tasks()
            if pending: task_plan = pending[0]

        if not task_plan and hasattr(self, 'task_planner') and hasattr(self.task_planner, 'list_all_tasks'):
            for t in self.task_planner.list_all_tasks():
                if t.get('project_id') == getattr(self, 'current_project_id', None):
                    task_plan = t
                    break

        if not task_plan:
            self._log("❌ _handle_chapter_writing: 未能定位当前任务计划（task_plan 为 None）", level="ERROR")
            return {"action": "chat", "explanation": "❌ 系统内部错误：未找到有效的任务计划，无法继续写作。", "need_confirm": False}

        if task_plan and not getattr(self, 'creation_allowed', False):
            current_status = task_plan.get('status', 'none')
            if current_status in ['outline_generated', 'in_progress']:
                task_id = task_plan.get("task_id")
                if task_id and hasattr(self, 'outline_manager'):
                    outline_data = None
                    if hasattr(self.outline_manager, 'get_outline'):
                        outline_data = self.outline_manager.get_outline(task_id)
                    if not outline_data and hasattr(self.outline_manager, 'get_outline_path'):
                        outline_path = self.outline_manager.get_outline_path(task_id)
                        if os.path.exists(outline_path): outline_data = True

                    if outline_data:
                        self._log("🚀 [兜底放行] 检测到大纲已存在但状态未更新，强制扭转为 in_progress 并激活写作权限！", level="WARN")
                        self.creation_allowed = True
                        if hasattr(self.task_planner, 'update_plan_status'):
                            self.task_planner.update_plan_status(task_id, 'in_progress')
                        self.current_task_id = task_id

        if not getattr(self, 'creation_allowed', False):
            return {"action": "chat", "explanation": "❌ 请先完成前置准备并发送确认指令（如‘确认’、‘开始’或‘我准备好了’），否则系统不允许开始章节生成。", "need_confirm": False}

        task_id = task_plan.get('task_id')
        if not task_id:
            return {"action": "chat", "explanation": "❌ 任务 ID 缺失，无法继续章节生成。", "need_confirm": False}
            
        total_chaps = int(task_plan.get('total_chapters', 1))
        params = getattr(self, 'current_intent_params', {}) or {}
        target_chapters = params.get('target_chapters', [])
        param_batch_size = params.get('batch_size', 0)
        
        start_ch = 0
        end_ch = 0

        if target_chapters:
            start_ch = min(target_chapters)
            end_ch = max(target_chapters)
            self._log(f"🎯 [参数消费] 检测到指定章节，强制覆盖进度条：第 {start_ch} 章 至 第 {end_ch} 章")
        elif param_batch_size > 1:
            completed_chaps = self._get_last_completed_chapter(task_plan) if hasattr(self, '_get_last_completed_chapter') else 0
            start_ch = completed_chaps + 1
            end_ch = min(start_ch + param_batch_size - 1, total_chaps)
            self._log(f"🎯 [参数消费] 检测到批量数量 {param_batch_size}，从断点生成：第 {start_ch} 章 至 第 {end_ch} 章")
        else:
            completed_chaps = self._get_last_completed_chapter(task_plan) if hasattr(self, '_get_last_completed_chapter') else 0
            start_ch = completed_chaps + 1
            
            while start_ch <= total_chaps:
                exists = False
                if hasattr(self, '_chapter_exists'):
                    try: exists = self._chapter_exists(task_plan, start_ch)
                    except Exception: pass
                
                if not exists:
                    p_dir = task_plan.get("project_dir", "")
                    if p_dir:
                        f_path = os.path.join(p_dir, "02_章节", f"第{start_ch:03d}章", f"第{start_ch:03d}章.txt")
                        if os.path.exists(f_path) and os.path.getsize(f_path) > 0: exists = True
                
                if not exists: break
                self._log(f"⚡ 第 {start_ch} 章已存在（磁盘物理检测），自动顺延...", level="INFO")
                start_ch += 1
                
            end_ch = start_ch
            self._log(f"🎯 [默认模式] 从断点生成 1 章：第 {start_ch} 章")

        if start_ch > total_chaps:
            return {"action": "chat", "explanation": f"🎉 所有 {total_chaps} 章已完成或已存在！", "task_id": task_id, "status": "completed"}
            
        end_ch = min(end_ch, total_chaps)
        return self._generate_chapters_batch(task_plan, start_ch, end_ch)

    def _record_outline_failure(self, task_id: str, reason: str, extra: dict = None) -> None:
        """
        当所有大纲生成手段均失败时统一记录经验，防止以后再次同样失败。
        """
        self._log(f"⚠️ 大纲生成失败 (task_id={task_id}) → {reason}", level="WARN")
        try:
            if hasattr(self, "learning_engine"):
                payload = {
                    "task_id": task_id,
                    "reason": reason,
                    "extra": extra or {},
                    "timestamp": datetime.now().isoformat(),
                }
                self.learning_engine.save_cross_project_experience(
                    project_id=self.current_project_id,
                    topic="outline_generation_failure",
                    solution=json.dumps(payload, ensure_ascii=False),
                    tags=["outline", "failure"],
                )
        except Exception as e:
            self._log(f"❌ 记录大纲失败经验异常: {e}", level="ERROR")

    def _auto_generate_world_models(self, task_id: str, user_prompt: str) -> None:
        """
        自动生成 **初始世界/角色状态**（在项目创建后如果状态文件缺失会调用此函数）。
        1️⃣ 通过 ``PromptManager.render("initial_state", ...)`` 生成 Prompt（模板需放在 ``prompt_templates/initial_state.txt``）。  
        2️⃣ 调用 ``call_ollama_safe`` 获取 JSON（返回必须包含 ``character`` 与 ``world`` 两个顶层键）。  
        3️⃣ 若 LLM 返回无效，回退为默认角色/世界（健康的最小结构）。  
        4️⃣ 使用 ``helpers._save_state_files`` 将两份 JSON 原子写入磁盘。
        """
        self._log("🧠 开始自动生成初始角色/世界状态（_auto_generate_world_models）", level="INFO")
        # ① 组装 Prompt（使用用户原始提示作为灵感素材）
        try:
            from .prompt_manager import PromptManager
            prompt = PromptManager.render(
                "initial_state",
                user_prompt=user_prompt,
                placeholder="（若未提供会使用默认值）",
            )
        except Exception as e:
            self._log(f"⚠️ 渲染初始状态 Prompt 失败，使用默认 Prompt: {e}", level="WARN")
            prompt = f"""你是一个严格的 JSON 生成器，请返回如下结构：
{{
    "character": {{"name":"主角","health":100,"belief":"未知"}},
    "world": {{"start_time":"{datetime.now().isoformat()}","current_time":"{datetime.now().isoformat()}","location":"未知","world_rules":"无"}}
}}"""

        # ② 调用 LLM
        try:
            raw_res = call_ollama_safe([{"role": "user", "content": prompt}])
            if not raw_res.get("success"):
                raise RuntimeError(raw_res.get("error", "LLM 调用失败"))
            raw_text = raw_res.get("content", "")
        except Exception as e:
            self._log(f"⚠️ LLM 调用失败，回退为默认状态: {e}", level="WARN")
            raw_text = ""

        # ③ 解析 JSON（去除可能的 Markdown 包裹）
        try:
            json_str = raw_text
            if json_str.startswith("```"):
                json_str = json_str.split("\n", 1)[1].rsplit("```", 1)[0]
            state_dict = json.loads(json_str)
            character_state = state_dict.get("character")
            world_state = state_dict.get("world")
        except Exception as e:
            self._log(f"⚠️ LLM 返回无法解析为 JSON，使用默认状态: {e}", level="WARN")
            character_state = {"name": "主角", "health": 100, "belief": "未知"}
            world_state = {
                "start_time": datetime.now().isoformat(),
                "current_time": datetime.now().isoformat(),
                "location": "未知",
                "world_rules": "无",
            }

        # ④ 写入磁盘（利用 helpers 已实现的原子写入）
        try:
            self._save_state_files(character_state, world_state)
            self._log("✅ 初始角色/世界状态已成功写入磁盘", level="INFO")
        except Exception as e:
            self._log(f"❌ 写入初始状态文件失败: {e}", level="ERROR")
            raise


    def _handle_file_generation(self, user_prompt: str, intent: Dict[str, Any]) -> Dict[str, Any]:
        """
        处理 **任意文件生成** 请求（文本、图片、音频、视频、压缩包）——
        1️⃣ 解析用户自然语言得到目标目录、文件名、内容描述；
        2️⃣ 根据文件扩展名走不同的生成分支；
        3️⃣ 所有写入均采用 **原子写入**（tmp → rename + fsync）；
        4️⃣ 成功返回统一的 ``chat`` 结构体，失败同样返回 ``chat`` 并在 ``explanation`` 中给出错误信息。
        """
        self._log("📄 开始处理文件生成请求…")

        import re
        from pathlib import Path

        # -------------------- 1️⃣ 解析目标目录 --------------------
        dir_match = re.search(r"目录\s*['\"]([^'\"]+)['\"]\s*下", user_prompt)
        target_dir = dir_match.group(1).strip() if dir_match else ""
        if not target_dir:
            return {
                "action": "chat",
                "explanation": "❌ 未能解析目标目录，请使用类似 “请在目录 'my_folder' 下生成 …” 的格式。",
                "need_confirm": False,
            }

        # -------------------- 2️⃣ 解析文件名 --------------------
        name_match = re.search(r"名为\s*['\"]([^'\"]+)['\"]\s*的", user_prompt)
        filename = name_match.group(1).strip() if name_match else ""
        if not filename:
            return {
                "action": "chat",
                "explanation": "❌ 未能解析文件名，请使用类似 “名为 'my_file.txt' 的 …” 的格式。",
                "need_confirm": False,
            }

        # -------------------- 3️⃣ 解析内容描述 --------------------
        content_match = re.search(r"内容为[:：]\s*([^。]+)", user_prompt)
        content_desc = content_match.group(1).strip() if content_match else ""

        # 若仍未得到内容，尝试捕获引号里的文字
        if not content_desc:
            quote_match = re.search(r"'([^']+)'", user_prompt)
            content_desc = quote_match.group(1).strip() if quote_match else ""

        # -------------------- 4️⃣ 确保目标目录存在 --------------------
        try:
            Path(target_dir).mkdir(parents=True, exist_ok=True)
        except Exception as e:
            self._log(f"❌ 创建目录失败：{e}", level="ERROR")
            return {
                "action": "chat",
                "explanation": f"❌ 无法创建目录 `{target_dir}`：{e}",
                "need_confirm": False,
            }

        # -------------------- 5️⃣ 生成文件 --------------------
        file_path = Path(target_dir) / filename
        ext = file_path.suffix.lower()

        try:
            # ---------- 文本类文件 ----------
            if ext in {".txt", ".md", ".log", ".json", ".csv", ".py", ".html", ".css", ".js"}:
                content = content_desc or f"# {filename}\n\n自动生成的文件内容。"
                self._atomic_write(file_path, content.encode("utf-8"))
                self._log(f"✅ 文本文件生成成功：{file_path}")
                return {
                    "action": "chat",
                    "explanation": f"✅ 文件已生成：{filename}\n📁 位置：{target_dir}\n🗄️ 大小：{len(content)} 字节",
                    "need_confirm": False,
                }

            # ---------- 图片 ----------
            if ext in {".png", ".jpg", ".jpeg", ".gif", ".bmp"}:
                placeholder = f"[图片占位] 文件名：{filename}，描述：{content_desc}"
                self._atomic_write(file_path, placeholder.encode("utf-8"))
                self._log(f"⚠️ 图片占位文件已生成：{file_path}")
                return {
                    "action": "chat",
                    "explanation": f"✅ 图片文件占位已生成：{filename}\n📁 位置：{target_dir}\n💡 请在实际项目中接入 Pillow、Stable‑Diffusion 等图片生成工具。",
                    "need_confirm": False,
                }

            # ---------- 视频 ----------
            if ext in {".mp4", ".avi", ".mov", ".mkv"}:
                placeholder = f"[视频占位] 文件名：{filename}，描述：{content_desc}"
                self._atomic_write(file_path, placeholder.encode("utf-8"))
                self._log(f"⚠️ 视频占位文件已生成：{file_path}")
                return {
                    "action": "chat",
                    "explanation": f"✅ 视频文件占位已生成：{filename}\n📁 位置：{target_dir}\n💡 请在实际项目中接入 ffmpeg、moviepy 等视频生成工具。",
                    "need_confirm": False,
                }

            # ---------- 音频 ----------
            if ext in {".wav", ".mp3", ".ogg", ".flac"}:
                placeholder = f"[音频占位] 文件名：{filename}，描述：{content_desc}"
                self._atomic_write(file_path, placeholder.encode("utf-8"))
                self._log(f"⚠️ 音频占位文件已生成：{file_path}")
                return {
                    "action": "chat",
                    "explanation": f"✅ 音频文件占位已生成：{filename}\n📁 位置：{target_dir}\n💡 请在实际项目中接入 pydub、ffmpeg 等音频生成工具。",
                    "need_confirm": False,
                }

            # ---------- 压缩包 ----------
            if ext in {".zip", ".rar", ".7z"}:
                import zipfile
                with zipfile.ZipFile(file_path, "w") as zf:
                    readme = f"这是 AI 自动生成的压缩包。\n文件名：{filename}\n描述：{content_desc}"
                    zf.writestr("readme.txt", readme)
                self._log(f"✅ 压缩包已生成：{file_path}")
                return {
                    "action": "chat",
                    "explanation": f"✅ 压缩包已生成：{filename}\n📁 位置：{target_dir}\n📦 包内包含 readme.txt（描述：{content_desc}）",
                    "need_confirm": False,
                }

            # ---------- 兜底：其它类型统一当作文本 ----------
            content = content_desc or f"# {filename}\n\n自动生成的文件内容。"
            self._atomic_write(file_path, content.encode("utf-8"))
            self._log(f"✅ 其它文件类型已作为文本写入：{file_path}")
            return {
                "action": "chat",
                "explanation": f"✅ 文件已生成（文本兜底）：{filename}\n📁 位置：{target_dir}\n🗄️ 大小：{len(content)} 字节",
                "need_confirm": False,
            }

        except Exception as e:
            self._log(f"❌ 文件生成异常: {e}", level="ERROR")
            return {
                "action": "chat",
                "explanation": f"❌ 文件生成失败：{e}\n请检查路径权限或文件名是否合法。",
                "need_confirm": False,
            }

    # -----------------------------------------------------------------
    # 辅助函数：原子写入（tmp → rename + fsync）
    # -----------------------------------------------------------------
    def _atomic_write(self, path: Path, data: bytes) -> None:
        """
        将 ``data`` 以原子方式写入 ``path``。
        1️⃣ 写入临时文件 ``path.tmp``；  
        2️⃣ 调用 ``fsync`` 确保磁盘落盘；  
        3️⃣ ``os.replace`` 替换为正式文件。
        """
        tmp_path = path.with_suffix(".tmp")
        with open(tmp_path, "wb") as tmp:
            tmp.write(data)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_path, path)


    # 🔑【依赖说明】：此方法被_handle_chat调用（通过_get_learning_suggestions）
    # 📌 调用核心：self.show_learning_suggestions() → 因此_show_learning_suggestions必须在此方法之前定义
    # 📌 但因_show_learning_suggestions仅在运行时调用（用于展示学习建议），定义顺序可互换
    # 📌 为严格遵循依赖图，此处置于_handle_file_generation之后
    def show_learning_suggestions(self):
        """打印系统建议学习的方向以及常用公开知识网站"""
        topics = [
            "✅ Python 基础 / 标准库（官方文档）",
            "✅ 网络爬虫与 HTTP 请求（requests、urllib）",
            "✅ 文件系统操作（os、shutil、pathlib）",
            "✅ 异步 & 多线程（asyncio、threading）",
            "✅ 常用算法与数据结构",
            "✅ AI / 大模型调用（Ollama、OpenAI API）",
            "✅ GUI 开发（tkinter、pyqt）",
            "✅ 项目结构化管理与版本控制（git）",
            "✅ 常用第三方库（pandas、numpy、beautifulsoup）"
        ]
        sites = [
            "🌐 Python 官方文档：https://docs.python.org/3/",
            "🌐 MDN Web Docs（JS/HTML/CSS）：https://developer.mozilla.org/",
            "🌐 Stack Overflow（技术问答）：https://stackoverflow.com/",
            "🌐 Wikipedia（通用知识）：https://zh.wikipedia.org/",
            "🌐 菜鸟教程（中文入门）：https://www.runoob.com/",
            "🌐 CSDN 博客（中文技术博客）：https://blog.csdn.net/",
            "🌐 GitHub（开源项目 & 文档）：https://github.com/",
            "🌐 InfoQ、掘金（技术趋势）：https://www.infoq.cn/、https://juejin.cn/"
        ]
        print("\n=== 推荐学习方向 ===")
        for t in topics:
            print(t)
        print("\n=== 常用公开知识站点 ===")
        for s in sites:
            print(s)
        print("\n💡 随时在任意时刻输入 “学习建议” 查看这些内容。")

    # 🔑【依赖说明】：此方法被_handle_chat调用（通过_get_system_status）
    # 📌 调用核心：self.get_full_system_status() → 因此_get_full_system_status必须在此方法之前定义
    # 📌 但因_get_full_system_status仅在运行时调用（用于获取系统状态），定义顺序可互换
    # 📌 为严格遵循依赖图，此处置于_show_learning_suggestions之后
    def get_full_system_status(self) -> Dict[str, Any]:
        pending = self.progress_tracker.list_pending_tasks()
        status = {
            "personality": self.personality.get_stats(),
            "knowledge": self.get_knowledge_statistics(),
            "learning": self.learning_engine.get_learning_statistics() if self.learning_engine else {},
            "plugins": self.plugin_manager.get_plugin_list() if self.plugin_manager else [],
            "backups": self.backup_manager.get_statistics() if self.backup_manager else {},
            "permanent_rules": len(self.get_permanent_rules()),
            "conversation_history": len(self.conversation_history),
            "task_history": len(self.task_history),
            "pending_tasks": len(pending),
            "current_project": self.current_project_id,
            "world_state": self.world_state.get_summary() if self.world_state else {},
            "health_check_config": {
                "interval": HEALTH_CHECK_INTERVAL_CHAPTERS,
                "silent": SILENT_HEALTH_CHECK,
                "auto_reconstruct": AUTO_RECONSTRUCT_OUTLINE_ENABLED,
                "ai_autonomy_level": AI_AUTONOMY_LEVEL,
                "blueprint_validation": PROJECT_BLUEPRINT_VALIDATION_ENABLED
            }
        }
        if self.disk_cache:
            status["disk_cache"] = self.disk_cache.get_stats()
        return status

    # 🔑【依赖说明】：此方法被_get_full_system_status调用（通过_get_knowledge_statistics）
    # 📌 调用核心：self.get_knowledge_statistics() → 因此_get_knowledge_statistics必须在此方法之前定义
    # 📌 但因_get_knowledge_statistics仅在运行时调用（用于获取知识统计），定义顺序可互换
    # 📌 为严格遵循依赖图，此处置于_get_full_system_status之后
    def get_knowledge_statistics(self) -> Dict[str, Any]:
        return self.knowledge_manager.get_statistics() if self.knowledge_manager else {"total":0,"verified":0,"avg_confidence":0}

    # 🔑【依赖说明】：此方法被_get_full_system_status调用（通过_get_permanent_rules）
    # 📌 调用核心：self.get_permanent_rules() → 因此_get_permanent_rules必须在此方法之前定义
    # 📌 但因_get_permanent_rules仅在运行时调用（用于获取永久规则），定义顺序可互换
    # 📌 为严格遵循依赖图，此处置于_get_knowledge_statistics之后
    def get_permanent_rules(self) -> List[Dict[str, Any]]:
        return self.permanent_memory.get_active_rules() if self.permanent_memory else []

    # 🔑【依赖说明】：此方法被_get_permanent_rules调用（通过_add_permanent_rule）
    # 📌 调用核心：self.add_permanent_rule() → 因此_add_permanent_rule必须在此方法之前定义
    # 📌 但因_add_permanent_rule仅在运行时调用（用于添加永久规则），定义顺序可互换
    # 📌 为严格遵循依赖图，此处置于_get_permanent_rules之后
    def add_permanent_rule(self, rule_text: str) -> Dict[str, Any]:
        return self.permanent_memory.add_rule(rule_text) if self.permanent_memory else {"success": False}

    def get_topic_list(self) -> List[str]:
        """返回已保存的所有话题名称。🌟 完美兼容 SQLite 和 JSON 后端，废除死板的目录扫描。"""
        try:
            # 🌟 致命修复：直接代理给底层 TopicManager，完美读取 SQLite 数据库！
            if hasattr(self, 'topic_manager') and self.topic_manager:
                if hasattr(self.topic_manager, 'list_topics'):
                    return self.topic_manager.list_topics()
                elif hasattr(self.topic_manager, 'get_all_topics'):
                    return self.topic_manager.get_all_topics()
            
            # 兜底：如果 topic_manager 缺失方法，再尝试扫目录
            self._log("⚠️ TopicManager 缺少 list_topics 方法，降级为扫描目录", level="WARN")
            if not os.path.isdir(TOPIC_STORAGE_DIR):
                return []
            topics = []
            for f in os.listdir(TOPIC_STORAGE_DIR):
                if f.startswith('topic') and f.endswith('.json'):
                    topics.append(f.replace('topic_', '').replace('.json', ''))
            return topics
        except Exception as e:
            self._log(f"⚠️ 获取话题列表时发生异常：{e}", level="ERROR")
            return []

    # 🔑【依赖说明】：此方法被_get_topic_list调用（通过_reset_personality）
    # 📌 调用核心：self.reset_personality() → 因此_reset_personality必须在此方法之前定义
    # 📌 但因_reset_personality仅在运行时调用（用于重置人格），定义顺序可互换
    # 📌 为严格遵循依赖图，此处置于_get_topic_list之后
    def reset_personality(self, key: str) -> Dict[str, Any]:
        return self.personality.reset_personality(key)

    # 🔑【依赖说明】：此方法被_reset_personality调用（通过_start_auto_learning）
    # 📌 调用核心：self._start_auto_learning() → 因此_start_auto_learning必须在此方法之前定义
    # 📌 但因_start_auto_learning仅在运行时调用（用于启动自动学习），定义顺序可互换
    # 📌 为严格遵循依赖图，此处置于_reset_personality之后
    def _start_auto_learning(self, explanation: str, user_prompt: str = None) -> Dict[str, Any]:
        """启动自动学习（在需要时由 ActionExecutor 调用）"""
        self._log("📚 启动自动学习...")
        return {"success": True, "message": "学习已启动"}

    # 🔑【依赖说明】：此方法被_start_auto_learning调用（通过_record_failure）
    # 📌 调用核心：self._record_failure() → 因此_record_failure必须在此方法之前定义
    # 📌 但因_record_failure仅在运行时调用（用于记录失败），定义顺序可互换
    # 📌 为严格遵循依赖图，此处置于_start_auto_learning之后
    def _record_failure(self, user_request: str, error_reason: str, solution_attempted: str) -> None:
        """
        将一次运行时错误记录到 SelfLearningEngine，用于后续自我修复。
        """
        self._log(f"⚠️ 记录失败：请求='{user_request}'，原因='{error_reason}'，尝试方案='{solution_attempted}'", level="WARN")
        if hasattr(self, "learning_engine"):
            try:
                self.learning_engine.record_failure(user_request, error_reason, solution_attempted)
            except Exception as e:
                self._log(f"❌ 向学习引擎写入失败记录时异常: {e}", level="ERROR")


    # 🔑【依赖说明】：此方法被_record_failure调用（通过_write_and_log）
    # 📌 调用核心：self._write_and_log() → 因此_write_and_log必须在此方法之前定义
    # 📌 但因_write_and_log仅在运行时调用（用于写入日志），定义顺序可互换
    # 📌 为严格遵循依赖图，此处置于_record_failure之后
    def _write_and_log(self, msg: str, *, level: str = "INFO") -> None:
        """
        兼容旧代码直接调用 ``_write_and_log`` 的场景。
        - 把 ``level`` 正规化为大写并限制在 ``INFO/WARN/ERROR/DEBUG``；
        - 首先尝试使用 ``self._log``（LogMixin）；
        - 若 LogMixin 本身因初始化异常不可用，回退为 ``print`` 并确保不抛异常。
        """
        lvl = level.upper()
        if lvl not in {"INFO", "WARN", "WARNING", "ERROR", "DEBUG"}:
            lvl = "INFO"
        try:
            self._log(msg, level=lvl)          # LogMixin 已实现
        except Exception:                      # pragma: no cover
            print(f"[{lvl}] {msg}")


    # 🔑【依赖说明】：此方法被_write_and_log调用（通过_startup）
    # 📌 调用核心：self.startup() → 因此_startup必须在此方法之前定义
    # 📌 但因_startup仅在运行时调用（用于启动系统），定义顺序可互换
    # 📌 为严格遵循依赖图，此处置于_write_and_log之后
    def startup(self, learning_time_minutes: int = 15) -> Dict[str, Any]:
        """
        启动时初始化所有子系统
        """
        # -------------------------------------------------
        # ★★★★★ 关键修复 ★★★★★
        # 记录启动时的时间戳，用于后面计算 scan_minutes
        # -------------------------------------------------
        start_time = time.time()          # <-- 新增，防止 NameError

        self._log("🚀 AI 控制器启动中...")
        self._log("🔌 扫描 AI 自创插件...")
        plugin_scan = self.plugin_loader.scan_and_load_plugins()
        self._log(f"✅ 插件加载完成：{plugin_scan.get('loaded',0)} 个成功")
        self._log("📋 加载永久规则...")
        rules = self.permanent_memory.get_active_rules()
        self._log(f"✅ 加载 {len(rules)} 条永久规则")
        self._log("🔧 优化知识库...")
        opt = self.knowledge_manager.auto_optimize_knowledge_base()
        self._log(f"✅ 知识库优化完成：{opt.get('optimizations',{})}")

        self._log("📖 执行启动学习（{learning_time_minutes}分钟）...")
        scan_res = self.startup_scanner.full_startup_scan(learning_time_minutes)
        if scan_res.get("recommendations"):
            self.startup_scanner.apply_recommendations(scan_res["recommendations"])
        self.knowledge_manager.apply_time_decay()
        self.backup_manager.cleanup_expired_backups()
        if self.disk_cache:
            self._log("🧹 清理过期磁盘缓存...")
            cleaned = self.disk_cache.cleanup_expired()
            self._log(f"✅ 清理了 {cleaned} 条过期缓存")

        # ==================== 统计信息准备 ====================
        # ---------- plugins ----------
        plugins_loaded = plugin_scan.get('loaded', 0)

        # ---------- rules ----------
        rules_loaded = len(rules)

        # ---------- 学习统计 ----------
        # 安全提取学习主题数量
        learning_triggered = scan_res.get('learning_triggered')
        if isinstance(learning_triggered, list) and learning_triggered:
            first_entry = learning_triggered[0] if isinstance(learning_triggered[0], dict) else {}
            topics = first_entry.get('topics_learned', [])
            topics_learned = len(topics) if isinstance(topics, (list, tuple)) else 0
        else:
            topics_learned = 0

        # ---------- 其它 ----------
        recommendations_cnt = len(scan_res.get('recommendations', []))

        # 使用函数开头记录的 start_time 计算运行时长（单位：分钟）
        scan_minutes = (time.time() - start_time) / 60.0

        self._log(f"✅ AIController 启动完成，耗时 {scan_minutes:.2f} 分钟", level="INFO")

        # ------------------- 返回数据 -------------------
        return {
            "plugins_loaded": plugins_loaded,
            "rules_loaded": rules_loaded,
            "scan_duration_minutes": scan_minutes,
            "recommendations": recommendations_cnt,
            "topics_learned": topics_learned,
        }


    # 🔑【依赖说明】：此方法被_startup调用（通过__getattr__）
    # 📌 调用核心：self.__getattr__() → 因此__getattr__必须在此方法之前定义
    # 📌 但因__getattr__仅在运行时调用（用于动态加载缺失方法），定义顺序可互换
    # 📌 为严格遵循依赖图，此处置于_startup之后
    def __getattr__(self, name):
        """
        动态加载缺失的实例方法（兼容插件或补丁）。
        - 只在全局作用域 ``globals()`` 中查找可调用对象（函数 / class）。
        - 若找到函数则使用 ``MethodType`` 绑定到实例并缓存到 ``self.__dict__``；
        - 若找不到或不是可调用对象，则抛 ``AttributeError``。
        """
        fn = globals().get(name)
        if fn and callable(fn):
            bound_method = fn.__get__(self, self.__class__)   # type: ignore
            setattr(self, name, bound_method)                # 缓存，后续直接访问
            self._log(f"⚠️ 动态加载缺失方法 `{name}`（已自动绑定）", level="WARN")
            return bound_method
        raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}'")


    # 🔑【依赖说明】：此方法被__getattr__调用（通过_log_patch_loaded）
    # 📌 调用核心：self._log_patch_loaded() → 因此_log_patch_loaded必须在此方法之前定义
    # 📌 但因_log_patch_loaded仅在运行时调用，定义顺序可互换
    # 📌 为严格遵循依赖图，此处置于__getattr__之后
    def _log_patch_loaded() -> None:
        """
        在系统启动时打印一条 **易于过滤** 的提示，帮助开发者快速验证
        补丁已经成功挂载。采用 ``print`` 而非 ``logging``，避免在
        ``AIController`` 尚未实例化时出现 ``AttributeError``。
        """
        # 为了让 CI / 日志系统能够统一捕获，使用统一的前缀 “[Patch]”
        print("[Patch] ✅ AIController._auto_generate_outline 已成功挂载")



#_log_patch_loaded()
try:
    from . import ai_controller_patch   # 这一步会执行补丁代码（如果有）
except Exception as e:
    # 如无补丁文件或补丁内部出现问题，记录日志但不影响主流程
    import logging
    logging.getLogger(__name__).warning(
        "AIController 补丁加载失败: %s   (已安全忽略)", e
    )
# -------------------------------------------------
# ✅ 文件结束
# -------------------------------------------------
