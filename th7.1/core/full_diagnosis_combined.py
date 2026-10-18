# -*- coding: utf-8 -*-
# full_diagnosis_combined.py
# --------------------------------------------------------------
# 综合诊断脚本（升级版）
#   1️⃣ 系统信息 & 环境变量
#   2️⃣ 递归扫描 **所有** Python 文件（core / knowledge_base / plugins / 其他子目录）
#        - 大小、MD5、语法编译
#        - 动态导入检查（包式或文件式）
#   3️⃣ 缓存文件统计
#   4️⃣ 第三方依赖检查
#   5️⃣ Core 包的模块导入测试（已由动态导入覆盖）
#   6️⃣ 配置文件完整性 & 校验
#   7️⃣ Prompt 模板完整性 & 渲染测试
#   8️⃣ Ollama 本地服务健康检查
#   9️⃣ AIController 基础实例化 & 轻量方法检测
#  10️⃣ 话题持久化检查（新增）
#  11️⃣ 汇总 & 写入报告
# --------------------------------------------------------------

import os
import sys
import platform
import hashlib
import py_compile
import importlib
import importlib.util
import json
import time
import traceback
import threading
from datetime import datetime

# ----------------------------------------------------------------------
# ① 统一配置与工具
# ----------------------------------------------------------------------
from .config import (
    OLLAMA_URL,
    MODEL_NAME,
    KNOWLEDGE_BASE_DIR,
    DEFAULT_SAVE_DIR,
    DATA_CONFIG_DIR,
    init_directories,
    PROFESSIONAL_LOG_ENABLED,
    PROJECT_LOG_ISOLATION,
    TOPIC_STORAGE_DIR,               # ← 新增：话题持久化根目录
)

# ----------------------------------------------------------------------
# 统一报告容器
# ----------------------------------------------------------------------
report_lines = []
IGNORE_ENTRY_FILES = {"main.py", "run.py", "start.py"}

def _add(section: str, content: str = ""):
    """向报告追加带标题的段落（空行自动分隔）"""
    if section:
        report_lines.append(section)
    if content:
        report_lines.append(content)


def _print_and_log(msg: str):
    """打印到终端并写入报告"""
    print(msg)
    report_lines.append(msg)


# ----------------------------------------------------------------------
# 1️⃣ 系统信息 & 环境变量
# ----------------------------------------------------------------------
_print_and_log("=" * 78)
_print_and_log("🔍 XIAOXI 智能助手 - 综合诊断报告（升级版）")
_print_and_log("=" * 78)

now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
_print_and_log(f"⏱️ 诊断时间：{now}")
_print_and_log(f"🐍 Python 版本 : {sys.version.replace(chr(10), ' ')}")
_print_and_log(f"💻 平台信息   : {platform.platform()}")
_print_and_log(f"📂 工作目录   : {os.getcwd()}")

_print_and_log("\n【环境变量】")
_print_and_log(f"PYTHONPATH : {os.environ.get('PYTHONPATH', '未设置')}")
_print_and_log(f"PYTHONHOME : {os.environ.get('PYTHONHOME', '未设置')}")

# ----------------------------------------------------------------------
# 2️⃣ 递归扫描所有 Python 文件（core / knowledge_base / plugins / 其他）
# ----------------------------------------------------------------------
_print_and_log("\n【递归 Python 文件检查】")

PROJECT_ROOT = os.getcwd()
SCAN_DIRS = {
    "core": os.path.join(PROJECT_ROOT, "core"),
    "knowledge_base": os.path.join(PROJECT_ROOT, "knowledge_base"),
    "plugins": os.path.join(PROJECT_ROOT, "plugins"),
    "others": PROJECT_ROOT,  # 包含根目录下的独立脚本
}

def _gather_py_files(base_dir):
    """返回 base_dir 下所有 .py 文件的完整路径（包括子目录）"""
    py_files = []
    for root, _, files in os.walk(base_dir):
        for f in files:
            if f.endswith(".py"):
                py_files.append(os.path.join(root, f))
    return py_files


# 收集所有目标文件（去重）
all_py_files = set()
for name, path in SCAN_DIRS.items():
    if os.path.isdir(path):
        files = _gather_py_files(path)
        all_py_files.update(files)
    else:
        _print_and_log(f"⚠️ 目录不存在或不可访问：{path}")

