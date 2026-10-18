from __future__ import annotations
import os
import sys
import json
import shutil
import traceback
import importlib
import py_compile
import re
from pathlib import Path
from datetime import datetime
from typing import Callable, Dict, List, Tuple

try:
    from . import config
except Exception:
    config = None

def _safe_log(log_func: Callable[[str], None], msg: str, level: str = "INFO") -> None:
    try:
        if log_func: log_func(msg)
        else: print(f"[SelfCheck][{level}] {msg}")
    except Exception:
        print(f"[SelfCheck][{level}] {msg}")

def _ensure_directories_and_prompts(log_func: Callable[[str], None]) -> None:
    if config is not None:
        try:
            res = config.init_directories()
            _safe_log(log_func, f"✅ 目录初始化完成（新建 {res.get('created', 0)} 项）")
        except Exception as e:
            _safe_log(log_func, f"⚠️ 目录初始化异常：{e}", "WARN")
    else:
        _safe_log(log_func, "⚠️ config 未加载，跳过目录初始化", "WARN")

    if config is not None and hasattr(config, "_ensure_prompt_templates"):
        try:
            config._ensure_prompt_templates()
            _safe_log(log_func, "✅ Prompt 占位模板已确认")
        except Exception as e:
            _safe_log(log_func, f"⚠️ 创建占位 Prompt 失败：{e}", "WARN")
    else:
        _safe_log(log_func, "⚠️ config 未提供 _ensure_prompt_templates，手动跳过", "WARN")

def _detect_syntax_errors(root_path: Path) -> Dict[str, str]:
    errors: Dict[str, str] = {}
    for dirpath, _, filenames in os.walk(str(root_path)):
        for fn in filenames:
            if not fn.endswith(".py"): continue
            file_path = os.path.join(dirpath, fn)
            try:
                py_compile.compile(file_path, doraise=True)
            except py_compile.PyCompileError as ce:
                errors[file_path] = str(ce)
            except Exception as e:
                errors[file_path] = f"未知异常：{e}"
    return errors

def _collect_importable_modules(project_root: Path) -> List[str]:
    modules: List[str] = []
    for dirpath, _, filenames in os.walk(str(project_root)):
        for fn in filenames:
            if not fn.endswith(".py"): continue
            rel_path = os.path.relpath(os.path.join(dirpath, fn), str(project_root))
            mod_name = rel_path[:-3].replace(os.sep, ".")
            if mod_name.endswith(".__init__"): mod_name = mod_name.rsplit(".", 1)[0]
            modules.append(mod_name)
    return modules

def _detect_import_errors(modules: List[str]) -> Dict[str, str]:
    errors: Dict[str, str] = {}
    for name in modules:
        try: importlib.import_module(name)
        except Exception: errors[name] = traceback.format_exc()
    return errors

def _backup_file(file_path: Path) -> Path:
    backup_dir = file_path.parent / "backup_selfcheck"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"{file_path.name}.{timestamp}.bak"
    shutil.copy2(str(file_path), str(backup_path))
    return backup_path

def _fix_core_imports(file_path: Path) -> Tuple[bool, str]:
    """🌟 核心修复：使用正则仅匹配行首的 import core.，防止误伤字符串和注释，并引入原子写入。"""
    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception:
        return False, ""

    if "import core." not in content:
        return False, ""

    # 仅替换行首（允许前导空白）的 import core.xxx
    new_content = re.sub(r'^(\s*)import core\.(\w+)', r'\1from . import \2', content, flags=re.MULTILINE)

    if new_content == content:
        return False, ""

    backup_path = _backup_file(file_path)
    
    # 原子写入防止损坏
    tmp_path = file_path.with_suffix(file_path.suffix + ".tmp")
    try:
        tmp_path.write_text(new_content, encoding="utf-8")
        os.replace(str(tmp_path), str(file_path))
    except Exception:
        if tmp_path.exists(): tmp_path.unlink(missing_ok=True)
        return False, ""
        
    return True, str(backup_path)

def self_check_and_autofix(log_func: Callable[[str], None]) -> bool:
    def _log(msg: str, level: str = "INFO") -> None:
        _safe_log(log_func, msg, level)

    _log("=== 开始自检与自动修复 ===")
    _ensure_directories_and_prompts(log_func)

    project_root = Path(__file__).resolve().parent.parent
    core_dir = project_root / "core"
    plugins_dir = project_root / "plugins"
    _log(f"🔎 项目根目录定位为：{project_root}")

    _log("🔍 开始语法错误检测（core & plugins）")
    syntax_errors = _detect_syntax_errors(core_dir)
    syntax_errors.update(_detect_syntax_errors(plugins_dir))

    if syntax_errors:
        _log(f"⚠️ 检测到 {len(syntax_errors)} 处语法错误", "WARN")
        for fp, err in syntax_errors.items(): _log(f"   {fp}: {err}", "WARN")
    else:
        _log("✅ 未发现语法错误")

    _log("🔍 收集可导入的模块名称")
    all_modules = _collect_importable_modules(project_root)
    _log(f"   共计 {len(all_modules)} 个模块待检测")

    _log("🔍 开始逐一尝试导入")
    import_errors = _detect_import_errors(all_modules)

    if import_errors: _log(f"⚠️ 导入错误数：{len(import_errors)}", "WARN")
    else: _log("✅ 所有模块均可成功导入")

    repaired_cnt = 0
    if import_errors:
        _log("🔧 尝试自动修复 core 包中的导入路径")
        for mod_name, tb in import_errors.items():
            if not mod_name.startswith("core."): continue
            rel_path = mod_name.replace(".", os.sep) + ".py"
            target_file = project_root / rel_path
            if not target_file.is_file():
                _log(f"⚠️ 预期文件不存在，无法修复: {target_file}", "WARN")
                continue
            fixed, backup = _fix_core_imports(target_file)
            if fixed:
                repaired_cnt += 1
                _log(f"✅ 修复成功: {mod_name}（备份: {backup})")
            else:
                _log(f"⚠️ 自动修复未生效: {mod_name}", "WARN")

        if repaired_cnt:
            _log("🔁 重新执行导入检测以验证修复效果")
            remaining_errors = _detect_import_errors([m for m in all_modules if m.startswith("core.")])
            if remaining_errors: _log(f"⚠️ 修复后仍有 {len(remaining_errors)} 条导入错误", "WARN")
            else: _log("✅ 所有 core 模块已成功导入（修复生效）")

    total_issues = len(syntax_errors) + len(import_errors)
    if total_issues == 0:
        _log("🎉 自检完成，未发现任何问题 🎉")
        return True
    if repaired_cnt > 0:
        _log(f"🛠️ 自检完成，检测到 {total_issues} 项问题，已自动修复 {repaired_cnt} 项", "INFO")
        return True
    else:
        _log(f"⚠️ 自检完成，检测到 {total_issues} 项问题，未能自动修复任何项目", "WARN")
        return False
