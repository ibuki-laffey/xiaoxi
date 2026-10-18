# -*- coding: utf-8 -*-
# =============================================================================
#  llm_json_utils.py
# =============================================================================
"""业务层代码：统一的 Ollama JSON 调用 + 辅助工具
- call_ollama_json（在 core.config 中实现）统一返回 ``content`` 为 dict/list
- 提供 RetryPolicy、ConcurrentCaller
- 包含 JSON‑Schema、校验、噪声容错等工具
"""

import os
import sys
import json
import time
import random
import logging
import threading
import traceback
from typing import Any, Dict, List, Tuple, Callable, Optional

import requests
from jsonschema import validate as jsonschema_validate, ValidationError

# -------------------------------------------------------------------------
# 自动把项目根目录加入 sys.path（保证在任何工作目录都能 import core.xxx）
# -------------------------------------------------------------------------
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# -------------------------------------------------------------------------
# 项目内部的配置 & LLM 调用封装
# -------------------------------------------------------------------------
try:
    from core.config import (
        OLLAMA_URL,          # 如 "http://127.0.0.1:11434/api/chat"
        MODEL_NAME,
        SAFE_TIMEOUT,
        REQUEST_TIMEOUT,
        call_ollama_json,   # 必须返回 {"success": bool, "content": dict|list|str, ...}
    )
except Exception as exc:  # pragma: no cover
    raise ImportError(
        "无法导入 core.config 中的 LLM 调用函数，请检查 core/config.py。"
        f"\n原始异常: {exc}"
    ) from exc

# -------------------------------------------------------------------------
# 统一日志（在测试中也会使用）
# -------------------------------------------------------------------------
LOGGER = logging.getLogger("LLM_JSON_UTILS")
LOGGER.setLevel(logging.DEBUG)
_handler = logging.StreamHandler()
_handler.setFormatter(
    logging.Formatter(fmt="%(asctime)s %(levelname)-8s %(message)s", datefmt="%H:%M:%S")
)
if not LOGGER.handlers:
    LOGGER.addHandler(_handler)

# -------------------------------------------------------------------------
# 辅助工具函数
# -------------------------------------------------------------------------

def _is_ollama_available() -> Tuple[bool, str]:
    """
    检查 Ollama 健康端点是否可达。

    Returns
    -------
    Tuple[bool, str]
        (是否可用, 错误信息或空串)
    """
    # 计算正确的 /api/tags URL
    if "/api/chat" in OLLAMA_URL:
        tags_url = OLLAMA_URL.replace("/api/chat", "/api/tags")
    else:
        tags_url = OLLAMA_URL.rstrip("/") + "/api/tags"

    try:
        resp = requests.get(tags_url, timeout=3)
        if resp.status_code == 200:
            return True, ""
        return False, f"状态码 {resp.status_code}"
    except Exception as exc:  # pragma: no cover
        return False, str(exc)


def is_valid_json(text: str) -> bool:
    """判断字符串能否被 ``json.loads`` 正常解析。"""
    try:
        json.loads(text)
        return True
    except Exception:
        return False


def parse_json(text: str) -> Any:
    """安全解析 JSON，若失败会抛出 ``ValueError``。"""
    return json.loads(text)


def ensure_json_response(
    raw_resp: Dict[str, Any],
    operation_desc: str,
    expect_json: bool = True,
) -> Tuple[bool, Optional[Any], str]:
    """
    轻量版的 ``core.ai_controller._validate_ollama_response``，专供单元测试。

    参数
    ------
    raw_resp : dict
        LLM 调用返回的原始结构
    operation_desc : str
        用于日志/错误信息的描述标签
    expect_json : bool, default True
        是否期望 ``content`` 为合法 JSON（dict/list 或可解析的字符串）

    返回
    ------
    Tuple[bool, Optional[Any], str]
        (校验通过?, 解析后的对象或 None, 错误信息)
    """
    if not isinstance(raw_resp, dict):
        msg = f"[{operation_desc}] 响应非 dict 类型：{type(raw_resp)}"
        LOGGER.error(msg)
        return False, None, msg

    missing = [k for k in ("success", "content") if k not in raw_resp]
    if missing:
        msg = f"[{operation_desc}] 缺失字段：{', '.join(missing)}"
        LOGGER.error(msg)
        return False, None, msg

    if not isinstance(raw_resp["success"], bool):
        msg = f"[{operation_desc}] `success` 不是 bool，而是 {type(raw_resp['success'])}"
        LOGGER.error(msg)
        return False, None, msg

    if expect_json:
        content = raw_resp["content"]
        if isinstance(content, (dict, list)):
            return True, content, ""
        if isinstance(content, str) and is_valid_json(content):
            return True, parse_json(content), ""
        msg = f"[{operation_desc}] `content` 不是合法 JSON"
        LOGGER.error(msg)
        return False, None, msg

    # 当不要求 JSON 时直接返回原始 content
    return True, raw_resp["content"], ""


# -------------------------------------------------------------------------
# 重试策略（指数退避 + jitter）
# -------------------------------------------------------------------------

class RetryPolicy:
    """指数退避重试包装器"""

    def __init__(self, max_attempts: int = 4, base_delay: float = 0.3, jitter: bool = True):
        self.max_attempts = max(1, max_attempts)
        self.base_delay = max(0.0, base_delay)
        self.jitter = jitter

    def attempt(self, fn: Callable[..., Any], *args, **kwargs) -> Any:
        """执行 ``fn``，在异常时自动重试。"""
        attempt_no = 0
        while True:
            try:
                attempt_no += 1
                LOGGER.debug("调用 %s（第 %d 次尝试）", fn.__name__, attempt_no)
                return fn(*args, **kwargs)
            except Exception as exc:  # pragma: no cover
                if attempt_no >= self.max_attempts:
                    LOGGER.error(
                        "重试已达上限 (%d)，仍然失败：%s", self.max_attempts, exc
                    )
                    raise
                delay = self.base_delay * (2 ** (attempt_no - 1))
                if self.jitter:
                    delay *= random.uniform(0.7, 1.3)
                LOGGER.warning(
                    "调用 %s 失败，将在 %.2f 秒后重试（异常：%s）",
                    fn.__name__,
                    delay,
                    exc,
                )
                time.sleep(delay)


