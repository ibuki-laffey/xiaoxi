# core/project_path_manager.py
# ==================== 统一项目目录映射服务 ====================
import os
import re
from typing import Dict, Optional
from . import config as config

class ProjectPathManager:
    """统一项目目录映射服务（推荐单例使用）。"""

    _prefix_cache: Dict[str, str] = {}

    @staticmethod
    def set_prefix_for_project(project_id: str, prefix: str) -> None:
        if not project_id or not isinstance(project_id, str):
            raise ValueError("project_id 必须为非空字符串")
        if prefix is None:
            prefix = ""
        ProjectPathManager._prefix_cache[project_id] = prefix.rstrip("_") + ("_" if prefix else "")

    @staticmethod
    def _format_prefix(project_id: str, **extra) -> str:
        if not getattr(config, "PROJECT_PREFIX_ENABLED", False):
            return ""

        fmt: str = getattr(config, "PROJECT_PREFIX_FORMAT", "{pid}")
        extra = dict(extra)
        extra.setdefault("pid", project_id)

        if project_id in ProjectPathManager._prefix_cache:
            return ProjectPathManager._prefix_cache[project_id]

        try:
            rendered = fmt.format(**extra)
        except KeyError:
            ProjectPathManager._log_warn(f"PROJECT_PREFIX_FORMAT 包含不存在的占位符，已回退。当前 format: {fmt!r}")
            rendered = project_id

        if "{pid}" in fmt:
            return rendered.rstrip("_")
        else:
            rendered = rendered.rstrip("_")
            return f"{rendered}_" if rendered else ""

    @staticmethod
    def resolve_project_dir(project_id: str, **extra) -> str:
        if not project_id or not isinstance(project_id, str):
            ProjectPathManager._log_warn(f"resolve_project_dir 接收到无效 project_id: {project_id!r}")
            return "invalid_project"

        prefix = ProjectPathManager._prefix_cache.get(project_id)
        if prefix is None:
            prefix = ProjectPathManager._format_prefix(project_id, **extra)

        return f"{prefix}{project_id}" if prefix else project_id

    @staticmethod
    def extract_pure_id(project_dir_name: str) -> str:
        if not project_dir_name or not isinstance(project_dir_name, str):
            return ""

        if not getattr(config, "PROJECT_PREFIX_ENABLED", False):
            return project_dir_name

        fmt: str = getattr(config, "PROJECT_PREFIX_FORMAT", "{pid}")
        if "{pid}" not in fmt:
            if "_" in project_dir_name:
                return project_dir_name.split("_")[-1]
            return project_dir_name

        match = re.search(r"([0-9a-fA-F]{8,32})$", project_dir_name)
        if match:
            return match.group(1)

        return project_dir_name

    @staticmethod
    def ensure_project_structure(project_id_or_path: str, **extra) -> str:
        """确保项目根目录存在并返回其绝对路径。"""
        if os.path.sep in project_id_or_path or os.path.isdir(project_id_or_path):
            project_dir = os.path.abspath(project_id_or_path)
        else:
            dir_name = ProjectPathManager.resolve_project_dir(project_id_or_path, **extra)
            base_dir = getattr(config, "NOVEL_PROJECTS_DIR", os.getcwd())
            project_dir = os.path.abspath(os.path.join(base_dir, dir_name))

        try:
            os.makedirs(project_dir, exist_ok=True)
        except Exception as exc:
            ProjectPathManager._log_warn(f"ensure_project_structure 创建目录失败: {project_dir} → {exc}")
            raise

        return project_dir

    @staticmethod
    def _log_warn(message: str) -> None:
        print(f"[ProjectPathManager][WARN] {message}")
