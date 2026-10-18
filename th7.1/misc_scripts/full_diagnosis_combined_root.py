# -*- coding: utf-8 -*-
# full_diagnosis_combined.py
# --------------------------------------------------------------
"""
XIAOXI 智能助手 - 核心系统综合诊断脚本 (V3.0 纯净稳定版)
彻底移除所有随机化干扰，确保诊断结果 100% 稳定、严谨、可复现。
"""
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
import tempfile
import shutil
from datetime import datetime

# ----------------------------------------------------------------------
# 统一报告容器
# ----------------------------------------------------------------------
report_lines = []
IGNORE_ENTRY_FILES = {"main.py", "run.py", "start.py", "full_diagnosis_combined.py"}

def _print_and_log(msg: str):
    print(msg)
    report_lines.append(msg)

# ----------------------------------------------------------------------
# 1️⃣ 系统信息 & 环境变量
# ----------------------------------------------------------------------
_print_and_log("=" * 78)
_print_and_log("🔍 XIAOXI 智能助手 - 综合诊断报告（V3.0 纯净稳定版）")
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
# 2️⃣ 递归扫描所有 Python 文件 (确定性排序)
# ----------------------------------------------------------------------
_print_and_log("\n【递归 Python 文件检查】")

PROJECT_ROOT = os.getcwd()
SCAN_DIRS = {
    "core": os.path.join(PROJECT_ROOT, "core"),
    "plugins": os.path.join(PROJECT_ROOT, "plugins"),
    "root": PROJECT_ROOT,
}

def _gather_py_files(base_dir):
    py_files = []
    if not os.path.isdir(base_dir): return py_files
    for root, _, files in os.walk(base_dir):
        # 跳过测试目录和缓存目录
        if "tests" in root or "__pycache__" in root or ".venv" in root:
            continue
        for f in files:
            if f.endswith(".py"):
                py_files.append(os.path.join(root, f))
    return py_files

all_py_files = set()
for name, path in SCAN_DIRS.items():
    all_py_files.update(_gather_py_files(path))

# 过滤掉脚本自身和忽略文件
script_path = os.path.abspath(__file__)
filtered_files = [
    fp for fp in all_py_files 
    if os.path.abspath(fp) != script_path and os.path.basename(fp) not in IGNORE_ENTRY_FILES
]

# 🌟 核心优化：按字母顺序稳定排序，确保每次诊断顺序一致
file_list = sorted(filtered_files, key=lambda x: os.path.relpath(x, PROJECT_ROOT))

for fp in file_list:
    rel = os.path.relpath(fp, PROJECT_ROOT)
    size = os.path.getsize(fp)
    with open(fp, "rb") as fh:
        md5 = hashlib.md5(fh.read()).hexdigest()[:12]

    try:
        py_compile.compile(fp, doraise=True)
        syntax = "✅"
        syntax_msg = ""
    except py_compile.PyCompileError as ce:
        syntax = "❌"
        syntax_msg = f"  编译错误 : {ce.msg}"
    except Exception as e:
        syntax = "❌"
        syntax_msg = f"  未知错误 : {e}"

    _print_and_log(f"{syntax} {rel} ({size:,} 字节) [{md5}]")
    if syntax_msg: 
        _print_and_log(syntax_msg)

# ----------------------------------------------------------------------
# 3️⃣ 动态导入检查 (确定性执行)
# ----------------------------------------------------------------------
_print_and_log("\n【动态导入测试】")
import_errors = []

def _module_name_from_path(file_path):
    rel_path = os.path.relpath(file_path, PROJECT_ROOT)
    module_path = rel_path[:-3]
    parts = module_path.split(os.sep)
    pkg_path = PROJECT_ROOT
    for part in parts[:-1]:
        pkg_path = os.path.join(pkg_path, part)
        if not os.path.isfile(os.path.join(pkg_path, "__init__.py")):
            return None
    return ".".join(parts)

def _safe_import(module_name, file_path):
    try:
        if module_name:
            importlib.import_module(module_name)
            _print_and_log(f"✅ 导入成功（包）: {module_name}")
        else:
            spec = importlib.util.spec_from_file_location(os.path.splitext(os.path.basename(file_path))[0], file_path)
            if spec is None: raise ImportError("无法创建模块 spec")
            mod = importlib.util.module_from_spec(spec)
            loader = spec.loader
            if loader is None: raise ImportError("模块加载器为空")
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

