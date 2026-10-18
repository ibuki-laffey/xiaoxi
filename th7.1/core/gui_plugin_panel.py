# core/gui_plugin_panel.py
# --------------------------------------------------------------
# 二级插件面板（左侧列表 + 右侧详情编辑） – 只依赖 EventBus
# --------------------------------------------------------------

import os
import shutil
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

# ----------------------------------------------------------------------
# 兼容加载 PluginRegistry
# ----------------------------------------------------------------------
# 旧版本曾经把 PluginRegistry 放在 ``core.plugin_registry`` 中，
# 但在后续重构里移到了 ``core.plugin_manager``。为了兼容两种
# 可能的路径，这里使用 ``try / except`` 进行回退。
try:
    # 1. 先尝试原来的路径（如果项目仍保留 legacy 文件则使用它）
    from .plugin_registry import PluginRegistry          # pragma: no cover
except ImportError:  # pragma: no cover
    # 2. 迁移后的正式实现位于 plugin_manager
    from .plugin_manager import PluginRegistry

from .plugin_auto_loader import PluginAutoLoader
from .event_bus import EventBus


class PluginPanel(tk.Frame):
    """在主 GUI 中展示插件列表并支持实时编辑/回滚/禁用"""
    def __init__(self, master, registry_path, loader: PluginAutoLoader, log_callback=None):
        super().__init__(master)
        self.log = log_callback
        self.registry = PluginRegistry(registry_path)
        self.loader = loader

        # UI 组件
        self.tree = ttk.Treeview(self, columns=("status", "version"))
        self.tree.heading("#0", text="插件名称")
        self.tree.heading("status", text="状态")
        self.tree.heading("version", text="版本")
        self.tree.column("#0", width=200, anchor="w")
        self.tree.column("status", width=80, anchor="center")
        self.tree.column("version", width=80, anchor="center")
        self.tree.pack(side="left", fill="y", expand=False)

        # 右侧详情区域（Text + 按钮）
        right_frame = ttk.Frame(self)
        right_frame.pack(side="right", fill="both", expand=True)

        self.detail_text = tk.Text(right_frame, wrap="none")
        self.detail_text.pack(fill="both", expand=True)

        btn_frame = ttk.Frame(right_frame)
        btn_frame.pack(fill="x")
        ttk.Button(btn_frame, text="保存 & 刷新", command=self._on_save).pack(side="left")
        ttk.Button(btn_frame, text="回滚", command=self._on_rollback).pack(side="left")
        ttk.Button(btn_frame, text="禁用/启用", command=self._on_toggle_active).pack(side="left")

        # 绑定事件
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        EventBus.subscribe("plugin_changed", self._on_external_change)
        EventBus.subscribe("plugin_scan_finished", self._refresh_tree)

        # 首次加载
        self._refresh_tree()

    # -----------------------------------------------------------------
    # 刷新插件列表（在加载/状态变更后调用）
    # -----------------------------------------------------------------
    def _refresh_tree(self, **kwargs):
        """清空并重新绘制 Treeview，使用最新的 Registry 数据"""
        for item in self.tree.get_children():
            self.tree.delete(item)

        for plugin in self.registry.list(active_only=False):
            name = plugin["name"]
            status = plugin.get("load_status", "pending")
            version = plugin.get("version", "0.0.0")
            icon = "✅" if status == "loaded" else ("⚠️" if status == "failed" else "⏳")
            self.tree.insert("", "end", iid=name, text=name, values=(icon + " " + status, version))

    # -----------------------------------------------------------------
    # 当用户在左侧树中选择插件时，加载源码到右侧 Text
    # -----------------------------------------------------------------
    def _on_select(self, event):
        selected = self.tree.selection()
        if not selected:
            return
        name = selected[0]
        info = self.registry.get(name)
        if not info:
            return
        file_path = info["file"]
        if not os.path.isfile(file_path):
            self.detail_text.delete("1.0", tk.END)
            self.detail_text.insert(tk.END, f"# 文件不存在: {file_path}")
            return
        with open(file_path, "r", encoding="utf-8") as f:
            src = f.read()
        self.detail_text.delete("1.0", tk.END)
        self.detail_text.insert(tk.END, src)
        # 记录当前编辑的插件名（保存时使用）
        self._current_editing = name

    # -----------------------------------------------------------------
    # 保存编辑内容 → 调用 PluginAutoLoader.validate_and_reload
    # -----------------------------------------------------------------
    def _on_save(self):
        if not hasattr(self, "_current_editing"):
            messagebox.showwarning("提示", "未选中任何插件")
            return
        name = self._current_editing
        new_code = self.detail_text.get("1.0", tk.END)

        # 写回磁盘
        info = self.registry.get(name)
        if not info:
            messagebox.showerror("错误", f"插件记录丢失：{name}")
            return
        file_path = info["file"]
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(new_code)
        except Exception as e:
            messagebox.showerror("写入错误", str(e))
            return

        # 调用自动校验与重新加载
        result = self.loader.validate_and_reload(name)
        if result["success"]:
            messagebox.showinfo("成功", result["message"])
        else:
            messagebox.showerror("修复失败", result["message"])

    # -----------------------------------------------------------------
    # 回滚到最近一次备份
    # -----------------------------------------------------------------
    def _on_rollback(self):
        if not hasattr(self, "_current_editing"):
            messagebox.showwarning("提示", "未选中任何插件")
            return
        name = self._current_editing
        info = self.registry.get(name)
        backups = info.get("backup_history", [])
        if not backups:
            messagebox.showinfo("提示", "暂无备份可回滚")
            return
        # 取最近的备份
        latest = sorted(backups, key=lambda b: b["timestamp"], reverse=True)[0]
        backup_path = latest["path"]
        try:
            shutil.copy2(backup_path, info["file"])
            messagebox.showinfo("已回滚", f"已恢复至备份 {os.path.basename(backup_path)}")
            # 重新加载
            self.loader.validate_and_reload(name)
            self._refresh_tree()
        except Exception as e:
            messagebox.showerror("回滚错误", str(e))

    # -----------------------------------------------------------------
    # 切换插件激活状态（active <-> inactive）
    # -----------------------------------------------------------------
    def _on_toggle_active(self):
        if not hasattr(self, "_current_editing"):
            messagebox.showwarning("提示", "未选中任何插件")
            return
        name = self._current_editing
        info = self.registry.get(name)
        new_state = not info.get("active", True)
        self.registry.update(name, {"active": new_state})
        self.registry.set_status(name, "pending" if new_state else "inactive")
        EventBus.publish("plugin_changed", name=name, status="pending" if new_state else "inactive")
        self._refresh_tree()
        msg = "已启用" if new_state else "已禁用"
        messagebox.showinfo("状态切换", f"{name} {msg}")

    # -----------------------------------------------------------------
    # 其他模块（如 PluginAutoLoader）发布插件状态变化时刷新 UI
    # -----------------------------------------------------------------
    def _on_external_change(self, name: str, status: str):
        # 直接刷新对应节点（更高效），这里简单直接全刷新
        self._refresh_tree()
