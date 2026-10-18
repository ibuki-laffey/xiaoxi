# core/config.py
# ==================== 配置模块（完整增强版 - 质量优先）====================
import os
import sys
import json
import threading
import sqlite3
import atexit
import time
from datetime import datetime
from typing import Dict, Any, List
from pathlib import Path

# -------------------------------------------------
# ① 统一路径管理（一次性导入 Path 实例）
# -------------------------------------------------
from .paths import (
    BASE_DIR, DEFAULT_SAVE_DIR, KNOWLEDGE_BASE_DIR, NOVEL_PROJECTS_DIR, ensure_path,
)

# ================= 🌡️ 温控与功耗限制总开关 =================
# 🌟 核心创新：全局温控节流模式，专为笔记本/散热受限设备设计
THERMAL_THROTTLE_MODE = True       # 🌟 温控总开关 (True=保护笔记本限制功耗, False=台式机性能全开)
LLM_COOLDOWN_SECONDS = 5         # 🌟 每次 LLM 请求后的强制物理冷却时间 (秒)，让硬件喘息
MAX_LLM_CONCURRENCY = 1 if THERMAL_THROTTLE_MODE else 4  # 🌟 最大并发 LLM 请求数 (温控下强制单线程)
OLLAMA_MAX_THREADS = 4 if THERMAL_THROTTLE_MODE else 0   # 🌟 限制 Ollama CPU 推理线程数 (0=不限制，使用所有核心)

# 全局信号量，用于在操作系统层面硬性限制并发，防止瞬间功耗尖峰
_LLM_CONCURRENCY_SEMAPHORE = threading.Semaphore(MAX_LLM_CONCURRENCY)

# -------------------------------------------------
# ② 线程锁 & 数据库连接
# -------------------------------------------------
_config_lock = threading.Lock()
_db_connections: Dict[str, Dict[str, Any]] = {}

# ================= 🎛️ 大纲生成模式控制开关 =================
ENABLE_OUTLINE_GENERATION = True       # 开关：是否启用 LLM 生成详细大纲（False 则使用占位大纲，进入状态驱动写作）
ENABLE_HIERARCHICAL_OUTLINE = True     # 开关：是否启用主干+分卷的动态分阶段大纲生成
VOLUME_SPLIT_THRESHOLD = 30            # 阈值：总章节数超过此值时强制启用分卷生成
CHAPTERS_PER_VOLUME = 20               # 参数：每卷包含的默认章节数
OUTLINE_DIR_NAME = "01_大纲"           # 路径：大纲文件存储目录名
ENABLE_DEEP_LORE_GENERATION = True    # 开关：是否生成 DeepLore 设定卡片（False 则跳过，依赖基础大纲）

# ------------------- 用户存档目录 (跨平台兼容) -------------------
NOVEL_WORKS_DIR = ensure_path(Path(DEFAULT_SAVE_DIR, "Novels"))   # 路径：小说成品存档目录
OUTLINES_DIR = ensure_path(Path(DEFAULT_SAVE_DIR, "Outlines"))    # 路径：大纲存档目录
RESEARCH_DIR = ensure_path(Path(DEFAULT_SAVE_DIR, "Research"))    # 路径：研究资料存档目录

MAX_BACKUP_VERSIONS = 3                # 参数：保留最近 N 版备份
BACKUP_SUFFIX_FMT = "_v{ver}.json"     # 格式：备份文件后缀格式
USE_SUMMARY_LAYER = True               # 开关：是否启用摘要层

# ---------- 话题持久化后端 ----------
TOPIC_BACKEND = "sqlite"               # 选项：话题持久化后端 ("json" | "sqlite")
TOPIC_LOAD_MODE = "full"               # 选项：话题加载模式 ("full" | "window" | "summarize")
TOPIC_WINDOW_LIMIT = 600               # 参数：滑动窗口模式下的最大消息数
TOPIC_SUMMARY_LENGTH = 500             # 参数：摘要模式下的最大字符数

# =============================================================================
# LLM JSON 可靠性相关全局配置
# =============================================================================
JSON_EXTRACT_MAX_RETRIES = 3           # 参数：JSON 提取失败时的最大重试次数
JSON_EXTRACT_BACKOFF = 0.5             # 参数：首次退避等待秒数（指数退避）

USE_TOPIC_BACKUP = True                # 开关：是否启用话题自动备份

# ------------------- Prompt 记录开关 -------------------
ENABLE_PROMPT_DUMP = os.getenv("ENABLE_PROMPT_DUMP", "1") == "1"  # 开关：是否把所有 Prompt/Response 写磁盘
# 🌟 核心修复：改为基于 BASE_DIR 的绝对路径，防止相对路径导致目录创建错乱
PROMPT_DUMP_DIR = ensure_path(Path(BASE_DIR, os.getenv("PROMPT_DUMP_DIR", "prompt_dump"))) 
PROMPT_BACKUP_INTERVAL = int(os.getenv("PROMPT_BACKUP_INTERVAL", "180"))  # 参数：Prompt 自动备份间隔（秒）

