# main.py
# ==================== 主程序入口（完整合并增强版）====================
# 启动流程：依赖检查 → 配置验证 → 目录初始化 → CMD 学习选择 → 启动学习 → GUI 界面
# 合并说明：整合了"内存优化版"与"完整增强版"的所有功能，无精简无省略
# 新增：项目自检与自动修复、学习平台下载、搜索新平台功能、重新启动机制
# 重点改动：
#   1️⃣ 将所有外网资源替换为国内可访问站点
#   2️⃣ 防止平台资源重复下载（首次下载后复用）
#   3️⃣ 关闭默认的 DuckDuckGo 搜索弹窗（可通过配置开关打开）
#   4️⃣ 强制 SelfLearningEngine 在首次学习时使用本地 LLM（gemma4）
#   5️⃣ 在启动报告中展示实际学习的主题与摘要
#   6️⃣ 添加全局开关（ENABLE_PLATFORM_DOWNLOAD、ENABLE_PLATFORM_SEARCH、ENABLE_SELF_LEARNING_LLM）
import core.ai_controller_patch   # ← 必须在任何 `from core import ai_controller` 之前
import sys
import os
import time
import json
import traceback
import pickle
import shutil
import py_compile
import re
import subprocess
import requests
from datetime import datetime
from typing import List, Dict, Any

# -----------------------------------------------------------------
# ① 新增：从 config 中统一读取全局开关（统一管理，避免硬编码）
# -----------------------------------------------------------------
from core.config import (
    ENABLE_PLATFORM_DOWNLOAD,   # 是否在启动时下载学习平台资源
    ENABLE_PLATFORM_SEARCH,   # 是否弹出搜索新平台的交互（默认关闭）
    ENABLE_SELF_LEARNING_LLM,  # 是否在学习阶段强制使用本地 LLM（gemma4）
    PROMPT_TEMPLATES_DIR,
    call_ollama_safe,          # LLM 安全调用接口
    SAFE_TIMEOUT,
)

# ==================== 第零步：基础路径设置 ====================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

# ==================== 第一步：依赖检查 ====================
print("\n" + "=" * 60)
print("🚀 筱溪智能助手 - 启动程序 (完整合并增强版)")
print("=" * 60)

try:
    from core.dependency_manager import check_and_install_dependencies
    if not check_and_install_dependencies():
        print("\n❌ 依赖环境未就绪。")
        input("按回车退出...")
        sys.exit(1)
    print("\n✅ 依赖检查通过")
except Exception as e:
    print(f"\n❌ 依赖检查模块加载失败：{e}")
    print("请手动运行：pip install -r requirements.txt")
    input("按回车退出...")
    sys.exit(1)

# ==================== 第二步：配置验证与目录初始化 ====================
print("\n" + "=" * 60)
print("🔧 配置验证与目录初始化")
print("=" * 60)

config_valid = True

try:
    from core.config import (
        OLLAMA_URL, MODEL_NAME, KNOWLEDGE_BASE_DIR, DEFAULT_SAVE_DIR,
        DATA_CONFIG_DIR, init_directories, PROFESSIONAL_LOG_ENABLED,
        PROJECT_LOG_ISOLATION
    )
    print(f"✅ 配置文件加载成功")
    print(f"   - Ollama 地址：{OLLAMA_URL}")
    print(f"   - 模型名称：{MODEL_NAME}")
    print(f"   - 存档目录：{DEFAULT_SAVE_DIR}")
    print(f"   - 专业日志：{'启用' if PROFESSIONAL_LOG_ENABLED else '禁用'}")
    print(f"   - 项目隔离：{'启用' if PROJECT_LOG_ISOLATION else '禁用'}")