# 按路径字典序遍历，保持报告稳定
for fp in sorted(all_py_files):
    rel = os.path.relpath(fp, PROJECT_ROOT)
    size = os.path.getsize(fp)
    with open(fp, "rb") as fh:
        md5 = hashlib.md5(fh.read()).hexdigest()[:12]

    # 语法编译
    try:
        py_compile.compile(fp, doraise=True)
        syntax = "✅"
        syntax_msg = ""
    except py_compile.PyCompileError as ce:
        syntax = "❌"
        syntax_msg = f"  编译错误 : {ce.msg}"
    except Exception as e:  # pragma: no cover
        syntax = "❌"
        syntax_msg = f"  未知错误 : {e}"

    line = f"{syntax} {rel} ({size:,} 字节) [{md5}]"
    _print_and_log(line)
    if syntax_msg:
        _print_and_log(syntax_msg)

# ----------------------------------------------------------------------
# 3️⃣ 动态导入检查（确保所有可导入的模块都能成功加载）
# ----------------------------------------------------------------------
_print_and_log("\n【动态导入测试】")
import_errors = []  # 收集错误信息，统一在报告末尾展示

def _module_name_from_path(file_path):
    """
    把绝对文件路径转换为可用于 import_module 的点式模块名。
    若路径所在目录不是包（没有 __init__.py），返回 None。
    """
    rel_path = os.path.relpath(file_path, PROJECT_ROOT)
    # 去掉 .py 扩展名
    module_path = rel_path[:-3]
    parts = module_path.split(os.sep)

    # 检查每一级是否为包（包含 __init__.py）
    pkg_path = PROJECT_ROOT
    for i, part in enumerate(parts[:-1]):  # 最后一个是文件本身
        pkg_path = os.path.join(pkg_path, part)
        if not os.path.isfile(os.path.join(pkg_path, "__init__.py")):
            # 只要有一级不是包，就不能使用点式导入
            return None
    return ".".join(parts)


def _safe_import(module_name, file_path):
    """
    先尝试标准 import_module（适用于包式模块），若失败回退到
    importlib.util.spec_from_file_location（适用于单文件脚本）。
    """
    filename = os.path.basename(file_path)
    if filename in IGNORE_ENTRY_FILES:
        _print_and_log(f"⚠️ 跳过入口脚本导入：{filename}")
        return False
    try:
        if module_name:
            importlib.import_module(module_name)
            _print_and_log(f"✅ 导入成功（包）: {module_name}")
            return True
        # 包式导入不适用，尝试文件式加载
        spec = importlib.util.spec_from_file_location(
            os.path.splitext(os.path.basename(file_path))[0],
            file_path,
        )
        if spec is None:
            raise ImportError("无法创建模块 spec")
        mod = importlib.util.module_from_spec(spec)
        loader = spec.loader
        if loader is None:
            raise ImportError("模块加载器为空")
        loader.exec_module(mod)
        _print_and_log(f"✅ 导入成功（文件）: {file_path}")
        return True
    except Exception as e:
        tb = traceback.format_exc().splitlines()
        err_msg = f"❌ 导入失败: {file_path} → {type(e).__name__}: {e}"
        _print_and_log(err_msg)
        _print_and_log("   Traceback: " + " | ".join(tb[-2:]))
        import_errors.append(err_msg)
        return False


# 对每个 .py 文件执行导入检测
for fp in sorted(all_py_files):
    mod_name = _module_name_from_path(fp)
    _safe_import(mod_name, fp)

if import_errors:
    _print_and_log("\n⚠️ 导入过程中出现错误，请检查上述信息。")

# ----------------------------------------------------------------------
# 4️⃣ 缓存文件统计
# ----------------------------------------------------------------------
_print_and_log("\n【缓存文件统计】")
pycache_dirs = 0
pyc_files = 0
for root, dirs, files in os.walk('.'):
    if '__pycache__' in dirs:
        pycache_dirs += 1
    for f in files:
        if f.endswith('.pyc'):
            pyc_files += 1
_print_and_log(f"__pycache__ 目录数量 : {pycache_dirs}")
_print_and_log(f".pyc 文件数量      : {pyc_files}")