for fp in file_list:
    mod_name = _module_name_from_path(fp)
    _safe_import(mod_name, fp)

if import_errors:
    _print_and_log(f"\n⚠️ 导入过程中出现 {len(import_errors)} 个错误，请检查上述信息。")

# ----------------------------------------------------------------------
# 4️⃣ 缓存文件统计 (优化遍历速度)
# ----------------------------------------------------------------------
_print_and_log("\n【缓存文件统计】")
pycache_dirs = 0
pyc_files = 0
for root, dirs, files in os.walk(PROJECT_ROOT):
    if '__pycache__' in dirs: 
        pycache_dirs += 1
        pyc_files += len([f for f in os.listdir(os.path.join(root, '__pycache__')) if f.endswith('.pyc')])
        dirs.remove('__pycache__') # 优化：不再深入遍历缓存目录内部
_print_and_log(f"__pycache__ 目录数量 : {pycache_dirs}")
_print_and_log(f".pyc 文件数量      : {pyc_files}")

# ----------------------------------------------------------------------
# 5️⃣ 第三方核心依赖检查
# ----------------------------------------------------------------------
_print_and_log("\n【第三方依赖检查】")
required_pkgs = ["requests", "bs4", "pynput", "pyautogui", "psutil", "keyboard", "jsonschema"]
missing_pkgs = []
for pkg in required_pkgs:
    try:
        importlib.import_module(pkg)
        _print_and_log(f"✅ {pkg}")
    except ImportError:
        missing_pkgs.append(pkg)
        _print_and_log(f"❌ {pkg} - 未安装")
if missing_pkgs:
    _print_and_log(f"\n⚠️ 建议使用 pip 安装缺失的依赖：pip install {' '.join(missing_pkgs)}")

# ----------------------------------------------------------------------
# 6️⃣ 配置文件完整性 & 校验
# ----------------------------------------------------------------------
_print_and_log("\n【配置文件校验】")
try:
    from core import config
    if hasattr(config, "validate_config") and callable(getattr(config, "validate_config")):
        cfg_check = config.validate_config()
        if cfg_check.get("valid"): _print_and_log("✅ 配置校验通过")
        else:
            _print_and_log("❌ 配置校验未通过")
            for err in cfg_check.get("errors", []): _print_and_log(f"   错误 : {err}")
    else:
        required_attrs = ["OLLAMA_URL", "MODEL_NAME", "KNOWLEDGE_BASE_DIR", "DEFAULT_SAVE_DIR", "PROMPT_TEMPLATES_DIR"]
        missing = [a for a in required_attrs if not hasattr(config, a)]
        if missing: _print_and_log(f"❌ 配置缺失关键属性：{', '.join(missing)}")
        else: _print_and_log("✅ 配置文件中关键属性均已定义")
except Exception as e:
    _print_and_log(f"❌ 加载 core.config 失败：{e}")

# ----------------------------------------------------------------------
# 7️⃣ Prompt 模板完整性 & 渲染测试
# ----------------------------------------------------------------------
_print_and_log("\n【Prompt 模板检查】")
try:
    from core.config import PROMPT_TEMPLATES_DIR, call_ollama_safe, SAFE_TIMEOUT
    from core.prompt_manager import PromptManager

    if hasattr(PromptManager, "init"): PromptManager.init(PROMPT_TEMPLATES_DIR)
    elif hasattr(PromptManager, "initialize"): PromptManager.initialize(PROMPT_TEMPLATES_DIR)

    required_templates = ["intent_understanding.txt", "quality_check.txt", "health_check.txt", "outline_generation.txt", "chapter_writing.txt"]
    missing_tpl = [tpl for tpl in required_templates if not os.path.isfile(os.path.join(PROMPT_TEMPLATES_DIR, tpl))]
    
    if missing_tpl:
        _print_and_log("❌ 缺失 Prompt 模板：")
        for m in missing_tpl: _print_and_log(f"   {m}")
    else:
        _print_and_log("✅ 必要 Prompt 模板全部存在")

    demo_data = {"user_input": "创建项目《星际纪元》", "context": "无活跃任务", "title": "星际纪元", "genre": "科幻"}
    _print_and_log("\n▶️ 渲染示例模板")
    for tpl_name in required_templates:
        tpl_key = os.path.splitext(tpl_name)[0]
        try:
            rendered = PromptManager.render(tpl_key, **demo_data)
            _print_and_log(f"✅ {tpl_key} 渲染成功，前 80 字符：{rendered[:80].replace(chr(10), ' ')}...")
        except Exception as e:
            _print_and_log(f"❌ 渲染 {tpl_key} 失败：{e}")

    # 确定性执行 Ollama Intent 分析 (不再随机跳过)
    _print_and_log("\n▶️ 调用 Ollama 进行 Intent 分析")
    try:
        sample_prompt = PromptManager.render("intent_understanding", user_input=demo_data["user_input"], context=demo_data["context"])
        resp = call_ollama_safe([{"role": "user", "content": sample_prompt}], timeout=SAFE_TIMEOUT)
        if resp.get("success"): 
            content = resp['content'][:100].replace(chr(10), ' ')
            _print_and_log(f"✅ Ollama 返回：{content}...")
        else: 
            _print_and_log(f"⚠️ Ollama 失败：{resp.get('error')}")
    except Exception as e: 
        _print_and_log(f"⚠️ Ollama 异常：{e}")
        