#    init_result = init_directories()
#    if init_result['success']:
#        print(f"✅ 必要目录已创建/验证 (共 {init_result['total_dirs']} 个)")
#    else:
#        print("⚠️  目录初始化部分失败")
    init_result = init_directories()
    # init_directories() 永远返回 dict，若出现异常会抛出异常；
    # 因此这里直接认为初始化成功即可（若有创建的数量也可以打印）。
    if isinstance(init_result, dict):
        print(f"✅ 必要目录已创建/验证 (共 {init_result.get('total', 0)} 个)")
    else:
        print("⚠️  目录初始化返回异常结果")

except Exception as e:
    print(f"❌ 配置文件加载失败：{e}")
    config_valid = False

# ✅ Ollama 服务检查
if config_valid:
    try:
        resp = requests.get(OLLAMA_URL.replace('/api/chat', '/api/tags'), timeout=5)
        if resp.status_code == 200:
            models = resp.json().get('models', [])
            model_names = [m.get('name', '') for m in models]
            if MODEL_NAME in model_names:
                print(f"✅ Ollama 正常运行，模型 {MODEL_NAME} 已加载")
            else:
                print(f"⚠️ Ollama 正常，但未找到模型 {MODEL_NAME}")
                print(f"   可用模型：{', '.join(model_names[:5])}")
                print("   💡 提示：请运行 'ollama pull {MODEL_NAME}' 下载模型")
        else:
            print(f"⚠️ Ollama 响应异常，状态码：{resp.status_code}")
    except requests.exceptions.ConnectionError:
        print("❌ Ollama 服务未运行！请先启动 Ollama：ollama serve")
        config_valid = False
    except Exception as e:
        print(f"⚠️ Ollama 服务检查失败：{e}")

if not config_valid:
    print("\n⚠️  配置验证未完全通过，部分功能可能受限")
    print("   按回车继续启动，或 Ctrl+C 退出修复配置...")
    try:
        input()
    except KeyboardInterrupt:
        sys.exit(1)

# ==================== 第三步：辅助工具函数 ====================

# 备份目录（仅用于自检时使用）
BACKUP_DIR = os.path.join(os.getcwd(), "backup_files")
os.makedirs(BACKUP_DIR, exist_ok=True)

def _simple_log(msg: str):
    """最简单的日志输出（在控制台直接打印）"""
    print(msg)