# ----------------------------------------------------------------------
# 5️⃣ 第三方依赖检查
# ----------------------------------------------------------------------
_print_and_log("\n【第三方依赖检查】")
required_pkgs = [
    "requests", "bs4", "pynput", "pyautogui",
    "psutil", "keyboard"
]
missing_pkgs = []
for pkg in required_pkgs:
    try:
        importlib.import_module(pkg)
        _print_and_log(f"✅ {pkg}")
    except ImportError:
        missing_pkgs.append(pkg)
        _print_and_log(f"❌ {pkg}  - 未安装")
if missing_pkgs:
    _print_and_log("\n⚠️ 建议使用 pip 安装缺失的依赖，如：")
    _print_and_log(f"   pip install {' '.join(missing_pkgs)}")

# ----------------------------------------------------------------------
# 6️⃣ 配置文件完整性 & 校验
# ----------------------------------------------------------------------
_print_and_log("\n【配置文件校验】")
try:
    from core import config
    cfg_check = config.validate_config()
    if cfg_check.get("valid"):
        _print_and_log("✅ 配置校验通过")
    else:
        _print_and_log("❌ 配置校验未通过")
        for err in cfg_check.get("errors", []):
            _print_and_log(f"   错误 : {err}")
        for warn in cfg_check.get("warnings", []):
            _print_and_log(f"   警告 : {warn}")
except Exception as e:  # pragma: no cover
    _print_and_log(f"❌ 加载 core.config 失败：{e}")

# ----------------------------------------------------------------------
# 7️⃣ Prompt 模板完整性 & 渲染测试
# ----------------------------------------------------------------------
_print_and_log("\n【Prompt 模板检查】")
try:
    from core.config import PROMPT_TEMPLATES_DIR, call_ollama_safe, SAFE_TIMEOUT
    from core.prompt_manager import PromptManager

    # 确保 PromptManager 已初始化
    if not getattr(PromptManager, "initialized", False):
        PromptManager.init(PROMPT_TEMPLATES_DIR)

    required_templates = [
        "intent_understanding.txt",
        "quality_check.txt",
        "health_check.txt",
        "outline_generation.txt",
        "chapter_writing.txt"
    ]

    missing_tpl = []
    for tpl in required_templates:
        full_path = os.path.join(PROMPT_TEMPLATES_DIR, tpl)
        if not os.path.isfile(full_path):
            missing_tpl.append(tpl)
    if missing_tpl:
        _print_and_log("❌ 缺失以下 Prompt 模板文件：")
        for m in missing_tpl:
            _print_and_log(f"   {m}")
    else:
        _print_and_log("✅ 必要 Prompt 模板全部存在")

    # 渲染示例（不调用 LLM）
    _print_and_log("\n▶️ 渲染示例模板（仅检查渲染是否报错）")
    demo_data = {
        "user_input": "请创建工程文件，项目名《高武穿越：林浩的系统人生》",
        "context": "当前状态：无活跃任务"
    }
    for tpl_name in required_templates:
        tpl_key = os.path.splitext(tpl_name)[0]
        try:
            rendered = PromptManager.render(tpl_key, **demo_data)
            _print_and_log(f"✅ {tpl_key} 渲染成功，前 120 字符：")
            _print_and_log(f"   {rendered[:120]}{'...' if len(rendered) > 120 else ''}")
        except Exception as e:
            _print_and_log(f"❌ 渲染 {tpl_key} 失败：{e}")

    # 可选：调用一次 Ollama 做意图分析
    _print_and_log("\n▶️（可选）调用 Ollama 进行一次 Intent 分析")
    try:
        sample_prompt = PromptManager.render(
            "intent_understanding",
            user_input=demo_data["user_input"],
            context=demo_data["context"]
        )
        resp = call_ollama_safe([{"role": "user", "content": sample_prompt}], timeout=SAFE_TIMEOUT)
        if resp.get("success"):
            _print_and_log("✅ Ollama 调用成功，返回示例（截取前 200 字符）：")
            _print_and_log(f"   {resp['content'][:200]}{'...' if len(resp['content']) > 200 else ''}")
        else:
            _print_and_log(f"⚠️ Ollama 调用失败：{resp.get('error')}")
    except Exception as e:
        _print_and_log(f"⚠️ Ollama 调用异常：{e}")
except Exception as e:  # pragma: no cover
    _print_and_log(f"❌ Prompt 检查异常：{e}")

