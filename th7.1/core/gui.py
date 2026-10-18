# core/gui.py
# ==================== GUI 模块（完整合并增强版）====================
# 功能：图形界面，支持进度显示、中断控制、修改确认、人格管理、宏录制、规则管理
# 合并说明：整合了“增强学习版”与“完整增强版”的所有功能，无精简无省略
# 新增：双层进度条、思考日志流、中断按钮、人格管理、宏控制、永久规则管理
# 修复：初始化顺序、日志缓冲、思考日志健壮性、聊天缓冲、按钮状态引用、进度条计算、
#       异常处理、弹窗更新、右键菜单状态、跨平台右键绑定、Treeview复制安全等

import os
import json
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog, simpledialog
from datetime import datetime
import requests
import pickle

# 右键菜单需要的额外 import
from tkinter import Menu

from .config import (
    MODEL_NAME,
    MACROS_DIR,
    PLUGINS_DIR,
    LOGS_DIR,
    KNOWLEDGE_BASE_DIR,
    DATA_CONFIG_DIR,
    TASK_TRACKING_DIR,
)
from .macro_recorder import MacroRecorder
from .plugin_manager import PluginManager
from .ai_controller import AIController

from .self_check_and_autofix import self_check_and_autofix  # ← 通过相对路径导入 core 包内的模块

from core.prompt_builder import get_prompt_builder


