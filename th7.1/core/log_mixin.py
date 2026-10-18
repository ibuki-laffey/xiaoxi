import json
from datetime import datetime
from typing import Any

def _load_global_flags():
    """读取日志相关的全局开关。"""
    try:
        from .constants import LOG_DEDUPLICATION
        from .config import PROFESSIONAL_LOG_ENABLED
    except Exception:
        LOG_DEDUPLICATION = False
        PROFESSIONAL_LOG_ENABLED = False
    return LOG_DEDUPLICATION, PROFESSIONAL_LOG_ENABLED

_LOG_DEDUPLICATION, _PROFESSIONAL_LOG_ENABLED = _load_global_flags()

class LogMixin:
    """Mixin 为任意类提供统一的 _log 方法。"""
    
    _dedup_set = set()
    _DEDUP_MAX_SIZE = 5000  # 🌟 核心修复：防止内存泄漏的阈值

    def _log(self, msg: str, level: str = "INFO") -> None:
        log_cb = getattr(self, "log_callback", None)
        timestamp = datetime.now().isoformat()

        # 🌟 核心修复：防止 _dedup_set 无限增长导致内存泄漏
        if _LOG_DEDUPLICATION and level.upper() in ("WARN", "ERROR"):
            if len(LogMixin._dedup_set) > self._DEDUP_MAX_SIZE:
                LogMixin._dedup_set.clear()
            dedup_key = (level.upper(), msg)
            if dedup_key in LogMixin._dedup_set: return
            LogMixin._dedup_set.add(dedup_key)

        if _PROFESSIONAL_LOG_ENABLED:
            entry = {
                "timestamp": timestamp, "level": level.upper(), "module": self.__class__.__name__,
                "project_id": getattr(self, "project_id", None), "message": msg,
            }
            log_line = json.dumps(entry, ensure_ascii=False)
        else:
            log_line = f"[{timestamp}] [{level.upper():5}] {msg}"

        if callable(log_cb):
            try: log_cb(log_line)
            except Exception: print(log_line)
        else:
            print(log_line)

class _LogMixinBase(LogMixin):
    """内部占位类，仅在需要显式实例化 LogMixin 时使用。"""
    def __init__(self, log_callback=None):
        self.log_callback = log_callback
        super().__init__()