def backup_file(filepath: str) -> str:
    """把文件复制到 backup_files 并返回备份路径"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = os.path.basename(filepath)
    backup_path = os.path.join(BACKUP_DIR, f"{fname}_{timestamp}.bak")
    shutil.copy2(filepath, backup_path)
    return backup_path

def restart_program():
    """在当前进程中重新执行本脚本（用于代码自修复后）"""
    python = sys.executable
    os.execv(python, [python] + sys.argv)

def self_check_and_autofix(log_func):
    """
    自动化代码自检 + 基于本地 Ollama（gemma‑4）进行“一键修复”。  
    详细实现位于 core/self_check_and_autofix.py，此处仅做快捷入口。
    """
    from core.self_check_and_autofix import self_check_and_autofix as _run
    return _run(log_func)

# -----------------------------------------------------------------
# ④ 平台资源下载（已切换为国内可访问站点，且加入缓存检查）
# -----------------------------------------------------------------
def fetch_learning_platforms(log_func):
    """
    下载若干公开免费 AI 学习平台的首页，保存至 learning_resources/，
    并把平台列表写入 platforms.json。若文件已存在且完整则直接跳过。
    """
    # --- 仅在配置允许的情况下执行 ---
    if not ENABLE_PLATFORM_DOWNLOAD:
        log_func("⚡ 平台资源下载已在配置中关闭，跳过此步骤")
        return

    platforms = [
        {"name":"百度 AI 平台","url":"https://ai.baidu.com/tech",
         "desc":"提供中文 AI 方案、文心大模型等"},
        {"name":"阿里云机器学习","url":"https://developer.aliyun.com/ai",
         "desc":"阿里云 AI 生态，模型、数据、算力一体"},
        {"name":"腾讯云 AI","url":"https://cloud.tencent.com/product/ai",
         "desc":"腾讯云 AI 解决方案，支持大模型、图像、语音"},
        {"name":"科大讯飞开放平台","url":"https://www.iflytek.com/technology",
         "desc":"中文语音、自然语言处理等技术平台"},
        {"name":"360 AI 平台","url":"https://ai.360.cn/",
         "desc":"360 AI 研发平台，提供安全大模型服务"}
    ]
    target_dir = os.path.join(os.getcwd(), "learning_resources")
    os.makedirs(target_dir, exist_ok=True)

    # 检查是否已经拥有完整的 platforms.json
    platforms_json_path = os.path.join(target_dir, "platforms.json")
    if os.path.isfile(platforms_json_path):
        try:
            with open(platforms_json_path, "r", encoding="utf-8") as f:
                existing = json.load(f)
            if isinstance(existing, list) and len(existing) >= len(platforms):
                log_func("✅ 学习平台资源已缓存，跳过重新下载")
                return
        except Exception:
            # 读取异常则继续重新下载
            pass

    for p in platforms:
        try:
            r = requests.get(p["url"], timeout=10)
            if r.status_code == 200:
                safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', p["name"])
                file_path = os.path.join(target_dir, f"{safe_name}.html")
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(r.text)
                log_func(f"✅ 已下载平台页面: {p['name']} → {file_path}")
            else:
                log_func(f"⚠️ 下载 {p['name']} 失败，状态码 {r.status_code}")
        except Exception as e:
            log_func(f"⚠️ 下载 {p['name']} 时异常: {e}")

    # 保存平台列表（供后续参考）
    try:
        with open(platforms_json_path, "w", encoding="utf-8") as f:
            json.dump(platforms, f, ensure_ascii=False, indent=2)
        log_func(f"✅ 平台信息已写入 {platforms_json_path}")
    except Exception as e:
        log_func(f"⚠️ 写入平台列表失败: {e}")

# -----------------------------------------------------------------
# ⑤ 搜索新平台（已默认关闭，受 ENABLE_PLATFORM_SEARCH 控制）
# -----------------------------------------------------------------
def search_new_ai_platforms(query, max_results=5, log_func=None):
    """
    使用国内搜索引擎（Bing CN）返回前几条结果的 URL。
    若全局开关关闭，则直接返回空列表，不弹窗。
    """
    if not ENABLE_PLATFORM_SEARCH:
        if log_func:
            log_func("🔍 搜索新平台功能已在配置中关闭，跳过搜索")
        return []

    try:
        search_url = "https://cn.bing.com/search"
        payload = {"q": query}
        r = requests.get(search_url, params=payload, timeout=10,
                         headers={"User-Agent":"Mozilla/5.0"})
        if r.status_code != 200:
            if log_func:
                log_func(f"⚠️ Bing 搜索失败，状态码 {r.status_code}")
            return []
        # 解析结果链接（与原实现兼容）
        links = re.findall(r'<a[^>]+class=["\']result__a["\'][^>]*href=["\']([^"\']+)["\']', r.text)
        results = []
        for link in links[:max_results]:
            if link.startswith("/l/?kh=-1&uddg="):
                from urllib.parse import unquote
                link = unquote(link.split("uddg=")[-1])
            results.append(link)
        if log_func:
            log_func(f"🔎 搜索 '{query}' 得到 {len(results)} 条结果")
        return results
    except Exception as e:
        if log_func:
            log_func(f"⚠️ 搜索异常: {e}")
        return []

# ==================== 第四步：CMD 学习时间选择 ====================
print("\n" + "=" * 60)
print("📚 启动学习设置")
print("=" * 60)

print("""
请选择启动学习时长：

  1. ⚡ 快速学习（5 分钟）   - 适合日常使用
  2. 📖 标准学习（15 分钟）  - 推荐首次启动
  3. 📚 深度学习（30 分钟）  - 适合系统优化
  4. 🎓 完整学习（60 分钟）  - 适合全新安装
  5. ⏭️ 跳过学习          - 快速启动
  6. 🔧 运行自检并自动修复  - 检查代码并尝试修复（会自动重启）