# ----------------------------------------------------------------------
# 8️⃣ Ollama 本地服务健康检查（可选）
# ----------------------------------------------------------------------
_print_and_log("\n【Ollama 本地服务检查】")
try:
    import requests
    resp = requests.get("http://localhost:11434/api/tags", timeout=5)
    if resp.status_code == 200:
        models = resp.json().get("models", [])
        _print_and_log(f"✅ Ollama 正常运行，已注册模型数量：{len(models)}")
    else:
        _print_and_log(f"⚠️ Ollama 响应异常，状态码：{resp.status_code}")
except Exception as e:
    _print_and_log(f"❌ Ollama 服务不可达：{e}")

# ----------------------------------------------------------------------
# 9️⃣ AIController 基础实例化 & 轻量方法检测
# ----------------------------------------------------------------------
_print_and_log("\n【AIController 基础实例化】")
try:
    from core.ai_controller import AIController

    def _log_cb(msg, level: str = "INFO"):
        _print_and_log(f"[AI LOG] {msg}")


    controller = AIController(log_callback=_log_cb, project_id="diagnostic_demo")
    _print_and_log("✅ AIController 实例化成功")

    # _detect_intent_context（不依赖 LLM）
    try:
        _print_and_log("▶️ 调用 _detect_intent_context（空上下文）")
        ctx_str = controller._detect_intent_context({})
        _print_and_log(f"   返回 : {ctx_str}")
    except Exception as e:
        _print_and_log(f"❌ _detect_intent_context 失败：{e}")

    # _load_last_active_topic
    try:
        _print_and_log("▶️ 调用 _load_last_active_topic")
        controller._load_last_active_topic()
        _print_and_log(f"   当前话题 : {controller.current_topic}")
    except Exception as e:
        _print_and_log(f"❌ _load_last_active_topic 失败：{e}")

    # 关键字回退意图分析
    try:
        _print_and_log("▶️ 调用 _fallback_intent_analysis（关键字回退）")
        fallback = controller._fallback_intent_analysis("创建一个新小说")
        _print_and_log(f"   结果 : {json.dumps(fallback, ensure_ascii=False)}")
    except Exception as e:
        _print_and_log(f"❌ _fallback_intent_analysis 失败：{e}")

except Exception as e:  # pragma: no cover
    _print_and_log(f"❌ AIController 实例化失败：{e}")
    _print_and_log(traceback.format_exc())

# ----------------------------------------------------------------------
# 10️⃣ 【话题持久化检查】（新增）
# ----------------------------------------------------------------------
_print_and_log("\n【话题持久化检查】")
try:
    from core.topic_manager import TopicManager

    # 使用诊断报告的打印函数作为日志回调
    _topic_manager = TopicManager(log_callback=_print_and_log)

    # ① 写入测试话题（空历史）并检查返回值
    test_topic = "diagnostic_test_topic"
    if not _topic_manager.save(test_topic, []):
        raise IOError("无法写入测试话题文件（save 失败）")

    # ② 读取刚才保存的空历史，检查一致性
    loaded = _topic_manager.load(test_topic)
    if loaded != []:
        raise ValueError("读取测试话题的内容不为空")

    # ③ 设置当前话题并读取，验证元信息写入/读取
    if not _topic_manager.set_current(test_topic):
        raise IOError("设置当前话题失败")
    if _topic_manager.get_current() != test_topic:
        raise ValueError("当前话题读取不一致")

    # ④ 列举话题，确保新建的话题在列表里
    if test_topic not in _topic_manager.list_topics():
        raise AssertionError("新建的话题未出现在话题列表中")

    # ⑤ 清理测试文件，保持工作目录整洁
    try:
        os.remove(os.path.join(TOPIC_STORAGE_DIR, f"topic_{test_topic}.json"))
        _topic_manager.set_current("default")
    except Exception:
        pass

    _print_and_log("✅ 话题持久化功能正常")
except Exception as e:
    _print_and_log(f"⚠️ 话题持久化检查失败: {e}")

# ----------------------------------------------------------------------
# 11️⃣ 汇总 & 写入报告
# ----------------------------------------------------------------------
_report_path = os.path.abspath("diagnosis_report.txt")
try:
    with open(_report_path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(report_lines))
    _print_and_log("\n✅ 诊断报告已写入：" + _report_path)
except Exception as e:
    _print_and_log("\n⚠️ 写入诊断报告失败：" + str(e))

_print_and_log("\n=== 诊断结束 ===\n")
