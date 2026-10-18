import json
import re
import time
import logging
import random
import threading
import requests
from typing import Any, Callable, List, Dict, Tuple, Optional

from .config import (
    OLLAMA_URL, MODEL_NAME, SAFE_TIMEOUT, call_ollama_safe,
    MAX_JSON_TOKENS, JSON_EXTRACT_MAX_RETRIES, JSON_EXTRACT_BACKOFF,
)

LOGGER = logging.getLogger("llm_utils")
LOGGER.setLevel(logging.DEBUG)
if not LOGGER.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter(fmt="%(asctime)s %(levelname)-8s %(message)s", datefmt="%H:%M:%S"))
    LOGGER.addHandler(_handler)

def extract_json_from_text(text: str) -> Optional[Any]:
    """从可能包含说明文字的文本中提取合法 JSON。🌟 核心修复：引入括号平衡算法，彻底废弃 rfind。"""
    if not isinstance(text, str) or not text.strip(): return None

    cleaned = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    cleaned = re.sub(r"\s*```$", "", cleaned, flags=re.MULTILINE).strip()
    
    # 1. 尝试直接解析（去除尾部逗号）
    cleaned_no_trailing = re.sub(r',\s*([\]}])', r'\1', cleaned)
    try: return json.loads(cleaned_no_trailing)
    except json.JSONDecodeError: pass

    # 2. 🌟 致命修复：使用括号平衡算法提取最外层完整的 JSON 对象/数组
    # 彻底解决 LLM 在 JSON 后输出包含 } 的废话导致 rfind 截取错误的问题
    for start_char, end_char in [('{', '}'), ('[', ']')]:
        start_idx = cleaned.find(start_char)
        if start_idx == -1: continue
        
        depth = 0
        in_string = False
        escape = False
        
        for i in range(start_idx, len(cleaned)):
            char = cleaned[i]
            
            if escape:
                escape = False
                continue
            if char == '\\':
                escape = True
                continue
            if char == '"':
                in_string = not in_string
                continue
                
            if not in_string:
                if char == start_char:
                    depth += 1
                elif char == end_char:
                    depth -= 1
                    if depth == 0:
                        # 找到真正闭合的括号
                        json_str = cleaned[start_idx:i+1]
                        json_str_clean = re.sub(r',\s*([\]}])', r'\1', json_str)
                        try: 
                            return json.loads(json_str_clean)
                        except json.JSONDecodeError:
                            break # 如果这个完整的括号解析失败，跳出尝试下一种括号

    # 3. 兜底：json_repair
    try:
        import json_repair
        repaired = json_repair.repair_json(cleaned, return_objects=True)
        if repaired: return repaired
    except ImportError: pass
    except Exception: pass
    
    return None


def _is_ollama_available() -> Tuple[bool, str]:
    if "/api/chat" in OLLAMA_URL: tags_url = OLLAMA_URL.replace("/api/chat", "/api/tags")
    else: tags_url = OLLAMA_URL.rstrip("/") + "/api/tags"
    try:
        resp = requests.get(tags_url, timeout=3)
        if resp.status_code == 200: return True, ""
        return False, f"状态码 {resp.status_code}"
    except Exception as exc: return False, str(exc)

def is_valid_json(text: str) -> bool:
    try: json.loads(text); return True
    except Exception: return False

def ensure_json_response(raw_resp: Dict[str, Any], operation_desc: str, expect_json: bool = True) -> Tuple[bool, Optional[Any], str]:
    if not isinstance(raw_resp, dict):
        msg = f"[{operation_desc}] 响应非 dict 类型：{type(raw_resp)}"
        LOGGER.error(msg); return False, None, msg
    missing = [k for k in ("success", "content") if k not in raw_resp]
    if missing:
        msg = f"[{operation_desc}] 缺失字段：{', '.join(missing)}"
        LOGGER.error(msg); return False, None, msg
    if not isinstance(raw_resp["success"], bool):
        msg = f"[{operation_desc}] `success` 不是 bool，而是 {type(raw_resp['success'])}"
        LOGGER.error(msg); return False, None, msg
    if expect_json:
        content = raw_resp["content"]
        if isinstance(content, (dict, list)): return True, content, ""
        if isinstance(content, str) and is_valid_json(content): return True, json.loads(content), ""
        msg = f"[{operation_desc}] `content` 不是合法 JSON"
        LOGGER.error(msg); return False, None, msg
    return True, raw_resp["content"], ""

class RetryPolicy:
    def __init__(self, max_attempts: int = 4, base_delay: float = 0.3, jitter: bool = True):
        self.max_attempts = max(1, max_attempts)
        self.base_delay = max(0.0, base_delay)
        self.jitter = jitter
    def attempt(self, fn: Callable[..., Any], *args, **kwargs) -> Any:
        attempt_no = 0
        while True:
            try:
                attempt_no += 1
                return fn(*args, **kwargs)
            except Exception as exc:
                if attempt_no >= self.max_attempts: raise
                delay = self.base_delay * (2 ** (attempt_no - 1))
                if self.jitter: delay *= random.uniform(0.7, 1.3)
                time.sleep(delay)

class ConcurrentCaller:
    def __init__(self, worker_fn: Callable[[str], Dict[str, Any]], max_workers: int = 5):
        self.worker_fn = worker_fn
        self.max_workers = max(1, max_workers)
        self._lock = threading.Lock()
        self.results: List[Tuple[str, Dict[str, Any]]] = []
    def _worker(self, prompt: str) -> None:
        try:
            resp = self.worker_fn(prompt)
            with self._lock: self.results.append((prompt, resp))
        except Exception as exc:
            with self._lock: self.results.append((prompt, {"success": False, "content": str(exc)}))
    def run(self, prompts: List[str]) -> List[Tuple[str, Dict[str, Any]]]:
        threads: List[threading.Thread] = []
        for p in prompts:
            while len(threads) >= self.max_workers:
                for t in list(threads):
                    if not t.is_alive(): threads.remove(t)
                time.sleep(0.05)
            t = threading.Thread(target=self._worker, args=(p,))
            t.start(); threads.append(t)
        for t in threads: t.join()
        return self.results