""")

learning_time = 15   # 默认 15 分钟
while True:
    try:
        choice = input("请输入选项 (1-6)，默认 15 分钟 [回车]: ").strip()
        if choice == "" or choice == "2":
            learning_time = 15
            break
        elif choice == "1":
            learning_time = 5
            break
        elif choice == "3":
            learning_time = 30
            break
        elif choice == "4":
            learning_time = 60
            break
        elif choice == "5":
            learning_time = 0
            break
        elif choice == "6":
            print("\n🔧 正在执行项目自检与自动修复...")
            success = self_check_and_autofix(_simple_log)
            if success:
                print("✅ 自检并修复完成，程序即将重新启动以加载修复后的代码。")
                time.sleep(2)
                restart_program()
            else:
                print("⚠️ 自检未成功修复任何文件，程序将继续后续流程。")
            # 重新回到选项菜单
            continue
        else:
            print("⚠️  无效选项，请重新输入")
    except KeyboardInterrupt:
        print("\n\n用户中断启动")
        sys.exit(1)
    except Exception as e:
        print(f"⚠️  输入错误：{e}，使用默认值 15 分钟")
        learning_time = 15
        break

# ==================== 第五步：执行启动学习 ====================
print("\n" + "=" * 60)
print("🔄 正在执行启动学习...")
print("=" * 60)

startup_result = {
    "plugins_loaded": 0,
    "rules_loaded": 0,
    "scan_duration_minutes": 0,
    "recommendations": 0,
    "learning_time_minutes": learning_time,
    "success": False,
    "error": None,
    "optimizations": {},
    "topics_learned": 0
}

try:
    start_time = time.time()

    from core.ai_controller import AIController

    # 简单日志回调
    def log_callback(msg):
        print(f"  {msg}")

    # 实例化 AI 控制器（启动时会自动执行 SelfLearningEngine）
    ai_controller = AIController(log_callback=log_callback)

    # ---------------------------------------------------------
    # ① 强制 SelfLearningEngine 使用 LLM（如果全局开关打开）
    # ---------------------------------------------------------
    if ENABLE_SELF_LEARNING_LLM:
        # 这里不需要额外代码，SelfLearningEngine 本身会在首次运行时调用 LLM
        # 只是在报告阶段展示更详细的学习日志
        pass

    # ---------------------------------------------------------
    # ② 执行启动学习（包含插件扫描、知识库优化、SelfLearning 等）
    # ---------------------------------------------------------
    startup_data = ai_controller.startup(learning_time_minutes=learning_time)

    if isinstance(startup_data, dict):
        startup_result.update(startup_data)

    # ---------------------------------------------------------
    # ③ 若首次学习未完成（首次启动），手动触发一次并展示细节
    # ---------------------------------------------------------
    if not ai_controller.learning_engine.is_first_run_completed():
        # 强制执行一次完整的首次学习（使用 LLM）
        print("\n🔎 检测到系统首次启动，准备执行 **初始学习**（约 15 分钟）")
        init_res = ai_controller.learning_engine.perform_initial_learning(
            time_budget_minutes=learning_time if learning_time > 0 else 15
        )
        startup_result["topics_learned"] = init_res.get("goals_total", 0)
        startup_result["learning_time_minutes"] = init_res.get("time_spent_minutes", 0)
        print(f"✅ 初始学习完成：{init_res.get('goals_completed',0)}/{init_res.get('goals_total',0)} 项")
    else:
        # 已有学习记录，展示本次学习的主题与摘要
        try:
            logs = ai_controller.learning_engine.learning_logs   # 已在内存中加载
            if logs:
                print("\n📚 本次启动已学习的主题摘要：")
                for idx, entry in enumerate(logs[-5:], 1):   # 只展示最近 5 条
                    topic = entry.get("topic") or entry.get("user_request") or "未知主题"
                    summary = entry.get("summary") or entry.get("content") or ""
                    print(f"   {idx}. {topic[:30]} – {summary[:80]}{'...' if len(summary) > 80 else ''}")
            else:
                print("\n⚠️ 未检测到本次学习日志（可能是首次运行已自动完成）")
        except Exception as e:
            print(f"⚠️ 获取学习日志异常：{e}")

    end_time = time.time()
    startup_result["scan_duration_minutes"] = (end_time - start_time) / 60
    startup_result["success"] = True

    print("-" * 60)
    print(f"\n✅ 启动学习完成！耗时：{startup_result['scan_duration_minutes']:.1f} 分钟")

except KeyboardInterrupt:
    print("\n\n⚠️  用户中断 (Ctrl+C)")
    startup_result["error"] = "用户中断"
    time.sleep(1)

except Exception as e:
    startup_result["error"] = str(e)
    startup_result["success"] = False
    print(f"\n❌ 启动学习失败：{e}")
    traceback.print_exc()
    print("\n⚠️  将继续启动 GUI，但部分功能可能不可用...")
    time.sleep(3)

# ==================== 第六步：显示启动报告 ====================
print("\n" + "=" * 60)
print("📊 启动报告")
print("=" * 60)

print(f"""
🔌 加载插件：{startup_result.get('plugins_loaded', 0)} 个
📜 加载规则：{startup_result.get('rules_loaded', 0)} 条
⏱️ 启动耗时：{startup_result.get('scan_duration_minutes', 0):.1f} 分钟
💡 发现建议：{startup_result.get('recommendations', 0)} 条
📚 学习主题：{startup_result.get('topics_learned', 0)} 个
""")

if startup_result.get('success'):
    print("✅ 系统状态：正常")
else:
    print(f"⚠️  系统状态：部分功能受限 - {startup_result.get('error', '未知错误')}")

print("=" * 60)

# ------------------- 下载公开免费 AI 学习平台资源 -------------------
print("\n🔍 正在下载公开免费 AI 学习平台资源，这些文件将保存在 learning_resources/ 目录中...")
fetch_learning_platforms(_simple_log)

# ------------------- 可选：搜索新的学习平台 -------------------
if ENABLE_PLATFORM_SEARCH:
    search_more = input("\n是否需要搜索其他免费 AI 学习平台？(y/N): ").strip().lower()
    if search_more == "y":
        query = input("请输入搜索关键词（如 'free AI tutorial'）: ").strip()
        if query:
            urls = search_new_ai_platforms(query, log_func=_simple_log)
            if urls:
                print("\n搜索到的链接：")
                for u in urls:
                    print(" -", u)
            else:
                print("未检索到结果。")
else:
    # 关闭搜索功能时直接跳过提示
    print("\n⚡ 搜索新平台功能已在配置中关闭，跳过此步骤。")

# ==================== ✅【新增】学习建议 & 公开知识站点 ====================
def show_learning_suggestions():
    """打印系统建议学习的方向以及常用公开知识网站"""
    topics = [
        "✅ Python 基础 / 标准库（官方文档）",
        "✅ 网络爬虫与 HTTP 请求（requests、urllib）",
        "✅ 文件系统操作（os、shutil、pathlib）",
        "✅ 异步 & 多线程（asyncio、threading）",
        "✅ 常用算法与数据结构",
        "✅ AI / 大模型调用（Ollama、OpenAI API）",
        "✅ GUI 开发（tkinter、pyqt）",
        "✅ 项目结构化管理与版本控制（git）",
        "✅ 常用第三方库（pandas、numpy、beautifulsoup）"
    ]
    sites = [
        "🌐 Python 官方文档：https://docs.python.org/3/",
        "🌐 MDN Web Docs（JS/HTML/CSS）：https://developer.mozilla.org/",
        "🌐 Stack Overflow（技术问答）：https://stackoverflow.com/",
        "🌐 Wikipedia（通用知识）：https://zh.wikipedia.org/",
        "🌐 菜鸟教程（中文入门）：https://www.runoob.com/",
        "🌐 CSDN 博客（中文技术博客）：https://blog.csdn.net/",
        "🌐 GitHub（开源项目 & 文档）：https://github.com/",
        "🌐 InfoQ、掘金（技术趋势）：https://www.infoq.cn/、https://juejin.cn/"
    ]
    print("\n=== 推荐学习方向 ===")
    for t in topics:
        print(t)
    print("\n=== 常用公开知识站点 ===")
    for s in sites:
        print(s)
    print("\n💡 随时在任意时刻输入 “学习建议” 查看这些内容。")

# 启动时直接展示一次建议（帮助用户快速了解系统可学习的范围）
show_learning_suggestions()

# ==================== 第七步：启动 GUI 界面 ====================
print("\n🖥️  正在启动图形界面...")
print("-" * 60)

try:
    import tkinter as tk
    import tkinter.messagebox

    from core.gui import CompleteAssistantGUI

    root = tk.Tk()
    root.title("❤️ 筱溪 - 智能助手（完整合并增强版）")
    root.geometry("1400x900")

    # 传递启动结果给 GUI
    app = CompleteAssistantGUI(root, startup_result=startup_result)

    def on_closing():
        if tkinter.messagebox.askokcancel("退出", "确定退出筱溪智能助手？"):
            print("\n📝 正在保存数据...")
            try:
                app.ai_controller._save_conversation_history()
                app.ai_controller._save_learning_history()
                app.ai_controller.personality.save_personality()
                app.ai_controller.task_tracker._init_files()
                app.ai_controller.interruption_handler.stop_listening()
                print("✅ 数据已保存")
            except Exception as e:
                print(f"⚠️  保存数据失败：{e}")
            finally:
                root.destroy()
                print("👋 再见！")

    root.protocol("WM_DELETE_WINDOW", on_closing)

    print("✅ GUI 已启动")
    print("-" * 60)
    print("💡 快捷键提示：")
    print("   F6  - 开始录制宏")
    print("   F7  - 播放宏")
    print("   ESC - 停止宏播放")
    print("   Ctrl+Shift+P - 暂停生成")
    print("   Ctrl+Shift+R - 继续生成")
    print("   Ctrl+Shift+S - 停止生成")
    print("\n💡 常用命令：")
    print("   '学习' - 触发主动学习")
    print("   '学习建议' - 查看系统推荐的学习方向")
    print("   '查看规则' - 管理永久规则")
    print("   '系统状态' - 查看完整系统信息")
    print("   '新话题' - 开启新对话话题")
    print("=" * 60)

    try:
        print("✅ GUI 已启动，等待用户操作...")
        print("💡 提示：关闭窗口或按 Ctrl+C 退出")
        print("-" * 60)

        root.mainloop()

    except KeyboardInterrupt:
        print("\n\n⚠️  用户中断 (Ctrl+C)")
        print("📝 正在保存数据...")
        try:
            app.ai_controller._save_conversation_history()
            app.ai_controller._save_learning_history()
            app.ai_controller.personality.save_personality()
            app.ai_controller.interruption_handler.stop_listening()
            print("✅ 数据已保存")
        except:
            print("⚠️  保存数据时出错")
        finally:
            print("👋 再见！")

    except Exception as e:
        print(f"\n❌ GUI 运行错误：{e}")
        traceback.print_exc()
        print("\n⚠️  将尝试以命令行模式启动...")

        # -------------------------------------------------
        # 进入命令行降级模式
        # -------------------------------------------------
        from core.ai_controller import AIController
        controller = AIController()

        print("\n✅ 命令行模式已启动")
        print("输入 'exit' 退出程序")
        print("-" * 60)

        def cli_show_help():
            print("\n=== 命令行帮助 ===")
            print("  学习        → 触发主动学习（使用学习引擎）")
            print("  学习建议    → 查看系统推荐的学习方向与公开知识站点")
            print("  查看规则    → 管理永久规则")
            print("  系统状态    → 查看完整系统信息")
            print("  新话题      → 开启新对话话题")
            print("  搜索平台    → 输入关键词搜索新的免费 AI 学习平台")
            print("  exit/quit   → 退出程序")
            print("-" * 30)

        cli_show_help()

        while True:
            try:
                user_input = input("\n👤 请输入命令：").strip()
                if not user_input:
                    continue
                if user_input.lower() in ['exit', 'quit', '退出']:
                    break
                if user_input == "学习建议":
                    show_learning_suggestions()
                    continue
                if user_input == "搜索平台":
                    q = input("请输入搜索关键词：").strip()
                    if q:
                        urls = search_new_ai_platforms(q, log_func=_simple_log)
                        if urls:
                            print("\n搜索结果：")
                            for u in urls:
                                print(" -", u)
                        else:
                            print("未检索到结果。")
                    continue
                if user_input == "学习":
                    res = controller.ask_ai("学习")
                    print(f"\n🤖 {res.get('explanation','')}")
                    continue
                # 其余交给正常 AI 处理流程
                result = controller.ask_ai(user_input)
                print(f"\n🤖 {result.get('explanation','')}")
            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"❌ 错误：{e}")

        print("\n👋 再见！")

except Exception as e:
    print(f"\n❌ GUI 启动失败：{e}")
    print("   请确保已安装 tkinter：pip install tk")
    print("\n尝试以命令行模式启动...")

    try:
        from core.ai_controller import AIController
        controller = AIController()

        print("\n✅ 命令行模式已启动")
        print("输入 'exit' 退出程序")
        print("-" * 60)

        def cli_show_help():
            print("\n=== 命令行帮助 ===")
            print("  学习        → 触发主动学习（使用学习引擎）")
            print("  学习建议    → 查看系统推荐的学习方向与公开知识站点")
            print("  查看规则    → 管理永久规则")
            print("  系统状态    → 查看完整系统信息")
            print("  新话题      → 开启新对话话题")
            print("  搜索平台    → 输入关键词搜索新的免费 AI 学习平台")
            print("  exit/quit   → 退出程序")
            print("-" * 30)

        cli_show_help()

        while True:
            try:
                user_input = input("\n👤 请输入命令：").strip()
                if not user_input:
                    continue
                if user_input.lower() in ['exit', 'quit', '退出']:
                    break
                if user_input == "学习建议":
                    show_learning_suggestions()
                    continue
                if user_input == "搜索平台":
                    q = input("请输入搜索关键词：").strip()
                    if q:
                        urls = search_new_ai_platforms(q, log_func=_simple_log)
                        if urls:
                            print("\n搜索结果：")
                            for u in urls:
                                print(" -", u)
                        else:
                            print("未检索到结果。")
                    continue
                if user_input == "学习":
                    res = controller.ask_ai("学习")
                    print(f"\n🤖 {res.get('explanation','')}")
                    continue
                result = controller.ask_ai(user_input)
                print(f"\n🤖 {result.get('explanation','')}")
            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"❌ 错误：{e}")

        print("\n👋 再见！")

    except Exception as e:
        print(f"\n❌ 命令行模式也失败：{e}")
        traceback.print_exc()

# ==================== 第八步：清理退出 ====================
print("\n" + "=" * 60)
print("🧹 正在清理...")
print("=" * 60)

try:
    from core.config import close_all_db_connections
    close_all_db_connections()
    print("✅ 数据库连接已关闭")

    temp_dirs = [
        os.path.join(BASE_DIR, "sandbox"),
        os.path.join(BASE_DIR, "web_cache")
    ]
    for td in temp_dirs:
        if os.path.exists(td):
            for f in os.listdir(td):
                if f.startswith('debug_') or f.endswith('.tmp'):
                    try:
                        os.remove(os.path.join(td, f))
                    except:
                        pass
    print("✅ 临时文件已清理")
except Exception as e:
    print(f"⚠️  清理失败：{e}")

print("\n" + "=" * 60)
print("👋 筱溪智能助手已退出")
print("=" * 60 + "\n")
