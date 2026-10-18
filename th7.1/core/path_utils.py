from __future__ import annotations
import os
import re
from pathlib import Path
from typing import List, Tuple
from .paths import NOVEL_PROJECTS_DIR

_ILLEGAL_WIN_CHARS_PATTERN = re.compile(r'[\/\\\:\*\?\"\<\>\|]')
_MULTIPLE_SPACES_PATTERN = re.compile(r'\s+')

def sanitize_path(name: str, *, max_len: int = 200) -> str:
    """将任意字符串转化为安全的文件或目录名称。"""
    if not isinstance(name, str):
        raise TypeError(f"name 必须是 str，实际为 {type(name)!r}")
    sanitized = _ILLEGAL_WIN_CHARS_PATTERN.sub('_', name)
    sanitized = _MULTIPLE_SPACES_PATTERN.sub(' ', sanitized).strip()
    if len(sanitized) > max_len:
        sanitized = sanitized[:max_len].rstrip()
    return sanitized or "unnamed"

def safe_join(base: str | os.PathLike, *paths: str) -> str:
    """在 base 下安全地拼接若干子路径，防止路径穿越。"""
    base_path = Path(base).expanduser().resolve()
    if not base_path.is_absolute():
        raise ValueError(f"base 必须是绝对路径，实际为 {base!r}")
    safe_components: List[str] = [sanitize_path(p) for p in paths]
    joined_path = base_path.joinpath(*safe_components).resolve()
    try:
        joined_path.relative_to(base_path)
    except Exception:
        raise ValueError(f"拼接后路径超出基目录范围！base={base_path}, result={joined_path}")
    return str(joined_path)

def ensure_dir(path: str | os.PathLike, *, exist_ok: bool = True) -> str:
    """确保 path 所指向的目录存在（递归创建），并返回其字符串路径。"""
    p = Path(path).expanduser().resolve()
    p.mkdir(parents=True, exist_ok=exist_ok)
    return str(p)

def read_text_file(filepath: str | os.PathLike, *, encoding: str = "utf-8", default: str = "") -> str:
    """读取文本文件，统一处理异常。"""
    try:
        with open(filepath, "r", encoding=encoding) as f:
            return f.read()
    except Exception:
        return default

def write_text_file(filepath: str | os.PathLike, content: str, *, encoding: str = "utf-8", atomic: bool = True) -> Tuple[bool, str]:
    """将 content 写入 filepath，提供原子写入选项。"""
    path = Path(filepath).expanduser().resolve()
    try:
        if atomic:
            tmp_path = path.with_suffix(path.suffix + ".tmp")
            with open(tmp_path, "w", encoding=encoding) as f:
                f.write(content)
            tmp_path.replace(path)
        else:
            with open(path, "w", encoding=encoding) as f:
                f.write(content)
        return True, str(path)
    except Exception as e:
        return False, str(e)

def get_project_dir(project_id: str) -> str:
    """获取指定 project_id 的项目根目录绝对路径。"""
    if not isinstance(project_id, str) or not project_id:
        raise ValueError("project_id 必须为非空字符串")
    project_path = safe_join(NOVEL_PROJECTS_DIR, project_id)
    ensure_dir(project_path)
    return project_path

def get_overall_outline_dir(project_id: str) -> str:
    """返回 novel_projects/<project_id>/01_大纲/overall/ 目录的绝对路径。"""
    if not isinstance(project_id, str) or not project_id:
        raise ValueError("project_id 必须为非空字符串")
    overall_path = safe_join(NOVEL_PROJECTS_DIR, project_id, "01_大纲", "overall")
    ensure_dir(overall_path)
    return overall_path

def get_chapter_outline_dir(project_id: str, chapter_index: int) -> str:
    """获取指定 project_id 的第 chapter_index 章节大纲目录路径。"""
    if not isinstance(project_id, str) or not project_id:
        raise ValueError("project_id 必须为非空字符串")
    if not isinstance(chapter_index, int) or chapter_index <= 0:
        raise ValueError("chapter_index 必须为大于 0 的整数")
    chapter_dir_name = f"chapter_{chapter_index:02d}"
    chapter_path = safe_join(NOVEL_PROJECTS_DIR, project_id, "01_大纲", chapter_dir_name)
    ensure_dir(chapter_path)
    return chapter_path

__all__ = [
    "sanitize_path",
    "safe_join",
    "ensure_dir",
    "read_text_file",
    "write_text_file",
    "get_project_dir",
    "get_overall_outline_dir",
    "get_chapter_outline_dir",
]
