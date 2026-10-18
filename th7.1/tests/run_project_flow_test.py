# run_project_flow_test.py
"""
🧪 项目完整流水线功能检测脚本（带结果导出）

本脚本在一次执行中完成 **单个工程** 的全部流程：
1️⃣ 随机生成极端的“创建项目”指令并创建项目（若已有未完成工程则直接复用）
2️⃣ 自动/手动生成大纲 → 确认大纲
3️⃣ 写第 1、2 章并手动触发一次健康检查
4️⃣ 循环“继续生成”直至所有章节完成
5️⃣ 保存学习历史、执行自检、写入最终报告
6️⃣ 最终删除记忆文件，确保下次运行不会再创建第二个工程

所有关键步骤均使用 **重试装饰器**（可配置最大重试次数），
异常信息完整写入 `project_flow_report.txt`，日志统一走 `log()` 回调。
"""

import os
import json
import time
import sys
import random               # 用于生成随机中文名、标题、字数等
import string               # 如需生成占位符（此处未使用，但保留）
import traceback            # 捕获异常堆栈并写入报告
from pathlib import Path
from datetime import datetime
from typing import Tuple, Dict, Any, Callable

# ----------------------------------------------------------------------
# 1️⃣ 配置开关（可自行调节）——请务必保留这些全局变量
# ----------------------------------------------------------------------
MAX_RETRY_COUNT      = 3      # 步骤最大重试次数
RETRY_BACKOFF_BASE   = 4      # 重试基准秒数，指数回退 1、2、4…
SKIP_BATCH_GENERATION = False # 若 True，则不走批量生成，仅使用“继续生成”
MAX_CONTINUE_LOOP    = 50     # 防止无限循环的上限（章节数量上限）
LAST_PROJECT_INFO    = Path(__file__).with_name(".last_project_info.json")
REPORT_PATH          = Path(__file__).with_name("project_flow_report.txt")
REPORT_FILE           = REPORT_PATH.open("w", encoding="utf-8")      # 立即打开，后续统一写入

# ----------------------------------------------------------------------
# 2️⃣ 基础报告写入 & 日志回调
# ----------------------------------------------------------------------
def _write_report(line: str = "") -> None:
    """
    向报告文件写入一行并立即 flush，防止意外中断导致内容丢失。
    """
    REPORT_FILE.write(line + "\n")
    REPORT_FILE.flush()


def log(msg: str) -> None:
    """
    AIController 的日志回调 – 同时打印到终端并写进报告
    """
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {msg}"
    print("[LOG]", msg)
    _write_report(line)


def _write_error(step: str, exc: BaseException) -> None:
    """
    将异常信息（包括完整 traceback）写入报告，并通过 `log` 输出。
    """
    err_msg = f"❌ [{step}] 异常: {exc}"
    log(err_msg)
    _write_report("\n".join([
        f"--- 异常堆栈 ({step}) ---",
        "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        "--- 异常结束 ---"
    ]))


# ----------------------------------------------------------------------
# 3️⃣ 重试装饰器（对所有可能失败的外部调用统一使用）
# ----------------------------------------------------------------------
def _retry(func: Callable) -> Callable:
    """
    装饰器：对被装饰函数最多重试 `MAX_RETRY_COUNT` 次，
    每次等待 `RETRY_BACKOFF_BASE * (2 ** (attempt-1))` 秒。
    若所有尝试均失败，则抛出最后一次异常。
    """
    def wrapper(*args, **kwargs):
        for attempt in range(1, MAX_RETRY_COUNT + 1):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                log(f"⚠️ {_retry.__name__} – {func.__name__} 第 {attempt} 次失败: {e}")
                if attempt == MAX_RETRY_COUNT:
                    raise
                time.sleep(RETRY_BACKOFF_BASE * (2 ** (attempt - 1)))
    return wrapper
    # 注意：不使用 functools.wraps，保持简洁直观，避免异常追踪层级过深。


