# core/outline_manager_patch.py
# ------------------------------------------------------------
# 给 OutlineManager 注入缺失的 ``config`` 引用
# ------------------------------------------------------------
import importlib
import sys

# 加载原始 OutlineManager 模块
_outline_mod = importlib.import_module('.outline_manager', __package__)

# 若模块全局未定义 ``config``，则手动注入
if not hasattr(_outline_mod, 'config'):
    # 导入项目统一配置（core.config）并挂载到模块全局
    _outline_mod.config = importlib.import_module('core.config')
