# core/prompt_manager.py
"""
PromptManager – 统一加载、热刷新与安全渲染

核心原则
-----------
* 只替换形如 ``{{var}}``（中间可以有或没有空格）的占位符。
* 其余所有字符（包括普通的大括号、Jinja2 的 ``{% … %}`` 等）保持原样。
* 🌟 核心修复：若模板不存在或缺少参数，**必须抛出异常**，以激活上层（如 PromptBuilder）的硬编码回退机制，绝不返回错误字符串污染 LLM 上下文。
* 支持点号嵌套键（如 ``{{state.character.name}}``），同时支持 dict 和对象属性查找。
"""

import os
import re
import threading
import logging
from typing import Dict, Optional, Any

# 使用标准 logging 替代 print，防止后台服务 stdout 阻塞
logger = logging.getLogger("PromptManager")

class PromptManager:
    # -----------------------------------------------------------------
    # 1️⃣ 单例状态
    # -----------------------------------------------------------------
    _templates: Dict[str, str] = {}
    _base_dir: str = ""
    _initialized: bool = False
    _lock = threading.RLock()

    # -----------------------------------------------------------------
    # 2️⃣ 初始化
    # -----------------------------------------------------------------
    @classmethod
    def init(cls, base_dir: Optional[str] = None) -> None:
        """初始化根目录并一次性加载所有模板。"""
        with cls._lock:
            if cls._initialized and base_dir is None:
                return  # 防止重复初始化
            
            if base_dir is None:
                from .config import PROMPT_TEMPLATES_DIR
                base_dir = PROMPT_TEMPLATES_DIR
                
            cls._base_dir = base_dir
            cls.reload()
            cls._initialized = True

    # -----------------------------------------------------------------
    # 3️⃣ 读取
    # -----------------------------------------------------------------
    @classmethod
    def reload(cls) -> None:
        """重新读取 _base_dir 下的所有 *.txt 与 *.prompt 文件。"""
        with cls._lock:
            cls._templates.clear()
            if not os.path.isdir(cls._base_dir):
                logger.warning(f"模板目录不存在: {cls._base_dir}")
                return
                
            for fname in os.listdir(cls._base_dir):
                if not (fname.endswith(".txt") or fname.endswith(".prompt")):
                    continue
                key = os.path.splitext(fname)[0]
                fpath = os.path.join(cls._base_dir, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as fp:
                        cls._templates[key] = fp.read()
                except Exception as exc:
                    logger.error(f"读取模板失败 {fpath}: {exc}")

    # -----------------------------------------------------------------
    # 4️⃣ 内部渲染实现（支持 dict 与对象属性）
    # -----------------------------------------------------------------
    @staticmethod
    def _render_raw(raw: str, variables: Dict[str, Any]) -> str:
        """
        用正则把所有 {{var}} 替换为 variables 中对应的值。
        🌟 核心修复：支持点号嵌套，且同时支持 dict 键和对象属性（getattr）。
        """
        pattern = re.compile(r"\{\{\s*([A-Za-z0-9_.]+)\s*\}\}")
        missing = set()

        def repl(match):
            key_path = match.group(1)
            cur: Any = variables
            for part in key_path.split('.'):
                if isinstance(cur, dict) and part in cur:
                    cur = cur[part]
                elif hasattr(cur, part):
                    cur = getattr(cur, part)
                else:
                    missing.add(key_path)
                    return match.group(0)
            return str(cur) if cur is not None else ""

        rendered = pattern.sub(repl, raw)

        if missing:
            # 🌟 核心修复：果断抛出 KeyError，让上层 try...except 捕获并触发回退！
            raise KeyError(f"缺少占位参数: {', '.join(sorted(missing))}")

        return rendered

    # -----------------------------------------------------------------
    # 5️⃣ 对外渲染入口（安全版 + 懒初始化 + 异常抛出）
    # -----------------------------------------------------------------
    @classmethod
    def render(cls, name: str, **variables) -> str:
        """
        渲染指定模板并返回完整字符串。
        🌟 核心修复：如果模板缺失或参数缺失，**必须抛出异常**（ValueError/KeyError），
        绝不能返回错误字符串，否则错误字符串会被当作 Prompt 发给 LLM 导致 JSON 解析崩溃！
        """
        # 🌟 修复竞态条件：双重检查锁定 (Double-Checked Locking)
        if not cls._initialized:
            with cls._lock:
                if not cls._initialized:
                    from .config import PROMPT_TEMPLATES_DIR
                    cls.init(PROMPT_TEMPLATES_DIR)

        with cls._lock:
            raw = cls._templates.get(name)

        if raw is None:
            # 🌟 核心修复：抛出 ValueError，激活 PromptBuilder 的硬编码回退
            raise ValueError(f"模板 '{name}' 在目录 {cls._base_dir} 中未找到")

        # _render_raw 内部如果缺少参数会抛出 KeyError
        return cls._render_raw(raw, variables)