# ------------------- 项目内部目录 -------------------
MACROS_DIR = ensure_path(Path(BASE_DIR, "macros"))                # 路径：宏命令目录
PLUGINS_DIR = ensure_path(Path(BASE_DIR, "plugins"))              # 路径：插件目录
LOGS_DIR = ensure_path(Path(BASE_DIR, "logs"))                    # 路径：全局日志目录
CODE_SANDBOX_DIR = ensure_path(Path(BASE_DIR, "sandbox"))         # 路径：代码执行沙箱目录
PROFILES_DIR = ensure_path(Path(KNOWLEDGE_BASE_DIR, "profiles"))  # 路径：用户配置档案目录
AUTO_PLUGINS_DIR = ensure_path(Path(BASE_DIR, "auto_plugins"))    # 路径：自动生成的插件目录
WEB_CACHE_DIR = ensure_path(Path(BASE_DIR, "web_cache"))          # 路径：网络请求缓存目录
TASK_TRACKING_DIR = ensure_path(Path(BASE_DIR, "task_tracking"))  # 路径：任务追踪目录
LEARNING_PLANS_DIR = ensure_path(Path(BASE_DIR, "learning_plans"))# 路径：学习计划目录
VECTOR_DB_DIR = ensure_path(Path(KNOWLEDGE_BASE_DIR, "vector_db"))# 路径：向量数据库目录
DISK_CACHE_DIR = ensure_path(Path(TASK_TRACKING_DIR, "disk_cache"))# 路径：磁盘缓存目录
REASONING_LOGS_DIR = ensure_path(Path(BASE_DIR, "reasoning_logs"))# 路径：思考引擎日志目录
WORLD_STATES_DIR = ensure_path(Path(BASE_DIR, "world_states"))    # 路径：世界状态目录
CHAPTER_CARDS_DIR = ensure_path(Path(TASK_TRACKING_DIR, "chapter_cards")) # 路径：章节卡片目录

# ---------- 新增全局开关 ----------
ENABLE_RAG = os.getenv("ENABLE_RAG", "1") == "1"                  # 开关：是否启用向量记忆检索 (RAG)
STRICT_JSON = os.getenv("STRICT_JSON", "1") == "1"                # 开关：Prompt 是否强制只返回 JSON
MAX_SELF_FIX_ROUNDS = int(os.getenv("MAX_SELF_FIX_ROUNDS", "3"))  # 参数：自纠循环上限
OUTLINE_SAMPLING_NUM = int(os.getenv("OUTLINE_SAMPLING_NUM", "5"))# 参数：多温度采样候选数

# ------------------- 数据分类目录 -------------------
DATA_DIR = ensure_path(Path(BASE_DIR, "data"))                    # 路径：数据根目录
DATA_ACTIVE_DIR = ensure_path(Path(DATA_DIR, "active"))           # 路径：活跃数据目录
DATA_ARCHIVE_DIR = ensure_path(Path(DATA_DIR, "archive"))         # 路径：归档数据目录
DATA_CONFIG_DIR = ensure_path(Path(DATA_DIR, "config"))           # 路径：配置数据目录
DATA_TEMP_DIR = ensure_path(Path(DATA_DIR, "temp"))               # 路径：临时数据目录
DB_DIR = ensure_path(Path(DATA_DIR, "db"))                        # 路径：数据库文件目录

EXPERIENCE_DB_DIR = ensure_path(Path(KNOWLEDGE_BASE_DIR, "experience_db")) # 路径：经验库目录
TOPIC_STORAGE_DIR = ensure_path(Path(DATA_CONFIG_DIR, "topics"))           # 路径：话题持久化目录
CURRENT_TOPIC_META = ensure_path(Path(DATA_CONFIG_DIR, "current_topic.txt")) # 路径：当前话题元信息文件

# ------------------- API 配置 -------------------
OLLAMA_URL = "http://localhost:11434/api/chat"  # 地址：Ollama API 端点
MODEL_NAME = "gemma4:latest"                    # 模型：默认使用的 LLM 模型名称；备用模型"qwen3.5:latest" "gemma4:latest" 

# ------------------- 长文本生成配置 -------------------
MAX_OUTPUT_TOKENS = 32000      # 参数：单次生成最大 Token 数
MAX_CONTEXT_TOKENS = 32000     # 参数：最大上下文 Token 数
TEMPERATURE = 0.7              # 参数：默认生成温度
REQUEST_TIMEOUT = 6000         # 参数：长文本请求超时时间（秒）
SAFE_TIMEOUT = 3000            # 参数：安全请求超时时间（秒）
CHAPTER_TIMEOUT = 3000         # 参数：章节生成超时时间（秒）
INTENT_CONFIDENCE_THRESHOLD = 0.6  # 阈值：意图识别置信度阈值
MAX_JSON_TOKENS = 32000        # 参数：JSON 交互专用 Token 上限

# ------------------- 全局开关 -------------------
ENABLE_PLATFORM_DOWNLOAD = True   # 开关：是否启用平台下载功能
ENABLE_PLATFORM_SEARCH = False    # 开关：是否启用平台搜索功能
ENABLE_SELF_LEARNING_LLM = True   # 开关：是否启用 LLM 自我学习

