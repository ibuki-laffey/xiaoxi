import os
import uuid
import time
import random
import string
import json
from datetime import datetime

# 关闭自动备份，避免刷屏日志
os.environ["ENABLE_PROMPT_DUMP"] = "0"

from core.ai_controller import AIController


def _random_str(length: int = 12, chars: str = string.ascii_letters + string.digits) -> str:
    """生成指定长度的随机字符串。"""
    return ''.join(random.choices(chars, k=length))


def _make_test_dir() -> str:
    """在当前工作目录下创建唯一测试文件夹，返绝对路径。"""
    test_id = uuid.uuid4().hex[:8]
    test_dir = os.path.join(os.getcwd(), f"test_run_{test_id}")
    os.makedirs(test_dir, exist_ok=True)
    return test_dir


def _wait_for_file(filepath: str, timeout: float = 20.0, poll_interval: float = 0.5) -> bool:
    """轮询等待文件出现，返回是否在超时前找到。"""
    elapsed = 0.0
    while elapsed < timeout:
        if os.path.isfile(filepath):
            return True
        time.sleep(poll_interval)
        elapsed += poll_interval
    return False


def _clear_controller_history(controller: AIController) -> None:
    """清空对话历史并强制写回（保证干净状态）。"""
    controller.conversation_history.clear()
    controller._save_conversation_history()


def _test_generation(controller: AIController, test_dir: str,
                     filename: str, request_str: str) -> dict:
    """
    执行单次文件生成测试并返回详细结果。
    :param controller: 已初始化的 AIController 实例
    :param test_dir:   用于保存生成文件的目录绝对路径
    :param filename:   期望生成的文件名（含扩展名）
    :param request_str: 已经拼接好的完整请求文本
    :return: 包含成功状态、文件路径、大小、AI 解释、耗时等信息的字典
    """
    _clear_controller_history(controller)

    start = time.time()
    print(f"\n[REQUEST] {request_str}")

    response = controller.ask_ai(request_str)
    explanation = response.get("explanation", "")
    print(f"[AI RESPONSE] {explanation}")

    filepath = os.path.join(test_dir, filename)
    found = _wait_for_file(filepath, timeout=20.0, poll_interval=0.5)
    elapsed = time.time() - start
    if found:
        size = os.path.getsize(filepath)
        print(f"[RESULT] ✅ 文件已生成：{filename}（大小：{size} 字节，耗时 {elapsed:.2f}s）")
        return {
            "success": True,
            "filepath": filepath,
            "size": size,
            "explanation": explanation,
            "elapsed": elapsed,
        }
    else:
        print(f"[RESULT] ❌ 在等待时间内未发现文件：{filename}")
        print(f"[DIR CONTENT] {os.listdir(test_dir)}")
        return {
            "success": False,
            "filepath": None,
            "size": 0,
            "explanation": explanation,
            "elapsed": elapsed,
        }


