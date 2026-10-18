# core/constants.py
"""
全局常量集中管理
- 让 AIController 以及其它模块可以统一引用
- 便于后期调参、打开/关闭功能
- 每个常量都有简要注释，防止误删
"""
import os

# ----------------------------------------------------------------------
# 日志相关
# ----------------------------------------------------------------------
# 是否对 WARN/ERROR 日志做去重（同一条消息只记录一次）
LOG_DEDUPLICATION: bool = True

# 是否开启结构化（JSON）日志。默认关闭，可在 core/config 中覆盖。
PROFESSIONAL_LOG_ENABLED: bool = False

# ----------------------------------------------------------------------
# 意图分析
# ----------------------------------------------------------------------
# LLM 返回的置信度阈值。低于此值将回退到关键字匹配。
INTENT_CONFIDENCE_THRESHOLD: float = 0.5

# 是否仅使用 AI 返回的意图（关闭关键字回退）
USE_ONLY_AI_INTENT: bool = False

# ----------------------------------------------------------------------
# 章节生成与质量检查
# ----------------------------------------------------------------------
# 章节质量检查未通过时的最大重写次数
MAX_CHAPTER_GENERATION_RETRIES: int = 4

# 角色成长系统开关
CHARACTER_GROWTH_ENABLED: bool = True

# 世界观真相层级（可供插件或推理使用）
WORLD_TRUTH_LEVELS = ["surface", "deep", "meta"]

# ----------------------------------------------------------------------
# 健康检查
# ----------------------------------------------------------------------
# 每多少章节触发一次健康检查
HEALTH_CHECK_INTERVAL_CHAPTERS: int = 10

# 健康检查错误是否直接阻塞写作
HEALTH_CHECK_STRICT_MODE: bool = False

# 检测到大纲缺失时是否自动重新生成
HEALTH_CHECK_AUTO_REGENERATE: bool = True

# 健康检查期间是否保持安静（不打印 INFO/WARN）
SILENT_HEALTH_CHECK: bool = False

# ----------------------------------------------------------------------
# 大纲与项目自动化开关
# ----------------------------------------------------------------------
# 大纲自动重建（在检测到缺失时自动恢复）
AUTO_RECONSTRUCT_OUTLINE_ENABLED: bool = True

# 项目创建后自动生成大纲
AUTO_OUTLINE_AFTER_CREATION: bool = True

# 是否在项目创建后自动进行蓝图校验
PROJECT_BLUEPRINT_VALIDATION_ENABLED: bool = True

# AI 自主度（0=完全被动，3=高度自治）
AI_AUTONOMY_LEVEL: int = 2

# ----------------------------------------------------------------------
# 其它全局开关
# ----------------------------------------------------------------------
# 是否记录内部详细日志（调试专用）
INTERNAL_LOG_ENABLED: bool = False

# 是否在文件写入时使用原子操作（防止异常导致文件损坏）
ATOMIC_OPERATION_ENABLED: bool = True

# ----------------------------------------------------------------------
# Ollama / LLM 参数
# ----------------------------------------------------------------------
# 本地 Ollama 服务地址（可以通过 core/config 覆盖）
OLLAMA_URL: str = "http://localhost:11434/api/chat"

# 使用的模型名称
MODEL_NAME: str = "gemma4:latest"    #备用模型"qwen3.5:latest" "gemma4:latest" 

# 调用 Ollama 时的最大 token 数
MAX_OUTPUT_TOKENS: int = 32000

# 对话历史所能容纳的最大 token 数（供 ConversationManager 使用）
MAX_CONTEXT_TOKENS: int = 32000

# LLM 采样温度（默认 0.7，实际可在 core/config 中覆盖）
TEMPERATURE: float = 0.7

# HTTP 请求超时时间（秒）
REQUEST_TIMEOUT: int = 15
SAFE_TIMEOUT: int = 15

# 代码执行沙箱目录（兼容 Windows 和 Linux 的环境变量）
CODE_SANDBOX_DIR: str = os.path.join(os.getenv("TMPDIR", os.getenv("TEMP", "/tmp")), "code_sandbox")

# ----------------------------------------------------------------------
# 导出列表 (🌟 修复了原有的重复项)
# ----------------------------------------------------------------------
__all__ = [
    "LOG_DEDUPLICATION",
    "PROFESSIONAL_LOG_ENABLED",
    "INTENT_CONFIDENCE_THRESHOLD",
    "USE_ONLY_AI_INTENT",
    "MAX_CHAPTER_GENERATION_RETRIES",
    "CHARACTER_GROWTH_ENABLED",
    "WORLD_TRUTH_LEVELS",
    "HEALTH_CHECK_INTERVAL_CHAPTERS",
    "HEALTH_CHECK_STRICT_MODE",
    "HEALTH_CHECK_AUTO_REGENERATE",
    "SILENT_HEALTH_CHECK",
    "AUTO_RECONSTRUCT_OUTLINE_ENABLED",
    "AUTO_OUTLINE_AFTER_CREATION",
    "PROJECT_BLUEPRINT_VALIDATION_ENABLED",
    "AI_AUTONOMY_LEVEL",
    "INTERNAL_LOG_ENABLED",
    "ATOMIC_OPERATION_ENABLED",
    "OLLAMA_URL",
    "MODEL_NAME",
    "MAX_OUTPUT_TOKENS",
    "MAX_CONTEXT_TOKENS",
    "TEMPERATURE",
    "REQUEST_TIMEOUT",
    "SAFE_TIMEOUT",
    "CODE_SANDBOX_DIR",
    "TaskMetaSchema",
]

# ----------------------------------------------------------------------
# 任务元信息 JSON‑Schema（用于 DialogAnalyzer 以及 AIController 的校验）
# ----------------------------------------------------------------------
TaskMetaSchema = {
    "type": "object",
    "required": [
        "title",
        "genre",
        "world_type",
        "main_character",
        "total_word_count",
        "words_per_chapter",
        "total_chapters",
    ],
    "properties": {
        "title": {"type": "string", "minLength": 1},
        "genre": {"type": "string", "minLength": 1},
        "world_type": {"type": "string", "minLength": 1},
        "main_character": {"type": "string", "minLength": 1},
        "total_word_count": {"type": "integer", "minimum": 1},
        "words_per_chapter": {"type": "integer", "minimum": 1},
        "total_chapters": {"type": "integer", "minimum": 1},
    },
    # 🌟 致命修复：必须改为 True！否则 DialogAnalyzer 注入的 _source_tracking 字段会导致校验崩溃
    "additionalProperties": True,  
}