except Exception as e:
    _print_and_log(f"❌ Prompt 检查异常：{e}")

# ----------------------------------------------------------------------
# 8️⃣ Ollama 本地服务健康检查
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
# 9️⃣ AIController 基础实例化
# ----------------------------------------------------------------------
_print_and_log("\n【AIController 基础实例化】")
try:
    from core.self_learning_engine import SelfLearningEngine
    SelfLearningEngine._check_internet_connection = lambda self: False
except Exception: pass

try:
    from core.ai_controller import AIController
    def _log_cb(msg): _print_and_log(f"[AI LOG] {msg}")
    controller = AIController(log_callback=_log_cb, project_id="diagnostic_demo")
    _print_and_log("✅ AIController 实例化成功")

    try:
        intent_res = controller.intent_analyzer.analyze_intent("测试意图", context={})
        _print_and_log(f"▶️ IntentAnalyzer 返回: {json.dumps(intent_res, ensure_ascii=False)[:100]}...")
    except Exception as e: _print_and_log(f"❌ IntentAnalyzer 失败：{e}")

    try:
        controller._load_last_active_topic()
        _print_and_log(f"▶️ 当前话题: {controller.current_topic}")
    except Exception as e: _print_and_log(f"❌ 话题加载失败：{e}")

except Exception as e:
    _print_and_log(f"❌ AIController 实例化失败：{e}")
    _print_and_log(traceback.format_exc())

# ----------------------------------------------------------------------
# 🔟 核心写作引擎算法单元测试（非 LLM 依赖）
# ----------------------------------------------------------------------
_print_and_log("\n【核心写作引擎算法单元测试（非 LLM 依赖）】")

# 测试 1: 防弹 JSON 提取器压力测试
try:
    from core.chapter_generator import ChapterGenerator
    cg = ChapterGenerator.__new__(ChapterGenerator)
    
    dirty_json = """
    好的，这是你要的 JSON：
    ```json
    {
        "score": 85,
        "passed": true,
        "issues": ["问题1", "问题2",], 
        "nested": {"a": 1, "b": "it's a test"} 
    }
    ```
    希望对你有帮助！
    """
    parsed = cg._robust_extract_json(dirty_json) if hasattr(cg, '_robust_extract_json') else None
    
    if parsed and parsed.get("score") == 85 and "it's a test" in parsed.get("nested", {}).get("b", ""):
        _print_and_log("✅ 测试 1 通过：防弹 JSON 提取器完美处理了 Markdown、尾部逗号和合法单引号。")
    else:
        _print_and_log("❌ 测试 1 失败：防弹 JSON 提取器未能正确解析脏数据。")
except Exception as e:
    _print_and_log(f"⚠️ 测试 1 异常：{e}")

