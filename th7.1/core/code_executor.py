import os
import re
import time
import json
from .config import CODE_SANDBOX_DIR
from .logger import action_logger

class CodeExecutor:
    def __init__(self, log_callback=None):
        self.log_callback = log_callback
        sandbox_dir = globals().get('CODE_SANDBOX_DIR')
        if not sandbox_dir:
            import tempfile
            sandbox_dir = os.path.join(tempfile.gettempdir(), "code_sandbox")
        os.makedirs(sandbox_dir, exist_ok=True)
        self._sandbox_dir = sandbox_dir
    
    def _log(self, msg: str, level: str = "INFO"):
        if self.log_callback:
            try: self.log_callback(msg, level=level)
            except TypeError: self.log_callback(msg)
    
    def _fix_ai_code(self, code):
        """修复 AI 生成代码中常见的缩进丢失、分号代替换行等问题。"""
        code = code.replace(';', '\n').replace('\\n', '\n').replace('\\t', '    ')
        lines = code.split('\n')
        fixed_lines = []
        indent_level = 0
        in_multiline_string = False
        
        for i, line in enumerate(lines):
            stripped = line.strip()
            triple_quote_count = stripped.count('"""') + stripped.count("'''")
            if triple_quote_count % 2 == 1: in_multiline_string = not in_multiline_string
            if in_multiline_string or not stripped or stripped.startswith('#'):
                fixed_lines.append(line)
                continue
            if stripped.startswith(('else:', 'elif', 'except:', 'finally:')):
                indent_level = max(0, indent_level - 1)
            fixed_lines.append('    ' * indent_level + stripped)
            if stripped.endswith(':'): indent_level += 1
            if i + 1 < len(lines):
                next_stripped = lines[i + 1].strip()
                if next_stripped.startswith(('else:', 'elif', 'except:', 'finally:')):
                    indent_level = max(0, indent_level - 1)
        
        code = '\n'.join(fixed_lines)
        for keyword in ['with', 'if', 'for', 'while', 'try', 'except', 'else', 'elif', 'def', 'class']:
            pattern = rf'({keyword}\s*.+?:)\s*(\S+)'
            code = re.sub(pattern, r'\1\n    \2', code)
        return '\n'.join(line.rstrip() for line in code.split('\n'))
    
    def execute(self, code, description="执行代码"):
        """执行 Python 代码（沙箱环境）。"""
        self._log(f"⚙️ 执行代码：{code[:300]}...")
        try:
            code = self._fix_ai_code(code)
            debug_file = os.path.join(self._sandbox_dir, f"debug_{int(time.time())}.py")
            try:
                with open(debug_file, 'w', encoding='utf-8') as f: f.write(code)
            except Exception: pass

            safe_modules = {}
            for mod_name in ('os', 'json', 'time', 'random', 'string', 'datetime', 'subprocess', 'shutil', 'pathlib'):
                try: safe_modules[mod_name] = __import__(mod_name)
                except Exception: safe_modules[mod_name] = None

            # 🌟 核心修复：从 __builtins__ 中剔除 exit 和 quit，防止 AI 生成的代码意外终止主程序
            safe_builtins = __builtins__.copy() if isinstance(__builtins__, dict) else vars(__builtins__).copy()
            safe_builtins.pop('exit', None)
            safe_builtins.pop('quit', None)

            exec_env = {
                'os': safe_modules.get('os'), 'json': safe_modules.get('json'),
                'time': safe_modules.get('time'), 'random': safe_modules.get('random'),
                'string': safe_modules.get('string'), 'datetime': safe_modules.get('datetime'),
                'subprocess': safe_modules.get('subprocess'), 'shutil': safe_modules.get('shutil'),
                'pathlib': safe_modules.get('pathlib'), 'open': open, 'print': print,
                '__builtins__': safe_builtins,
            }

            exec(code, exec_env, {})
            action_logger.log("CODE_EXECUTE", code[:500], "成功")
            return {"success": True, "message": f"✅ {description}"}
        except SyntaxError as e:
            return {"success": False, "message": f"语法错误：{e} (行 {e.lineno})"}
        except Exception as e:
            return {"success": False, "message": f"执行失败：{e}"}