# ==============================================================
# ✨ 大纲多子任务生成 / 并行 / 缓存 参数
# ==============================================================
OUTLINE_SUBTASK_SIZE = 2                       # 参数：每个子任务负责的章节数（1=每章单独调用）
OUTLINE_MAX_WORKERS = 12                       # 参数：并行工作线程数
OUTLINE_MAX_RPS = 5                            # 参数：每秒最多向 Ollama 发起的请求数（防限流）
OUTLINE_MAX_RETRIES_PER_SUBTASK = 3            # 参数：单个子任务最多重试次数
OUTLINE_TEMPERATURES = [0.2, 0.4, 0.6, 0.8, 1.0]  # 列表：子任务温度采样列表
OUTLINE_FALLBACK_MODELS = ["qwen3.5", ""]      # 列表：主模型失效时的备用模型列表
OUTLINE_CACHE_TTL = 86_800                     # 参数：本地 DiskCache 缓存有效期（秒，0=永不过期）

# ------------------- 温度控制 -------------------
DEFAULT_TEMPERATURE = 0.7      # 参数：默认温度
MAX_TEMPERATURE = 1.0          # 参数：最大温度
TEMPERATURE_STEP = 0.15        # 参数：温度调整步长
MAX_OUTLINE_GEN_RETRIES = 4    # 参数：大纲生成最大重试次数

# -------------------------------------------------
# ① 兼容占位常量
# -------------------------------------------------
__path__: str = ""             # 兼容：早期 package-style 导入的模块路径
MAX_OUTLINE_SIMILARITY: float = 0.85  # 阈值：大纲相似度判断阈值

# ------------------- 超时分割配置 -------------------
CHAPTER_GENERATION_TIMEOUT = 1800  # 参数：章节生成超时时间（秒）
ENABLE_CHAPTER_SPLIT = True        # 开关：是否启用章节自动分割
MIN_SPLIT_WORD_COUNT = 1000        # 阈值：触发分割的最小字数
MAX_SPLIT_ATTEMPTS = 3             # 参数：最大分割尝试次数
SPLIT_OVERLAP_WORDS = 200          # 参数：分割时的重叠字数

# ------------------- 内存 & 磁盘缓存 -------------------
MEMORY_LIMIT_MB = 10000             # 阈值：内存使用上限（MB）
ENABLE_MEMORY_CLEANUP = True       # 开关：是否启用内存自动清理
AUTO_SAVE_INTERVAL_SECONDS = 600   # 参数：自动保存间隔（秒）
DISK_CACHE_ENABLED = True          # 开关：是否启用磁盘缓存
MAX_CACHE_SIZE_MB = 10000          # 阈值：磁盘缓存最大容量（MB）
CACHE_COMPRESSION_ENABLED = True   # 开关：是否启用缓存压缩
CACHE_AUTO_CLEANUP_DAYS = 30       # 参数：缓存自动清理天数
CACHE_LOCK_TIMEOUT = 1800          # 参数：缓存锁超时时间（秒）
MAX_DISK_CACHE_INDEX = 1000        # 参数：磁盘缓存索引最大条目数

# ------------------- 自我学习配置 -------------------
AUTO_LEARNING_ENABLED = True       # 开关：是否启用自动学习
AUTO_EXECUTE_ENABLED = True        # 开关：是否启用自动执行
MAX_LEARNING_ITERATIONS = 5        # 参数：最大学习迭代次数
LEARNING_CHECK_ENABLED = True      # 开关：是否启用学习检查
MAX_LEARNING_MINUTES = 0           # 参数：最大学习时间（分钟，0=无限制）

# ------------------- 学习配置 -------------------
MIN_LEARNING_TIME_MINUTES = 0      # 参数：最小学习时间（分钟）
MAX_LEARNING_RESOURCES = 100       # 参数：最大学习资源数
LEARNING_VERIFICATION_ENABLED = True # 开关：是否启用学习验证
AUTO_LEARN_GOAL_COUNT = 5          # 参数：自动学习目标数量
USE_WEB_SEARCH_FOR_LEARNING = True # 开关：是否使用网络搜索辅助学习
LEARNING_HISTORY_FILE = str(DATA_CONFIG_DIR / "learning_history.json")  # 路径：学习历史文件
LEARNING_STATE_FILE = str(DATA_CONFIG_DIR / "learning_state.json")      # 路径：学习状态文件

NOVEL_PROJECTS_ROOT = NOVEL_PROJECTS_DIR   # 兼容：旧名称
OVERALL_OUTLINE_DIR = os.path.join("01_大纲", "overall")  # 路径：总体大纲目录
CHAPTER_OUTLINE_ROOT = os.path.join("01_大纲", "chapter_{num:02d}") # 路径：章节大纲目录模板

AUTO_CREATE_DIRECTORIES = True     # 开关：是否自动创建缺失目录
MIN_SEARCH_QUALITY = 0.6           # 阈值：最小搜索质量分数
SAFE_MODE = False                  # 开关：是否启用安全模式
REQUIRE_CONFIRM = False            # 开关：是否要求用户确认