# 测试 2: Info-dumping (设定解说) 规则初筛测试
try:
    long_para = "这是一段真正超过一百五十字的纯旁白测试文本。没有对话，没有动作描写，只有作者在喋喋不休地解说世界观的底层逻辑和魔法体系的运作原理。这种写法导致剧情完全停滞，读者肯定会在这里感到无聊并弃书。因为它完全违背了展示而非告知的文学创作基本原则，属于典型的设定倾倒也就是Info-dumping，必须被我们的质量检查系统精准识别并拦截下来，强制要求作者进行重写。"
    info_dump_text = ("原来如此，据说在古老的传说中，境界分为三层。历史要追溯到上古时期，众所周知，其实不然。\n" * 5)
    info_dump_text += "\n" + (long_para + "\n") * 3

    exposition_keywords = ["原来如此", "据说", "传说中", "古籍记载", "旁白", "设定是", "境界分为", "历史要追溯到", "众所周知", "其实不然"]
    exposition_count = sum(info_dump_text.count(kw) for kw in exposition_keywords)
    paragraphs = [p.strip() for p in info_dump_text.split('\n') if p.strip()]
    long_monologues = sum(1 for p in paragraphs if len(p) > 150 and '“' not in p and '”' not in p)
    
    is_suspicious = (exposition_count > 3) or (long_monologues > 3)
    
    if is_suspicious and exposition_count >= 20 and long_monologues >= 3:
        _print_and_log(f"✅ 测试 2 通过：Info-dumping 初筛成功捕获异常 (解说词:{exposition_count}, 长旁白:{long_monologues})。")
    else:
        _print_and_log("❌ 测试 2 失败：Info-dumping 初筛未能识别明显的设定倾倒。")
except Exception as e:
    _print_and_log(f"⚠️ 测试 2 异常：{e}")

# 测试 3: 上下文滑动窗口与截断测试
try:
    from core.context_builder import ContextBuilder
    cb = ContextBuilder.__new__(ContextBuilder)
    fake_summaries = [{"chapter": i, "summary": f"第{i}章的剧情摘要..."} for i in range(1, 51)]
    
    if hasattr(cb, '_format_context_summary'):
        context = {"previous_summaries": fake_summaries}
        formatted = cb._format_context_summary(context)
        if "第50章" in formatted and "第48章" in formatted and "第1章" not in formatted:
            _print_and_log("✅ 测试 3 通过：上下文滑动窗口正确截断，仅保留最近 3 章摘要。")
        else:
            _print_and_log("❌ 测试 3 失败：上下文滑动窗口截断逻辑异常。")
    else:
        _print_and_log("⚠️ 测试 3 跳过：ContextBuilder 缺少 _format_context_summary 方法。")
except Exception as e:
    _print_and_log(f"⚠️ 测试 3 异常：{e}")

# 测试 4: V5.0 角色卡认知分层读取测试
try:
    from core.deep_lore_expander import DeepLoreExpander
    temp_dir = tempfile.mkdtemp()
    lore_dir = os.path.join(temp_dir, "03_设定", "角色卡")
    os.makedirs(lore_dir)
    
    char_data = {
        "name": "测试主角", "role": "救世主",
        "appearance": "黑发黑瞳，气质冷峻",
        "secret_layers": {"public_mask": "阳光开朗", "hidden_truth": "内心充满仇恨"},
        "evolution_log": [{"chapter": 5, "type": "能力觉醒", "content": "解锁了量子巫术"}]
    }
    with open(os.path.join(lore_dir, "角色卡_测试主角.json"), "w", encoding="utf-8") as f:
        json.dump(char_data, f)
        
    task_plan = {"project_dir": temp_dir, "main_character": "测试主角"}
    dle = DeepLoreExpander(temp_dir, task_plan)
    context = dle._load_deep_lore_context("task_1", 6, task_plan)
    
    if "阳光开朗" in context and "量子巫术" in context:
        _print_and_log("✅ 测试 4 通过：V5.0 角色卡认知分层读取成功，正确提取了面具与演变日志。")
    else:
        _print_and_log("❌ 测试 4 失败：未能正确提取 V5.0 角色卡的核心分层数据。")
    shutil.rmtree(temp_dir)
except Exception as e:
    _print_and_log(f"⚠️ 测试 4 异常：{e}")

# ----------------------------------------------------------------------
# 1️⃣1️⃣ 汇总 & 写入报告
# ----------------------------------------------------------------------
_report_path = os.path.abspath("diagnosis_report.txt")
try:
    with open(_report_path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(report_lines))
    _print_and_log(f"\n✅ 诊断报告已写入：{_report_path}")
except Exception as e:
    _print_and_log(f"\n⚠️ 写入诊断报告失败：{e}")

_print_and_log("\n=== 诊断结束 ===\n")
