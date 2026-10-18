# core/plugin_registry.py
# ==============================================================
# PluginRegistry – 插件元数据持久化（JSON）实现
# 负责统一保存、查询、更新插件的基本信息、加载状态、
# 是否激活以及备份历史等。
# ==============================================================
"""
JSON 文件结构示例::

    {
        "plugins": [
            {
                "name": "my_plugin",
                "file": ".../my_plugin.py",
                "version": "0.0.1",
                "load_status": "pending",   # loaded / pending / failed / inactive
                "error": null,
                "active": true,
                "backup_history": [
                    {
                        "path": ".../backups/my_plugin.py.20240501_123456.bak",
                        "timestamp": "2024-05-01T12:34:56"
                    }
                ]
            },
            …
        ]
    }

所有写入都会立即落盘，读取时若文件不存在或损坏则
回退为空结构，防止因异常导致系统崩溃。
"""

import os
import json
from datetime import datetime
from typing import List, Dict, Optional


class PluginRegistry:
    """
    负责管理插件的注册信息、加载状态、激活状态以及备份历史等。

    主要方法：
        - list(active_only=True) → 获取插件列表
        - get(name) → 单条记录
        - update(name, fields) → 更新（若不存在则创建）
        - set_status(name, status, error=None) → 设置 load_status
        - add_backup(name, backup_path) → 追加备份记录
    """

    # -----------------------------------------------------------------
    # 初始化 – 加载已有文件或创建空文件
    # -----------------------------------------------------------------
    def __init__(self, registry_path: str):
        """
        参数
        ----------
        registry_path : str
            JSON 注册表文件完整路径（可以是相对路径）。
        """
        self.path = os.path.abspath(registry_path)

        # 确保所在目录存在
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

        if not os.path.isfile(self.path):
            # 首次创建空结构
            self._data = {"plugins": []}
            self._write()
        else:
            self._load()

    # -----------------------------------------------------------------
    # 私有：读取 / 写入 JSON
    # -----------------------------------------------------------------
    def _load(self) -> None:
        """读取磁盘中的 JSON 数据，若读取异常则回退为空结构。"""
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
        except Exception:
            # 文件损坏或读取错误时安全回滚
            self._data = {"plugins": []}
            self._write()

    def _write(self) -> None:
        """把当前内存数据写回磁盘，保持可读的缩进格式。"""
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)

    # -----------------------------------------------------------------
    # 公共 API – 列表、获取、更新、状态、备份
    # -----------------------------------------------------------------
    def list(self, active_only: bool = True) -> List[Dict]:
        """
        返回插件信息列表。

        参数
        ----------
        active_only : bool, optional
            True → 只返回 ``active==True`` 的插件（默认）。
            False → 返回所有插件（包括已停用/失效的）。

        Returns
        -------
        List[Dict]
            已按 ``name`` 排序的插件信息字典列表。
        """
        plugins = self._data.get("plugins", [])
        if active_only:
            plugins = [p for p in plugins if p.get("active", True)]

        plugins.sort(key=lambda p: p.get("name", ""))
        return plugins

    def get(self, name: str) -> Optional[Dict]:
        """
        按插件名称查询单条记录。

        Returns
        -------
        dict or None
            若找到返回对应字典，未找到返回 ``None``。
        """
        for p in self._data.get("plugins", []):
            if p.get("name") == name:
                return p
        return None

    # -----------------------------------------------------------------
    # 辅助：查找插件在列表中的下标
    # -----------------------------------------------------------------
    def _find_index(self, name: str) -> Optional[int]:
        """返回插件在 ``self._data["plugins"]`` 中的下标，未找到返回 ``None``。"""
        for idx, p in enumerate(self._data.get("plugins", [])):
            if p.get("name") == name:
                return idx
        return None

    # -----------------------------------------------------------------
    # 更新插件信息（若不存在则自动创建）
    # -----------------------------------------------------------------
    def update(self, name: str, fields: Dict) -> None:
        """
        更新（或新建）插件记录。

        参数
        ----------
        name   : str
            插件唯一名称（通常与文件名保持一致）。
        fields : dict
            需要写入/覆盖的键值对。
        """
        idx = self._find_index(name)
        if idx is None:
            entry = {"name": name}
            entry.update(fields)
            self._data.setdefault("plugins", []).append(entry)
        else:
            self._data["plugins"][idx].update(fields)

        self._write()

    # -----------------------------------------------------------------
    # 设置插件加载状态
    # -----------------------------------------------------------------
    def set_status(self, name: str, status: str, error: Optional[str] = None) -> None:
        """
        设置插件的加载状态（``load_status``）以及错误信息。

        参数
        ----------
        name   : str
            插件名称。
        status : str
            新的 ``load_status``（如 ``loaded``、``pending``、``failed``、``inactive``）。
        error  : str|None, optional
            若提供则写入 ``error`` 字段，供 UI 展示错误细节。
        """
        idx = self._find_index(name)
        if idx is None:
            # 若记录不存在则创建最小条目
            entry = {
                "name": name,
                "load_status": status,
                "error": error,
                "active": True,
                "backup_history": [],
                "file": "",
                "version": "0.0.0",
            }
            self._data.setdefault("plugins", []).append(entry)
        else:
            self._data["plugins"][idx]["load_status"] = status
            if error is not None:
                self._data["plugins"][idx]["error"] = error

        self._write()

    # -----------------------------------------------------------------
    # 添加备份记录
    # -----------------------------------------------------------------
    def add_backup(self, name: str, backup_path: str) -> None:
        """
        为插件追加一次备份记录。

        参数
        ----------
        name        : str   插件名称
        backup_path : str   备份文件完整路径
        """
        timestamp = datetime.now().isoformat()
        backup_entry = {"path": backup_path, "timestamp": timestamp}

        idx = self._find_index(name)
        if idx is None:
            # 若插件记录尚未创建，创建最小记录并写入备份
            entry = {
                "name": name,
                "backup_history": [backup_entry],
                "active": True,
                "load_status": "pending",
                "error": None,
                "file": "",
                "version": "0.0.0",
            }
            self._data.setdefault("plugins", []).append(entry)
        else:
            hist = self._data["plugins"][idx].setdefault("backup_history", [])
            hist.append(backup_entry)

        self._write()