# ------------------- 长篇小说专用配置 -------------------
MIN_WORD_COUNT_PER_CHAPTER = 1500  # 阈值：每章最小字数
MAX_WORD_COUNT_PER_CHAPTER = 4000  # 阈值：每章最大字数
DEFAULT_WORD_COUNT_PER_CHAPTER = 3000 # 参数：每章默认字数
MAX_OUTLINE_GENERATION_ATTEMPTS = 5   # 参数：大纲生成最大尝试次数

# ------------------- 质量检查配置 -------------------
QUALITY_CHECK_ENABLED = True       # 开关：是否启用质量检查
CONSISTENCY_CHECK_ENABLED = True   # 开关：是否启用一致性检查
STYLE_DRIFT_CHECK_ENABLED = True   # 开关：是否启用风格漂移检查
MAX_RETRY_COUNT = 5                # 参数：最大重试次数
RECONCILIATION_INTERVAL = 5        # 参数：对账间隔（章）

# ------------------- 备份设置 -------------------
AUTO_BACKUP_ENABLED = True         # 开关：是否启用自动备份
BACKUP_INTERVAL_CHAPTERS = 3       # 参数：备份间隔（章）
MAX_BACKUP_COUNT = 10              # 参数：最大备份数量

# ------------------- 断点续传 -------------------
ENABLE_CHECKPOINT = True           # 开关：是否启用断点续传
CHECKPOINT_INTERVAL_SECONDS = 60   # 参数：检查点保存间隔（秒）
ATOMIC_OPERATION_ENABLED = True    # 开关：是否启用原子操作

# ------------------- 多世界支持 -------------------
MULTI_WORLD_ENABLED = True         # 开关：是否启用多世界支持
WORLD_ISOLATION_ENABLED = True     # 开关：是否启用世界隔离

VECTOR_DB_ENABLED = True           # 开关：是否启用向量数据库

# ------------------- 清理策略 -------------------
AUTO_CLEANUP_ENABLED = True        # 开关：是否启用自动清理
CLEANUP_REQUIRES_APPROVAL = True   # 开关：清理是否需要审批

# ------------------- 角色成长追踪配置 -------------------
CHARACTER_GROWTH_ENABLED = True    # 开关：是否启用角色成长追踪
GROWTH_CHECK_INTERVAL_CHAPTERS = 10 # 参数：成长检查间隔（章）
GROWTH_DEVATION_THRESHOLD = 0.4    # 阈值：成长偏差阈值
GROWTH_TRANSITION_CHECK_ENABLED = True # 开关：是否启用成长过渡检查

# ------------------- 世界观分层揭露配置 -------------------
WORLD_TRUTH_LEVELS = 3             # 参数：世界观真相层级数
DEFAULT_UNLOCK_CHAPTER = 0         # 参数：默认解锁章节
FORESHADOWING_AUTO_TRACK = True    # 开关：是否自动追踪伏笔
TRUTH_REVEAL_RHYTHM_ENABLED = True # 开关：是否启用真相揭露节奏控制

# ------------------- 需求处理配置 -------------------
UNEXPECTED_DEMAND_ENABLED = True   # 开关：是否启用意外需求处理
DEMAND_IMPACT_THRESHOLD = 0.5      # 阈值：需求影响阈值
DEMAND_ANALYSIS_TIMEOUT = 1800     # 参数：需求分析超时时间（秒）
MODIFICATION_CONFIRMATION_REQUIRED = True # 开关：修改是否需要确认

# ------------------- 文件组织配置 -------------------
FILE_ORGANIZATION_ENABLED = True   # 开关：是否启用文件组织
PROJECT_ISOLATION_ENABLED = True   # 开关：是否启用项目隔离
MACHINE_FORMAT_PRIORITY = True     # 开关：是否优先使用机器格式
HUMAN_FORMAT_EXPORT = True         # 开关：是否导出人类格式

PROJECT_DIR_STRUCTURE = {
    "00_工程配置": ["project_config.pickle", "project_config.txt", "thinking_log.pickle", "thinking_log.txt"],
    "01_大纲": ["总体大纲.pickle", "总体大纲.txt", "章节大纲/", "简介.txt"],
}

# ------------------- 章节卡片配置 -------------------
CHAPTER_CARD_ENABLED = True        # 开关：是否启用章节卡片
CHAPTER_CARD_FORMAT = "json"       # 格式：章节卡片格式
CHAPTER_CARD_FIELDS = ["plot_summary", "state_changes", "new_entities", "foreshadowing", "emotional_tone", "key_quotes"] # 列表：卡片包含的字段

# ------------------- 上下文加载策略 -------------------
CONTEXT_SLIDING_WINDOW_SIZE = 3    # 参数：上下文滑动窗口大小（章）
CONTEXT_CARD_RANGE = 20            # 参数：上下文卡片加载范围（章）
CONTEXT_VECTOR_SEARCH_ENABLED = True # 开关：是否启用上下文向量搜索

MAX_CHAPTER_GENERATION_RETRIES = 3 # 参数：章节生成最大重写次数

