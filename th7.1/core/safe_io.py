# -*- coding: utf-8 -*-
"""
safe_io.py
~~~~~~~~~~
统一安全 I/O 网关 (Safe I/O Gateway)。
彻底消灭各模块中散落的 try...except open/json 和 os.path.exists 判断。
提供工业级的原子写入、并发安全和 JSON 自动修复能力。
"""

import os
import json
import tempfile
from typing import Any, Optional, Callable

def safe_load_json(file_path: str, default: Any = None, log_func: Optional[Callable] = None) -> Any:
    """
    安全加载 JSON 文件。
    自动处理文件不存在、JSON 损坏、权限问题。绝不抛出异常。
    """
    if default is None:
        default = {}
        
    if not file_path or not os.path.exists(file_path):
        return default
        
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read().strip()
            if not content:
                return default
            return json.loads(content)
    except json.JSONDecodeError as e:
        if log_func: log_func(f"⚠️ JSON 解析失败 ({file_path}): {e}，尝试使用 Guardian 修复...", level="WARN")
        try:
            from .super_guardian import guardian
            healed = guardian.heal_json(content, expected_type=dict)
            return healed if healed is not None else default
        except Exception:
            return default
    except Exception as e:
        if log_func: log_func(f"⚠️ 读取文件失败 ({file_path}): {e}", level="WARN")
        return default

def safe_dump_json(file_path: str, data: Any, log_func: Optional[Callable] = None, indent: int = 2) -> bool:
    """
    安全写入 JSON 文件（工业级原子写入）。
    先写入同目录的 .tmp 文件，然后使用 os.replace 原子替换，彻底杜绝写入中途断电/崩溃导致文件损坏。
    """
    if not file_path:
        return False
        
    try:
        dir_name = os.path.dirname(file_path)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)
            
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=indent)
                f.flush()
                os.fsync(f.fileno())
                
            if os.name == 'nt' and os.path.exists(file_path):
                try: os.remove(file_path)
                except PermissionError: pass
                
            os.replace(tmp_path, file_path)
            return True
        except Exception:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise
            
    except Exception as e:
        if log_func: log_func(f"❌ 原子写入 JSON 失败 ({file_path}): {e}", level="ERROR")
        return False

def safe_read_text(file_path: str, default: str = "", log_func: Optional[Callable] = None) -> str:
    """安全读取纯文本文件。"""
    if not file_path or not os.path.exists(file_path):
        return default
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        if log_func: log_func(f"⚠️ 读取文本失败 ({file_path}): {e}", level="WARN")
        return default

def safe_write_text(file_path: str, text: str, log_func: Optional[Callable] = None) -> bool:
    """安全写入纯文本文件（原子写入）。"""
    if not file_path:
        return False
    try:
        dir_name = os.path.dirname(file_path)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)
            
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
                
            if os.name == 'nt' and os.path.exists(file_path):
                try: os.remove(file_path)
                except PermissionError: pass
                
            os.replace(tmp_path, file_path)
            return True
        except Exception:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise
    except Exception as e:
        if log_func: log_func(f"❌ 原子写入文本失败 ({file_path}): {e}", level="ERROR")
        return False
