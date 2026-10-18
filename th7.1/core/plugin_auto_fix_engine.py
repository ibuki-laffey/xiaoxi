# core/plugin_auto_fix_engine.py
# ============================================================== 
# 自动修复引擎（PluginAutoFixEngine）  
# 负责：  
#   • 备份原插件文件  
#   • 根据异常信息定位错误类型  
#   • 逐步执行一系列可配置的“修复步骤”  
#   • 每一步后重新编译验证，成功则返回；失败则回滚并尝试下一步  
#   • 最终返回统一结构体 {"success":bool,"fixed":bool,"method":str,"error":str}  
# ==============================================================

import os
import re
import ast
import json
import shutil
import tokenize
import traceback
from io import BytesIO
from datetime import datetime
from typing import Dict, Any, List, Tuple, Optional

# ----------------------------------------------------------------------
# 兼容加载 PluginRegistry
# ----------------------------------------------------------------------
# 同 gui_plugin_panel 中的处理方式，旧代码曾经从
# ``core.plugin_registry`` 导入 PluginRegistry，后来的实现
# 已迁移到 ``core.plugin_manager``。为了兼容两种路径，这里
# 采用 ``try / except`` 回退。
try:
    from .plugin_registry import PluginRegistry          # pragma: no cover
except ImportError:  # pragma: no cover
    from .plugin_manager import PluginRegistry

from .log_mixin import LogMixin
from .event_bus import EventBus