# ------------------- 日志配置 -------------------
PROFESSIONAL_LOG_ENABLED = True    # 开关：是否启用专业日志（JSONL 格式）
LOG_FORMAT = "jsonl"               # 格式：日志格式
LOG_LEVELS = ["DEBUG", "INFO", "WARN", "ERROR", "REASONING"] # 列表：支持的日志级别
PROJECT_LOG_ISOLATION = True       # 开关：是否启用项目日志隔离
INTERNAL_LOG_ENABLED = False       # 开关：是否启用内部调试日志
LOG_DEDUPLICATION: bool = True     # 开关：同一条 WARN 是否只打印一次

# ------------------- 中断与恢复配置 -------------------
INTERRUPTION_ENABLED = True        # 开关：是否启用中断与恢复
INTERRUPTION_CHECK_INTERVAL = 10   # 参数：中断检查间隔（秒）
SAFE_SHUTDOWN_ENABLED = True       # 开关：是否启用安全关闭

# ------------------- 用户编辑优先配置 -------------------
USER_EDIT_PRIORITY = "highest"     # 选项：用户编辑优先级 ("highest" | "normal")
AUTO_ADAPT_USER_EDITS = True       # 开关：是否自动适应用户编辑
EDIT_CONFLICT_DETECTION = True     # 开关：是否启用编辑冲突检测

# ------------------- 健康检查配置 -------------------
HEALTH_CHECK_INTERVAL_CHAPTERS = 10 # 参数：健康检查间隔（章）
SILENT_HEALTH_CHECK = True         # 开关：是否静默执行健康检查
AUTO_RECONSTRUCT_OUTLINE_ENABLED = True # 开关：是否允许自动重构大纲
AI_AUTONOMY_LEVEL = "high"         # 选项：AI 自主性级别 ("low" | "medium" | "high")
PROJECT_BLUEPRINT_VALIDATION_ENABLED = True # 开关：是否启用项目蓝图验证
HEALTH_CHECK_STRICT_MODE = False   # 开关：健康检查是否启用严格模式（失败即阻断）
HEALTH_CHECK_AUTO_REGENERATE = True # 开关：健康检查失败时是否自动重新生成

# ------------------- 自动化行为开关 -------------------
AUTO_OUTLINE_AFTER_CREATION = True # 开关：创建项目后是否自动生成大纲
USE_SYNTHETIC_FALLBACK_OUTLINE = False # 开关：是否使用合成回退大纲
USE_ONLY_AI_INTENT = False         # 开关：是否仅使用 AI 进行意图识别
QUALITY_CHECK_TEMPERATURE = 0.45   # 参数：质量检查时的 LLM 温度
HEALTH_CHECK_TIMEOUT = 1800        # 参数：健康检查超时时间（秒）

VERSION: str = "1.0.0"             # 版本：系统版本号

# ------------------- 业务关键字常量 -------------------
CUSTOM_SETTING_INTENT_KEYWORDS = [
    "设定自定义", "自定义设定", "创建自定义", "我要自定义", "自定义项目",
    "custom setting", "custom setting project", "你自己定义",
]

# ================= 状态文件路径配置 =================
# 🌟 核心修复：直接使用顶部的 BASE_DIR，废弃末尾复杂的 globals() 获取逻辑
STATE_DIR = ensure_path(Path(BASE_DIR, "task_tracking", "states"))
CHARACTER_FILE = str(STATE_DIR / "character_state.json")  # 路径：角色状态文件
WORLD_FILE = str(STATE_DIR / "world_state.json")          # 路径：世界状态文件
CONFLICT_FILE = str(STATE_DIR / "conflict_state.json")    # 路径：冲突状态文件
THOUGHT_LOG_FILE = str(STATE_DIR / "thought_log.json")    # 路径：思考日志文件
PLOT_STATE_FILE = str(STATE_DIR / "plot_state.json")      # 路径：剧情状态文件

# -------------------------------------------------
# ④ 统一目录初始化
# -------------------------------------------------
def init_directories() -> Dict[str, Any]:
    dirs: List[Path] = [
        BASE_DIR, DEFAULT_SAVE_DIR, KNOWLEDGE_BASE_DIR, NOVEL_PROJECTS_DIR,
        NOVEL_WORKS_DIR, OUTLINES_DIR, RESEARCH_DIR, MACROS_DIR, PLUGINS_DIR,
        LOGS_DIR, CODE_SANDBOX_DIR, PROFILES_DIR, AUTO_PLUGINS_DIR, WEB_CACHE_DIR,
        TASK_TRACKING_DIR, LEARNING_PLANS_DIR, VECTOR_DB_DIR, DISK_CACHE_DIR,
        REASONING_LOGS_DIR, WORLD_STATES_DIR, CHAPTER_CARDS_DIR, DATA_DIR,
        DATA_ACTIVE_DIR, DATA_ARCHIVE_DIR, DATA_CONFIG_DIR, DATA_TEMP_DIR,
        DB_DIR, EXPERIENCE_DB_DIR, TOPIC_STORAGE_DIR, PROMPT_DUMP_DIR,
    ]
    created = 0
    for d in dirs:
        if not d.exists():
            ensure_path(d)
            created += 1
    if created: print(f"[config] ✅ 创建了 {created} 个缺失目录")
    else: print("[config] ✅ 所有目录已就绪")
    return {"created": created, "total": len(dirs)}