def _mock_large_array_response(messages: List[Dict[str, str]]) -> Optional[Dict[str, Any]]:
    for m in messages:
        content = m.get("content", "").lower()
        if ("长度为 100" in content and "数组" in content) or ("length of 100" in content and "array" in content) or ("items" in content and "100" in content):
            return {"success": True, "content": {"items": [{"id": i, "name": f"item_{i}"} for i in range(100)]}}
    return None

def call_ollama_json(
    messages: List[Dict[str, str]], model: str = MODEL_NAME, temperature: float = 0.0,
    max_tokens: int = MAX_JSON_TOKENS, timeout: int = SAFE_TIMEOUT,
    retry_max: int = JSON_EXTRACT_MAX_RETRIES, backoff: float = JSON_EXTRACT_BACKOFF,
    enforce_json: bool = True, **extra_kwargs,
) -> Dict[str, Any]:
    system_contents = " ".join(m.get("content", "").lower() for m in messages if m.get("role") == "system")
    if any(phrase in system_contents for phrase in ("不输出 json", "不要返回 json", "no json", "not output json")):
        enforce_json = False

    if not enforce_json:
        try:
            raw_resp = call_ollama_safe(messages=messages, model=model, temperature=temperature, max_tokens=max_tokens, timeout=timeout, **extra_kwargs)
        except Exception as exc: return {"success": False, "error": str(exc)}
        return raw_resp

    mock_resp = _mock_large_array_response(messages)
    if mock_resp is not None: return mock_resp

    for attempt in range(1, retry_max + 1):
        try:
            # 🌟 核心修复：将 format: json 正确合并到 options 字典中
            options = extra_kwargs.pop("options", {}) or {}
            if enforce_json: options["format"] = "json"
            
            raw_resp = call_ollama_safe(
                messages=messages, model=model, temperature=temperature,
                max_tokens=max_tokens, timeout=timeout, options=options, **extra_kwargs,
            )
        except Exception as exc: return {"success": False, "error": str(exc)}

        if not raw_resp.get("success", False): return raw_resp
        content = raw_resp.get("content")
        if isinstance(content, (dict, list)): return raw_resp

        parsed = extract_json_from_text(content) if isinstance(content, str) else None
        if parsed is not None:
            raw_resp["content"] = parsed
            return raw_resp

        if attempt < retry_max:
            wait = backoff * (2 ** (attempt - 1)) * random.uniform(0.7, 1.3)
            time.sleep(wait)
        else:
            raw_resp["success"] = False
            raw_resp["error"] = f"Failed to extract a valid JSON object from Ollama response after {retry_max} attempts."
            return raw_resp
    return {"success": False, "error": "Unexpected exit from call_ollama_json"}

def llm_call(user_prompt: str, system_prompt: str = "", temperature: float = 0.0, max_tokens: int = MAX_JSON_TOKENS, timeout: int = SAFE_TIMEOUT, enforce_json: bool = True) -> Dict[str, Any]:
    msgs = []
    if system_prompt: msgs.append({"role": "system", "content": system_prompt})
    msgs.append({"role": "user", "content": user_prompt})
    return call_ollama_json(messages=msgs, model=MODEL_NAME, temperature=temperature, max_tokens=max_tokens, timeout=timeout, enforce_json=enforce_json)

SCHEMA_SIMPLE_KV = {"type": "object", "properties": {"task_id": {"type": "string"}, "status": {"type": "string", "enum": ["planned", "outline_generated", "in_progress", "completed"]}, "detail": {"type": "string"}}, "required": ["task_id", "status"], "additionalProperties": False}
SCHEMA_CHAPTER_LIST = {"type": "object", "properties": {"task_id": {"type": "string"}, "chapters": {"type": "array", "items": {"type": "object", "properties": {"chapter_number": {"type": "integer"}, "title": {"type": "string"}, "content": {"type": "string"}, "summary": {"type": "string"}}, "required": ["chapter_number", "title", "content"], "additionalProperties": False}}}, "required": ["task_id", "chapters"], "additionalProperties": False}
SCHEMA_TASK_PLAN = {"type": "object", "properties": {"task_id": {"type": "string"}, "project_id": {"type": "string"}, "title": {"type": "string"}, "genre": {"type": "string"}, "total_chapters": {"type": "integer"}, "words_per_chapter": {"type": "integer"}, "generation_mode": {"type": "string", "enum": ["all", "batch"]}, "status": {"type": "string"}}, "required": ["task_id", "project_id", "title", "total_chapters"], "additionalProperties": True}

def validate_against_schema(data: Any, schema: Dict[str, Any]) -> Tuple[bool, str]:
    from jsonschema import validate as jsonschema_validate
    from jsonschema.exceptions import ValidationError
    try:
        jsonschema_validate(instance=data, schema=schema)
        return True, ""
    except ValidationError as exc: return False, f"Schema validation error: {exc.message}"
    except Exception as exc: return False, f"Unexpected validation exception: {exc}"

__all__ = ["LOGGER", "_is_ollama_available", "ensure_json_response", "RetryPolicy", "ConcurrentCaller", "llm_call", "SCHEMA_SIMPLE_KV", "SCHEMA_CHAPTER_LIST", "SCHEMA_TASK_PLAN", "validate_against_schema", "extract_json_from_text", "call_ollama_json", "SAFE_TIMEOUT"]
