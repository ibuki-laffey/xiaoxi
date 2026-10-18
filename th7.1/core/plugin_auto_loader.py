# core/plugin_auto_loader.py
# ==================== AI 插件自动加载器 ====================
import os
import sys
import importlib
import importlib.util
import hashlib
import json
import shutil
from datetime import datetime

from .config import PLUGINS_DIR, KNOWLEDGE_BASE_DIR

from core.prompt_builder import get_prompt_builder

class PluginAutoLoader:
    """
    AI 插件自动加载器
    扫描、加载、验证 AI 自主创建的插件
    支持插件自我修复和更新
    """
    
    def __init__(self, log_callback=None, plugin_manager=None):
        self.log_callback = log_callback
        self.plugin_manager = plugin_manager
        
        self.auto_plugins_dir = os.path.join(PLUGINS_DIR, "auto")
        self.plugin_registry_file = os.path.join(KNOWLEDGE_BASE_DIR, "auto_plugins_registry.json")
        self.plugin_errors_file = os.path.join(KNOWLEDGE_BASE_DIR, "auto_plugin_errors.json")
        self.backups_dir = os.path.join(KNOWLEDGE_BASE_DIR, "backups", "plugins")
        
        os.makedirs(self.auto_plugins_dir, exist_ok=True)
        os.makedirs(self.backups_dir, exist_ok=True)
        os.makedirs(KNOWLEDGE_BASE_DIR, exist_ok=True)
        
        self._init_registry()
        self._log("✅ AI 插件自动加载器已初始化")
    
    # -----------------------------------------------------------------
    # 日志 & 初始化
    # -----------------------------------------------------------------
    def _log(self, msg):
        if self.log_callback:
            self.log_callback(msg)
    
    def _init_registry(self):
        """初始化插件注册表"""
        if not os.path.exists(self.plugin_registry_file):
            with open(self.plugin_registry_file, 'w', encoding='utf-8') as f:
                json.dump({"plugins": [], "last_scan": None, "total_loaded": 0}, f, indent=2, ensure_ascii=False)
        
        if not os.path.exists(self.plugin_errors_file):
            with open(self.plugin_errors_file, 'w', encoding='utf-8') as f:
                json.dump({"errors": [], "auto_fixed": []}, f, indent=2, ensure_ascii=False)
    
    def _load_registry(self):
        try:
            with open(self.plugin_registry_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {"plugins": [], "last_scan": None, "total_loaded": 0}
    
    def _save_registry(self, registry):
        with open(self.plugin_registry_file, 'w', encoding='utf-8') as f:
            json.dump(registry, f, indent=2, ensure_ascii=False)
    
    def _load_errors(self):
        try:
            with open(self.plugin_errors_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {"errors": [], "auto_fixed": []}
    
    def _save_errors(self, errors_data):
        with open(self.plugin_errors_file, 'w', encoding='utf-8') as f:
            json.dump(errors_data, f, indent=2, ensure_ascii=False)
    
    # -----------------------------------------------------------------
    # 主扫描&加载入口
    # -----------------------------------------------------------------
    def scan_and_load_plugins(self):
        """扫描并加载所有 AI 自创插件"""
        self._log("🔍 开始扫描 AI 自创插件...")
        registry = self._load_registry()
        loaded_count = 0
        error_count = 0
        
        if os.path.exists(self.auto_plugins_dir):
            for filename in os.listdir(self.auto_plugins_dir):
                if filename.endswith('.py') and not filename.startswith('_'):
                    plugin_path = os.path.join(self.auto_plugins_dir, filename)
                    try:
                        validation = self._validate_plugin(plugin_path)
                        if validation["success"]:
                            load_result = self._load_plugin(plugin_path, filename)
                            if load_result["success"]:
                                loaded_count += 1
                                self._register_plugin(filename, validation, load_result)
                            else:
                                error_count += 1
                                self._record_error(filename, load_result["error"])
                        else:
                            error_count += 1
                            self._record_error(filename, validation["error"])
                            # 尝试自动修复
                            fix_result = self._auto_fix_plugin(plugin_path, validation["error"])
                            if fix_result["success"]:
                                self._log(f"🔧 插件已自动修复：{filename}")
                    except Exception as e:
                        error_count += 1
                        self._record_error(filename, str(e))
        
        registry["last_scan"] = datetime.now().isoformat()
        registry["total_loaded"] = loaded_count
        self._save_registry(registry)
        self._log(f"📊 插件扫描完成：加载 {loaded_count} 个，失败 {error_count} 个")
        return {"success": True, "loaded": loaded_count, "errors": error_count}
    
    # -----------------------------------------------------------------
    # 插件验证
    # -----------------------------------------------------------------
    def _validate_plugin(self, plugin_path):
        """验证插件文件是否有效"""
        result = {"success": False, "errors": [], "warnings": []}
        if not os.path.exists(plugin_path):
            return result
        if os.path.getsize(plugin_path) == 0:
            result["errors"].append("文件为空")
            return result
        try:
            with open(plugin_path, 'r', encoding='utf-8') as f:
                code = f.read()
            compile(code, plugin_path, 'exec')
            result["success"] = True
        except SyntaxError as e:
            result["errors"].append(f"语法错误：行{e.lineno} - {e.msg}")
        except Exception as e:
            result["errors"].append(f"读取失败：{e}")
        
        if result["success"]:
            if 'from core.plugin_manager import Plugin' not in code:
                result["warnings"].append("缺少 Plugin 导入")
            if 'class' not in code:
                result["errors"].append("未找到类定义")
                result["success"] = False
            if 'def execute' not in code:
                result["warnings"].append("未找到 execute 方法")
        return result
    
    # -----------------------------------------------------------------
    # 动态加载
    # -----------------------------------------------------------------
    def _load_plugin(self, plugin_path, filename):
        """动态导入并注册插件"""
        result = {"success": False, "module": None, "error": None}
        try:
            if self.auto_plugins_dir not in sys.path:
                sys.path.insert(0, self.auto_plugins_dir)
            module_name = filename[:-3]
            spec = importlib.util.spec_from_file_location(module_name, plugin_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            result["module"] = module
            result["success"] = True
            if self.plugin_manager:
                self._register_to_plugin_manager(module, filename)
        except Exception as e:
            result["error"] = str(e)
        return result
    
    def _register_to_plugin_manager(self, module, filename):
        """把插件实例注入 PluginManager"""
        try:
            for name in dir(module):
                obj = getattr(module, name)
                if isinstance(obj, type) and name.endswith('Plugin') and name != 'Plugin':
                    plugin_instance = obj(log_callback=self.log_callback)
                    self.plugin_manager.plugins[filename[:-3]] = plugin_instance
                    for cap in plugin_instance.get_capabilities():
                        self.plugin_manager.plugin_registry[cap.get('action', '')] = plugin_instance
                    self._log(f"🔌 注册插件能力：{filename[:-3]} - {cap.get('action', '')}")
        except Exception as e:
            self._log(f"❌ 注册插件失败：{e}")
    
    # -----------------------------------------------------------------
    # 注册信息写回 registry
    # -----------------------------------------------------------------
    def _register_plugin(self, filename, validation, load_result):
        registry = self._load_registry()
        existing = next((p for p in registry["plugins"] if p["filename"] == filename), None)
        plugin_info = {
            "filename": filename,
            "path": os.path.join(self.auto_plugins_dir, filename),
            "hash": self._calculate_file_hash(os.path.join(self.auto_plugins_dir, filename)),
            "loaded_at": datetime.now().isoformat(),
            "validation": validation,
            "usage_count": existing["usage_count"] + 1 if existing else 0,
            "last_used": datetime.now().isoformat(),
            "is_active": True
        }
        if existing:
            idx = registry["plugins"].index(existing)
            registry["plugins"][idx] = plugin_info
        else:
            registry["plugins"].append(plugin_info)
        self._save_registry(registry)
    
    def _calculate_file_hash(self, filepath):
        hasher = hashlib.md5()
        with open(filepath, 'rb') as f:
            buf = f.read()
            hasher.update(buf)
        return hasher.hexdigest()
    
    # -----------------------------------------------------------------
    # 错误记录与自动修复
    # -----------------------------------------------------------------
    def _record_error(self, filename, error_msg):
        errors_data = self._load_errors()
        errors_data["errors"].append({
            "filename": filename,
            "error": error_msg,
            "timestamp": datetime.now().isoformat(),
            "fixed": False
        })
        errors_data["errors"] = errors_data["errors"][-100:]   # 保留最近 100 条
        self._save_errors(errors_data)
    
    def _auto_fix_plugin(self, plugin_path, error_msg):
        """尝试修复常见的 AI 代码错误"""
        result = {"success": False, "fixed": False, "method": None}
        backup_path = self._backup_plugin(plugin_path)
        try:
            with open(plugin_path, 'r', encoding='utf-8') as f:
                code = f.read()
            fixed = False
            if '缺少 Plugin 导入' in error_msg:
                code = self._add_missing_imports(code)
                fixed = True
                result["method"] = "add_imports"
            elif 'IndentationError' in error_msg:
                code = self._fix_indentation(code)
                fixed = True
                result["method"] = "fix_indentation"
            elif 'SyntaxError' in error_msg:
                code = self._fix_syntax(code)
                fixed = True
                result["method"] = "fix_syntax"
            
            if fixed:
                with open(plugin_path, 'w', encoding='utf-8') as f:
                    f.write(code)
                if self._validate_plugin(plugin_path)["success"]:
                    result["success"] = True
                    result["fixed"] = True
                    errors_data = self._load_errors()
                    errors_data["auto_fixed"].append({
                        "filename": os.path.basename(plugin_path),
                        "original_error": error_msg,
                        "fix_method": result["method"],
                        "timestamp": datetime.now().isoformat()
                    })
                    self._save_errors(errors_data)
                else:
                    self._restore_plugin(plugin_path, backup_path)
            else:
                self._restore_plugin(plugin_path, backup_path)
        except Exception as e:
            self._restore_plugin(plugin_path, backup_path)
        return result
    
    def _backup_plugin(self, plugin_path):
        filename = os.path.basename(plugin_path)
        backup_name = f"{filename}.{datetime.now().strftime('%Y%m%d_%H%M%S')}.bak"
        backup_path = os.path.join(self.backups_dir, backup_name)
        shutil.copy2(plugin_path, backup_path)
        return backup_path
    
    def _restore_plugin(self, plugin_path, backup_path):
        if os.path.exists(backup_path):
            shutil.copy2(backup_path, plugin_path)
            return True
        return False
    
    # -----------------------------------------------------------------
    # 常用自动修复小工具
    # -----------------------------------------------------------------
    def _add_missing_imports(self, code):
        required = [
            "import sys",
            "import os",
            "from core.plugin_manager import Plugin"
        ]
        for imp in required:
            if imp not in code:
                code = imp + "\n" + code
        path_setup = "\ncurrent_dir = os.path.dirname(os.path.abspath(__file__))\nparent_dir = os.path.dirname(current_dir)\nsys.path.insert(0, parent_dir)\n"
        if 'sys.path.insert' not in code:
            lines = code.split('\n')
            code = "\n".join(lines[:1] + [path_setup] + lines[1:])
        return code
    
    def _fix_indentation(self, code):
        lines = code.split('\n')
        fixed_lines = []
        indent_level = 0
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                fixed_lines.append(line)
                continue
            if stripped.startswith(('else:', 'elif', 'except:', 'finally:')):
                indent_level = max(0, indent_level - 1)
            fixed_lines.append('    ' * indent_level + stripped)
            if stripped.endswith(':') and not stripped.startswith(('import', 'from', 'class', 'def')):
                indent_level += 1
        return '\n'.join(fixed_lines)
    
    def _fix_syntax(self, code):
        # 补全括号
        if code.count('(') != code.count(')'):
            diff = code.count('(') - code.count(')')
            code += ')' * diff if diff > 0 else '(' * (-diff) + code
        # 补全引号
        if code.count('"') % 2 != 0:
            code += '"'
        if code.count("'") % 2 != 0:
            code += "'"
        # 把单行缺失的冒号转换为块
        return code.replace(': ', ':\n    ')
    
    # -----------------------------------------------------------------
    # 统计信息
    # -----------------------------------------------------------------
    def get_plugin_list(self):
        registry = self._load_registry()
        return registry["plugins"]
    
    def unload_plugin(self, filename):
        registry = self._load_registry()
        registry["plugins"] = [p for p in registry["plugins"] if p["filename"] != filename]
        self._save_registry(registry)
        if self.plugin_manager:
            plugin_key = filename[:-3]
            if plugin_key in self.plugin_manager.plugins:
                del self.plugin_manager.plugins[plugin_key]
            # 删除对应的 action 映射
            for action, plugin in list(self.plugin_manager.plugin_registry.items()):
                if plugin == self.plugin_manager.plugins.get(plugin_key):
                    del self.plugin_manager.plugin_registry[action]
        return {"success": True}
    
    def deactivate_plugin(self, filename):
        registry = self._load_registry()
        for p in registry["plugins"]:
            if p["filename"] == filename:
                p["is_active"] = False
        self._save_registry(registry)
        return {"success": True}
    
    def activate_plugin(self, filename):
        registry = self._load_registry()
        for p in registry["plugins"]:
            if p["filename"] == filename:
                p["is_active"] = True
        self._save_registry(registry)
        return {"success": True}
    
    def delete_plugin(self, filename):
        plugin_path = os.path.join(self.auto_plugins_dir, filename)
        if not os.path.exists(plugin_path):
            return {"success": False}
        self._backup_plugin(plugin_path)
        self.unload_plugin(filename)
        os.remove(plugin_path)
        return {"success": True}
    
    def update_plugin(self, filename, new_code):
        plugin_path = os.path.join(self.auto_plugins_dir, filename)
        if not os.path.exists(plugin_path):
            return {"success": False}
        backup_path = self._backup_plugin(plugin_path)
        try:
            with open(plugin_path, 'w', encoding='utf-8') as f:
                f.write(new_code)
            if self._validate_plugin(plugin_path)["success"]:
                self.unload_plugin(filename)
                self._load_plugin(plugin_path, filename)
                return {"success": True}
            else:
                self._restore_plugin(plugin_path, backup_path)
                return {"success": False, "message": "验证失败"}
        except Exception as e:
            self._restore_plugin(plugin_path, backup_path)
            return {"success": False, "error": str(e)}
    
    def get_plugin_errors(self, filename=None):
        errors_data = self._load_errors()
        return [e for e in errors_data["errors"] if not filename or e["filename"] == filename]
    
    def get_auto_fixes(self):
        return self._load_errors().get("auto_fixed", [])
    
    def get_statistics(self):
        registry = self._load_registry()
        plugins = registry["plugins"]
        errors_data = self._load_errors()
        return {
            "total_plugins": len(plugins),
            "active_plugins": sum(1 for p in plugins if p.get("is_active", True)),
            "total_usage": sum(p.get("usage_count", 0) for p in plugins),
            "total_errors": len(errors_data["errors"]),
            "auto_fixed": len(errors_data["auto_fixed"]),
            "last_scan": registry.get("last_scan")
        }