# -------------------------------------------------------------------------
# 并发调用（多线程）
# -------------------------------------------------------------------------

class ConcurrentCaller:
    """多线程并发调用 LLM"""

    def __init__(self, worker_fn: Callable[[str], Dict[str, Any]], max_workers: int = 5):
        self.worker_fn = worker_fn
        self.max_workers = max(1, max_workers)
        self._lock = threading.Lock()
        self.results: List[Tuple[str, Dict[str, Any]]] = []

    def _worker(self, prompt: str) -> None:
        """线程入口，捕获异常后写入 ``self.results``。"""
        try:
            resp = self.worker_fn(prompt)
            with self._lock:
                self.results.append((prompt, resp))
        except Exception as exc:  # pragma: no cover
            with self._lock:
                self.results.append((prompt, {"success": False, "content": str(exc)}))

    def run(self, prompts: List[str]) -> List[Tuple[str, Dict[str, Any]]]:
        """启动线程池并返回所有 (prompt, response) 列表。"""
        threads: List[threading.Thread] = []
        for p in prompts:
            # 保证不超过 max_workers
            while len(threads) >= self.max_workers:
                for t in threads:
                    if not t.is_alive():
                        threads.remove(t)
                time.sleep(0.05)
            t = threading.Thread(target=self._worker, args=(p,))
            t.start()
            threads.append(t)

        for t in threads:
            t.join()
        return self.results


# -------------------------------------------------------------------------
# 统一的 LLM 调用入口（强制返回 JSON）
# -------------------------------------------------------------------------

def llm_call(
    user_prompt: str,
    system_prompt: str = "",
    temperature: float = 0.0,
    max_tokens: int = 2048,
    timeout: int = SAFE_TIMEOUT,
) -> Dict[str, Any]:
    """
    统一的 LLM 调用，**全部走 ``call_ollama_json``**，保证返回的 ``content``
    已经是合法的 ``dict``/``list``（若仍失败则 ``success=False`` 并携带 ``error``）。

    参数
    ------
    user_prompt : str
        用户输入
    system_prompt : str, optional
        系统指令（可以为空）
    temperature : float, default 0.0
        采样温度
    max_tokens : int, default 2048
        最大输出 token
    timeout : int, default SAFE_TIMEOUT
        超时时间（秒）

    返回
    ------
    dict
        {"success": bool, "content": dict|list, "error": str (若失败)}
    """
    # 1️⃣ 组装消息列表
    msgs: List[Dict[str, str]] = []
    if system_prompt:
        msgs.append({"role": "system", "content": system_prompt})
    msgs.append({"role": "user", "content": user_prompt})

    # 2️⃣ 调用核心封装（已在 core.config 中实现）
    return call_ollama_json(
        messages=msgs,
        model=MODEL_NAME,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
    )


# -------------------------------------------------------------------------
# JSON Schema 定义（业务中常用结构）
# -------------------------------------------------------------------------

SCHEMA_SIMPLE_KV = {
    "type": "object",
    "properties": {
        "task_id": {"type": "string"},
        "status": {
            "type": "string",
            "enum": ["planned", "outline_generated", "in_progress", "completed"],
        },
        "detail": {"type": "string"},
    },
    "required": ["task_id", "status"],
    "additionalProperties": False,
}

SCHEMA_CHAPTER_LIST = {
    "type": "object",
    "properties": {
        "task_id": {"type": "string"},
        "chapters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "chapter_number": {"type": "integer"},
                    "title": {"type": "string"},
                    "content": {"type": "string"},
                    "summary": {"type": "string"},
                },
                "required": ["chapter_number", "title", "content"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["task_id", "chapters"],
    "additionalProperties": False,
}

SCHEMA_TASK_PLAN = {
    "type": "object",
    "properties": {
        "task_id": {"type": "string"},
        "project_id": {"type": "string"},
        "title": {"type": "string"},
        "genre": {"type": "string"},
        "total_chapters": {"type": "integer"},
        "words_per_chapter": {"type": "integer"},
        "generation_mode": {
            "type": "string",
            "enum": ["all", "batch"],
        },
        "status": {"type": "string"},
    },
    "required": ["task_id", "project_id", "title", "total_chapters"],
    "additionalProperties": True,
}


def validate_against_schema(data: Any, schema: Dict[str, Any]) -> Tuple[bool, str]:
    """
    使用 ``jsonschema`` 对 ``data`` 进行校验。

    Returns
    -------
    Tuple[bool, str]
        (是否通过, 错误信息)
    """
    try:
        jsonschema_validate(instance=data, schema=schema)
        return True, ""
    except ValidationError as exc:
        return False, str(exc)


# -------------------------------------------------------------------------
# 导出符号列表（让 `from llm_json_utils import *` 更可控）
# -------------------------------------------------------------------------

__all__ = [
    "LOGGER",
    "_is_ollama_available",
    "is_valid_json",
    "parse_json",
    "ensure_json_response",
    "RetryPolicy",
    "ConcurrentCaller",
    "llm_call",
    "SCHEMA_SIMPLE_KV",
    "SCHEMA_CHAPTER_LIST",
    "SCHEMA_TASK_PLAN",
    "validate_against_schema",
]