class CompleteAssistantGUI:
    """
    完整助手 GUI（完整合并增强版）
    支持：
    1. 双层进度条显示（总进度/当前章）
    2. 思考日志流（实时展示内部推理）
    3. 中断/暂停/恢复/停止控制
    4. 人格管理（编辑/训练/导入/导出/重置）
    5. 永久规则管理（添加/删除/查看）
    6. 宏录制与播放控制
    7. 文件操作与扫描
    8. 插件管理
    9. 话题持久化与恢复
    10. 项目独立日志
    """

    # -----------------------------------------------------------------
    # ① 初始化入口
    # -----------------------------------------------------------------
    def __init__(self, root, startup_result=None, project_id: str = None):
        """
        完整助手 GUI 主入口。

        为避免在 UI 组件（self.log_win / self.thinking_log）尚未创建时
        就被 AIController 的日志回调调用导致 AttributeError，当前实现顺序为：

        1️⃣ 先创建 UI（包括右侧按钮 + 思考日志）
        2️⃣ 再实例化 AIController 并把 ``self.add_log`` 作为回调
        3️⃣ 最后把缓存的日志一次性刷入 UI
        """
        self.root = root
        self.robot_name = "筱溪"
        self.robot_icon = "❤️"
        self.startup_result = startup_result
        self.project_id = project_id

        # ------------------- 基础窗口属性 -------------------
        self.root.title(f"{self.robot_icon} 筱溪 - 智能助手（完整合并增强版）")
        self.root.geometry("1400x900")
        self.root.minsize(1200, 800)
        self.root.configure(bg="#ffffff")

        # ------------------- 线程锁（必须最先创建） -------------------
        # 用于所有 UI 更新的互斥，防止定时器、日志线程的竞争
        self._ui_lock = threading.Lock()

        # ------------------- 日志缓冲（UI 未创建前） -------------------
        self._log_buffer = []          # [(msg, is_think), …]
        self._chat_buffer = []         # [(sender, msg, msg_type), …]
        self.log_win = None            # 运行日志（隐藏窗口）在 _init_log_windows 中创建
        self.thinking_log = None       # 思考日志（嵌入右侧按钮下方）

        # ------------------- 创建主 UI（左/中/右 三栏） -------------------
        self._create_ui()

        # ------------------- 为思考日志分配控件 -------------------
        # 思考日志位于右侧按钮区域的最底部，固定高度占比约 30%
        self.thinking_log = scrolledtext.ScrolledText(
            self.right_bottom_frame,
            wrap="word",
            font=("Consolas", 10),
            bg="#f5f5f5",
        )
        self.thinking_log.pack(fill=tk.BOTH, expand=True, pady=(5, 0))
        self.thinking_log.config(state=tk.DISABLED)

        # ------------------- 创建隐藏的运行日志窗口 -------------------
        self._init_log_windows()      # 只创建 log_win，思考日志已在 UI 中

        # ------------------- 启动周期更新（200 ms） -------------------
        self.root.after(200, self._periodic_update)

        # ------------------- 实例化 AIController（日志回调指向 add_log） -------------------
        self.macro_recorder = MacroRecorder(log_callback=self.add_log)
        self.plugin_manager = PluginManager(log_callback=self.add_log)
        self.ai_controller = AIController(
            log_callback=self.add_log,
            plugin_manager=self.plugin_manager,
            project_id=project_id,
        )

        # 🌟🌟🌟 致命修复：启动时强制读取上次活跃话题，并同步更新 UI 标签（清理了重复代码） 🌟🌟🌟
        last_topic = "default"
        if hasattr(self.ai_controller, 'topic_manager') and hasattr(self.ai_controller.topic_manager, 'get_current'):
            last_topic = self.ai_controller.topic_manager.get_current() or "default"
        
        if hasattr(self.ai_controller, 'current_topic'):
            self.ai_controller.current_topic = last_topic
            
        self.topic_label.config(text=last_topic)
        self._log_buffer.append((f"✅ 启动恢复：当前活跃话题 [{last_topic}]", False))

        # ------------------- 将启动期间缓存的日志一次性写入 UI -------------------
        self._flush_log_buffer()

        # ------------------- 话题恢复后加载历史 -------------------
        self.root.after(1000, self._load_chat_history)

        self.processing = False
        self.startup_complete = True

        # ------------------- 进度状态 -------------------
        self.total_progress = 0
        self.chapter_progress = 0
        self.current_chapter = 0
        self.total_chapters = 0

        # ------------------- 热键 & 关闭处理 -------------------
        self._setup_hotkeys()
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        # ------------------- 其它延迟任务 -------------------
        self.root.after(500, self._check_ollama)
        self.root.after(1000, self._update_personality_display)

        if startup_result:
            self.root.after(1500, lambda: self._show_startup_report(startup_result))

        # 初始化按钮引用（防止每次遍历 UI 树）
        self._init_button_refs()

    # -----------------------------------------------------------------
    # ✅ 按钮引用初始化
    # -----------------------------------------------------------------
    def _init_button_refs(self):
        """在 UI 构建完成后，缓存经常需要修改状态的按钮引用。"""
        # 发送按钮
        for child in self.root.winfo_children():
            if isinstance(child, tk.Frame):
                for sub in child.winfo_children():
                    if isinstance(sub, tk.Button) and sub.cget("text") == "发送":
                        self._send_btn = sub
                        break
        # 生成控制按钮（暂停/继续/停止）
        if hasattr(self, "right_top_frame"):
            for child in self.right_top_frame.winfo_children():
                if isinstance(child, tk.LabelFrame) and child.cget("text") == "🛠️ 生成控制":
                    for btn in child.winfo_children():
                        txt = btn.cget("text")
                        if "暂停" in txt:
                            self._btn_pause = btn
                        elif "继续" in txt:
                            self._btn_resume = btn
                        elif "停止" in txt:
                            self._btn_stop = btn
                    break

    # -----------------------------------------------------------------
    # ✅ 定时刷新方法（防止因缺少 update 导致 AttributeError）
    # -----------------------------------------------------------------
    def _periodic_update(self):
        """每 200 ms 执行一次，内部统一使用 UI 锁防止竞争。
        这里可加入轻量状态刷新，例如检查 AIController 任务状态并更新状态栏。"""
        with self._ui_lock:
            # 示例：若有任务进行中，可更新状态标签（这里保持原样，仅防止空函数导致的报警）
            pass
        self.root.after(200, self._periodic_update)

    # -----------------------------------------------------------------
    # ✅ 启动报告弹窗
    # -----------------------------------------------------------------
    def _show_startup_report(self, startup_result):
        """显示启动报告窗口"""
        report = tk.Toplevel(self.root)
        report.title("📊 启动报告")
        report.geometry("500x400")
        report.configure(bg="#ffffff")

        tk.Label(
            report,
            text="📊 启动报告",
            font=("Microsoft YaHei", 14, "bold"),
            bg="#ffffff",
        ).pack(pady=10)

        report_text = scrolledtext.ScrolledText(
            report, height=15, font=("Microsoft YaHei", 10), bg="#f5f5f5"
        )
        report_text.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)

        report_content = f"""✅ 启动完成！

📋 启动结果：
──────────────────────────────────────
🔌 加载插件：{startup_result.get('plugins_loaded', 0)} 个
📜 加载规则：{startup_result.get('rules_loaded', 0)} 条
⏱️  启动耗时：{startup_result.get('scan_duration_minutes', 0):.1f} 分钟
💡 发现建议：{startup_result.get('recommendations', 0)} 条
──────────────────────────────────────

🎯 系统已准备就绪！
"""
        report_text.insert(tk.END, report_content)
        report_text.config(state=tk.DISABLED)

        tk.Button(
            report,
            text="关闭",
            command=report.destroy,
            bg="#5b7cfa",
            fg="white",
        ).pack(pady=10)

    # -----------------------------------------------------------------
    # UI 创建区（左侧保留占位、右侧统一放按钮 + 思考日志）
    # -----------------------------------------------------------------
    def _create_ui(self):
        """创建 UI 界面——左、中心、右 三栏布局"""
        # ------------------- 顶部标题栏 -------------------
        top = tk.Frame(self.root, bg="#ffffff")
        top.pack(fill=tk.X, padx=15, pady=10)

        tk.Label(
            top,
            text="❤️ 筱溪",
            font=("Microsoft YaHei", 16, "bold"),
            bg="#ffffff",
        ).pack(side=tk.LEFT)
        tk.Label(
            top,
            text="智能电脑助手（完整合并增强版）",
            font=("Microsoft YaHei", 10),
            bg="#ffffff",
            fg="#666666",
        ).pack(side=tk.LEFT, padx=10)

        self.status_lbl = tk.Label(
            top,
            text="⚪ 检查中...",
            font=("Microsoft YaHei", 9),
            bg="#ffffff",
            fg="#666666",
        )
        self.status_lbl.pack(side=tk.RIGHT, padx=10)

        # ------------------- 主内容区（左右分栏） -------------------
        content_paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        content_paned.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        # ← 左侧占位（仅用于保持视觉平衡，可在未来扩展）
        left_placeholder = tk.Frame(content_paned, bg="#f5f5f7")
        content_paned.add(left_placeholder, weight=1)

        # ← 中央聊天区（保持原有功能）
        center_panel = self._create_center_panel(content_paned)
        content_paned.add(center_panel, weight=2)

        # ← 右侧统一按钮区 + 思考日志
        right_panel = tk.Frame(content_paned, bg="#f5f5f7")
        content_paned.add(right_panel, weight=1)

        # 右侧顶部用于放置全部按钮（原左侧 & 原右侧合并）
        self.right_top_frame = tk.Frame(right_panel, bg="#f5f5f7")
        self.right_top_frame.pack(fill=tk.X, side=tk.TOP, pady=5, padx=5)

        # 人格 & 学习按钮（原左侧入口）
        self._create_personality_section(self.right_top_frame)

        # 功能面板按钮（原右侧入口）
        self._create_function_section(self.right_top_frame)

        # 右侧底部用于放置思考日志（在 __init__ 中创建）
        self.right_bottom_frame = tk.Frame(right_panel, bg="#f5f5f7")
        self.right_bottom_frame.pack(fill=tk.BOTH, side=tk.BOTTOM, expand=True, pady=5, padx=5)

        # ------------------- 底部状态栏 -------------------
        bottom = tk.Frame(self.root, bg="#ffffff")
        bottom.pack(fill=tk.X, padx=15, pady=5)

        self.stats_lbl = tk.Label(
            bottom,
            text="📊 统计：加载中...",
            font=("Microsoft YaHei", 9),
            bg="#ffffff",
            fg="#666666",
        )
        self.stats_lbl.pack(side=tk.LEFT)

        # 🌟 新增：总控设置与运行日志按钮
        tk.Button(
            bottom, text="⚙️ 总控设置", command=self._open_settings_popup,
            bg="#5b7cfa", fg="white", font=("Microsoft YaHei", 9, "bold")
        ).pack(side=tk.LEFT, padx=15)
        
        tk.Button(
            bottom, text="📜 运行日志", command=self._toggle_log_window,
            bg="#f0f0f0", font=("Microsoft YaHei", 9)
        ).pack(side=tk.LEFT, padx=5)

        # 原有按钮保持不变
        tk.Button(bottom, text="🚪 退出", command=self.on_closing, bg="#ff6b6b", fg="white").pack(side=tk.RIGHT, padx=5)
        tk.Button(bottom, text="📊 系统状态", command=self.show_system_status, bg="#f0f0f0").pack(side=tk.RIGHT, padx=5)
        tk.Button(bottom, text="📂 知识库", command=lambda: self.open_folder(KNOWLEDGE_BASE_DIR), bg="#f0f0f0").pack(side=tk.RIGHT, padx=5)
        tk.Button(bottom, text="📂 日志文件", command=lambda: self.open_folder(LOGS_DIR), bg="#f0f0f0").pack(side=tk.RIGHT, padx=5)

    # -----------------------------------------------------------------
    # 右侧按钮分块 —— 人格 & 学习（原左侧入口）
    # -----------------------------------------------------------------
    def _create_personality_section(self, parent):
        """
        在右侧顶部创建 “人格 & 学习” 相关按钮，
        包括：
        * 人格 & 学习弹窗入口
        * 生成控制（暂停 / 继续 / 停止）按钮
        """
        panel = tk.Frame(parent, bg="#f5f5f7")
        panel.pack(fill=tk.X, pady=5)

        tk.Button(
            panel,
            text="⚙️ 人格 & 学习面板",
            command=self._open_personality_popup,
            bg="#5b7cfa",
            fg="white",
            font=("Microsoft YaHei", 10),
        ).pack(fill=tk.X, pady=2)

        # 生成控制按钮（放在一个 LabelFrame 里，保持层次感）
        ctrl_frame = tk.LabelFrame(
            panel,
            text="🛠️ 生成控制",
            font=("Microsoft YaHei", 10, "bold"),
            bg="#f5f5f7",
        )
        ctrl_frame.pack(fill=tk.X, pady=4)

        tk.Button(
            ctrl_frame,
            text="⏸️ 暂停",
            command=self.pause_generation,
            bg="#ff9500",
            fg="white",
        ).pack(fill=tk.X, pady=2)
        tk.Button(
            ctrl_frame,
            text="▶️ 继续",
            command=self.resume_generation,
            bg="#34c759",
            fg="white",
        ).pack(fill=tk.X, pady=2)
        tk.Button(
            ctrl_frame,
            text="⏹️ 停止",
            command=self.stop_generation,
            bg="#ff3b30",
            fg="white",
        ).pack(fill=tk.X, pady=2)

    # -----------------------------------------------------------------
    # 右侧按钮分块 —— 功能面板（原右侧入口）
    # -----------------------------------------------------------------
    def _create_function_section(self, parent):
        """
        在右侧顶部继续放置 “功能面板” 按钮
        """
        panel = tk.Frame(parent, bg="#f5f5f7")
        panel.pack(fill=tk.X, pady=5)

        tk.Button(
            panel,
            text="📁 功能面板",
            command=self._open_function_popup,
            bg="#5b7cfa",
            fg="white",
            font=("Microsoft YaHei", 10),
        ).pack(fill=tk.X, pady=2)

    # -----------------------------------------------------------------
    # 中央面板（话题、聊天、输入）保持原实现
    # -----------------------------------------------------------------
    # ==============================================================
    # ① 中央面板（聊天区）——使用 ScrolledText 支持多行、超长输入
    # ==============================================================    
    def _create_center_panel(self, parent):
        """创建中心聊天区（话题、聊天、输入）——
        这里把单行 Entry 升级为 ScrolledText（支持多行、超长消息编辑），
        并在发送后自动清空输入框。"""
        panel = tk.Frame(parent, bg="#ffffff")

        # ----- 话题选择区 -----
        topic_frame = tk.Frame(panel, bg="#ffffff")
        topic_frame.pack(fill=tk.X, pady=(0, 8))

        tk.Label(
            topic_frame,
            text="📁 话题:",
            font=("Microsoft YaHei", 9),
            bg="#ffffff",
            fg="#666666",
        ).pack(side=tk.LEFT)

        self.topic_label = tk.Label(
            topic_frame,
            text="default",
            font=("Microsoft YaHei", 9, "bold"),
            bg="#ffffff",
            fg="#5b7cfa",
        )
        self.topic_label.pack(side=tk.LEFT, padx=5)

        btn_frame = tk.Frame(topic_frame, bg="#ffffff")
        btn_frame.pack(side=tk.RIGHT)

        tk.Button(
            btn_frame,
            text="🆕 新话题",
            command=self.start_new_topic,
            bg="#f0f0f0",
            width=8,
        ).pack(side=tk.LEFT, padx=2)
        tk.Button(
            btn_frame,
            text="📂 加载",
            command=self.load_topic,
            bg="#f0f0f0",
            width=8,
        ).pack(side=tk.LEFT, padx=2)

        # ----- 对话区 -----
        chat_card = tk.LabelFrame(
            panel,
            text="💬 对话",
            font=("Microsoft YaHei", 10, "bold"),
            bg="#ffffff",
            padx=10,
            pady=10,
        )
        chat_card.pack(fill=tk.BOTH, expand=True)

        self.chat = scrolledtext.ScrolledText(
            chat_card, height=25, font=("Microsoft YaHei", 10), bg="#f5f5f5"
        )
        self.chat.pack(fill=tk.BOTH, expand=True)

        self.chat.tag_config("user", foreground="#0066cc", font=("Microsoft YaHei", 10, "bold"))
        self.chat.tag_config("ai", foreground="#cc0066")
        self.chat.tag_config(
            "system",
            foreground="#666666",
            font=("Microsoft YaHei", 9, "italic"),
        )
        self.chat.tag_config("operation", foreground="#009900", font=("Microsoft YaHei", 9))
        self.chat.tag_config("separator", foreground="#999999", font=("Microsoft YaHei", 9, "bold"))
        self.chat.tag_config("thinking", foreground="#9966cc", font=("Microsoft YaHei", 9))
        self.chat.config(state=tk.DISABLED)

        # 为对话区添加右键菜单（保持原有功能）
        self._bind_context_menu(
            self.chat,
            [
                ("复制选中", lambda: self._copy_selection(self.chat)),
                ("清空对话", lambda: self._clear_widget(self.chat)),
            ],
        )

        # ----- 输入区（已改为 ScrolledText） -----
        inp_frame = tk.Frame(panel, bg="#ffffff")
        inp_frame.pack(fill=tk.X, pady=(8, 0))

        # 使用 ScrolledText 让用户可以自由换行、编辑、粘贴超长文本
        self.input = scrolledtext.ScrolledText(
            inp_frame,
            height=2,
            wrap="word",
            font=("Microsoft YaHei", 11),
            bg="#ffffff",
            relief=tk.SUNKEN,
            bd=1,
        )
        self.input.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=4)

        # Ctrl+Enter 发送，Enter 只换行（更符合多行编辑习惯）
        self.input.bind("<Control-Return>", lambda e: self.send())
        self.input.bind("<Return>", lambda e: self.input.insert(tk.INSERT, "\n"))

        # 发送按钮保持不变
        tk.Button(
            inp_frame,
            text="发送",
            command=self.send,
            bg="#5b7cfa",
            fg="white",
            width=10,
        ).pack(side=tk.LEFT, padx=8)

        # ----- 进度条区 -----
        progress_frame = tk.Frame(panel, bg="#ffffff")
        progress_frame.pack(fill=tk.X, pady=(5, 0))

        tk.Label(
            progress_frame,
            text="总进度:",
            font=("Microsoft YaHei", 9),
            bg="#ffffff",
        ).pack(side=tk.LEFT)
        self.total_progress_bar = ttk.Progressbar(
            progress_frame, orient="horizontal", length=200, mode="determinate"
        )
        self.total_progress_bar.pack(side=tk.LEFT, padx=5)

        tk.Label(
            progress_frame,
            text="章节进度:",
            font=("Microsoft YaHei", 9),
            bg="#ffffff",
        ).pack(side=tk.LEFT, padx=(10, 0))
        self.chapter_progress_bar = ttk.Progressbar(
            progress_frame, orient="horizontal", length=200, mode="determinate"
        )
        self.chapter_progress_bar.pack(side=tk.LEFT, padx=5)

        self.progress_label = tk.Label(
            progress_frame,
            text="0/0 章",
            font=("Microsoft YaHei", 9),
            bg="#ffffff",
        )
        self.progress_label.pack(side=tk.LEFT, padx=10)

        return panel

    # -----------------------------------------------------------------
    # 人格弹窗（原左侧全部内容迁入）
    # -----------------------------------------------------------------
    def _open_personality_popup(self):
        """弹出完整的人格 / 学习面板（原左侧所有控件）"""
        popup = tk.Toplevel(self.root)
        popup.title("⚙️ 人格 & 学习面板")
        popup.geometry("400x720")
        popup.configure(bg="#f5f5f7")

        # ---- 人格卡片 ----
        personality_card = tk.LabelFrame(
            popup,
            text="🧠 人格信息",
            font=("Microsoft YaHei", 10, "bold"),
            bg="#f5f5f7",
        )
        personality_card.pack(fill=tk.X, pady=(0, 10), padx=10)

        self.personality_icon = tk.Label(
            personality_card,
            text="❤️",
            font=("Segoe UI Emoji", 36),
            bg="#f5f5f7",
        )
        self.personality_icon.pack(pady=5)

        self.personality_name = tk.Label(
            personality_card,
            text="筱溪",
            font=("Microsoft YaHei", 12, "bold"),
            bg="#f5f5f7",
        )
        self.personality_name.pack()

        self.personality_style = tk.Label(
            personality_card,
            text="风格：友好",
            font=("Microsoft YaHei", 9),
            bg="#f5f5f7",
            fg="#666666",
        )
        self.personality_style.pack(pady=3)

        # ---- 学习统计卡片 ----
        stats_card = tk.LabelFrame(
            popup,
            text="📊 学习统计",
            font=("Microsoft YaHei", 10, "bold"),
            bg="#f5f5f7",
        )
        stats_card.pack(fill=tk.X, pady=(0, 10), padx=10)

        self.stat_conversations = tk.Label(
            stats_card,
            text="💬 对话：0",
            font=("Microsoft YaHei", 9),
            bg="#f5f5f7",
            anchor=tk.W,
        )
        self.stat_conversations.pack(fill=tk.X, pady=1)

        self.stat_tasks = tk.Label(
            stats_card,
            text="✅ 任务：0",
            font=("Microsoft YaHei", 9),
            bg="#f5f5f7",
            anchor=tk.W,
        )
        self.stat_tasks.pack(fill=tk.X, pady=1)

        self.stat_success = tk.Label(
            stats_card,
            text="📈 成功率：0%",
            font=("Microsoft YaHei", 9),
            bg="#f5f5f7",
            anchor=tk.W,
        )
        self.stat_success.pack(fill=tk.X, pady=1)

        self.stat_knowledge = tk.Label(
            stats_card,
            text="📚 知识：0 条",
            font=("Microsoft YaHei", 9),
            bg="#f5f5f7",
            anchor=tk.W,
        )
        self.stat_knowledge.pack(fill=tk.X, pady=1)

        # ---- 人格管理按钮区 ----
        tk.Button(
            popup,
            text="📝 编辑人格",
            command=self.edit_personality,
            bg="#5b7cfa",
            fg="white",
        ).pack(fill=tk.X, padx=10, pady=2)
        tk.Button(
            popup,
            text="🔄 深度训练",
            command=self.run_training,
            bg="#5b7cfa",
            fg="white",
        ).pack(fill=tk.X, padx=10, pady=2)
        tk.Button(
            popup,
            text="🔑 人格管理",
            command=self.show_personality_menu,
            bg="#ff6b9d",
            fg="white",
        ).pack(fill=tk.X, padx=10, pady=2)
        tk.Button(
            popup,
            text="📊 查看规则",
            command=self.show_permanent_rules,
            bg="#f0f0f0",
        ).pack(fill=tk.X, padx=10, pady=2)

        # ---- 永久规则管理卡片（滚动列表） ----
        rule_frame = tk.LabelFrame(
            popup,
            text="🗒️ 永久规则",
            font=("Microsoft YaHei", 10, "bold"),
            bg="#f5f5f7",
        )
        rule_frame.pack(fill=tk.BOTH, expand=True, pady=10, padx=10)

        self.rule_listbox = tk.Listbox(rule_frame, height=5)
        self.rule_listbox.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        tk.Button(
            rule_frame,
            text="刷新规则",
            command=self._refresh_rules_listbox,
            bg="#f0f0f0",
        ).pack(fill=tk.X, padx=5, pady=2)

        # 为规则列表绑定右键菜单（复制、删除）
        self._bind_context_menu(
            self.rule_listbox,
            [
                ("复制选中规则", lambda: self._copy_selection(self.rule_listbox)),
                ("删除选中规则", self._delete_selected_rule),
            ],
        )

        # 添加刷新按钮，使弹窗内容能够实时更新
        tk.Button(
            popup,
            text="🔄 刷新人格信息",
            command=self._refresh_personality_popup,
            bg="#f0f0f0",
        ).pack(fill=tk.X, padx=10, pady=5)

    def _refresh_personality_popup(self):
        """更新人格弹窗中的实时数据（人格名称、风格、统计等）。"""
        try:
            stats = self.ai_controller.get_personality_stats()
            self.personality_name.config(text=stats.get("name", "筱溪"))
            # 风格可从 personality 中取得，这里简化为固定文本
            self.personality_style.config(text="风格：友好")
            self.stat_conversations.config(text=f"💬 对话：{stats.get('conversations', 0)}")
            self.stat_tasks.config(text=f"✅ 任务：{stats.get('tasks', 0)}")
            succ = stats.get("success_rate", 0)
            self.stat_success.config(text=f"📈 成功率：{succ:.1f}%")
            ks = self.ai_controller.get_knowledge_statistics()
            self.stat_knowledge.config(text=f"📚 知识：{ks.get('total', 0)} 条")
        except Exception as e:
            self.add_log(f"⚠️ 刷新人格弹窗失败：{e}")

    # -----------------------------------------------------------------
    # 功能弹窗（原右侧全部内容迁入）
    # -----------------------------------------------------------------
    def _open_function_popup(self):
        """弹出功能面板（文件、宏、快捷、插件）"""
        popup = tk.Toplevel(self.root)
        popup.title("📁 功能面板")
        popup.geometry("500x720")
        popup.configure(bg="#f5f5f7")

        notebook = ttk.Notebook(popup)
        notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # ------- 文件操作标签页 -------
        file_tab = ttk.Frame(notebook)
        notebook.add(file_tab, text="📄 文件操作")
        tk.Button(
            file_tab,
            text="📂 选择文件",
            command=self.select_file_to_read,
            bg="#f0f0f0",
        ).pack(fill=tk.X, pady=2, padx=5)
        tk.Button(
            file_tab,
            text="📁 扫描文件夹",
            command=self.scan_folder,
            bg="#f0f0f0",
        ).pack(fill=tk.X, pady=2, padx=5)

        # ------- 宏控制标签页 -------
        macro_tab = ttk.Frame(notebook)
        notebook.add(macro_tab, text="📼 宏控制")
        macro_btn_frame = tk.Frame(macro_tab, bg="#f5f5f7")
        macro_btn_frame.pack(fill=tk.X, pady=5, padx=5)

        tk.Button(
            macro_btn_frame,
            text="🔴",
            command=self.start_recording,
            bg="#ff3b30",
            fg="white",
            width=3,
        ).pack(side=tk.LEFT, padx=2)
        tk.Button(
            macro_btn_frame,
            text="▶",
            command=self.play_macro,
            bg="#34c759",
            fg="white",
            width=3,
        ).pack(side=tk.LEFT, padx=2)
        tk.Button(
            macro_btn_frame,
            text="⏹",
            command=self.stop_playback,
            bg="#ff9500",
            fg="white",
            width=3,
        ).pack(side=tk.LEFT, padx=2)

        self.macro_list = tk.Listbox(
            macro_tab, height=4, font=("Consolas", 8), bg="#ffffff"
        )
        self.macro_list.pack(fill=tk.X, pady=3, padx=5)
        tk.Button(
            macro_tab,
            text="🔄 刷新宏列表",
            command=self.refresh_macros,
            bg="#f0f0f0",
        ).pack(fill=tk.X, pady=2, padx=5)

        # 为宏列表绑定右键菜单（复制、删除）
        self._bind_context_menu(
            self.macro_list,
            [
                ("复制选中宏", lambda: self._copy_selection(self.macro_list)),
                ("删除选中宏", self._delete_selected_macro),
            ],
        )

        # ------- 快捷操作标签页 -------
        quick_tab = ttk.Frame(notebook)
        notebook.add(quick_tab, text="⚡ 快捷操作")
        tk.Button(
            quick_tab,
            text="📝 记事本",
            command=lambda: self.quick_action("open_app", "notepad"),
            bg="#f0f0f0",
        ).pack(fill=tk.X, pady=2, padx=5)
        tk.Button(
            quick_tab,
            text="🧮 计算器",
            command=lambda: self.quick_action("open_app", "calc"),
            bg="#f0f0f0",
        ).pack(fill=tk.X, pady=2, padx=5)
        tk.Button(
            quick_tab,
            text="🌐 浏览器",
            command=lambda: self.quick_action("open_app", "chrome"),
            bg="#f0f0f0",
        ).pack(fill=tk.X, pady=2, padx=5)

        # ------- 插件列表标签页 -------
        plugin_tab = ttk.Frame(notebook)
        notebook.add(plugin_tab, text="🔌 插件列表")
        self.plugin_tree = ttk.Treeview(
            plugin_tab,
            columns=("name", "version"),
            show="headings",
            height=6,
        )
        self.plugin_tree.heading("name", text="插件")
        self.plugin_tree.heading("version", text="版本")
        self.plugin_tree.column("name", width=150)
        self.plugin_tree.column("version", width=80)
        self.plugin_tree.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        tk.Button(
            plugin_tab,
            text="🔄 刷新插件",
            command=self.refresh_plugins,
            bg="#f0f0f0",
        ).pack(fill=tk.X, pady=5, padx=5)

        # 为插件树绑定右键菜单（复制插件信息）
        self._bind_context_menu(
            self.plugin_tree,
            [("复制插件信息", self._copy_selected_plugin)],
        )

        # 初始加载
        self.refresh_plugins()
        self.refresh_macros()
        self._load_rule_listbox()

    # -----------------------------------------------------------------
    # 右键菜单通用绑定（支持 Text、Listbox、Treeview）
    # -----------------------------------------------------------------
    def _bind_context_menu(self, widget, actions):
        """
        为 ``widget`` 绑定统一的右键菜单（支持跨平台）。

        参数
        ----------
        widget : tkinter widget
            目标控件（Text、Listbox、Treeview 等）。
        actions : list[tuple[str, Callable]]
            每项为 (“菜单标题”, 回调函数）。
        """
        menu = Menu(widget, tearoff=0)
        for label, command in actions:
            menu.add_command(label=label, command=command)

        def _show(event):
            # 跨平台右键：Windows <Button-3>, X11 <Button-2>, macOS <Control-Button-1>
            if event.num == 3 or (event.num == 2 and event.state & 0x0004) or (event.num == 1 and event.state & 0x0004):
                try:
                    menu.tk_popup(event.x_root, event.y_root)
                finally:
                    menu.grab_release()

        widget.bind("<Button>", _show)

    def _copy_selection(self, widget):
        """复制当前选中的文本/列表项"""
        try:
            if isinstance(widget, (tk.Text, scrolledtext.ScrolledText)):
                sel = widget.get(tk.SEL_FIRST, tk.SEL_LAST)
            elif isinstance(widget, tk.Listbox):
                sel = "\n".join([widget.get(i) for i in widget.curselection()])
            elif isinstance(widget, ttk.Treeview):
                rows = []
                for iid in widget.selection():
                    values = widget.item(iid, "values")
                    if isinstance(values, (list, tuple)) and len(values) >= 2:
                        rows.append("\t".join(values[:2]))
                    else:
                        # 退而求其次，只取第一列
                        rows.append(str(values[0]) if values else "")
                sel = "\n".join(rows)
            else:
                sel = ""
            self.root.clipboard_clear()
            self.root.clipboard_append(sel)
        except Exception:
            pass

    def _clear_widget(self, widget):
        """清空 Text、Listbox、Treeview 内容"""
        if isinstance(widget, (tk.Text, scrolledtext.ScrolledText)):
            widget.config(state=tk.NORMAL)
            widget.delete("1.0", tk.END)
            widget.config(state=tk.DISABLED)
        elif isinstance(widget, tk.Listbox):  # ✅ 修复：ListBox 改为 Listbox (小写 b)
            widget.delete(0, tk.END)
        elif isinstance(widget, ttk.Treeview):
            for i in widget.get_children():
                widget.delete(i)


    # -----------------------------------------------------------------
    # 永久规则列表相关（保持原实现）
    # -----------------------------------------------------------------
    def _load_rule_listbox(self):
        """把当前规则加载到弹窗中的 Listbox"""
        self.rule_listbox.delete(0, tk.END)
        self._refresh_rules_listbox()

    def _refresh_rules_listbox(self):
        """刷新左侧弹窗中的规则列表"""
        self.rule_listbox.delete(0, tk.END)
        rules = self.ai_controller.get_permanent_rules()
        for rule in rules:
            self.rule_listbox.insert(
                tk.END, f"ID: {rule['id'][:8]}... | {rule['rule']}"
            )

    def _delete_selected_rule(self):
        """右键菜单中删除选中规则"""
        sel = self.rule_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        if idx < 0 or idx >= self.rule_listbox.size():
            return
        rule_text = self.rule_listbox.get(idx)
        try:
            rule_id = self.ai_controller.get_permanent_rules()[idx]["id"]
        except Exception:
            return
        if messagebox.askyesno("确认删除", f"确定删除规则？\n{rule_text}"):
            self.ai_controller.permanent_memory.delete_rule(rule_id)
            self._refresh_rules_listbox()
            self.add_log(f"✅ 已删除永久规则 {rule_id[:8]}")

    # -----------------------------------------------------------------
    # 宏列表右键删除
    # -----------------------------------------------------------------
    def _delete_selected_macro(self):
        """右键菜单中删除选中的宏文件"""
        sel = self.macro_list.curselection()
        if not sel:
            return
        idx = sel[0]
        if idx < 0 or idx >= self.macro_list.size():
            return
        macro_name = self.macro_list.get(idx)
        if messagebox.askyesno("删除宏", f"确定删除宏 `{macro_name}` 吗？"):
            self.macro_recorder.delete_macro(macro_name)
            self.refresh_macros()
            self.add_log(f"✅ 已删除宏 {macro_name}")

    # -----------------------------------------------------------------
    # 插件信息右键复制
    # -----------------------------------------------------------------
    def _copy_selected_plugin(self):
        """复制当前选中插件的 name / version"""
        sel = self.plugin_tree.selection()
        if not sel:
            return
        values = self.plugin_tree.item(sel[0], "values")
        if not isinstance(values, (list, tuple)) or len(values) < 2:
            messagebox.showwarning("提示", "所选插件信息不完整，无法复制")
            return
        txt = f"{values[0]}=={values[1]}"
        self.root.clipboard_clear()
        self.root.clipboard_append(txt)
        self.add_log(f"✅ 已复制插件信息: {txt}")

    # -----------------------------------------------------------------
    # ✅ add_log – 精简并确保线程安全写入 UI
    # -----------------------------------------------------------------
    def add_log(self, msg):
        """向右侧日志窗（self.log_win）写入一条记录。"""
        is_think = any(k in msg for k in ("REASONING", "思考", "🧠"))

        if self.log_win is None or self.thinking_log is None:
            self._log_buffer.append((msg, is_think))
            return

        def update():
            with self._ui_lock:
                ts = datetime.now().strftime("%H:%M:%S")
                
                # ----------- 主日志（log_win） ---------- 
                if self.log_win is not None:
                    try:
                        # 🌟 关键防护：检查窗口是否已被用户手动关闭
                        if self.log_win.winfo_exists():
                            self.log_win.config(state=tk.NORMAL)
                            self.log_win.insert(tk.END, f"[{ts}] {msg}\n")
                            self.log_win.see(tk.END)
                            self.log_win.config(state=tk.DISABLED)
                    except tk.TclError:
                        pass  # 窗口已销毁，静默忽略，防止后台线程崩溃

                # ----------- 思考日志（thinking_log） ---------- 
                if is_think and self.thinking_log is not None:
                    try:
                        if self.thinking_log.winfo_exists():
                            json_part = msg.split(":", 1)[-1].strip()
                            try:
                                data = json.loads(json_part)
                                pretty = json.dumps(data, ensure_ascii=False, indent=2)
                                entry = f"[{ts}] 思考详情:\n{pretty}\n"
                            except Exception:
                                entry = f"[{ts}] {msg}\n"
                                
                            self.thinking_log.config(state=tk.NORMAL)
                            self.thinking_log.insert(tk.END, entry)
                            self.thinking_log.see(tk.END)
                            self.thinking_log.config(state=tk.DISABLED)
                    except tk.TclError:
                        pass

        self.root.after(0, update)


    def _flush_log_buffer(self):
        """一次性把启动期间的缓存日志写入 UI（log_win / thinking_log）"""
        if not self._log_buffer:
            return
        for msg, is_think in self._log_buffer:
            ts = datetime.now().strftime("%H:%M:%S")
            self.log_win.config(state=tk.NORMAL)
            self.log_win.insert(tk.END, f"[{ts}] {msg}\n")
            self.log_win.see(tk.END)
            self.log_win.config(state=tk.DISABLED)

            if is_think:
                try:
                    json_part = msg.split(":", 1)[-1].strip()
                    data = json.loads(json_part)
                    pretty = json.dumps(data, ensure_ascii=False, indent=2)
                    log_entry = f"[{ts}] 思考详情:\n{pretty}\n"
                except Exception:
                    log_entry = f"[{ts}] {msg}\n"
                self.thinking_log.config(state=tk.NORMAL)
                self.thinking_log.insert(tk.END, log_entry)
                self.thinking_log.see(tk.END)
                self.thinking_log.config(state=tk.DISABLED)

        self._log_buffer.clear()

    # -----------------------------------------------------------------
    # ✅ add_chat – 统一日志与 UI 更新（安全检查）
    # -----------------------------------------------------------------
    def add_chat(self, sender, msg, msg_type="normal"):
        """向聊天区写入一条信息。🌟 移除冗余线程锁，增强 Tag 容错。"""
        if not hasattr(self, 'chat') or self.chat is None:
            if not hasattr(self, '_chat_buffer'):
                self._chat_buffer = []
            self._chat_buffer.append((sender, msg, msg_type))
            return

        def update():
            # 🌟 致命修复：Tkinter 的 after 已经保证了在主线程执行，移除 self._ui_lock 防止死锁或 AttributeError
            try:
                self.chat.config(state=tk.NORMAL)
                icon = "👤" if sender == "user" else "🤖" if sender in ["assistant", "ai"] else "⚙️"
                
                # 🌟 容错：确保 msg_type 是已配置的合法 tag，否则降级
                valid_tags = ["user", "ai", "system", "operation", "separator", "thinking", "normal"]
                safe_tag = msg_type if msg_type in valid_tags else "normal"
                
                self.chat.insert(tk.END, f"\n{icon} {msg}\n", safe_tag)
                self.chat.see(tk.END)
                self.chat.config(state=tk.DISABLED)
                
                if hasattr(self, '_update_personality_display'):
                    self._update_personality_display()
            except Exception as e:
                print(f"[GUI] add_chat 渲染异常: {e}")

        self.root.after(0, update)


    def _flush_chat_buffer(self):
        """一次性把启动期间的聊天缓存写入聊天区"""
        if not self._chat_buffer:
            return
        for sender, msg, msg_type in self._chat_buffer:
            self.add_chat(sender, msg, msg_type)
        self._chat_buffer.clear()

    # -----------------------------------------------------------------
    # ✅ 获取当前热键激活状态（仅作展示，不影响功能）
    # -----------------------------------------------------------------
    def _get_current_hotkey_state(self):
        """
        返回一个简短文字，指示热键是否已经在系统中注册。
        """
        if getattr(self, "keyboard", None):
            return "已使用 keyboard（全局热键）"
        else:
            return "使用 Tkinter 本地热键（窗口获得焦点时有效）"

    def update_progress(self, current_chapter: int, total_chapters: int, chapter_progress: float = 0):
        """更新进度条（如有）"""
        def update():
            with self._ui_lock:
                self.current_chapter = current_chapter
                self.total_chapters = total_chapters
                self.chapter_progress = chapter_progress

                if total_chapters > 0:
                    total_pct = (current_chapter / total_chapters) * 100
                    # 限制在 0~100 范围内
                    total_pct = max(0.0, min(100.0, total_pct))
                    self.total_progress_bar["value"] = total_pct
                else:
                    # 无总章节时显示为 0
                    self.total_progress_bar["value"] = 0

                # 章节进度同样限制在 0~100
                chap_pct = max(0.0, min(100.0, chapter_progress))
                self.chapter_progress_bar["value"] = chap_pct
                self.progress_label.config(text=f"{current_chapter}/{total_chapters} 章")

        self.root.after(0, update)


    # ==============================================================
    # ⑦ 防弹 AI 回复提取（保持原实现，仅供内部调用）
    # ==============================================================    
    def _extract_ai_reply(self, result):
        """🌟 防弹提取 AI 回复，彻底消灭 ‘未返回可显示内容’ 的兜底报错"""
        if not result:
            return "⚠️ AI 响应为空。"

        # 1. 字符串直接返回
        if isinstance(result, str):
            return result.strip() if result.strip() else "⚠️ AI 返回了空文本。"

        # 2. 字典优先检查常见字段
        if isinstance(result, dict):
            keys_to_check = [
                "explanation", "content", "reply", "message", "text",
                "response", "answer", "output", "summary", "msg"
            ]
            for key in keys_to_check:
                val = result.get(key)
                if val and isinstance(val, str) and val.strip():
                    return val.strip()

            # 3. 若是 chat/action 错误，取 error 信息
            if result.get("action") == "chat" or result.get("action") == "error":
                err = result.get("error") or result.get("err_msg")
                if err and isinstance(err, str):
                    return f"⚠️ {err}"

            # 4. 兜底：取最长的字符串字段
            str_vals = [v for v in result.values() if isinstance(v, str) and len(v.strip()) > 2]
            if str_vals:
                return max(str_vals, key=len).strip()

            return f"⚠️ AI 返回了数据，但未包含可读文本 (Keys: {list(result.keys())})"
        return f"⚠️ 未知的响应类型: {type(result)}"
    
    def _get_input_text(self) -> str:
        """🌟 防弹读取输入框：自动兼容 tk.Entry (单行) 和 tk.Text/ScrolledText (多行)"""
        try:
            # 尝试 Text 组件的读取方式
            return self.input.get("1.0", tk.END).strip()
        except tk.TclError:
            # 降级为 Entry 组件的读取方式
            return self.input.get().strip()

    def _clear_input_text(self):
        """🌟 防弹清空输入框：自动兼容 tk.Entry 和 tk.Text"""
        try:
            self.input.delete("1.0", tk.END)
        except tk.TclError:
            self.input.delete(0, tk.END)

    # ==============================================================
    # ② 发送函数 – 支持多行输入、自动清空、UI 锁定/恢复
    # ==============================================================    
    def send(self):
        """发送用户消息（后台线程）——
        - 读取 ScrolledText（多行）内容；
        - 发送前禁用输入框与「发送」按钮，防止重复点击；
        - 处理完成后恢复 UI 并自动清空输入框；
        - 对返回的 AI 结果使用防弹提取器 `_extract_ai_reply`（不再依赖固定 key）。"""
        if self.processing:
            return

        # ---- 读取并清理输入框 ----
        raw_text = self._get_input_text()
        if not raw_text:
            return

        # ---- UI 锁定 ----
        self.input.config(state=tk.DISABLED)          # 禁止编辑
        self._set_send_button_state(tk.DISABLED)      # 禁止二次点击
        self.processing = True

        # 显示用户自己的对话
        self.add_chat("user", raw_text, "user")
        # 清空输入框（立即清空，防止用户误删）
        self._clear_input_text()

        def process():
            error_msg = None
            response_text = None
            operation_details = None
            has_operation_result = False  # 记录是否返回 operation_result

            try:
                resp = self.ai_controller.ask_ai(raw_text)

                # 使用防弹提取器统一获取可展示的文字
                response_text = self._extract_ai_reply(resp)

                # 若返回了 operation_result，则抽取详情
                if isinstance(resp, dict) and resp.get("operation_result"):
                    has_operation_result = True
                    op = resp["operation_result"]
                    details = op.get("details", {}) if isinstance(op, dict) else {}
                    if details:
                        operation_details = "\n【操作详情】\n"
                        for k, v in details.items():
                            operation_details += f"  • {k}: {v}\n"

            except Exception as err:
                error_msg = str(err)
                import traceback
                self.add_log(f"❌ 核心处理错误：{err}\n{traceback.format_exc()}")
            finally:
                # 必须回到主线程恢复 UI
                self.root.after(
                    0,
                    lambda: self._finalize_response(
                        error_msg,
                        response_text,
                        operation_details,
                        has_operation_result,
                    ),
                )

        threading.Thread(target=process, daemon=True).start()


    # ==============================================================
    # ⑥ 发送按钮状态统一（保持原实现）
    # ==============================================================    
    def _set_send_button_state(self, state):
        """在 UI 中启用/禁用「发送」按钮（发送前禁用，完成后恢复）。"""
        if hasattr(self, "_send_btn"):
            self._send_btn.config(state=state)


    # -----------------------------------------------------------------
    # ✅ _finalize_response – 响应完毕后恢复 UI
    # -----------------------------------------------------------------
    def _finalize_response(self, error_msg, response_text, operation_details, has_operation_result=False):
        """渲染 AI 的返回结果并恢复 UI 状态"""
        # ---------- 恢复 UI ----------
        self.input.config(state=tk.NORMAL)
        self._set_send_button_state(tk.NORMAL)
        self.processing = False

        try:
            self._clear_input_text()
        except Exception:
            pass

        if error_msg:
            self.add_chat("ai", f"❌ 发生严重错误：{error_msg}", "system")
        else:
            if response_text:
                self.add_chat("ai", response_text, "ai")
            else:
                # AI 返回成功但无文本，给出友好提示
                self.add_chat("ai", "✅ AI 已完成请求，但未返回可显示的内容。", "ai")
            if operation_details:
                self.add_chat("ai", operation_details, "operation")
            elif has_operation_result:  # ✅ 修复：使用传入的布尔值，替代未定义的 resp
                # 有 operation_result 但 details 为空
                self.add_chat("ai", "✅ 操作已完成，无详细信息。", "operation")


    # -----------------------------------------------------------------
    # 人格 / 训练 / 导入 / 导出 / 重置
    # -----------------------------------------------------------------
    def _update_personality_display(self):
        """实时刷新左侧面板的人格统计数据（带节流以避免频繁查询）"""
        # 简单节流：若距离上次更新不到 300ms，则直接返回
        now = datetime.now().timestamp()
        if hasattr(self, "_last_personality_update"):
            if now - self._last_personality_update < 0.3:
                return
        self._last_personality_update = now

        def update():
            with self._ui_lock:
                try:
                    stats = self.ai_controller.get_personality_stats()
                    if hasattr(self, "personality_name"):
                        self.personality_name.config(text=stats.get("name", "筱溪"))
                    if hasattr(self, "stat_conversations"):
                        self.stat_conversations.config(
                            text=f"💬 对话：{stats.get('conversations', 0)}"
                        )
                    if hasattr(self, "stat_tasks"):
                        self.stat_tasks.config(
                            text=f"✅ 任务：{stats.get('tasks', 0)}"
                        )
                    if hasattr(self, "stat_success"):
                        self.stat_success.config(
                            text=f"📈 成功率：{stats.get('success_rate', 0):.1f}%"
                        )
                    if hasattr(self, "stats_lbl"):
                        self.stats_lbl.config(
                            text=f"📊 {stats.get('name', '筱溪')} | 对话：{stats.get('conversations', 0)} | 任务：{stats.get('tasks', 0)}"
                        )
                    if hasattr(self, "stat_knowledge"):
                        ks = self.ai_controller.get_knowledge_statistics()
                        self.stat_knowledge.config(
                            text=f"📚 知识：{ks.get('total', 0)} 条"
                        )
                except Exception as e:
                    print(f"更新人格显示失败：{e}")

        self.root.after(0, update)

    # ==============================================================
    # ③ 设置当前话题并持久化（防止下次启动丢失）
    # ==============================================================    
    def _set_active_topic(self, topic_name: str, is_new_topic: bool = False) -> None:
        """⚠️ 核心重构：区分“新建”与“切换”，强制清空内存历史，防止跨话题污染。"""
        try:
            tm = getattr(self.ai_controller, "topic_manager", None)
            
            # ----- 1️⃣ 操作 TopicManager（新建 vs 切换） -----
            if tm:
                if is_new_topic and hasattr(tm, "create_new_topic"):
                    tm.create_new_topic(topic_name) 
                
                if hasattr(tm, "switch_topic"):
                    tm.switch_topic(topic_name)
                elif hasattr(tm, "set_current_topic"):
                    tm.set_current_topic(topic_name)
                elif hasattr(tm, "set_current"):
                    tm.set_current(topic_name)

            # ----- 2️⃣ 同步到 AIController 内部属性 -----
            if hasattr(self.ai_controller, "current_topic"):
                self.ai_controller.current_topic = topic_name

            # 🌟🌟🌟 致命修复：切换话题后，必须清空内存中的旧历史，防止新话题被旧记录污染！ 🌟🌟🌟
            if hasattr(self.ai_controller, "conversation_history"):
                self.ai_controller.conversation_history = [] 
                
            # 如果不是新话题，需要从数据库加载该话题的历史到内存
            if not is_new_topic and tm and hasattr(tm, "load"):
                try:
                    loaded_history = tm.load(topic_name) or []
                    self.ai_controller.conversation_history = loaded_history
                    self.add_log(f"📥 已从数据库加载话题 [{topic_name}] 的 {len(loaded_history)} 条历史到内存。")
                except Exception as e:
                    self.add_log(f"⚠️ 加载话题 [{topic_name}] 历史到内存失败: {e}")

            # ----- 3️⃣ 持久化到磁盘 -----
            if hasattr(self.ai_controller, "_save_last_active_topic"):
                self.ai_controller._save_last_active_topic()
            elif tm and hasattr(tm, "set_current"):
                tm.set_current(topic_name)

            # ----- 4️⃣ 更新 UI 文字 -----
            self.topic_label.config(text=topic_name)
            self.add_log(f"✅ 已{'创建并' if is_new_topic else ''}切换至话题 [{topic_name}]")
        except Exception as e:
            self.add_log(f"⚠️ 切换话题 [{topic_name}] 失败: {e}")

    # ==============================================================
    # ④ 创建新话题 – 自动保存、持久化并刷新 UI
    # ==============================================================    
    def start_new_topic(self):
        """⚠️ 核心重构：新建话题前强制保存旧对话，防止数据丢失。"""
        try:
            # ----- 1️⃣ 🌟 致命修复：先保存当前对话历史！ -----
            if hasattr(self.ai_controller, "_save_conversation_history"):
                self.ai_controller._save_conversation_history()

            topic_name = f"topic_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

            # ----- 2️⃣ 设置并激活新话题（传入 is_new_topic=True） -----
            self._set_active_topic(topic_name, is_new_topic=True)

            # ----- 3️⃣ 清空聊天窗口 -----
            self.chat.config(state=tk.NORMAL)
            self.chat.delete(1.0, tk.END)
            self.chat.config(state=tk.DISABLED)

            self.add_chat("system", f"🆕 已开启新话题：{topic_name}", "system")
            self.add_log(f"🆕 新话题：{topic_name}")
        except Exception as e:
            messagebox.showerror("错误", f"新建话题失败: {e}")
            self.add_log(f"❌ 新建话题失败: {e}")

    # ==============================================================
    # ⑤ 加载已有话题 – 从数据库/JSON 读取、切换并持久化
    # ==============================================================    
    def load_topic(self):
        """⚠️ ★★★★★ 关键修改 ★★★★★  
        1️⃣ 先保存当前对话历史；  
        2️⃣ 读取全部已保存的话题列表（`AIController.get_topic_list()`），若为空弹提示；  
        3️⃣ 弹出选择对话框，用户选中后 **切换话题并持久化**（调用 `_set_active_topic`）；  
        4️⃣ 重新加载该话题的聊天记录并刷新 UI。"""
        try:
            # ----- 1️⃣ 保存当前对话（防止数据丢失） -----
            if hasattr(self.ai_controller, "_save_conversation_history"):
                self.ai_controller._save_conversation_history()

            # ----- 2️⃣ 获取话题列表 -----
            topics = []
            if hasattr(self.ai_controller, "get_topic_list"):
                topics = self.ai_controller.get_topic_list()
            elif hasattr(self.ai_controller, "topic_manager") and hasattr(
                self.ai_controller.topic_manager, "get_all_topics"
            ):
                topics = self.ai_controller.topic_manager.get_all_topics()

            if not topics:
                messagebox.showinfo("提示", "暂无已保存的话题")
                return

            # ----- 3️⃣ 弹出选择对话框 -----
            dialog = tk.Toplevel(self.root)
            dialog.title("选择历史话题")
            dialog.geometry("300x400")
            dialog.configure(bg="#ffffff")

            tk.Label(
                dialog,
                text="请选择要加载的话题",
                font=("Microsoft YaHei", 10),
                bg="#ffffff",
            ).pack(pady=10)

            listbox = tk.Listbox(dialog, height=15, font=("Microsoft YaHei", 10))
            listbox.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)

            for topic in topics:
                # 统一使用字符串展示（topic 可能是对象或字典）
                name = topic if isinstance(topic, str) else topic.get("name", str(topic))
                listbox.insert(tk.END, name)

            def load_selected():
                sel = listbox.curselection()
                if not sel:
                    return
                selected_topic = listbox.get(sel[0])

                try:
                    # ----- 4️⃣ 切换为新话题并持久化 -----
                    self._set_active_topic(selected_topic)

                    # ----- 5️⃣ 清空当前聊天 UI 并重新加载历史 -----
                    self.chat.config(state=tk.NORMAL)
                    self.chat.delete(1.0, tk.END)
                    self.chat.config(state=tk.DISABLED)

                    # 触发 AIController 重新加载对应历史（内部已把 current_topic 设好）
                    self._load_chat_history()
                    self._flush_chat_buffer()
                    dialog.destroy()
                except Exception as e:
                    messagebox.showerror("错误", f"加载话题失败: {e}")

            tk.Button(
                dialog,
                text="确认加载",
                command=load_selected,
                bg="#5b7cfa",
                fg="white",
            ).pack(pady=10)
        except Exception as e:
            messagebox.showerror("错误", f"打开话题列表失败: {e}")
            self.add_log(f"❌ 打开话题列表异常: {e}")

    def _load_chat_history(self):
        """把历史对话渲染到中心聊天框，精确调用获取方法"""
        try:
            history = []
            # 精确调用获取历史
            if hasattr(self.ai_controller, 'get_conversation_history'):
                history = self.ai_controller.get_conversation_history()
            elif hasattr(self.ai_controller, 'topic_manager') and hasattr(self.ai_controller.topic_manager, 'get_current_history'):
                history = self.ai_controller.topic_manager.get_current_history()
                
            if history:
                self.add_chat("system", f"📚 已加载 {len(history)} 条历史对话记录", "system")
                for msg in history:
                    if isinstance(msg, dict):
                        role = msg.get("role", "assistant")
                        content = msg.get("content", "")
                    else:
                        role = "assistant"
                        content = str(msg)
                        
                    tag = "user" if role == "user" else "ai"
                    self.add_chat(role, content, tag)
            else:
                cur = "default"
                if hasattr(self.ai_controller, 'current_topic'):
                    cur = self.ai_controller.current_topic
                    
                self.add_chat("system", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "separator")
                self.add_chat("system", f"📂 已恢复上次话题：[{cur}]", "separator")
                self.add_chat("system", f"🕒 恢复时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}", "separator")
                self.add_chat("system", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "separator")
        except Exception as e:
            self.add_chat("system", f"⚠️ 加载历史记录异常: {e}", "system")

    def edit_personality(self):
        """弹出简易对话框编辑人物名称"""
        dialog = tk.Toplevel(self.root)
        dialog.title("✏️ 编辑人格设定")
        dialog.geometry("400x300")
        dialog.configure(bg="#ffffff")

        tk.Label(
            dialog,
            text="人格细节设置",
            font=("Microsoft YaHei", 12, "bold"),
            bg="#ffffff",
        ).pack(pady=15)

        tk.Label(dialog, text="名字:", bg="#ffffff").pack(anchor=tk.W, padx=30, pady=5)
        name_entry = tk.Entry(dialog, width=30, font=("Microsoft YaHei", 10))
        name_entry.pack(padx=30, pady=5)
        name_entry.insert(
            0,
            self.ai_controller.personality.personality.get("base", {}).get("name", "筱溪"),
        )

        def save():
            new_name = name_entry.get().strip()
            if new_name:
                self.ai_controller.personality.update_preference("name", new_name)
                self._update_personality_display()
                dialog.destroy()
                messagebox.showinfo("成功", "人格设定已保存！")

        tk.Button(
            dialog,
            text="💾 保存修改",
            command=save,
            bg="#5b7cfa",
            fg="white",
            width=15,
        ).pack(pady=20)

    def run_training(self):
        """触发深度反思学习"""
        self.processing = True
        self.add_log("🧠 启动深度反思训练...")

        def task():
            result = self.ai_controller.personality.train_from_topics()
            self.root.after(0, lambda: self._finish_training(result))

        threading.Thread(target=task, daemon=True).start()

    def _finish_training(self, result):
        self.processing = False
        self._update_personality_display()
        if result.get("success"):
            messagebox.showinfo(
                "训练完成",
                f"反思完成，成功从历史话题中提取了 {result.get('learned_count', 0)} 条新记忆。",
            )
        else:
            messagebox.showerror("训练失败", "深度训练过程中出现异常。")

    def show_personality_menu(self):
        """人格高级管理（导入 / 导出 / 重置）"""
        dialog = tk.Toplevel(self.root)
        dialog.title("🔑 人格高级管理")
        dialog.geometry("350x250")
        dialog.configure(bg="#ffffff")

        tk.Label(
            dialog,
            text="人格管理中心",
            font=("Microsoft YaHei", 12, "bold"),
            bg="#ffffff",
        ).pack(pady=15)

        tk.Button(
            dialog,
            text="📤 导出人格 JSON",
            command=self.export_personality,
            bg="#f0f0f0",
        ).pack(fill=tk.X, padx=40, pady=5)
        tk.Button(
            dialog,
            text="📥 导入人格 JSON",
            command=self.import_personality,
            bg="#f0f0f0",
        ).pack(fill=tk.X, padx=40, pady=5)
        tk.Button(
            dialog,
            text="🔄 重置为人格初始状态",
            command=self.reset_personality,
            bg="#ff6b6b",
            fg="white",
        ).pack(fill=tk.X, padx=40, pady=5)
        tk.Button(dialog, text="关闭", command=dialog.destroy, bg="#f0f0f0").pack(pady=20)

    def export_personality(self):
        filepath = filedialog.asksaveasfilename(
            defaultextension=".json", filetypes=[("JSON 文件", "*.json")]
        )
        if filepath:
            result = self.ai_controller.personality.export_personality(filepath)
            messagebox.showinfo("导出结果", result.get("message"))

    def import_personality(self):
        filepath = filedialog.askopenfilename(filetypes=[("JSON 文件", "*.json")])
        if filepath:
            result = self.ai_controller.personality.import_personality(filepath)
            self._update_personality_display()
            messagebox.showinfo("导入结果", result.get("message"))

    def reset_personality(self):
        key = simpledialog.askstring("安全验证", "请输入重置密钥：", show="*")
        if key:
            result = self.ai_controller.reset_personality(key)
            self._update_personality_display()
            messagebox.showinfo("重置结果", result.get("message"))

    # -----------------------------------------------------------------
    # 窗口关闭处理（保存所有状态）
    # -----------------------------------------------------------------
    def on_closing(self):
        if messagebox.askokcancel("退出", "确定退出筱溪智能助手？"):
            self.add_log("📝 正在保存数据...")
            try:
                self.ai_controller._save_conversation_history()
                self.add_log("✅ 对话历史已保存")

                # 🌟🌟🌟 致命修复：关闭前强制将当前活跃话题写入 current_topic.txt 🌟🌟🌟
                current_topic = getattr(self.ai_controller, 'current_topic', 'default') or 'default'
                if hasattr(self.ai_controller, 'topic_manager') and hasattr(self.ai_controller.topic_manager, 'set_current'):
                    self.ai_controller.topic_manager.set_current(current_topic)
                    self.add_log(f"✅ 当前活跃话题 [{current_topic}] 已持久化")

                if hasattr(self.ai_controller, '_save_learning_history'):
                    self.ai_controller._save_learning_history()
                    self.add_log("✅ 学习历史已保存")

                if hasattr(self.ai_controller, 'personality') and hasattr(self.ai_controller.personality, 'save_personality'):
                    self.ai_controller.personality.save_personality()
                    self.add_log("✅ 人格数据已保存")

                if hasattr(self.ai_controller, 'task_tracker') and hasattr(self.ai_controller.task_tracker, '_init_files'):
                    self.ai_controller.task_tracker._init_files()
                    self.add_log("✅ 任务进度已保存")

                if hasattr(self.ai_controller, 'interruption_handler') and hasattr(self.ai_controller.interruption_handler, 'stop_listening'):
                    self.ai_controller.interruption_handler.stop_listening()
                    self.add_log("✅ 中断监听已停止")
            except Exception as e:
                self.add_log(f"⚠️ 保存数据时出错：{e}")
            finally:
                self.add_log("👋 再见！")
                self.root.destroy()

    # -----------------------------------------------------------------
    # 永久规则管理弹窗
    # -----------------------------------------------------------------
    def show_permanent_rules(self):
        """永久规则管理（增删改）"""
        dialog = tk.Toplevel(self.root)
        dialog.title("📜 永久规则管理器")
        dialog.geometry("600x500")
        dialog.configure(bg="#ffffff")

        tk.Label(
            dialog,
            text="永久规则管理 (最高优先级指令)",
            font=("Microsoft YaHei", 12, "bold"),
            bg="#ffffff",
        ).pack(pady=10)

        listbox = tk.Listbox(dialog, height=20, font=("Microsoft YaHei", 10))
        listbox.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)

        rules = self.ai_controller.get_permanent_rules()
        for rule in rules:
            listbox.insert(tk.END, f"ID: {rule['id'][:8]}... | {rule['rule']}")

        btn_frame = tk.Frame(dialog, bg="#ffffff")
        btn_frame.pack(fill=tk.X, padx=20, pady=10)

        def add_rule():
            rule_text = simpledialog.askstring(
                "添加规则", "请输入 AI 必须遵守的规则："
            )
            if rule_text:
                self.ai_controller.add_permanent_rule(rule_text)
                self._refresh_rules_listbox()
                messagebox.showinfo("成功", "规则已添加并立即生效")

        def delete_rule():
            sel = listbox.curselection()
            if sel:
                idx = sel[0]
                full_id = rules[idx]["id"]
                if messagebox.askyesno("确认", "确定删除此项永久规则吗？"):
                    self.ai_controller.permanent_memory.delete_rule(full_id)
                    self._refresh_rules_listbox()

        tk.Button(
            btn_frame,
            text="➕ 添加新规则",
            command=add_rule,
            bg="#5b7cfa",
            fg="white",
        ).pack(side=tk.LEFT, padx=5)
        tk.Button(
            btn_frame,
            text="🗑️ 删除选中规则",
            command=delete_rule,
            bg="#ff6b6b",
            fg="white",
        ).pack(side=tk.LEFT, padx=5)
        tk.Button(
            btn_frame,
            text="关闭",
            command=dialog.destroy,
            bg="#f0f0f0",
        ).pack(side=tk.RIGHT, padx=5)

    def _refresh_rules_listbox(self):
        """刷新右侧弹窗中的规则列表（用于永久规则窗口刷新）"""
        try:
            self.rule_listbox.delete(0, tk.END)
            rules = self.ai_controller.get_permanent_rules()
            for rule in rules:
                self.rule_listbox.insert(
                    tk.END, f"ID: {rule['id'][:8]}... | {rule['rule']}"
                )
        except Exception:
            pass

    # -----------------------------------------------------------------
    # ⚙️ 核心总控设置弹窗（全中文、动态扫描、兼容未来新增）
    # -----------------------------------------------------------------
    def _open_settings_popup(self):
        """
        打开全局总控设置二级界面。
        🌟 核心特性：自动扫描 config.py，中文映射显示，动态渲染 UI，即时生效。
        """
        popup = tk.Toplevel(self.root)
        popup.title("⚙️ 全局总控设置 (动态同步)")
        popup.geometry("800x850")
        popup.configure(bg="#ffffff")

        from . import config  # 动态导入以读取和修改全局变量

        tk.Label(
            popup, text="⚙️ 全局总控设置 (自动同步 config.py)",
            font=("Microsoft YaHei", 14, "bold"), bg="#ffffff"
        ).pack(pady=10)
        
        tk.Label(
            popup, text="💡 提示：修改后即时生效。新增配置项会自动出现在对应分类中。",
            font=("Microsoft YaHei", 9), bg="#ffffff", fg="#666666"
        ).pack(pady=(0, 10))

        # ================= 中英文映射字典 =================
        # 未来新增开关时，只需在这里加一行翻译即可；如果不加，界面会自动显示英文原名。
        ZH_MAP = {
            # 核心开关
            "ENABLE_OUTLINE_GENERATION": "启用大纲生成 (关闭则使用占位大纲)",
            "ENABLE_HIERARCHICAL_OUTLINE": "启用动态分卷大纲 (长篇必备)",
            "ENABLE_DEEP_LORE_GENERATION": "启用 DeepLore 深度设定生成 (关闭可加速)",
            "AUTO_OUTLINE_AFTER_CREATION": "项目创建后自动生成大纲",
            "QUALITY_CHECK_ENABLED": "启用章节质量检查 (低分自动重写)",
            "ENABLE_RAG": "启用 RAG 向量记忆检索",
            "ENABLE_SELF_LEARNING_LLM": "启用 LLM 自我学习与反思",
            "ENABLE_PROMPT_DUMP": "启用 Prompt 落盘记录",
            "ENABLE_CHAPTER_SPLIT": "启用章节超时分割",
            "ENABLE_MEMORY_CLEANUP": "启用内存自动清理",
            "DISK_CACHE_ENABLED": "启用磁盘缓存",
            "CACHE_COMPRESSION_ENABLED": "启用缓存压缩",
            "AUTO_LEARNING_ENABLED": "启用自动学习",
            "AUTO_EXECUTE_ENABLED": "启用自动执行",
            "LEARNING_CHECK_ENABLED": "启用学习检查",
            "USE_WEB_SEARCH_FOR_LEARNING": "学习时启用网络搜索",
            "SAFE_MODE": "安全模式",
            "CONSISTENCY_CHECK_ENABLED": "启用一致性检查",
            "STYLE_DRIFT_CHECK_ENABLED": "启用风格漂移检查",
            "AUTO_BACKUP_ENABLED": "启用自动备份",
            "ENABLE_CHECKPOINT": "启用断点续传",
            "ATOMIC_OPERATION_ENABLED": "启用原子操作",
            "MULTI_WORLD_ENABLED": "启用多世界支持",
            "VECTOR_DB_ENABLED": "启用向量数据库",
            "AUTO_CLEANUP_ENABLED": "启用自动清理",
            "CHARACTER_GROWTH_ENABLED": "启用角色成长追踪",
            "FORESHADOWING_AUTO_TRACK": "启用伏笔自动追踪",
            "TRUTH_REVEAL_RHYTHM_ENABLED": "启用真相揭露节奏控制",
            "UNEXPECTED_DEMAND_ENABLED": "启用意外需求处理",
            "FILE_ORGANIZATION_ENABLED": "启用文件组织",
            "PROJECT_ISOLATION_ENABLED": "启用项目隔离",
            "CHAPTER_CARD_ENABLED": "启用章节卡片",
            "CONTEXT_VECTOR_SEARCH_ENABLED": "启用上下文向量搜索",
            "PROFESSIONAL_LOG_ENABLED": "启用专业日志",
            "INTERRUPTION_ENABLED": "启用中断与恢复",
            "SAFE_SHUTDOWN_ENABLED": "启用安全关闭",
            "AUTO_ADAPT_USER_EDITS": "自动适应用户编辑",
            "EDIT_CONFLICT_DETECTION": "启用编辑冲突检测",
            "SILENT_HEALTH_CHECK": "静默健康检查",
            "AUTO_RECONSTRUCT_OUTLINE_ENABLED": "启用大纲自动重构",
            "HEALTH_CHECK_STRICT_MODE": "健康检查严格模式",
            "STRICT_JSON": "强制严格 JSON 输出",
            "USE_SYNTHETIC_FALLBACK_OUTLINE": "使用合成回退大纲",
            "LOG_DEDUPLICATION": "启用日志去重",
            
            # 阈值与数值
            "VOLUME_SPLIT_THRESHOLD": "分卷章节阈值 (超过此数自动分卷)",
            "CHAPTERS_PER_VOLUME": "每卷默认章节数",
            "MAX_BACKUP_VERSIONS": "最大备份版本数",
            "JSON_EXTRACT_MAX_RETRIES": "JSON 提取最大重试次数",
            "JSON_EXTRACT_BACKOFF": "JSON 提取退避时间 (秒)",
            "MAX_OUTPUT_TOKENS": "最大输出 Token 数",
            "MAX_CONTEXT_TOKENS": "最大上下文 Token 数",
            "TEMPERATURE": "默认生成温度 (0.0-1.0)",
            "REQUEST_TIMEOUT": "全局请求超时 (秒)",
            "SAFE_TIMEOUT": "安全超时时间 (秒)",
            "CHAPTER_TIMEOUT": "章节生成超时 (秒)",
            "INTENT_CONFIDENCE_THRESHOLD": "意图识别置信度阈值",
            "MAX_JSON_TOKENS": "JSON 交互 Token 上限",
            "OUTLINE_MAX_WORKERS": "大纲生成最大并发线程",
            "OUTLINE_MAX_RETRIES_PER_SUBTASK": "大纲子任务最大重试",
            "MEMORY_LIMIT_MB": "内存限制 (MB)",
            "MAX_CACHE_SIZE_MB": "最大缓存大小 (MB)",
            "MAX_LEARNING_ITERATIONS": "最大学习迭代次数",
            "MIN_WORD_COUNT_PER_CHAPTER": "每章最小字数",
            "MAX_WORD_COUNT_PER_CHAPTER": "每章最大字数",
            "DEFAULT_WORD_COUNT_PER_CHAPTER": "每章默认字数",
            "MAX_RETRY_COUNT": "质量检查最大重试次数",
            "RECONCILIATION_INTERVAL": "状态对账间隔 (章)",
            "BACKUP_INTERVAL_CHAPTERS": "自动备份间隔 (章)",
            "CHECKPOINT_INTERVAL_SECONDS": "检查点保存间隔 (秒)",
            "GROWTH_CHECK_INTERVAL_CHAPTERS": "角色成长检查间隔 (章)",
            "WORLD_TRUTH_LEVELS": "世界观真相层级数",
            "CONTEXT_SLIDING_WINDOW_SIZE": "上下文滑动窗口大小 (章)",
            "MAX_CHAPTER_GENERATION_RETRIES": "章节生成最大重试次数",
            "HEALTH_CHECK_INTERVAL_CHAPTERS": "健康检查间隔 (章)",
            "QUALITY_CHECK_TEMPERATURE": "质量检查专用温度",
        }

        # ================= 创建带滚动条的 Canvas =================
        canvas_frame = tk.Frame(popup, bg="#ffffff")
        canvas_frame.pack(fill=tk.BOTH, expand=True, padx=15, pady=5)
        
        canvas = tk.Canvas(canvas_frame, bg="#ffffff", highlightthickness=0)
        scrollbar = ttk.Scrollbar(canvas_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = tk.Frame(canvas, bg="#ffffff")
        
        scrollable_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        # ================= 变量扫描与过滤 =================
        blacklist = {
            'BASE_DIR', 'DEFAULT_SAVE_DIR', 'KNOWLEDGE_BASE_DIR', 'NOVEL_PROJECTS_DIR',
            '_config_lock', '_db_connections', '__path__', 'VERSION', 'OLLAMA_URL',
            'MODEL_NAME', 'LOGS_DIR', 'MACROS_DIR', 'PLUGINS_DIR', 'DATA_DIR',
            'STATE_DIR', 'CHARACTER_FILE', 'WORLD_FILE', 'CONFLICT_FILE', 'PROMPT_TEMPLATES_DIR'
        }
        
        config_vars = {}
        for name in dir(config):
            if name.isupper() and name not in blacklist and not name.startswith('_'):
                val = getattr(config, name)
                if isinstance(val, (bool, int, float, str)):
                    config_vars[name] = val

        # ================= 自动分类逻辑 =================
        categories = {
            "🚀 核心功能开关 (Enable/Auto/Use/Strict)": {},
            "📏 阈值、限制与超时 (Max/Min/Timeout/Limit/Interval)": {},
            "📝 大纲与生成专属 (Outline/Chapter/Context/Quality)": {},
            "⚙️ 其他核心参数": {}
        }
        
        for name, val in config_vars.items():
            if name.startswith(('ENABLE_', 'AUTO_', 'USE_', 'STRICT_', 'SAFE_', 'REQUIRE_', 'SILENT_')):
                categories["🚀 核心功能开关 (Enable/Auto/Use/Strict)"][name] = val
            elif name.startswith(('MAX_', 'MIN_', 'LIMIT_', 'INTERVAL_', 'TIMEOUT_', 'THRESHOLD_', 'BACKUP_', 'CHECKPOINT_')):
                categories["📏 阈值、限制与超时 (Max/Min/Timeout/Limit/Interval)"][name] = val
            elif name.startswith(('OUTLINE_', 'CHAPTER_', 'CONTEXT_', 'QUALITY_', 'GROWTH_', 'WORLD_', 'FORESHADOWING_')):
                categories["📝 大纲与生成专属 (Outline/Chapter/Context/Quality)"][name] = val
            else:
                categories["⚙️ 其他核心参数"][name] = val

        # ================= 动态渲染 UI =================
        widgets_ref = {}
        
        for cat_name, cat_vars in categories.items():
            if not cat_vars: continue
                
            cat_frame = tk.LabelFrame(
                scrollable_frame, text=cat_name, 
                font=("Microsoft YaHei", 10, "bold"), 
                bg="#f9f9f9", padx=10, pady=10
            )
            cat_frame.pack(fill=tk.X, padx=5, pady=8)
            
            row_idx = 0
            for var_name, var_val in cat_vars.items():
                zh_name = ZH_MAP.get(var_name)
                
                # 列 0: 中文名称 (或英文回退)
                display_name = zh_name if zh_name else var_name.replace('_', ' ').title()
                lbl_zh = tk.Label(
                    cat_frame, text=display_name, 
                    bg="#f9f9f9", font=("Microsoft YaHei", 10), anchor="w"
                )
                lbl_zh.grid(row=row_idx, column=0, sticky="w", pady=3, padx=(0, 5))
                
                # 列 1: 英文变量名 (灰色小字，仅当有中文名时显示，方便对照)
                ctrl_col = 1
                if zh_name:
                    lbl_en = tk.Label(
                        cat_frame, text=f"({var_name})", 
                        bg="#f9f9f9", font=("Consolas", 8), fg="gray", anchor="w"
                    )
                    lbl_en.grid(row=row_idx, column=1, sticky="w", pady=3, padx=(0, 10))
                    ctrl_col = 2
                
                # 列 2: 控件
                if isinstance(var_val, bool):
                    var = tk.BooleanVar(value=var_val)
                    ctrl = ttk.Checkbutton(cat_frame, variable=var)
                    ctrl.grid(row=row_idx, column=ctrl_col, sticky="w")
                    widgets_ref[var_name] = (var, bool)
                elif isinstance(var_val, int):
                    var = tk.StringVar(value=str(var_val))
                    ctrl = ttk.Entry(cat_frame, textvariable=var, width=15)
                    ctrl.grid(row=row_idx, column=ctrl_col, sticky="w")
                    widgets_ref[var_name] = (var, int)
                elif isinstance(var_val, float):
                    var = tk.StringVar(value=str(var_val))
                    ctrl = ttk.Entry(cat_frame, textvariable=var, width=15)
                    ctrl.grid(row=row_idx, column=ctrl_col, sticky="w")
                    widgets_ref[var_name] = (var, float)
                elif isinstance(var_val, str):
                    var = tk.StringVar(value=var_val)
                    ctrl = ttk.Entry(cat_frame, textvariable=var, width=30)
                    ctrl.grid(row=row_idx, column=ctrl_col, sticky="w")
                    widgets_ref[var_name] = (var, str)
                    
                row_idx += 1

        # ================= 保存与类型校验逻辑 =================
        def save_all():
            errors = []
            for var_name, (var, var_type) in widgets_ref.items():
                try:
                    raw_val = var.get()
                    if var_type == bool: setattr(config, var_name, bool(raw_val))
                    elif var_type == int: setattr(config, var_name, int(raw_val))
                    elif var_type == float: setattr(config, var_name, float(raw_val))
                    elif var_type == str: setattr(config, var_name, str(raw_val))
                except ValueError:
                    errors.append(f"{var_name} (必须是 {var_type.__name__})")
                    
            if errors:
                messagebox.showerror("保存失败", "以下参数格式错误，请检查:\n" + "\n".join(errors))
            else:
                self.add_log("✅ 全局总控设置已动态更新并即时生效！")
                messagebox.showinfo("成功", "所有设置已保存并即时生效！\n(部分底层路径或模型参数可能需要重启)")
                on_closing()

        # ================= 底部按钮与清理 =================
        btn_frame = tk.Frame(popup, bg="#ffffff")
        btn_frame.pack(fill=tk.X, pady=15)
        
        tk.Button(
            btn_frame, text="💾 保存并应用所有修改", command=save_all,
            bg="#5b7cfa", fg="white", font=("Microsoft YaHei", 11, "bold"), width=20
        ).pack()
        
        def on_closing():
            canvas.unbind_all("<MouseWheel>")
            popup.destroy()
            
        popup.protocol("WM_DELETE_WINDOW", on_closing)

    # -----------------------------------------------------------------
    # 📜 运行日志窗口控制（防关闭/重建）
    # -----------------------------------------------------------------
    def _toggle_log_window(self):
        """切换/重新打开运行日志窗口"""
        if hasattr(self, 'log_win') and self.log_win is not None:
            try:
                # 如果窗口还存在，直接恢复显示并置顶
                if self.log_win.winfo_exists():
                    self.log_win.deiconify()
                    self.log_win.lift()
                    self.log_win.focus_force()
                    return
            except tk.TclError:
                pass  # 窗口已被销毁，继续往下执行重建逻辑
        
        # 如果不存在或已销毁，重新初始化
        self._init_log_windows()
        self.add_log("📜 运行日志窗口已重新打开")

    # -----------------------------------------------------------------
    # 系统状态报告弹窗
    # -----------------------------------------------------------------
    def show_system_status(self):
        """弹出完整的系统运行状态报告"""
        dialog = tk.Toplevel(self.root)
        dialog.title("📊 系统完整状态报告")
        dialog.geometry("700x600")
        dialog.configure(bg="#ffffff")

        tk.Label(
            dialog,
            text="📊 筱溪智能助手 - 系统状态",
            font=("Microsoft YaHei", 14, "bold"),
            bg="#ffffff",
        ).pack(pady=10)

        status_text = scrolledtext.ScrolledText(
            dialog, height=30, font=("Consolas", 10), bg="#f5f5f5"
        )
        status_text.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)

        status = self.ai_controller.get_full_system_status()

        status_content = f"""
╔════════════════════════════════════════════════════════════════════════════════╗
║                          📊 筱溪智能助手 - 系统状态报告                             ║
╠════════════════════════════════════════════════════════════════════════════════╣

 [🧠 人格设定]
 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  名字：{status['personality'].get('name', '筱溪')}
  对话次数：{status['personality'].get('conversations', 0)}
  任务次数：{status['personality'].get('tasks', 0)}
  成功率：{status['personality'].get('success_rate', 0):.1f}%

 [📚 知识库]
 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  总知识条数：{status['knowledge'].get('total', 0)}
  已验证知识：{status['knowledge'].get('verified', 0)}
  平均置信度：{status['knowledge'].get('avg_confidence', 0):.2f}

 [📖 自主学习]
 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  学习会话总数：{status['learning'].get('total_learning_sessions', 0)}
  综合成功率：{status['learning'].get('success_rate', 0) * 100:.1f}%
  平均学习耗时：{status['learning'].get('avg_time_minutes', 0):.1f} 分钟

 [🔌 插件系统]
 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  加载插件数：{status['plugins'].get('total_plugins', 0)}
  活跃插件数：{status['plugins'].get('active_plugins', 0)}
  历史错误数：{status['plugins'].get('total_errors', 0)}
  自动修复数：{status['plugins'].get('auto_fixed', 0)}

 [💾 备份系统]
 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  总备份文件：{status['backups'].get('total_backups', 0)}
  占用存储：{status['backups'].get('total_size_mb', 0):.2f} MB

 [📜 核心规则]
 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  活跃永久规则：{status['permanent_rules']} 条
  对话历史记录：{status['conversation_history']} 条
  任务执行记录：{status['task_history']} 条

 [📈 生成进度]
 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  当前项目：{status.get('current_project', '无')}
  待处理任务：{status.get('pending_tasks', 0)} 个

╚═════════════════════════════════════════════════════════════════════════════════╝

 [🔑 热键状态]
 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  {self._get_current_hotkey_state()}
"""
        status_text.insert(tk.END, status_content)
        status_text.config(state=tk.DISABLED)

        tk.Button(
            dialog,
            text="关闭",
            command=dialog.destroy,
            bg="#5b7cfa",
            fg="white",
        ).pack(pady=10)

    # -----------------------------------------------------------------
    # 打开系统文件夹
    # -----------------------------------------------------------------
    def open_folder(self, path):
        """在资源管理器中打开指定文件夹"""
        try:
            os.startfile(path)
        except Exception as e:
            messagebox.showerror("错误", f"无法打开文件夹 {path}：{e}")

    # -----------------------------------------------------------------
    # 文件读取 & 文件夹扫描入口
    # -----------------------------------------------------------------
    def select_file_to_read(self):
        """选择本地文件并发送读取请求"""
        filepath = filedialog.askopenfilename(title="选择要读取的文件")
        if filepath:
            self.input.delete(0, tk.END)
            self.input.insert(0, f"读取文件：{filepath}")
            self.send()

    def scan_folder(self):
        """选择文件夹并发送扫描请求"""
        folderpath = filedialog.askdirectory(title="选择要扫描的文件夹")
        if folderpath:
            self.input.delete(0, tk.END)
            self.input.insert(0, f"扫描文件夹：{folderpath}")
            self.send()

    # -----------------------------------------------------------------
    # 宏录制 / 播放 / 停止
    # -----------------------------------------------------------------
    def start_recording(self):
        """开始录制宏（F6）"""
        if self.macro_recorder.start_recording():
            self.add_log("🔴 宏录制已启动 (F6)")
        else:
            self.add_log("⚠️ 录制失败：可能已在录制或播放中")

    def play_macro(self):
        """播放选中的宏（F7）"""
        sel = self.macro_list.curselection()
        if sel:
            macro_name = self.macro_list.get(sel[0])
            if self.macro_recorder.load_macro(macro_name):
                self.add_log(f"▶️ 正在播放宏：{macro_name}")
                self.macro_recorder.play_macro()
        else:
            self.add_log("⚠️ 请先在列表中选择一个宏")

    def stop_playback(self):
        """停止宏播放（Esc）"""
        self.macro_recorder.stop_playback()
        self.add_log("⏹ 宏播放已停止 (Esc)")

    def refresh_macros(self):
        """刷新宏列表"""
        self.macro_list.delete(0, tk.END)
        for m in self.macro_recorder.list_macros():
            self.macro_list.insert(tk.END, m)

    # -----------------------------------------------------------------
    # 快捷操作（记事本、计算器、浏览器）
    # -----------------------------------------------------------------
    def quick_action(self, action, target):
        """执行快捷启动操作"""
        if self.processing:
            return
        self.add_log(f"快捷启动：{target}")
        try:
            if action == "open_app":
                if target == "chrome":
                    import webbrowser

                    webbrowser.open("https://www.google.com")
                else:
                    os.system(f'start "" "{target}"')
        except Exception as e:
            self.add_log(f"快捷启动失败：{e}")

    # -----------------------------------------------------------------
    # ✅ 插件列表刷新（兼容多种 PluginManager 实现）
    # -----------------------------------------------------------------
    def refresh_plugins(self):
        """
        将已加载的插件写入 ``self.plugin_tree``。  
        兼容旧的 ``plugins`` 属性、``loaded_plugins``、``registry``，
        以及 ``list_plugins`` / ``get_plugins`` 等获取函数。
        若插件尚未加载，则尝试调用 ``load_plugins`` 强制一次加载。
        出现异常或没有插件时只写日志，不会导致 GUI 崩溃。
        """
        # ① 先清空已有的树形视图
        self.plugin_tree.delete(*self.plugin_tree.get_children())

        # ② 试图获得一个 “name → plugin” 的字典
        plugins_dict = None

        # ---- a) 直接属性名 ----
        if hasattr(self.plugin_manager, "plugins"):
            plugins_dict = getattr(self.plugin_manager, "plugins")
        elif hasattr(self.plugin_manager, "loaded_plugins"):
            plugins_dict = getattr(self.plugin_manager, "loaded_plugins")
        elif hasattr(self.plugin_manager, "registry"):
            plugins_dict = getattr(self.plugin_manager, "registry")

        # ---- b) 通过函数获取 ----
        if plugins_dict is None:
            for meth_name in ("list_plugins", "get_plugins"):
                if hasattr(self.plugin_manager, meth_name):
                    try:
                        result = getattr(self.plugin_manager, meth_name)()
                       # 统一转成 dict
                        if isinstance(result, dict):
                            plugins_dict = result
                        elif isinstance(result, (list, tuple)):
                            # 列表形式时假设每个对象都有 ``name`` 属性
                            plugins_dict = {p.name: p for p in result}
                    except Exception as exc:
                        self.add_log(f"⚠️ PluginManager.{meth_name}() 失败: {exc}")
                    break

        # ---- c) 若仍为空，尝试手动加载一次 ----
        if plugins_dict is None and hasattr(self.plugin_manager, "load_plugins"):
            try:
                self.plugin_manager.load_plugins()
                # 再次尝试最常见的属性
                plugins_dict = getattr(self.plugin_manager, "plugins", None) or \
                               getattr(self.plugin_manager, "loaded_plugins", None)
            except Exception as exc:
                self.add_log(f"⚠️ PluginManager.load_plugins() 失败: {exc}")

        # ---- d) 没有插件 → 只记录日志不抛错 ----
        if not plugins_dict:
            self.add_log("ℹ️ 当前没有可用的插件（PluginManager 未返回插件列表）")
            return

        # ---- e) 填充 UI ----
        for name, plugin in plugins_dict.items():
            # 防御性读取 name / version，若不存在则使用回退值
            plugin_name = getattr(plugin, "name", name)
            plugin_version = getattr(plugin, "version", "?.?")
            self.plugin_tree.insert("", tk.END, values=(plugin_name, plugin_version))

    # -----------------------------------------------------------------
    # ✅ 生成控制（暂停 / 继续 / 强制停止） – 增强实现
    # -----------------------------------------------------------------
    def pause_generation(self):
        """
        暂停生成：向 InterruptionHandler 发送 “pause” 信号，
        并在 UI 上同步显示“已暂停”状态、禁用不合法的按钮。
        """
        if not self.ai_controller.interruption_handler:
            self.add_log("⚠️ 中断处理器未初始化，无法暂停")
            return

        # 发送 pause 信号
        self.ai_controller.interruption_handler.send_interrupt_signal("pause")
        self.add_log("⏸️ 已发送暂停信号")
        # 更新 UI 状态
        self._update_generation_status("paused")
        self._set_control_buttons(state="paused")

    def resume_generation(self):
        """
        恢复生成：调用 InterruptionHandler 的 `resume_generation`
        将中断标记清除，并恢复 UI 按钮为“运行中”状态。
        """
        if not self.ai_controller.interruption_handler:
            self.add_log("⚠️ 中断处理器未初始化，无法恢复")
            return

        # 发送恢复指令（内部会清除 is_interrupted 标记）
        self.ai_controller.interruption_handler.resume_generation()
        self.add_log("▶️ 已发送恢复信号")
        # 更新 UI 状态
        self._update_generation_status("running")
        self._set_control_buttons(state="running")

    def stop_generation(self):
        """
        强制停止生成：向 InterruptionHandler 发送 “stop” 信号，
        并立即清除中断状态（防止后续任务被错误阻塞）。
        同时在 UI 上展示 “已停止” 状态并恢复所有按钮为可点状态。
        """
        if not self.ai_controller.interruption_handler:
            self.add_log("⚠️ 中断处理器未初始化，无法停止")
            return

        # 发送 stop 信号
        self.ai_controller.interruption_handler.send_interrupt_signal("stop")
        # 立即清理中断状态，使后续任务不受阻塞
        self.ai_controller.interruption_handler.clear_interruption_state()
        self.add_log("⏹️ 已发送停止信号并清除中断状态")
        # 更新 UI 状态
        self._update_generation_status("stopped")
        self._set_control_buttons(state="stopped")


    # -----------------------------------------------------------------
    # 【内部】更新右下角「生成状态」标签
    # -----------------------------------------------------------------
    def _update_generation_status(self, state: str):
        """
        更新右上角的“生成状态”指示文字与颜色。

        参数
        ----------
        state : str
            - ``running`` → 正在生成
            - ``paused``  → 已暂停
            - ``stopped`` → 已停止
        """
        status_map = {
            "running": ("🚀 正在生成", "green"),
            "paused":  ("⏸ 已暂停", "orange"),
            "stopped": ("⏹ 已停止", "red"),
        }
        text, color = status_map.get(state, ("", "black"))

        # 第一次创建标签
        if not hasattr(self, "_gen_status_lbl"):
            self._gen_status_lbl = tk.Label(
                self.root,
                text=text,
                font=("Microsoft YaHei", 9),
                bg="#ffffff",
                fg=color,
            )
            # 右上角固定位置
            self._gen_status_lbl.place(relx=0.99, rely=0.02, anchor="ne")
        else:
            self._gen_status_lbl.config(text=text, fg=color)


    # -----------------------------------------------------------------
    # 【内部】根据生成状态启用/禁用 「暂停 / 继续 / 停止」 按钮
    # -----------------------------------------------------------------
    def _set_control_buttons(self, state: str):
        """
        根据当前的生成状态自动切换按钮的可点/禁用状态。

        参数
        ----------
        state : str
            - ``running`` → 只能点「暂停」和「停止」
            - ``paused``  → 只能点「继续」和「停止」
            - ``stopped`` → 所有按钮均可点（重新开始时使用）
        """
        # 确保已经缓存了三个按钮的引用
        if not hasattr(self, "_btn_pause"):
            # 第一次调用时遍历一次 UI 并缓存引用
            for child in self.right_top_frame.winfo_children():
                if isinstance(child, tk.LabelFrame) and child.cget("text") == "🛠️ 生成控制":
                    for btn in child.winfo_children():
                        txt = btn.cget("text")
                        if "暂停" in txt:
                            self._btn_pause = btn
                        elif "继续" in txt:
                            self._btn_resume = btn
                        elif "停止" in txt:
                            self._btn_stop = btn
                    break
            # 未找到按钮则直接返回（防止后续 AttributeError）
            if not hasattr(self, "_btn_pause"):
                return

        if state == "running":
            self._btn_pause.config(state=tk.NORMAL)
            self._btn_resume.config(state=tk.DISABLED)
            self._btn_stop.config(state=tk.NORMAL)
        elif state == "paused":
            self._btn_pause.config(state=tk.DISABLED)
            self._btn_resume.config(state=tk.NORMAL)
            self._btn_stop.config(state=tk.NORMAL)
        elif state == "stopped":
            # 停止后全部恢复为可点状态，用户可以再次点击「暂停」或「停止」重新触发
            self._btn_pause.config(state=tk.NORMAL)
            self._btn_resume.config(state=tk.NORMAL)
            self._btn_stop.config(state=tk.NORMAL)


    # -----------------------------------------------------------------
    # Ollama 在线检查
    # -----------------------------------------------------------------
    def _check_ollama(self):
        """检查本地 LLM 服务是否在线"""
        def check():
            try:
                resp = requests.get(
                    "http://localhost:11434/api/tags", timeout=120
                )
                if resp.status_code == 200:
                    models = resp.json().get("models", [])
                    model_names = [m.get("name", "") for m in models]
                    if MODEL_NAME in model_names:
                        self.root.after(
                            0,
                            lambda: self.status_lbl.config(
                                text=f"✅ {MODEL_NAME}", fg="green"
                            ),
                        )
                    else:
                        self.root.after(
                            0,
                            lambda: self.status_lbl.config(
                                text=f"⚠️ {MODEL_NAME} 未找到", fg="orange"
                            ),
                        )
                else:
                    self.root.after(
                        0,
                        lambda: self.status_lbl.config(
                            text="❌ 服务异常", fg="red"
                        ),
                    )
            except Exception:
                self.root.after(
                    0,
                    lambda: self.status_lbl.config(
                        text="❌ 未连接", fg="red"
                    ),
                )

        threading.Thread(target=check, daemon=True).start()

    # -----------------------------------------------------------------
    # ✅ 【全局热键】注册（优先使用 keyboard，若不可用则使用 Tkinter）
    # -----------------------------------------------------------------
    def _setup_hotkeys(self):
        """
        注册两套热键：
        1️⃣ 使用 `keyboard`（全局系统热键，需管理员权限）。
        2️⃣ 若 `keyboard` 导入失败或报错，自动回退为 Tkinter
           `root.bind_all`（仅在当前窗口拥有焦点时生效）。
        热键对应功能：
        - F6  → 开始宏录制
        - F7  → 播放选中宏
        - Esc → 停止宏播放
        - Ctrl+Shift+P → 暂停生成
        - Ctrl+Shift+R → 恢复生成
        - Ctrl+Shift+S → 停止生成
        """
        # ------------------- 1️⃣ 尝试使用 keyboard -------------------
        try:
            import keyboard  # 只在成功导入后才使用
            self.keyboard = keyboard  # 保存以便后续状态查询
            keyboard.add_hotkey("f6", lambda: self.start_recording(), suppress=False)
            keyboard.add_hotkey("f7", lambda: self.play_macro(), suppress=False)
            keyboard.add_hotkey("esc", lambda: self.stop_playback(), suppress=False)

            keyboard.add_hotkey(
                "ctrl+shift+p",
                lambda: self.pause_generation(),
                suppress=False,
            )
            keyboard.add_hotkey(
                "ctrl+shift+r",
                lambda: self.resume_generation(),
                suppress=False,
            )
            keyboard.add_hotkey(
                "ctrl+shift+s",
                lambda: self.stop_generation(),
                suppress=False,
            )
            self.add_log("✅ 全局热键（keyboard）已注册")
            return   # 成功后直接返回
        except Exception as e:
            self.add_log(f"⚠️ keyboard 热键注册失败，回退到 Tkinter 绑定：{e}")
            self.keyboard = None

        # ------------------- 2️⃣ Tkinter 本地绑定（回退） -------------------
        self.root.bind_all("<F6>", lambda e: self.start_recording())
        self.root.bind_all("<F7>", lambda e: self.play_macro())
        self.root.bind_all("<Escape>", lambda e: self.stop_playback())

        self.root.bind_all("<Control-Shift-P>", lambda e: self.pause_generation())
        self.root.bind_all("<Control-Shift-p>", lambda e: self.pause_generation())
        self.root.bind_all("<Control-Shift-R>", lambda e: self.resume_generation())
        self.root.bind_all("<Control-Shift-r>", lambda e: self.resume_generation())
        self.root.bind_all("<Control-Shift-S>", lambda e: self.stop_generation())
        self.root.bind_all("<Control-Shift-s>", lambda e: self.stop_generation())

        self.add_log("✅ Tkinter 本地热键已注册（仅在本窗口有效）")

    # -----------------------------------------------------------------
    # 隐藏的运行日志窗口（仅用于日志文件、调试）
    # -----------------------------------------------------------------
    def _init_log_windows(self):
        """
        创建后台运行日志窗口（log_win），
        思考日志已被嵌入主界面右侧的 ``self.thinking_log``，因此这里不再创建。
        """
        log_win_top = tk.Toplevel(self.root)
        log_win_top.title("运行日志")
        log_win_top.geometry("700x300")
        log_win_top.configure(bg="#f5f5f5")
        self.log_win = scrolledtext.ScrolledText(
            log_win_top, wrap="word", font=("Consolas", 10), bg="#ffffff"
        )
        self.log_win.pack(fill=tk.BOTH, expand=True)

        # 启动时最小化，避免打扰
        log_win_top.iconify()


# 入口
if __name__ == "__main__":
    print("⚠️ 请使用 main.py 启动程序，以获得完整的启动学习功能！")
    root = tk.Tk()
    app = CompleteAssistantGUI(root)
    root.mainloop()