def _build_report(test_dir: str, results: dict) -> str:
    """生成 JSON 测试报告并写入文件，返回报告路径。"""
    report = {
        "test_dir": test_dir,
        "timestamp": datetime.now().isoformat(),
        "tests": {},
        "summary": {
            "total": len(results),
            "passed": sum(1 for v in results.values() if v["success"]),
            "failed": sum(1 for v in results.values() if not v["success"]),
        }
    }
    for name, info in results.items():
        report["tests"][name] = {
            "success": info["success"],
            "elapsed_sec": round(info["elapsed"], 3),
            "file_size_bytes": info["size"] if info["success"] else 0,
            "excerpt_explanation": (
                info["explanation"][:200] + "…"
                if len(info["explanation"]) > 200
                else info["explanation"]
            ),
        }
    report_path = os.path.join(test_dir, "test_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return report_path


def main() -> None:
    # 1️⃣ 创建唯一测试目录
    test_dir = _make_test_dir()
    print(f"[INFO] 测试目录已创建：{test_dir}")

    # 2️⃣ 实例化 AIController（不使用日志回调）
    controller = AIController()

    # 3️⃣ 准备随机关键词库
    environments = ["森林", "沙漠", "海边", "城市夜景", "山间小屋", "未来实验室", "古代庙宇", "星际空间站"]
    locations = ["东京", "巴黎", "纽约", "撒哈拉", "亚马逊雨林", "珠穆朗玛峰", "深海沟壑", "火星殖民地"]
    persons = ["年轻女战士", "老巫师", "机器人伙伴", "探险家夫妇", "神秘猫耳少女", "星际舰长", "古代学者", "未来AI"]
    times = ["黎明时分", "正午阳光", "黄昏黄金时刻", "星夜微光", "雨后初晴", "暴风雨来临", "极光闪耀", "电子网络脉冲"]
    styles = ["超写实", "赛博朋克", "水彩画", "低Poly", "像素艺术", "油画", "概念艺术", "手绘 sketch"]

    # ---------- 文本文件 ----------
    txt_content = _random_str(48)
    txt_base = f"test_{uuid.uuid4().hex}"
    txt_filename = f"{txt_base}.txt"
    txt_request = (
        f"请在目录 '{test_dir}' 下**生成**一个名为 '{txt_filename}' 的纯文本文件，"
        f"文件内容为：{txt_content}。"
    )

    # ---------- 图片文件 ----------
    env = random.choice(environments)
    loc = random.choice(locations)
    per = random.choice(persons)
    tim = random.choice(times)
    sty = random.choice(styles)
    img_base = f"test_{uuid.uuid4().hex}"
    img_filename = f"{img_base}.png"
    img_request = (
        f"请在目录 '{test_dir}' 下**生成**一个名为 '{img_filename}' 的图片文件，"
        f"画面描述：{env}的{loc}，{per}在{tim}出现，风格为{sty}，"
        f"分辨率 1024x1024像素，色彩丰富，细节锐利，适合作为概念艺术使用。"
    )

    # ---------- 视频文件 ----------
    vid_duration = random.randint(15, 30)          # 15‑30秒随机
    vid_env = random.choice(environments)
    vid_loc = random.choice(locations)
    vid_per = random.choice(persons)
    vid_tim = random.choice(times)
    vid_sty = random.choice(styles)
    vid_base = f"test_{uuid.uuid4().hex}"
    vid_filename = f"{vid_base}.mp4"
    vid_request = (
        f"请在目录 '{test_dir}' 下**生成**一个名为 '{vid_filename}' 的视频文件，"
        f"时长 {vid_duration} 秒，分辨率 1280x720，帧率 30fps，"
        f"画面描述：{vid_env}的{vid_loc}，{vid_per}在{vid_tim}进行活动，风格为{vid_sty}，"
        f"包含平滑的摄像机移动、光影变化以及粒子特效，确保视频流畅无卡顿。"
    )

    # ---------- 音频文件 ----------
    audio_freq = random.choice([261, 294, 329, 349, 392, 440, 494, 523])  # C4~C5
    audio_dur = random.choice([5, 8, 10, 12])      # 秒
    audio_wave = random.choice(["正弦波", "方波", "锯齿波", "白噪声"])
    audio_base = f"test_{uuid.uuid4().hex}"
    audio_filename = f"{audio_base}.wav"
    audio_request = (
        f"请在目录 '{test_dir}' 下**生成**一个名为 '{audio_filename}' 的音频文件（WAV 格式），"
        f"时长 {audio_dur} 秒，采样率 44100 Hz，单声道，"
        f"波形为{audio_wave}，基频为{audio_freq} Hz，幅度为 0.6，"
        f"音色饱满，适合作为背景音乐或特效使用。"
    )

    # ---------- 压缩包 ----------
    zip_inner_a = f"inner_{_random_str(6)}.txt"
    zip_inner_b = f"inner_{_random_str(6)}.txt"
    zip_inner_content_a = _random_str(30)
    zip_inner_content_b = _random_str(30)
    zip_base = f"test_{uuid.uuid4().hex}"
    zip_filename = f"{zip_base}.zip"
    zip_request = (
        f"请在目录 '{test_dir}' 下**创建**一个名为 '{zip_filename}' 的 ZIP 压缩包，"
        f"内部包含以下两个文件：\n"
        f"1. {zip_inner_a}：内容为「{zip_inner_content_a}」\n"
        f"2. {zip_inner_b}：内容为「{zip_inner_content_b}」\n"
        f"请确保压缩结构正确，解压后可直接读取。"
    )

    # ---------- 小程序 ----------
    prog_name = f"demo_{_random_str(6)}.py"
    prog_request = (
        f"请在目录 '{test_dir}' 下**创建**一个名为 '{prog_name}' 的 Python 脚本，"
        f"脚本功能为：\n"
        f"1. 导入 datetime、time、random 模块；\n"
        f"2. 获取当前本地时间并格式化为 \"%Y-%m-%d %H:%M:%S\"；\n"
        f"3. 将该时间戳打印到控制台；\n"
        f"4. 将同一时间戳追加写入同目录下的 run.log 文件（每次运行一行）；\n"
        f"5. 生成一个 0~100 的随机整数并写入同目录下的 rand.log；\n"
        f"6. 脚本应具备完整的异常捕获，确保在任何 I/O 失败时仅打印错误信息而不崩溃。\n"
        f"请直接输出完整的可运行 Python 代码。"
    )

    # ---------- 小文章 ----------
    article_title = f"《{_random_str(8)}》的探索"
    article_abstract = _random_str(40)
    article_sections = [
        f"## 背景\n{_random_str(60)}",
        f"## 方法\n{_random_str(60)}",
        f"## 结果\n{_random_str(60)}",
        f"## 结论\n{_random_str(40)}",
    ]
    article_body = "\n\n".join(article_sections)
    article_filename = f"article_{_random_str(6)}.md"
    article_request = (
        f"请在目录 '{test_dir}' 下**生成**一个名为 '{article_filename}' 的 markdown 文章，"
        f"文章标题为：{article_title}\n"
        f"文章摘要：{article_abstract}\n"
        f"正文内容如下（请保持 markdown 格式）：\n{article_body}\n"
        f"文章结构应包括标题、摘要、至少三个章节（背景、方法、结果、结论），"
        f"语言通顺、逻辑清晰，适合作为技术博客或学习笔记使用。"
    )

    # 4️⃣ 依次执行测试
    results = {}
    results["txt"] = _test_generation(controller, test_dir, txt_filename, txt_request)
    results["png"] = _test_generation(controller, test_dir, img_filename, img_request)
    results["mp4"] = _test_generation(controller, test_dir, vid_filename, vid_request)
    results["wav"] = _test_generation(controller, test_dir, audio_filename, audio_request)
    results["zip"] = _test_generation(controller, test_dir, zip_filename, zip_request)

    # 小程序（特殊文件名）
    prog_start = time.time()
    print(f"\n[REQUEST] {prog_request}")
    prog_response = controller.ask_ai(prog_request)
    prog_explanation = prog_response.get("explanation", "")
    print(f"[AI RESPONSE] {prog_explanation}")
    prog_elapsed = time.time() - prog_start
    prog_path = os.path.join(test_dir, prog_name)
    prog_found = _wait_for_file(prog_path, timeout=20.0, poll_interval=0.5)
    if prog_found:
        prog_size = os.path.getsize(prog_path)
        print(f"[RESULT] ✅ 文件已生成：{prog_name}（大小：{prog_size} 字节，耗时 {prog_elapsed:.2f}s）")
        results["py"] = {
            "success": True,
            "filepath": prog_path,
            "size": prog_size,
            "explanation": prog_explanation,
            "elapsed": prog_elapsed,
        }
    else:
        print(f"[RESULT] ❌ 在等待时间内未发现文件：{prog_name}")
        print(f"[DIR CONTENT] {os.listdir(test_dir)}")
        results["py"] = {
            "success": False,
            "filepath": None,
            "size": 0,
            "explanation": prog_explanation,
            "elapsed": prog_elapsed,
        }

    # 小文章
    article_start = time.time()
    print(f"\n[REQUEST] {article_request}")
    article_response = controller.ask_ai(article_request)
    article_explanation = article_response.get("explanation", "")
    print(f"[AI RESPONSE] {article_explanation}")
    article_elapsed = time.time() - article_start
    article_path = os.path.join(test_dir, article_filename)
    article_found = _wait_for_file(article_path, timeout=20.0, poll_interval=0.5)
    if article_found:
        article_size = os.path.getsize(article_path)
        print(f"[RESULT] ✅ 文件已生成：{article_filename}（大小：{article_size} 字节，耗时 {article_elapsed:.2f}s）")
        results["md"] = {
            "success": True,
            "filepath": article_path,
            "size": article_size,
            "explanation": article_explanation,
            "elapsed": article_elapsed,
        }
    else:
        print(f"[RESULT] ❌ 在等待时间内未发现文件：{article_filename}")
        print(f"[DIR CONTENT] {os.listdir(test_dir)}")
        results["md"] = {
            "success": False,
            "filepath": None,
            "size": 0,
            "explanation": article_explanation,
            "elapsed": article_elapsed,
        }

    # 5️⃣ 生成并保存运行报告
    report_path = _build_report(test_dir, results)
    print(f"\n[INFO] 测试报告已生成：{report_path}")

    # 6️⃣ 输出测试汇总
    print("\n" + "=" * 70)
    print("[SUMMARY] 测试结果汇总")
    for typ, info in results.items():
        status = "PASS" if info["success"] else "FAIL"
        print(f"  {typ.upper():<4} : {status} (耗时 {info['elapsed']:.2f}s)")
    summary = {
        "total": len(results),
        "passed": sum(1 for v in results.values() if v["success"]),
        "failed": sum(1 for v in results.values() if not v["success"]),
    }
    print(f"\n[INFO] 总计：{summary['total']} 项，通过：{summary['passed']}，失败：{summary['failed']}")
    if summary["failed"] == 0:
        print("\n[INFO] 全部测试通过！")
    else:
        print("\n[WARN] 部分测试未通过，请检查 AI 的文件生成能力或相关插件/工具是否可用。")


if __name__ == "__main__":
    main()