class PluginAutoFixEngine(LogMixin):
    """
    自动修复引擎 – 对单个插件文件执行全流程修复。

    参数
    ----------
    registry : PluginRegistry
        负责插件元数据持久化的对象（写回状态、备份记录）。
    log_callback : callable, optional
        与系统统一的日志回调（可为 ``print``）。
    """
    # -----------------------------------------------------------------
    # 初始化
    # -----------------------------------------------------------------
    def __init__(self, registry: PluginRegistry, log_callback=None):
        self.registry = registry
        self.log_callback = log_callback
        # 可由 config 控制的修复顺序（默认开启全部）
        self.fix_steps = [
            "add_missing_imports",
            "fix_indentation",
            "fix_syntax_errors",
            "add_missing_colon",
            "close_brackets",
            "add_dummy_definitions",
            "add_missing_plugin_class",
            "add_missing_execute_method",
            "install_missing_dependencies",
        ]

    # -----------------------------------------------------------------
    # 主入口：尝试对指定插件进行全自动修复
    # -----------------------------------------------------------------
    def fix_plugin(self, plugin_path: str, error_msg: str) -> Dict[str, Any]:
        """
        对 ``plugin_path`` 执行自动修复。

        参数
        ----------
        plugin_path : str
            完整文件路径（.py）。
        error_msg : str
            捕获的异常信息（包括 Traceback）。

        返回
        -------
        dict
            {
                "success": bool,      # 是否最终成功加载
                "fixed": bool,        # 是否在本轮修复中完成
                "method": str,        # 最后一次成功的修复步骤名
                "error": str|None,    # 若全部步骤失败则返回最后的错误信息
            }
        """
        # 1️⃣ 备份原文件（可以多次回滚）
        backup_path = self._backup_plugin(plugin_path)
        self._log(f"🗂️ 已备份插件 {os.path.basename(plugin_path)} → {backup_path}")

        # 2️⃣ 根据错误类型决定起始步骤（避免无意义修复循环）
        error_type = self._detect_error_type(error_msg)
        self._log(f"🔎 检测到错误类型: {error_type}")

        # 3️⃣ 逐步执行修复步骤
        for step in self.fix_steps:
            # 如果错误类型明确排除某些步骤，可 skip（如 SyntaxError 不需要 add_missing_imports）
            if not self._should_apply_step(error_type, step):
                continue

            method = getattr(self, f"_fix_{step}", None)
            if not callable(method):
                continue  # 防御性检查

            self._log(f"⚙️ 执行修复步骤: {step}")
            try:
                # 读取当前文件内容
                with open(plugin_path, "r", encoding="utf-8") as f:
                    code = f.read()
                # 交给具体修复函数返回新代码
                new_code = method(code, error_msg)
                # 写回磁盘
                with open(plugin_path, "w", encoding="utf-8") as f:
                    f.write(new_code)
                # 4️⃣ 编译校验（确保语法通过）
                compile_success, compile_err = self._validate_plugin(plugin_path)
                if compile_success:
                    self._log(f"✅ 修复步骤 `{step}` 成功，插件已通过编译")
                    # 写入成功状态并返回
                    self.registry.set_status(
                        name=self._extract_plugin_name(plugin_path),
                        status="loaded",
                        error=None,
                    )
                    return {
                        "success": True,
                        "fixed": True,
                        "method": step,
                        "error": None,
                    }
                else:
                    self._log(f"❌ 修复步骤 `{step}` 编译仍失败: {compile_err}")
                    # 继续尝试下一步
                    error_msg = compile_err
                    continue
            except Exception as e:
                tb = traceback.format_exc()
                self._log(f"⚠️ 修复步骤 `{step}` 异常: {e}\n{tb}")

        # 所有步骤尝试完毕仍未成功 → 回滚备份
        self._restore_plugin(plugin_path, backup_path)
        self._log(f"❎ 所有自动修复步骤均已失败，已回滚至备份")
        return {
            "success": False,
            "fixed": False,
            "method": None,
            "error": error_msg,
        }

    # -----------------------------------------------------------------
    # 备份插件（返回备份文件完整路径）
    # -----------------------------------------------------------------
    def _backup_plugin(self, plugin_path: str) -> str:
        """
        为插件创建时间戳备份并写入 Registry 的 backup_history。
        """
        fname = os.path.basename(plugin_path)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"{fname}.{timestamp}.bak"
        backup_dir = os.path.join(os.path.dirname(plugin_path), "backups")
        os.makedirs(backup_dir, exist_ok=True)
        backup_path = os.path.join(backup_dir, backup_name)
        shutil.copy2(plugin_path, backup_path)

        # 写入 Registry（自动管理保留天数由 Registry 负责）
        self.registry.add_backup(
            name=self._extract_plugin_name(plugin_path),
            backup_path=backup_path,
        )
        return backup_path

    # -----------------------------------------------------------------
    # 回滚插件（恢复到备份文件）
    # -----------------------------------------------------------------
    def _restore_plugin(self, plugin_path: str, backup_path: str) -> None:
        """
        将插件恢复为备份状态。若备份不存在则不做任何事。
        """
        if os.path.exists(backup_path):
            shutil.copy2(backup_path, plugin_path)
            self._log(f"🔄 已回滚插件 {os.path.basename(plugin_path)} 至备份 {backup_path}")
            # 同时更新 Registry 状态为 pending（待人工干预）
            self.registry.set_status(
                name=self._extract_plugin_name(plugin_path),
                status="pending",
                error="自动修复失败，已回滚",
            )

    # -----------------------------------------------------------------
    # 根据异常信息判断错误种类（返回统一标识）
    # -----------------------------------------------------------------
    def _detect_error_type(self, error_msg: str) -> str:
        """
        简单规则匹配错误信息，返回以下标识之一：
        - syntax
        - indentation
        - missing_import
        - module_not_found
        - name_error
        - attribute_error
        - missing_plugin_class
        - missing_execute
        - other
        """
        lower = error_msg.lower()
        if "syntaxerror" in lower:
            return "syntax"
        if "indentationerror" in lower:
            return "indentation"
        if "importerror" in lower:
            # 区分缺少 Plugin 导入 vs 其他模块
            if "core.plugin_manager" in lower:
                return "missing_import"
            else:
                return "module_not_found"
        if "modulenotfounderror" in lower:
            return "module_not_found"
        if "nameerror" in lower:
            return "name_error"
        if "attributeerror" in lower:
            return "attribute_error"
        if "no plugin class" in lower or "未找到类定义" in lower:
            return "missing_plugin_class"
        if "未找到 execute 方法" in lower or "no execute method" in lower:
            return "missing_execute"
        return "other"

    # -----------------------------------------------------------------
    # 决定当前错误类型是否需要执行特定修复步骤
    # -----------------------------------------------------------------
    def _should_apply_step(self, error_type: str, step: str) -> bool:
        """
        根据错误类型过滤不相关的修复步骤，提高效率。
        """
        mapping = {
            "syntax": {"fix_syntax_errors", "add_missing_colon", "close_brackets"},
            "indentation": {"fix_indentation"},
            "missing_import": {"add_missing_imports"},
            "module_not_found": {"install_missing_dependencies"},
            "name_error": {"add_dummy_definitions"},
            "attribute_error": {"add_dummy_definitions"},
            "missing_plugin_class": {"add_missing_plugin_class"},
            "missing_execute": {"add_missing_execute_method"},
            "other": set(self.fix_steps),  # 全部尝试
        }
        allowed = mapping.get(error_type, set())
        return step in allowed or not allowed  # 若 mapping 返回空则默认全部

    # -----------------------------------------------------------------
    # 步骤 1：自动添加缺少的核心导入（Plugin 基类等）
    # -----------------------------------------------------------------
    def _fix_add_missing_imports(self, code: str, _error_msg: str) -> str:
        """
        检查代码是否缺少以下必备 import：
        - import os
        - import sys
        - from core.plugin_manager import Plugin
        若缺失则在文件顶部依次加入。
        """
        required = [
            "import os",
            "import sys",
            "from core.plugin_manager import Plugin",
        ]

        lines = code.splitlines()
        existing = set(l.strip() for l in lines if l.strip().startswith(("import", "from")))
        inserts = [imp for imp in required if imp not in existing]

        if not inserts:
            return code

        # 将插入内容放在文件最前面（保持 shebang、encoding 注释后）
        insert_block = "\n".join(inserts) + "\n"
        # 若第一行是 #! 或 # -*- coding -*-，保留其位置
        if lines and (lines[0].startswith("#!") or lines[0].lstrip().startswith("# -*-")):
            return "\n".join([lines[0], insert_block] + lines[1:])
        else:
            return insert_block + "\n".join(lines)

    # -----------------------------------------------------------------
    # 步骤 2：统一使用 4 空格缩进（处理 Tab、混合缩进等）
    # -----------------------------------------------------------------
    def _fix_fix_indentation(self, code: str, _error_msg: str) -> str:
        """
        通过 tokenize 将所有 Tab/混合缩进统一为 4 空格。
        不改变已有空行，仅对有内容的行进行处理。
        """
        import io, tokenize

        result = []
        g = tokenize.generate_tokens(io.StringIO(code).readline)

        for toknum, tokval, start, end, line in g:
            if toknum == tokenize.INDENT:
                # 将任意缩进（Tab、若干空格）转为 4 空格乘以层级数
                level = (len(tokval.replace("\t", "    ")) // 4)
                result.append("    " * level)
            elif toknum == tokenize.DEDENT:
                # DEDENT 直接忽略（已经在 INDENT 中处理）
                continue
            elif toknum == tokenize.NL:
                result.append("\n")
            else:
                result.append(tokval)

        fixed_code = "".join(result)
        return fixed_code

    # -----------------------------------------------------------------
    # 步骤 3：常见语法错误自动补全
    #   - 缺失冒号
    #   - 未闭合的圆括号、方括号、花括号、单双引号
    # -----------------------------------------------------------------
    def _fix_fix_syntax_errors(self, code: str, _error_msg: str) -> str:
        """
        通过正则/计数修复最常见的语法错误。
        """
        # 1️⃣ 补全缺失冒号（主要针对 class / def / if / for / while / else / elif）
        lines = code.splitlines()
        fixed_lines = []
        for line in lines:
            stripped = line.rstrip()
            if re.match(r"^\s*(class|def|if|for|while|elif|else)\b[^\:]*$", stripped):
                # 行尾若没有冒号，则加上
                fixed_lines.append(stripped + ":")
            else:
                fixed_lines.append(line)
        code = "\n".join(fixed_lines)

        # 2️⃣ 补全括号、方括号、花括号
        pairs = {"(": ")", "[": "]", "{": "}"}
        open_counts = {"(": 0, "[": 0, "{": 0}
        for ch in code:
            if ch in open_counts:
                open_counts[ch] += 1
            elif ch in pairs.values():
                # 对应的左括号计数 -1
                for left, right in pairs.items():
                    if right == ch and open_counts[left] > 0:
                        open_counts[left] -= 1
                        break
        # 追加缺失的右括号
        for left, cnt in open_counts.items():
            if cnt > 0:
                code += pairs[left] * cnt

        # 3️⃣ 补全单双引号（如果出现奇数个单/双引号）
        if code.count('"') % 2 != 0:
            code += '"'
        if code.count("'") % 2 != 0:
            code += "'"

        return code

    # -----------------------------------------------------------------
    # 步骤 4：在可能缺失冒号的结构后自动添加
    # -----------------------------------------------------------------
    def _fix_add_missing_colon(self, code: str, _error_msg: str) -> str:
        """
        与 ``fix_syntax_errors`` 类似，但专门处理
        “行尾缺失冒号但已有其他代码” 的情况，例如：
        `def foo( )` → `def foo():`
        """
        pattern = re.compile(
            r"^(?P<indent>\s*)(?P<keyword>class|def|if|for|while|elif|else)\b(?P<body>.*?)(?P<colon>\s*)$",
            re.MULTILINE,
        )

        def repl(m):
            return f"{m.group('indent')}{m.group('keyword')}{m.group('body')}:"

        return pattern.sub(repl, code)

    # -----------------------------------------------------------------
    # 步骤 5：闭合未匹配的括号/方括号/花括号（仅在行内补全）
    # -----------------------------------------------------------------
    def _fix_close_brackets(self, code: str, _error_msg: str) -> str:
        """
        通过计数方式在源码末尾补全缺失的右括号、右方括号、右花括号。
        """
        pairs = {"(": ")", "[": "]", "{": "}"}
        open_counts = {"(": 0, "[": 0, "{": 0}
        for ch in code:
            if ch in open_counts:
                open_counts[ch] += 1
            elif ch in pairs.values():
                # 找到对应左括号并减一
                for left, right in pairs.items():
                    if right == ch and open_counts[left] > 0:
                        open_counts[left] -= 1
                        break
        # 补全右括号
        for left, cnt in open_counts.items():
            if cnt > 0:
                code += pairs[left] * cnt
        return code

    # -----------------------------------------------------------------
    # 步骤 6：为报错的未定义变量/属性自动添加占位赋值（None）
    # -----------------------------------------------------------------
    def _fix_add_dummy_definitions(self, code: str, error_msg: str) -> str:
        """
        解析 NameError / AttributeError 信息，提取未定义的标识符，
        并在文件顶部（在所有 import 之后）添加 `identifier = None`。
        """
        missing = set()

        # NameError 示例: name 'my_var' is not defined
        name_err_match = re.search(r"name '([^']+)' is not defined", error_msg)
        if name_err_match:
            missing.add(name_err_match.group(1))

        # AttributeError 示例: 'MyClass' object has no attribute 'my_attr'
        attr_err_match = re.search(r"has no attribute '([^']+)'", error_msg)
        if attr_err_match:
            missing.add(attr_err_match.group(1))

        if not missing:
            return code

        # 在所有 import 之后插入 dummy 定义
        lines = code.splitlines()
        insert_idx = 0
        for i, line in enumerate(lines):
            if line.strip().startswith(("import ", "from ")):
                insert_idx = i + 1

        dummy_defs = [f"{name} = None  # 自动补全占位" for name in missing]
        new_lines = (
            lines[:insert_idx] + dummy_defs + lines[insert_idx:]
        )
        return "\n".join(new_lines)

    # -----------------------------------------------------------------
    # 步骤 7：若文件中没有继承自 core.plugin_manager.Plugin 的类，自动生成
    # -----------------------------------------------------------------
    def _fix_add_missing_plugin_class(self, code: str, _error_msg: str) -> str:
        """
        检查文件是否已经定义了 Plugin 子类（排除基类本身），若不存在则
        在文件末尾追加最小实现的插件类（类名取自文件名）。
        """
        # 1️⃣ 检测是否已有 Plugin 子类
        try:
            tree = ast.parse(code)
        except Exception:
            # 若代码本身仍然无法解析，则直接返回原始（后续步骤会继续修复）
            return code

        has_plugin = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for base in node.bases:
                    if (
                        isinstance(base, ast.Name) and base.id == "Plugin"
                    ) or (
                        isinstance(base, ast.Attribute) and base.attr == "Plugin"
                    ):
                        has_plugin = True
                        break
        if has_plugin:
            return code

        # 2️⃣ 生成占位类（类名基于文件名）
        plugin_name = os.path.splitext(os.path.basename(_error_msg))[0]  # 只作占位示例
        class_name = f"{plugin_name.title().replace('_', '')}Plugin"

        class_template = f"""

class {class_name}(Plugin):
    \"\"\"自动生成的占位插件类（未实现具体业务）\"\"\"
    name = "{plugin_name}"
    description = "自动生成占位插件（缺失 Plugin 子类）"
    version = "0.0.1"
    author = "auto_fix"

    def get_capabilities(self):
        return []

    def execute(self, action, params):
        return {{"success": False, "message": "此插件为占位实现，未实现任何动作"}}
"""

        # 将类插入文件末尾（确保在 import 后）
        return code.rstrip() + "\n\n" + class_template.strip() + "\n"

    # -----------------------------------------------------------------
    # 步骤 8：若已有 Plugin 子类但缺少 execute 方法，自动添加占位实现
    # -----------------------------------------------------------------
    def _fix_add_missing_execute_method(self, code: str, _error_msg: str) -> str:
        """
        查找所有继承自 Plugin 的类，如果类体中没有 `def execute`，则插入占位实现。
        """
        try:
            tree = ast.parse(code)
        except Exception:
            return code

        class_nodes = [
            node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
        ]

        # 找出需要补全的类名
        to_patch = []
        for cls in class_nodes:
            inherits_plugin = any(
                (isinstance(base, ast.Name) and base.id == "Plugin")
                or (isinstance(base, ast.Attribute) and base.attr == "Plugin")
                for base in cls.bases
            )
            if not inherits_plugin:
                continue
            has_execute = any(
                isinstance(item, ast.FunctionDef) and item.name == "execute"
                for item in cls.body
            )
            if not has_execute:
                to_patch.append(cls.name)

        if not to_patch:
            return code

        # 在类定义内部（最末尾）插入占位 execute
        lines = code.splitlines()
        new_lines = []
        i = 0
        while i < len(lines):
            line = lines[i]
            new_lines.append(line)
            # 检测类定义开始
            class_match = re.match(r"^\s*class\s+(\w+)\s*\(.*\)\s*:", line)
            if class_match and class_match.group(1) in to_patch:
                # 找到类体结束位置（缩进回到类声明层级）
                indent = len(line) - len(line.lstrip())
                class_indent = " " * (indent + 4)  # 方法缩进 4 空格
                exec_stub = [
                    f"{class_indent}def execute(self, action, params):",
                    f"{class_indent}    \"\"\"自动占位实现，返回未实现错误\"\"\"",
                    f"{class_indent}    return {{\"success\": False, \"message\": \"execute 方法未实现（自动补全）\"}}",
                ]
                # 插入占位实现
                new_lines.extend(exec_stub)
            i += 1

        return "\n".join(new_lines)

    # -----------------------------------------------------------------
    # 步骤 9：针对 ModuleNotFoundError 给出安装提示（可选自动 pip）
    # -----------------------------------------------------------------
    def _fix_install_missing_dependencies(self, code: str, error_msg: str) -> str:
        """
        解析错误信息中缺失的模块名，向文件顶部写入 # pip install 注释。
        若环境变量 XIAOXI_AUTO_PIP=1（极端开发环境），会直接执行 pip 安装。
        """
        missing_mods = re.findall(r"No module named '([^']+)'", error_msg)
        if not missing_mods:
            return code

        comment_lines = [
            f"# TODO: 缺失依赖，请手动执行: pip install {mod}" for mod in missing_mods
        ]

        # 开发模式下尝试自动 pip（极其危险，仅在显式环境变量开启时）
        if os.getenv("XIAOXI_AUTO_PIP", "0") == "1":
            for mod in missing_mods:
                try:
                    import subprocess
                    subprocess.run(
                        [sys.executable, "-m", "pip", "install", mod],
                        check=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    )
                    comment_lines.append(f"# 已自动安装依赖 {mod}")
                except Exception as e:
                    comment_lines.append(f"# 自动安装失败 {mod}: {e}")

        # 将提示写在文件最前（紧随 import 之后）
        lines = code.splitlines()
        insert_idx = 0
        for i, line in enumerate(lines):
            if line.strip().startswith(("import ", "from ")):
                insert_idx = i + 1

        new_code = (
            "\n".join(lines[:insert_idx] + comment_lines + lines[insert_idx:])
        )
        return new_code

    # -----------------------------------------------------------------
    # 编译验证：返回 (bool, error_msg)
    # -----------------------------------------------------------------
    def _validate_plugin(self, plugin_path: str) -> Tuple[bool, Optional[str]]:
        """
        读取插件文件并尝试 ``compile``，返回成功标记与错误信息（若有）。
        """
        try:
            with open(plugin_path, "r", encoding="utf-8") as f:
                source = f.read()
            compile(source, plugin_path, "exec")
            return True, None
        except Exception as e:
            return False, str(e)

    # -----------------------------------------------------------------
    # 从路径提取插件名称（不含 ".py"）
    # -----------------------------------------------------------------
    def _extract_plugin_name(self, plugin_path: str) -> str:
        return os.path.splitext(os.path.basename(plugin_path))[0]
