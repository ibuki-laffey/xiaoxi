# core/plugin_manager.py
# ==================== 插件管理模块（懒加载版） ====================

import os
import sys
import importlib
import importlib.util
import inspect
from typing import Dict, Any, List

class Plugin:
    """
    插件基类
    所有插件必须继承此类
    """
    name = "未命名插件"
    description = "无描述"
    version = "1.0.0"
    author = "未知"

    def __init__(self, log_callback=None):
        self.log_callback = log_callback

    def _log(self, msg):
        if self.log_callback:
            self.log_callback(msg)

    def get_capabilities(self):
        """返回插件提供的所有动作及其描述"""
        return []

    def execute(self, action, params):
        """执行具体动作，子类必须实现"""
        raise NotImplementedError("插件必须实现 execute 方法")


class PluginManager:
    """
    插件管理器（懒加载）

    主要特性
    ----------
    1. 在初始化阶段仅 **扫描文件**，不直接 import 插件，避免因缺失依赖导致整个系统启动失败。
    2. 当真正需要插件功能时（读取 capabilities 或执行动作），才进行 **动态 import**。
    3. 失败的插件会被记录在 ``self.failed_plugins``，后续仍可手动重新加载。
    4. 所有插件实例均 **缓存**（首次加载后复用），提升后续调用性能。
    5. 自动为每个 capability 注入 ``plugin`` 与 ``version`` 字段，统一日志格式。
    """

    def __init__(self, log_callback=None):
        self.log_callback = log_callback
        self.plugin_instances: Dict[str, Plugin] = {}          # plugin_name -> 实例
        self.plugin_files: Dict[str, str] = {}                # plugin_name -> 文件路径
        self.action_registry: Dict[str, str] = {}             # action -> plugin_name
        self.failed_plugins: List[str] = []                   # 加载失败的插件文件名

        # ------------------- 读取插件目录 -------------------
        try:
            from .config import PLUGINS_DIR
            self.plugin_dir = PLUGINS_DIR
        except Exception:
            # 兼容旧版：若 config 中未定义 PLUGINS_DIR，使用相对路径 fallback
            self.plugin_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "plugins"))

        os.makedirs(self.plugin_dir, exist_ok=True)
        self._create_init_file()

        # 仅扫描插件文件，建立 file <-> name 映射，**不** import
        self._scan_plugins()

        # 立即构建一次 action->plugin 映射（懒加载时会尝试 import）
        self._build_action_registry()

    # -----------------------------------------------------------------
    # 目录 & 初始化
    # -----------------------------------------------------------------
    def _create_init_file(self):
        """确保插件目录是一个合法的 Python 包"""
        init_path = os.path.join(self.plugin_dir, "__init__.py")
        if not os.path.exists(init_path):
            with open(init_path, "w", encoding="utf-8") as f:
                f.write("# 插件目录入口文件\n")

    def _scan_plugins(self):
        """遍历插件目录，只记录文件路径，不进行 import"""
        for filename in os.listdir(self.plugin_dir):
            if filename.endswith(".py") and filename != "__init__.py":
                plugin_name = filename[:-3]        # 去掉 .py
                file_path = os.path.join(self.plugin_dir, filename)
                self.plugin_files[plugin_name] = file_path

    # -----------------------------------------------------------------
    # 懒加载实现
    # -----------------------------------------------------------------
    def _load_plugin(self, plugin_name: str) -> Plugin:
        """
        动态 import 插件文件并实例化其 ``Plugin`` 子类。

        - 若已经缓存则直接返回实例；
        - 若 import 失败，会把插件名加入 ``failed_plugins``，并返回 ``None``。
        """
        if plugin_name in self.plugin_instances:
            return self.plugin_instances[plugin_name]

        file_path = self.plugin_files.get(plugin_name)
        if not file_path:
            self._log(f"⚠️ 插件文件未找到：{plugin_name}")
            return None

        try:
            # 使用 importlib 读取文件，避免依赖 sys.path 中的同名模块冲突
            spec = importlib.util.spec_from_file_location(plugin_name, file_path)
            module = importlib.util.module_from_spec(spec)
            loader = spec.loader
            if loader is None:
                raise ImportError("loader 为 None")
            loader.exec_module(module)

            # 在模块中寻找 ``Plugin`` 子类（排除基类本身）
            plugin_cls = None
            for _, obj in inspect.getmembers(module, inspect.isclass):
                if issubclass(obj, Plugin) and obj is not Plugin:
                    plugin_cls = obj
                    break

            if plugin_cls is None:
                raise ImportError("未在模块中找到 Plugin 子类")

            instance = plugin_cls(log_callback=self._log)
            self.plugin_instances[plugin_name] = instance
            self._log(f"✅ 懒加载成功：{plugin_name}")
            return instance

        except Exception as e:
            self._log(f"❌ 懒加载插件 {plugin_name} 失败：{e}")
            if plugin_name not in self.failed_plugins:
                self.failed_plugins.append(plugin_name)
            return None

    # -----------------------------------------------------------------
    # Capability & Action 注册（懒加载）
    # -----------------------------------------------------------------
    def _build_action_registry(self):
        """
        为每个插件读取 capabilities 并建立 ``action -> plugin_name`` 映射。

        - 如果插件加载失败，则跳过该插件的 capability；
        - 同一个 action 若出现冲突，后加载的插件会覆盖前者（同样记录日志）。
        """
        for plugin_name in list(self.plugin_files.keys()):
            plugin = self._load_plugin(plugin_name)
            if not plugin:
                continue
            try:
                caps = plugin.get_capabilities()
                for cap in caps:
                    action = cap.get('action')
                    if action:
                        # 注入插件信息，保持统一
                        cap['plugin'] = plugin_name
                        cap['version'] = plugin.version
                        self.action_registry[action] = plugin_name
            except Exception as e:
                self._log(f"⚠️ 获取插件 {plugin_name} capabilities 失败：{e}")

    # -----------------------------------------------------------------
    # 公共 API
    # -----------------------------------------------------------------
    def _log(self, msg):
        if self.log_callback:
            self.log_callback(msg)

    def get_all_capabilities(self) -> List[Dict[str, Any]]:
        """
        返回系统中 **所有** 已加载插件的 capability 列表。
        这里会再次尝试加载在初始化阶段未成功的插件（一次性补救）。
        """
        # 再次尝试加载之前失败的插件
        for failed in list(self.failed_plugins):
            self._load_plugin(failed)

        caps = []
        for plugin_name, instance in self.plugin_instances.items():
            try:
                plugin_caps = instance.get_capabilities()
                for cap in plugin_caps:
                    cap['plugin'] = plugin_name
                    cap['version'] = instance.version
                caps.extend(plugin_caps)
            except Exception as e:
                self._log(f"⚠️ 读取插件 {plugin_name} capabilities 失败：{e}")
        return caps

    def execute_action(self, action: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        根据动作名称调用对应插件的 ``execute``。

        - 若 ``action`` 不在 ``action_registry``，尝试一次性 **重新扫描** 所有插件，
          可能是新加入的插件导致的未注册情况。
        """
        if action not in self.action_registry:
            # 重新扫描并尝试加载
            self._log(f"⚠️ 动作 '{action}' 未注册，重新扫描插件目录")
            self._scan_plugins()
            self._build_action_registry()

        plugin_name = self.action_registry.get(action)
        if not plugin_name:
            return {"success": False, "message": f"未知动作：{action}"}

        plugin = self._load_plugin(plugin_name)
        if not plugin:
            return {"success": False, "message": f"插件加载失败：{plugin_name}"}

        try:
            return plugin.execute(action, params)
        except Exception as e:
            self._log(f"⚠️ 插件 {plugin_name} 执行动作 {action} 时异常：{e}")
            return {"success": False, "message": f"插件执行异常：{e}"}

    def get_plugin_list(self) -> List[Dict[str, Any]]:
        """获取已成功加载的插件基本信息（name、version、author、description）"""
        lst = []
        for name, instance in self.plugin_instances.items():
            lst.append({
                "name": instance.name,
                "version": instance.version,
                "author": instance.author,
                "description": instance.description,
            })
        return lst