# ----------------------------------------------------------------------
# 4️⃣ 单工程唯一标识管理（确保只创建一次工程）
# ----------------------------------------------------------------------
def _load_last_project_info() -> Dict[str, Any]:
    """
    读取 `.last_project_info.json`，若文件不存在或解析错误返回空 dict。
    结构示例：
    {
        "project_dir": "/abs/path/to/project",
        "task_id": "task_20260510_212024",
        "completed": false
    }
    """
    if not LAST_PROJECT_INFO.is_file():
        return {}
    try:
        with LAST_PROJECT_INFO.open("r", encoding="utf-8") as f:
            data = json.load(f)
        # 基础字段校验，缺失任一视为无效
        if not all(k in data for k in ("project_dir", "task_id", "completed")):
            return {}
        return data
    except Exception as e:
        _write_error("读取上一次项目记录", e)
        return {}


def _save_last_project_info(info: Dict[str, Any]) -> None:
    """
    将当前工程信息写入 ``.last_project_info.json``，确保写入成功后 flush。
    """
    try:
        # 必须对文件对象调用 flush，而不是对 Path 本身。
        with LAST_PROJECT_INFO.open("w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=2)
            f.flush()                      # ← 正确的 flush 调用
    except Exception as e:
        _write_error("保存上一次项目记录", e)

def _remove_last_project_info() -> None:
    """
    删除持久化的工程记录文件，通常在工程**完成**后调用。
    """
    try:
        if LAST_PROJECT_INFO.is_file():
            LAST_PROJECT_INFO.unlink()
    except Exception as e:
        _write_error("删除上一次项目记录", e)


# ----------------------------------------------------------------------
# 5️⃣ 辅助函数 – 随机生成项目指令（保持原有随机化特性）
# ----------------------------------------------------------------------
def _random_chinese_name() -> str:
    """生成一个随机的中文人物名字（姓+名）。"""
    surnames = ["赵","钱","孙","李","周","吴","郑","王","冯","陈","褚","卫",
                "蒋","沈","韩","杨","朱","秦","尤","许"]
    given   = ["浩","轩","宇","澈","霖","哲","辰","熙","铭","烨","朗","宁",
                "琪","雯","珂","雅","菲","妍","欣","然"]
    return random.choice(surnames) + random.choice(given)


def _random_title() -> str:
    """随机生成一个带有奇怪组合的项目标题。"""
    adjectives = ["星际","古代","赛博","蒸汽","异能","时空","幻影","血色","梦境","荒原"]
    nouns      = ["王座","纪元","遗迹","曙光","逆流","轮回","神器","暗潮","终章","黎明"]
    return random.choice(adjectives) + random.choice(nouns) + "：" + _random_chinese_name() + "的奇遇"


def _random_genre_combo() -> str:
    """随机生成一组（可能冲突）的题材组合。"""
    genres = ["奇幻","玄幻","科幻","武侠","都市","历史","悬疑","恐怖",
              "轻小说","魔法","古代","现代","后末日"]
    chosen = random.sample(genres, k=random.randint(1, 3))
    return " + ".join(chosen)


def _random_world_type() -> str:
    """随机返回可能的世界类型（包括奇怪组合）。"""
    types = ["single","infinite","multi","古代+未来","赛博朋克+古典",
             "平行宇宙+现实","无限流+低武","星际+废土"]
    return random.choice(types)


def _build_random_create_prompt() -> Tuple[str, int, int]:
    """
    生成一个极端、随机组合的“创建项目”指令，返回：
    (prompt, total_words, words_per_chapter)
    """
    title   = _random_title()
    genre   = _random_genre_combo()
    world   = _random_world_type()
    main_ch = _random_chinese_name()

    total_words    = random.randint(5_000, 100_000)   # 5k~100k
    words_per_chap = random.randint(1_800, 3_000)   # 1.8k~3k

    prompt = (f"请创建工程文件，项目名《{title}》，"
              f"题材：{genre}，"
              f"世界类型：{world}，"
              f"主角名：{main_ch}，"
              f"总字数：{total_words}，"
              f"每章字数：{words_per_chap}")
    return prompt, total_words, words_per_chap


# ----------------------------------------------------------------------
# 6️⃣ 核心步骤包装（每一步均使用 @_retry 进行容错）
# ----------------------------------------------------------------------
@_retry
def _create_project(controller: Any, create_prompt: str) -> Tuple[Dict[str, Any], str, str]:
    """
    调用 AIController 创建项目，返回 (response, task_id, project_dir)。
    若返回的 `action` 为 `project_created`（未自动生成大纲）亦返回。
    若返回 `chat`，则视为创建失败并抛异常。
    """
    log("⚡ 开始项目创建")
    resp = controller.ask_ai(create_prompt)

    # 判定返回的 action 是否符合预期
    action = resp.get("action")
    if action not in ("project_created", "project_created_and_outline_generated", "chat"):
        raise RuntimeError(f"创建项目返回异常 action: {action}")

    # 提取 task_id 与 project_dir（可能在不同层级）
    task_id = resp.get("task_id") or controller._get_current_task_id()
    project_dir = resp.get("project_dir") or resp.get("project_info", {}).get("project_dir")

    if not project_dir or not os.path.isdir(project_dir):
        raise RuntimeError("创建项目后未检测到有效的项目目录")

    log(f"✅ 项目创建成功，task_id={task_id}, project_dir={project_dir}")
    return resp, task_id, project_dir


@_retry
def _ensure_outline(controller: Any, task_id: str, init_resp: Dict[str, Any]) -> Dict[str, Any]:
    """
    确保大纲已生成并返回最新的大纲响应。
    - 若 `init_resp` 已包含 `outline` → 直接返回。
    - 否则发送 “生成大纲” 并返回响应。
    在失败情况下调用 `controller._record_outline_failure` 记录经验。
    """
    if "outline" in init_resp and init_resp["outline"]:
        log("✅ 大纲已在项目创建阶段自动生成")
        return init_resp

    log("🔎 大纲未自动生成，手动请求生成大纲")
    resp = controller.ask_ai("生成大纲")
    action = resp.get("action")
    if action != "outline_generated":
        # 记录失败经验并抛异常，后续可在外层捕获重试
        controller._record_outline_failure(task_id, "outline generation failed", {"response": resp})
        raise RuntimeError(f"大纲生成失败，返回 action={action}")
    log("✅ 大纲手动生成成功")
    return resp


@_retry
def _confirm_outline(controller: Any) -> Dict[str, Any]:
    """
    发送 “确认大纲” 请求，确保返回 `chat`（即大纲已确认）。
    """
    log("✅ 发送大纲确认指令")
    resp = controller.ask_ai("确认大纲")
    if resp.get("action") != "chat":
        raise RuntimeError("大纲确认返回异常")
    log("✅ 大纲已确认")
    return resp


@_retry
def _write_chapter(controller: Any, chapter_no: int) -> Dict[str, Any]:
    """
    发送 “写第 X 章” 指令并返回响应。
    若返回的 `action` 不是 `chat`，视为错误并抛异常。
    """
    log(f"✍️ 开始写第 {chapter_no} 章")
    resp = controller.ask_ai(f"写第 {chapter_no} 章")
    if resp.get("action") != "chat":
        raise RuntimeError(f"写第 {chapter_no} 章返回异常 action={resp.get('action')}")
    # 将章节内容写入报告（截取前 200 字符做预览）
    excerpt = resp.get("explanation", "")[:200] + ("..." if len(resp.get("explanation", "")) > 200 else "")
    _write_report(f"【第 {chapter_no} 章响应预览】\n{excerpt}")
    log(f"✅ 第 {chapter_no} 章生成完成")
    return resp


@_retry
def _run_health_check(controller: Any, task_id: str, chapter_no: int) -> Dict[str, Any]:
    """
    调用 AIController 私有方法 `_run_health_check`，若检查失败且开启自动重建大纲，
    则尝试调用 `controller._auto_generate_outline` 自动修复。
    """
    log(f"🩺 执行健康检查（第 {chapter_no} 章）")
    health_res = controller._run_health_check(task_id, current_chapter=chapter_no)
    if not health_res.get("success"):
        log(f"⚠️ 健康检查未通过：{health_res.get('error', '未知错误')}")
        # 若配置允许自动重建，则尝试一次
        if getattr(controller, "HEALTH_CHECK_AUTO_REGENERATE", False):
            log("🔧 尝试自动重建大纲（健康检查回退）")
            auto_res = controller._auto_generate_outline(controller.task_planner.get_plan(task_id),
                                                      f"健康检查自动重建（章节 {chapter_no})")
            if auto_res.get("action") == "outline_generated":
                log("✅ 自动重建大纲成功")
                # 重建成功后再次执行健康检查保证状态恢复
                health_res = controller._run_health_check(task_id, current_chapter=chapter_no)
            else:
                log(f"❌ 自动重建大纲仍失败：{auto_res.get('explanation','未知')}")
        else:
            raise RuntimeError(f"健康检查失败且未启用自动重建（错误:{health_res.get('error','未知')})")
    log("✅ 健康检查通过")
    return health_res


def _all_chapters_done(controller: Any, task_id: str) -> bool:
    """
    检查任务计划和进度追踪器，判断是否已完成全部章节。
    """
    plan = controller.task_planner.get_plan(task_id)
    if not plan:
        return False
    total = plan.get("total_chapters") or plan.get("total_chapters", 0)
    progress = controller.progress_tracker.get_progress(task_id) or {}
    completed = progress.get("completed_chapters", 0)
    return completed >= total


@_retry
def _continue_generation_loop(controller: Any, task_id: str) -> None:
    """
    循环调用 “继续生成” 直至所有章节完成。
    使用 `_all_chapters_done` 判定结束，最多循环 `MAX_CONTINUE_LOOP` 次防止死循环。
    """
    loop_cnt = 0
    while not _all_chapters_done(controller, task_id):
        if loop_cnt >= MAX_CONTINUE_LOOP:
            raise RuntimeError(f"已达到最大循环次数 {MAX_CONTINUE_LOOP}，仍未完成全部章节")
        log(f"🔁 第 {loop_cnt + 1} 次继续生成请求")
        resp = controller.ask_ai("继续生成")
        if resp.get("action") != "chat":
            raise RuntimeError(f"继续生成返回异常 action={resp.get('action')}")
        _write_report(json.dumps(resp, ensure_ascii=False, indent=2))
        time.sleep(1)   # 防止对 Ollama 发送洪峰
        loop_cnt += 1
    log("✅ 所有章节已生成完成")


def _finalize(controller: Any, task_id: str, project_dir: str) -> None:
    """
    **收尾工作**（保存学习历史、执行自检、写入最终报告、清理记忆文件）。
    若任务已完成，则删除 `.last_project_info.json`，确保下次运行不会再创建新工程。
    """
    log("🔚 开始执行最终收尾工作")
    try:
        # ① 保存学习历史（如果存在该子模块）
        if hasattr(controller, "learning_engine"):
            controller._save_learning_history()
            log("✅ 学习历史已保存")
        # ② 调用自检与自动修复（如果可用）
        if hasattr(controller, "self_check_and_autofix"):
            log("🔧 执行自检与自动修复")
            controller.self_check_and_autofix(log)
            log("✅ 自检与自动修复完成")
    except Exception as e:
        _write_error("收尾工作（学习历史/自检）", e)

    # ③ 写入最终状态信息到报告
    final_plan = controller.task_planner.get_plan(task_id) or {}
    final_progress = controller.progress_tracker.get_progress(task_id) or {}
    _write_report("\n=== 最终任务计划 ===")
    _write_report(json.dumps(final_plan, ensure_ascii=False, indent=2))
    _write_report("\n=== 最终进度记录 ===")
    _write_report(json.dumps(final_progress, ensure_ascii=False, indent=2))

    total_chapters = final_plan.get("total_chapters", "?")
    completed = final_progress.get("completed_chapters", "?")
    _write_report(f"\n📊 章节总数 → {total_chapters}，已完成 → {completed}")

    # ④ 若任务已标记为 completed，则删除持久化的工程记录文件
    if final_plan.get("status") == "completed":
        _remove_last_project_info()
        log("🗑️ 已删除上一次工程记录文件，保证下次运行不会重复创建")
    else:
        # 若未完成，保留信息以便下次继续
        _save_last_project_info({
            "project_dir": project_dir,
            "task_id": task_id,
            "completed": False
        })
        log("⚠️ 任务未完成，已保留工程信息以便后续继续")

    log("✅ 收尾工作全部完成")


# ----------------------------------------------------------------------
# 7️⃣ 主流程入口
# ----------------------------------------------------------------------
def main() -> None:
    """
    主函数：负责读取/写入工程标识、执行完整流水线、捕获异常并写入报告。
    整体思路：
    1. 检查 `.last_project_info.json` → 若已有未完成任务则复用
    2. 否则随机生成创建指令并创建新项目
    3. 完成大纲、确认、章节写作、健康检查、循环生成
    4. 最终收尾（保存学习历史、自动修复、报告写入）
    """
    # --------------------------------------------------------------
    # ① 环境准备
    # --------------------------------------------------------------
    sys.path.append(str(Path(__file__).parent))
    from core.ai_controller import AIController

    # 实例化 AIController 并绑定日志回调
    controller = AIController(log_callback=log)

    # --------------------------------------------------------------
    # ② 读取上一次工程信息（如果存在且未完成则直接复用）
    # --------------------------------------------------------------
    _write_report("=== 项目完整流水线检测报告 ===")
    _write_report(f"启动时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    _write_report("-" * 70)

    last_info = _load_last_project_info()
    if last_info and not last_info.get("completed", True):
        # 读取已有任务信息
        project_dir = last_info["project_dir"]
        task_id = last_info["task_id"]
        log(f"🔁 检测到未完成的上一次工程，直接复用 task_id={task_id}")
        # 为安全，确认任务仍在 task_planner 中；若不存在则走新建流程
        if not controller.task_planner.get_plan(task_id):
            log("⚠️ 上一次的 task_id 已不存在，准备重新创建工程")
            last_info = {}
        else:
            # 只在报告中记录目录结构（不重新遍历全盘）
            if os.path.isdir(project_dir):
                _write_report("\n【项目目录结构（仅前两层）】")
                for root, dirs, files in os.walk(project_dir):
                    level = root.count(os.sep) - project_dir.count(os.sep)
                    if level > 1:
                        break
                    rel_root = os.path.relpath(root, project_dir)
                    _write_report(f"目录: {rel_root}")
                    for d in dirs:
                        _write_report(f"  📁 {d}")
                    for f in files:
                        _write_report(f"  📄 {f}")
            else:
                log("⚠️ 记录的项目目录不存在，仍继续执行后续流程")
    # --------------------------------------------------------------
    # ③ 若无有效记录 → 进入全新项目创建流程
    # --------------------------------------------------------------
    if not last_info:
        # 随机生成创建指令
        create_prompt, total_words, words_per_chap = _build_random_create_prompt()
        _write_report("\n=== 第 1 步 – 项目创建（随机组合） ===")
        _write_report(f"使用的创建指令 → {create_prompt}")

        # 记录意图置信度（可选）帮助定位潜在错误
        try:
            pending = controller._safe_pending_tasks()
            intent, fallback = decide_intent(controller, create_prompt)   # 参考原脚本的辅助函数
            _write_report(f"[Intent] primary_intent={intent.get('primary_intent')}, confidence={intent.get('confidence')}")
        except Exception as e:
            _write_error("意图分析（创建项目）", e)

        # 正式创建项目（可能会重试）
        init_resp, task_id, project_dir = _create_project(controller, create_prompt)

        # 将本次工程信息写入持久化文件，以防脚本异常退出后仍能恢复
        _save_last_project_info({
            "project_dir": project_dir,
            "task_id": task_id,
            "completed": False
        })
        log("✅ 本次工程信息已持久化")
    # --------------------------------------------------------------
    # ④ 确保大纲已生成
    # --------------------------------------------------------------
    _write_report("\n=== 第 2 步 – 确保大纲已生成 ===")
    # 此时 `init_resp` 只在新建项目时可用；若是复用旧任务则直接读取最新的响应
    init_resp = {} if not "init_resp" in locals() else locals()["init_resp"]
    outline_resp = _ensure_outline(controller, task_id, init_resp)
    outline = outline_resp.get("outline", {})
    _write_report(f"大纲标题 → {outline.get('title', '未获取')}")

    # --------------------------------------------------------------
    # ⑤ 大纲确认
    # --------------------------------------------------------------
    _write_report("\n=== 第 3 步 – 大纲确认 ===")
    _confirm_outline(controller)

    # --------------------------------------------------------------
    # ⑥ 写第 1、2 章（分别调用单独函数，确保异常可捕获并重试）
    # --------------------------------------------------------------
    _write_report("\n=== 第 4 步 – 写第 1 章 ===")
    _write_chapter(controller, 1)

    _write_report("\n=== 第 5 步 – 写第 2 章 ===")
    _write_chapter(controller, 2)

    # --------------------------------------------------------------
    # ⑦ 手动触发一次健康检查（第 2 章后）
    # --------------------------------------------------------------
    _write_report("\n=== 第 6 步 – 手动健康检查（第 2 章） ===")
    _run_health_check(controller, task_id, current_chapter=2)

    # --------------------------------------------------------------
    # ⑧ (可选) 批量生成剩余章节 —— 如需跳过请设置 SKIP_BATCH_GENERATION=True
    # --------------------------------------------------------------
    if not SKIP_BATCH_GENERATION:
        _write_report("\n=== 第 7 步 – 批量生成剩余章节 ===")
        # 本脚本的批量生成已被 `continue_generation` 包装，直接调用循环
        _continue_generation_loop(controller, task_id)
    else:
        _write_report("\n=== 第 7 步 – 跳过批量生成（仅保留继续生成） ===")
        _continue_generation_loop(controller, task_id)

    # --------------------------------------------------------------
    # ⑨ 最终状态检查与报告输出
    # --------------------------------------------------------------
    _write_report("\n=== 第 8 步 – 最终状态检查 ===")
    final_plan = controller.task_planner.get_plan(task_id) or {}
    final_progress = controller.progress_tracker.get_progress(task_id) or {}
    _write_report("【最终任务计划】")
    _write_report(json.dumps(final_plan, ensure_ascii=False, indent=2))
    _write_report("【最终进度记录】")
    _write_report(json.dumps(final_progress, ensure_ascii=False, indent=2))

    total_chapters = final_plan.get("total_chapters", "?")
    completed = final_progress.get("completed_chapters", "?")
    _write_report(f"章节总数 → {total_chapters}，已完成 → {completed}")

    # --------------------------------------------------------------
    # ⑩ 收尾（保存学习历史、执行自检、清理标记文件）
    # --------------------------------------------------------------
    _finalize(controller, task_id, project_dir)

    # --------------------------------------------------------------
    # 结束日志
    # --------------------------------------------------------------
    end_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _write_report("\n🎉 流水线全部执行完毕！")
    _write_report(f"结束时间：{end_ts}")
    _write_report(f"项目所在目录：{project_dir}")
    _write_report("-" * 70)

    REPORT_FILE.close()
    print(f"\n✅ 详细报告已写入: {REPORT_PATH}")


# ----------------------------------------------------------------------
# 8️⃣ 辅助函数 – 用于意图置信度判定（保留自原脚本，供调试使用）
# ----------------------------------------------------------------------
# run_project_flow_test.py
def decide_intent(controller: Any, prompt: str):
    """
    使用 AIController 内部的 IntentAnalyzer 再一次获取置信度，返回
    (intent_dict, is_fallback)。
    """
    pending = controller._safe_pending_tasks()
    task_status = pending[0].get('status') if pending else 'none'
    task_id = pending[0].get('task_id') if pending else ''

    # -----------------------------------------------------------------
    # ⛔ 关键防御：如果 task_id 为空，直接认为大纲不存在
    # -----------------------------------------------------------------
    if task_id:
        outline_exists = bool(controller.outline_manager.get_outline(task_id))
    else:
        outline_exists = False   # 没有任务时直接返回 False，避免抛异常

    intent = controller.intent_analyzer.analyze_intent(
        prompt,
        context={
            'pending_tasks': pending,
            'current_project_id': controller.current_project_id,
            'task_status': task_status,
            'outline_exists': outline_exists
        }
    )
    confidence = float(intent.get('confidence', 0))
    # 使用全局阈值（constants 中已统一）而不是硬编码 0.75
    is_fallback = confidence < INTENT_CONFIDENCE_THRESHOLD

    _write_report(f"[Intent 判定] Prompt: \"{prompt}\"")
    _write_report(f"    -> primary_intent: {intent.get('primary_intent')}, confidence: {confidence:.2f}")
    _write_report(f"    -> {'使用关键字回退' if is_fallback else 'LLM 直接识别'}")
    return intent, is_fallback


# ----------------------------------------------------------------------
# 9️⃣ 脚本入口
# ----------------------------------------------------------------------
if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        _write_error("全局", exc)
        REPORT_FILE.close()
        print(f"\n❌ 脚本执行出错，详情已写入 {REPORT_PATH}")
        sys.exit(1)