init_directories()

# -----------------------------------------------------------------
# 🌟 核心升级：智能 GPU 调度与防 OOM 降级辅助函数
# -----------------------------------------------------------------
def _is_oom_error(err_msg: str) -> bool:
    """判断是否为显存溢出 (OOM) 或 GPU 分配错误"""
    oom_keywords = ["out of memory", "oom", "cuda error", "vram", "alloc", "memory", "ggml"]
    err_lower = str(err_msg).lower()
    return any(kw in err_lower for kw in oom_keywords)

def _apply_oom_fallback(payload: Dict[str, Any]) -> None:
    """触发 OOM 降级策略：减小上下文、减少 GPU 层数，强制退回内存"""
    options = payload.setdefault("options", {})
    
    # 1. 将上下文减半 (最低降到 2048，防止 KV Cache 撑爆)
    current_ctx = options.get("num_ctx", 8192)
    new_ctx = max(2048, current_ctx // 2)
    options["num_ctx"] = new_ctx
    
    # 2. 减少 GPU 层数 (强制将部分模型层退回系统内存)
    current_gpu = options.get("num_gpu", 999)
    if current_gpu > 0:
        options["num_gpu"] = 50 if current_gpu == 999 else max(0, current_gpu // 2)
        
    print(f"[config] ⚠️ 检测到显存不足 (OOM)，已触发智能降级：num_ctx={new_ctx}, num_gpu={options['num_gpu']}，正在重试...")

# -----------------------------------------------------------------
# ⑨ 统一 Ollama 调用
# -----------------------------------------------------------------
def call_ollama_safe(
    messages: List[Dict[str, str]], model: str = None, temperature: float = 0.7,
    max_tokens: int = 32000, timeout: int = None, **kwargs
) -> Dict[str, Any]:
    """统一包装 Ollama API 调用，注入 GPU 强制满载参数与 🌡️ 温控节流机制。"""
    import requests
    import json
    import os
    
    if model is None: model = MODEL_NAME
    if timeout is None: timeout = SAFE_TIMEOUT

    # 🌟 致命修复：构建智能硬件调度参数
    base_options = {
        "num_gpu": 999,       
        "num_ctx": min(max_tokens, 8192), 
        "temperature": temperature, 
        "num_predict": max_tokens
    }
    
    # 🌡️ 温控注入：限制 CPU 线程数，防止 CPU 过热
    if OLLAMA_MAX_THREADS > 0:
        base_options["num_thread"] = OLLAMA_MAX_THREADS
    
    # 合并用户传入的 options
    user_options = kwargs.pop("options", {})
    if isinstance(user_options, dict):
        base_options.update(user_options)
        
    # 处理其他直接传入的生成参数
    for k in ("format", "num_predict", "temperature", "top_p", "stop"):
        if k in kwargs:
            base_options[k] = kwargs.pop(k)

    payload = {
        "model": model, 
        "messages": messages, 
        "stream": False,
        "options": base_options
    }
    payload.update(kwargs) 

    # 🌡️ 温控核心：使用信号量限制并发，防止瞬间功耗尖峰
    with _LLM_CONCURRENCY_SEMAPHORE:
        # 🌟 防 OOM 机制：带自动降级的重试循环
        max_retries = 2
        for attempt in range(max_retries):
            try:
                response = requests.post(OLLAMA_URL, json=payload, timeout=timeout)
                status_code = getattr(response, 'status_code', 200)
                
                raw_data = {}
                if hasattr(response, 'json') and callable(response.json):
                    try: raw_data = response.json()
                    except Exception: pass
                
                if not raw_data and hasattr(response, 'text'):
                    try: raw_data = json.loads(response.text)
                    except Exception: pass
                    
                if not raw_data and hasattr(response, 'content'):
                    try:
                        content_bytes = response.content
                        if isinstance(content_bytes, (bytes, bytearray)):
                            raw_data = json.loads(content_bytes.decode('utf-8', errors='ignore'))
                    except Exception: pass

                # 检查 HTTP 错误
                if hasattr(response, 'status_code') and status_code != 200:
                    err_msg = raw_data.get("error", f"HTTP Error {status_code}") if isinstance(raw_data, dict) else f"HTTP Error {status_code}"
                    
                    # 防 OOM 判断
                    oom_keywords = ["out of memory", "oom", "cuda error", "vram", "alloc", "memory", "ggml"]
                    is_oom = any(kw in str(err_msg).lower() for kw in oom_keywords)
                    
                    if attempt < max_retries - 1 and is_oom:
                        # 触发 OOM 降级
                        current_ctx = payload["options"].get("num_ctx", 8192)
                        payload["options"]["num_ctx"] = max(2048, current_ctx // 2)
                        current_gpu = payload["options"].get("num_gpu", 999)
                        if current_gpu > 0:
                            payload["options"]["num_gpu"] = 50 if current_gpu == 999 else max(0, current_gpu // 2)
                        continue # 重试
                        
                    return {"success": False, "error": err_msg, "content": ""}

                # 解析成功内容
                content = ""
                if isinstance(raw_data, dict):
                    if "message" in raw_data and isinstance(raw_data["message"], dict):
                        content = raw_data["message"].get("content", "")
                    elif "content" in raw_data:
                        content = raw_data["content"]
                
                # 🌡️ 温控核心：请求成功后，强制物理冷却，让 GPU/CPU 喘息
                if LLM_COOLDOWN_SECONDS > 0:
                    time.sleep(LLM_COOLDOWN_SECONDS)
                    
                return {"success": True, "content": str(content), "raw": raw_data}

            except requests.exceptions.Timeout:
                return {"success": False, "error": f"Request timed out after {timeout}s", "content": ""}
            except requests.exceptions.ConnectionError:
                return {"success": False, "error": "Connection error. Is Ollama running?", "content": ""}
            except Exception as exc:
                err_str = str(exc)
                oom_keywords = ["out of memory", "oom", "cuda error", "vram", "alloc", "memory", "ggml"]
                is_oom = any(kw in err_str.lower() for kw in oom_keywords)
                if attempt < max_retries - 1 and is_oom:
                    current_ctx = payload["options"].get("num_ctx", 8192)
                    payload["options"]["num_ctx"] = max(2048, current_ctx // 2)
                    continue
                return {"success": False, "error": f"Unexpected error: {err_str}", "content": ""}
                
        return {"success": False, "error": "Max retries exceeded after OOM fallback", "content": ""}



def call_ollama_json(
    messages, model=MODEL_NAME, temperature=0.0, max_tokens=32000, timeout=SAFE_TIMEOUT,
    retry_max=JSON_EXTRACT_MAX_RETRIES, backoff=JSON_EXTRACT_BACKOFF, enforce_json=True, **extra_kwargs
):
    from .llm_utils import call_ollama_json as _inner_json_wrapper
    return _inner_json_wrapper(
        messages=messages, model=model, temperature=temperature, max_tokens=max_tokens,
        timeout=timeout, retry_max=retry_max, backoff=backoff, enforce_json=enforce_json, **extra_kwargs
    )
    
# -------------------------------------------------
# ⑥ 数据库连接管理
# -------------------------------------------------
def get_db_connection(db_path: str, timeout: int = 30) -> sqlite3.Connection:
    with _config_lock:
        if db_path not in _db_connections:
            _db_connections[db_path] = {"conn": sqlite3.connect(db_path, timeout=timeout, check_same_thread=False), "last_used": datetime.now().timestamp()}
        else:
            _db_connections[db_path]["last_used"] = datetime.now().timestamp()
        return _db_connections[db_path]["conn"]

def close_db_connection(db_path: str) -> None:
    with _config_lock:
        if db_path in _db_connections:
            try: _db_connections[db_path]["conn"].close()
            except Exception: pass
            del _db_connections[db_path]

def close_all_db_connections() -> None:
    with _config_lock:
        for info in _db_connections.values():
            try: info["conn"].close()
            except Exception: pass
        _db_connections.clear()

# -------------------------------------------------
# ⑦ 配置验证函数
# -------------------------------------------------
def validate_config() -> Dict[str, Any]:
    errors, warnings = [], []
    if not OLLAMA_URL.startswith("http"): errors.append("OLLAMA_URL 必须是有效的 HTTP 地址")
    if not MODEL_NAME or len(MODEL_NAME) < 3: errors.append("MODEL_NAME 配置无效")
    if MEMORY_LIMIT_MB < 8000: warnings.append(f"MEMORY_LIMIT_MB ({MEMORY_LIMIT_MB}) 可能过低")
    if CHAPTER_GENERATION_TIMEOUT < 1800: warnings.append(f"CHAPTER_GENERATION_TIMEOUT ({CHAPTER_GENERATION_TIMEOUT}) 可能过短")
    for test_dir in [KNOWLEDGE_BASE_DIR, TASK_TRACKING_DIR, NOVEL_PROJECTS_DIR, DEFAULT_SAVE_DIR]:
        if not os.access(str(test_dir), os.W_OK): errors.append(f"目录不可写：{test_dir}")
    return {"valid": len(errors) == 0, "errors": errors, "warnings": warnings}

# -----------------------------------------------------------------
# ⑯ 配置导出/导入/重置
# -----------------------------------------------------------------
def export_config(filepath: str = None) -> Dict[str, Any]:
    if filepath is None: filepath = str(KNOWLEDGE_BASE_DIR / f"config_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    config_data = {
        "export_timestamp": datetime.now().isoformat(),
        "paths": {"base_dir": str(BASE_DIR), "default_save_dir": str(DEFAULT_SAVE_DIR)},
        "api": {"ollama_url": OLLAMA_URL, "model_name": MODEL_NAME}
    }
    try:
        with open(filepath, "w", encoding="utf-8") as f: json.dump(config_data, f, indent=2, ensure_ascii=False)
        return {"success": True, "filepath": filepath}
    except Exception as e: return {"success": False, "error": str(e)}

def import_config(filepath: str) -> Dict[str, Any]:
    if not os.path.exists(filepath): return {"success": False, "error": "配置文件不存在"}
    try:
        with open(filepath, "r", encoding="utf-8") as f: cfg = json.load(f)
        return {"success": True, "imported_keys": len(cfg)}
    except Exception as e: return {"success": False, "error": str(e)}

def reset_config_to_default() -> Dict[str, Any]:
    global MODEL_NAME, TEMPERATURE, MAX_OUTPUT_TOKENS
    MODEL_NAME, TEMPERATURE, MAX_OUTPUT_TOKENS = "gemma4:latest", 0.7, 32000
    init_directories()
    return {"success": True, "message": "配置已重置为默认值"}

@atexit.register
def cleanup_on_exit() -> None: close_all_db_connections()
# -------------------------------------------------
# ⑩ Prompt 模板目录
# -------------------------------------------------
_base_dir_str = str(BASE_DIR) if isinstance(BASE_DIR, Path) else str(globals().get('BASE_DIR', '.'))

# 🌟 致命修复：智能探测 prompt_templates 目录，完美兼容放在 core/ 下或根目录下的情况
_core_prompt_dir = os.path.join(_base_dir_str, "core", "prompt_templates")
_root_prompt_dir = os.path.join(_base_dir_str, "prompt_templates")

# 优先使用 core 下的目录，如果 core 下不存在，再回退到根目录
if os.path.isdir(_core_prompt_dir):
    PROMPT_TEMPLATES_DIR: str = _core_prompt_dir
else:
    PROMPT_TEMPLATES_DIR: str = _root_prompt_dir

if not os.path.isdir(PROMPT_TEMPLATES_DIR): 
    os.makedirs(PROMPT_TEMPLATES_DIR, exist_ok=True)

def _initialize_prompt_manager() -> None:
    prompt_dir = globals().get("PROMPT_TEMPLATES_DIR", "./prompt_templates")
    try: 
        os.makedirs(str(prompt_dir), exist_ok=True)
    except Exception as e: 
        print(f"[config] ⚠️ 创建 Prompt 模板目录失败: {e}")
    try:
        from .prompt_manager import PromptManager
        PromptManager.init(prompt_dir)
    except Exception as e: 
        print(f"[config] ⚠️ PromptManager 初始化失败: {e}")

def _ensure_prompt_templates() -> None:
    prompt_dir = globals().get("PROMPT_TEMPLATES_DIR", "./prompt_templates")
    # 🌟 核心修复：将 project_plan.txt 加入必需模板列表
    required = ["intent_understanding.txt", "quality_check.txt", "health_check.txt", "outline_generation.txt", "chapter_writing.txt", "project_plan.txt"]
    for fname in required:
        fpath = os.path.join(str(prompt_dir), fname)
        if not os.path.isfile(fpath):
            try:
                # 🌟 核心修复：为 project_plan.txt 写入真实的默认 Prompt，而不是无用的占位符
                if fname == "project_plan.txt":
                    content = """你是严谨的小说工程规划师。请根据以下项目元信息生成详细的工程执行计划。

【项目元信息】
{{ project_meta | tojson }}

【策划铁律】
1. 核心卖点 (selling_points) 必须极具网文吸引力（3条）。
2. 力量体系 (power_system) 必须分前、中、后期详细描述。
3. 核心冲突 (core_conflict) 必须一句话概括主线悬念。
4. 高潮节点 (climax_nodes) 至少规划 3 个预期的大高潮。

只返回纯 JSON，绝不输出 Markdown 标记或废话：
{
  "selling_points": ["卖点1", "卖点2", "卖点3"],
  "power_system": {"early": "前期体系", "mid": "中期体系", "late": "后期体系"},
  "core_conflict": "核心冲突描述",
  "climax_nodes": ["高潮节点1", "高潮节点2", "高潮节点3"]
}"""
                else:
                    content = f"# Placeholder template for {fname}\n"
                    
                with open(fpath, "w", encoding="utf-8") as f: 
                    f.write(content)
            except Exception as e: 
                print(f"[config] ⚠️ 创建占位模板 {fname} 失败: {e}")

# =====================================================================
# 👉 Ⅸ. __getattr__ 万能兜底
# =====================================================================
def __getattr__(name: str):
    if os.getenv("CONFIG_DEBUG", "0") == "1": print(f"[CONFIG] ⚠️ 未在 config 中显式定义常量 '{name}'，使用智能回退默认值。")
    fallback_values = {"MAX_OUTLINE_SIMILARITY": 0.85, "__path__": "", "PROMPT_TEMPLATES_DIR": globals().get("PROMPT_TEMPLATES_DIR", "./prompt_templates")}
    if name in fallback_values: return fallback_values[name]
    if name.isupper():
        if any(p in name for p in ["ENABLE_", "AUTO_", "USE_", "IS_", "ALLOW_", "REQUIRE_"]): return True
        if any(p in name for p in ["MAX_", "LIMIT_", "TIMEOUT_", "INTERVAL_", "COUNT_"]): return 9999
        if name.startswith("MIN_"): return 0
        if any(p in name for p in ["_DIR", "_PATH", "_FILE"]): return "."
        return None
    raise AttributeError(f"module 'core.config' has no attribute '{name}'")

_initialize_prompt_manager()
_ensure_prompt_templates()
