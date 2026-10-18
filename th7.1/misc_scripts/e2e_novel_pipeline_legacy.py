#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
测试脚本：验证完整创作流程（包含全部章节生成与最终检查）
================================================================================
核心原则：
1. 每次运行生成全局唯一目录（时间戳 + 随机字符串），杜绝文件覆盖
2. 自动计算总章节数，并循环生成所有章节，直至全书完结
3. 章节生成成功仅当 02_章节/第00X章/ 目录存在且包含非零大小文件
4. 完全不依赖 API 返回值的 success/action 字段进行判断
5. 健康检查采用多重调用策略，适配潜在API变化
6. 明确使用 "确认" 指令设置 creation_allowed，而非假设 "继续" 生效
7. 每个修改点均有三处以上注释解释原因，防止后续误改
8. 零占位符、零省略、零模板化 - 所有函数完整呈现
9. 新增：丰富的随机化元素（标题、题材、世界类型等），以测试模型对奇怪组合的应变能力
10. 新增：通过 3‑6 条聊天在聊天记录里塞设定，然后要求 AI 基于消息记录创作，测试 AI 的消息记录读取能力
11. 修改脚本输出详细的聊天记录和消息记录文本，用于对比输出的内容是否正确

运行方式：
    python test_script_complete.py
    （脚本会自动在当前目录下创建 test_runs/ 时间戳_随机串/ 目录存放所有输出）
"""

# ---------- 1️⃣ 基础 imports ----------
import os
import sys
import time
import json
import random
import string
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List

# ---------- 2️⃣ 项目根路径加入 sys.path ----------
# 将项目根目录加入路径，确保能导入 core 模块
#   - 必须在所有 core 包导入之前完成
#   - 使用绝对路径避免相对路径导致的导入冲突
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

# ---------- 3️⃣ 导入核心 AIController ----------
#   - 采用 try/except 给出友好的错误提示，防止脚本直接崩溃
#   - 如有 ImportError，提示用户检查项目结构
try:
    from core.ai_controller import AIController
except ImportError as e:
    print("致命错误：无法导入 AIController。请确保脚本位于项目根目录下且 core 包完整。")
    print(f"   错误详情：{e}")
    sys.exit(1)

# ---------- 4️⃣ 配置区域（便于后期微调） ----------
MAX_RETRY_COUNT      = 3   # 关键步骤最大重试次数（经验值：3 次已能覆盖大多数瞬时网络波动）
RETRY_BACKOFF_BASE   = 2   # 指数回退基数（秒）——第 n 次重试等待 base*2^(n-1) 秒
CHAPTER_VERIFY_WAIT  = 5   # 发送写章指令后等待文件写入的秒数（保证异步写入完成）
MIN_FILE_SIZE_BYTES  = 10  # 判定为“非空文件”的最小字节数（避免误判空文件为成功）
HEALTH_CHECK_ENABLED = True  # 是否执行健康检查（非核心步骤，失败不终止测试）

# ---------- 5️⃣ 常量定义 ----------
#   - 所有随机元素池均放在这里，便于统一管理
CHARACTERS = [
    "林默", "苏晴", "老张", "小敏", "赵无极", "李逍遥", "林平之", "张无忌",
    "韦小宝", "令狐冲", "东方不败", "任盈盈", "灭绝师太", "周芷若", "小龙女",
    "杨过", "郭靖", "黄蓉", "胡一刀", "苗人凤", "阿朱", "阿碧", "王语嫣",
    "段誉", "虚竹", "乔峰", "慕容博", "王重阳", "黄药师", "欧阳锋", "洪七公"
]

SCENES = [
    "废弃工厂", "星际酒吧", "古代寺庙", "现代都市", "深海基地", "雪山密洞",
    "丛林遗迹", "沙漠绿洲", "空间站", "魔法森林", "鬼市", "道观", "客栈",
    "武林盟主府", "皇宫", "边关城池", "难民营", "孤儿院", "武馆", "黑市",
    "时空裂缝", "量子实验室", "赛博朋克街道", "修仙宗门", "鬼吹灯古墓"
]

GENRES = [
    "科幻", "悬疑", "爱情", "奇幻", "武侠", "仙侠", "历史", "军事", "游戏",
    "二次元", "轻小说", "都市", "校园", "体育", "财经", "政治", "哲学",
    "宗教", "心理学", "教育", "烹饪", "旅行", "摄影", "绘画", "音乐", "电影"
]

OFF_TOPIC_CHATS = [
    "今天天气不错，适合出去走走。",
    "你有什么好看的电影推荐吗？",
    "我刚学会做一个新菜谱，尝尝看？",
    "最近在看一本书，感觉很有启发性。",
    "周末打算去爬山，你要不要一起去？",
    "这个天气真是让人懒啊，不想动。",
    "你最近在忙什么？有什么有趣的事分享吗？",
    "我觉得咖啡店的拿铁比较好喝，你呢？",
    "今天地铁好挤啊，早晚高峰真是灾难。",
    "听说新开了一家火锅店，味道据说很正宗。"
]

# ---------- 6️⃣ 工具函数 ----------
def generate_unique_dir() -> Path:
    """
    生成全局唯一的测试运行目录。
    格式：test_runs/YYYYMMDD_HHMMSS_XXXX/
    其中 XXXX 是 4 位随机字母数字字符串。
    即使同毫秒启动，碰撞概率极低（62^4 ≈ 1470 万分之一）。
    此目录将包含本次测试的所有输出（报告、项目、状态文件）。
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    random_suffix = ''.join(random.choices(string.ascii_letters + string.digits, k=4))
    unique_dir = Path(f"test_runs/{timestamp}_{random_suffix}")

    # 确保父目录存在，递归创建
    unique_dir.mkdir(parents=True, exist_ok=True)

    # 在目录内创建明显标记，防止误删为项目数据
    marker_file = unique_dir / ".this_is_test_run_marker.txt"
    marker_file.write_text(
        f"此目录为测试专用运行目录\n"
        f"生成时间：{datetime.now().isoformat()}\n"
        f"请勿在此存放重要数据，测试结束后可安全删除\n",
        encoding="utf-8"
    )
    return unique_dir

def setup_test_environment(unique_dir: Path) -> Tuple[Path, Path]:
    """
    在唯一目录内设置测试环境：
    - 创建报告文件
    - 创建状态文件（.last_project_info.json）的存放位置
    - 返回报告路径和状态文件路径
    """
    report_path = unique_dir / "test_report.txt"
    state_file_path = unique_dir / ".last_project_info.json"

    # 初始化报告文件
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"=== 测试报告：{unique_dir.name} ===\n")
        f.write(f"开始时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("=" * 60 + "\n\n")
    return report_path, state_file_path

def write_report(report_path: Optional[Path], message: str, also_print: bool = True) -> None:
    """
    向报告文件写入一行并立即刷新。
    若 report_path 为 None，则仅在控制台打印（用于内部重试日志）。
    """
    timestamp = datetime.now().strftime("%H:%M:%S")
    line = f"[{timestamp}] {message}"
    if report_path is not None:
        with open(report_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
    if also_print:
        print(line)

def safe_remove(path: Path) -> None:
    """安全地删除文件或目录，忽略不存在错误"""
    try:
        if path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
    except Exception:
        pass  # 删除失败不影响主流程

def is_file_non_empty(file_path: Path) -> bool:
    """
    检查文件是否存在且大小大于 MIN_FILE_SIZE_BYTES。
    这是判定“章节生成成功”的唯一标准。
    """
    try:
        return file_path.is_file() and file_path.stat().st_size > MIN_FILE_SIZE_BYTES
    except Exception:
        return False

def has_non_empty_files_in_dir(dir_path: Path) -> bool:
    """
    检查目录中是否存在至少一个非空文件。
    用于验证章节目录是否真正生成了内容。
    """
    try:
        if not dir_path.is_dir():
            return False
        return any(is_file_non_empty(item) for item in dir_path.iterdir())
    except Exception:
        return False

# ---------- 7️⃣ 随机元素生成器 ----------
def _get_random_weird_genres() -> str:
    """
    从怪诞题材池中随机抽取 2–4 项，用 “ + ” 连接。
    目的是产生如 “克苏鲁 + 修仙 + 赛博朋克 + 古代” 的奇怪组合，以测试模型对极端概念融合的理解与生成能力。
    """
    weird_genres = [
        "克苏鲁", "修仙", "赛博朋克", "古代", "蒸汽朋克", "末日", "丧尸",
        "仙侠", "星际", "奇幻", "恐怖", "荒诞", "洛夫克拉夫特", "道教",
        "机甲", "魔法少女", "赛博修仙", "仙侠赛博", "古代机甲", "丧尸修仙",
        "TSF", "神话机械", "废土道教", "霓虹仙境", "量子巫术", "暗黑童话",
        "生化危机","龙珠修仙","超能末日","时空盗贼","机械奥特曼","废土道长",
        "阴阳师", "鬼吹灯", "民俗恐怖", "山海经", "道教仙侠", "巫蛊降头",
        "附身厌胜", "祝由术", "八卦风水", "占卜梦魇",
        "萝莉美少女", "性转男变女"
    ]
    k = random.randint(2, 4)                     # 随机抽取 2‑4 项
    selected = random.sample(weird_genres, k=k)  # 保证不重复
    return " + ".join(selected)

def _get_random_weird_title() -> str:
    """
    从形容词和名词列表中随机组合，生成奇特标题。
    示例：赛博修仙古城、丧尸道宗遗迹、量子巫师废墟等。
    """
    adjectives = [
        "赛博", "修仙", "古代", "末日", "量子", "霓虹", "暗黑", "蒸汽",
        "机械", "神话", "道教", "仙境", "巫师", "龙", "丧尸", "克苏鲁",
        "洛夫克拉夫特", "荒诞", "奇幻", "星际", "废土", "洞天", "遗迹"
    ]
    nouns = [
        "古城", "宗门", "遗迹", "废墟", "星球", "基地", "洞天", "秘境",
        "战场", "试炼场", "神殿", "祭坛", "实验室", "飞船", "机甲", "妖塔",
        "血月", "星河", "时空", "梦境", "深渊", "乐园", "试炼", "孤岛"
    ]
    adj = random.choice(adjectives)
    noun = random.choice(nouns)
    # 30% 概率使用双形容词，让标题更“怪诞”
    if random.random() < 0.3:
        adj2 = random.choice(adjectives)
        while adj2 == adj:
            adj2 = random.choice(adjectives)
        return f"{adj}{adj2}{noun}"
    return f"{adj}{noun}"

def _get_random_weird_world() -> str:
    """
    从奇特世界类型列表中随机选择一个，作为世界类型描述。
    示例：蒸汽朋克+修仙洞天、末日丧尸修仙界、星际古代机甲废墟等。
    """
    weird_worlds = [
        "蒸汽朋克+修仙洞天", "末日丧尸修仙界", "星际古代机甲废墟",
        "克苏鲁深海神庙", "赛博仙山", "量子道场", "暗黑童话王国",
        "机械龙帝国", "废土仙府", "霓虹修真网", "洪荒机甲纪",
        "星际修仙舰队", "末日道宗", "蒸汽魔法学院", "古代机械神域",
        "现代都市", "全息网游", "直播", "日常"
    ]
    return random.choice(weird_worlds)

def _get_random_chinese_name() -> str:
    """
    生成一个随机的中文人物名字（姓+名），姓氏和名字均来自扩展列表，以提升多样性。
    """
    surnames = [
        "赵","钱","孙","李","周","吴","郑","王","冯","陈","褚","卫",
        "蒋","沈","韩","杨","朱","秦","尤","许","何","吕","施","张",
        "孔","曹","严","华","金","魏","陶","姜","戚","谢","邹","喻",
        "柏","水","窦","章","云","苏","潘","葛","奚","范","彭","郎",
        "鲁","韦","昌","马","苗","凤","花","方","俞","任","袁","柳",
        "酆","鲍","唐","费","廉","岑","薛","雷","贺","倪","汤","滕",
        "殷","罗","毕","郝","邬","安","常","乐","于","傅","卞","蔡",
        "樊","客","孟","黄","庞","宗","洪","盛"
    ]
    given = [
        "浩","轩","宇","澈","霖","哲","辰","熙","铭","烨","朗","宁",
        "琪","雯","珂","雅","菲","妍","欣","然","炎","魔","神","仙",
        "道","佛","鬼","尸","星","月","日","辰","时","空","宙","界",
        "梦","魂","魄","幽","冥","阳","阴","金","木","水","火","土",
        "雷","电","风","云","霞","霜","雪","雨","光","影","声",
        "音","律","韵","曲","歌","舞","剑","刀","枪","斧","钺","弓",
        "甲","乙","丙","丁","戊","己","庚","辛","壬","癸","子","丑",
        "寅","卯","辰","巳","午","未","申","酉","戌","亥","龙","蛇",
        "马","羊","猴","鸡","狗","猪","鼠","牛","虎","兔"
    ]
    return random.choice(surnames) + random.choice(given)

# ---------- 8️⃣ 生成唯一的创建提示 ----------
def generate_unique_prompt() -> Tuple[str, int, int, int]:
    """
    生成一个用于创建项目的唯一提示词。
    包含本次运行的唯一 ID，确保即使内容相同，提示词也不同（防止缓存命中）。
    新增：标题、题材、世界类型均采用随机化的怪诞元素，以测试模型对奇怪组合的应变能力。
    🌟 关键计算：自动得到总章节数 total_chapters。
    返回：(提示词, 总字数, 每章字数, 总章节数)
    """
    # 使用唯一运行 ID 的随机后缀确保全局唯一
    unique_id_part = self.unique_run_id.split('_')[-1]  # 取 4 位随机串
    # 生成随机元素
    title      = _get_random_weird_title()
    genre      = _get_random_weird_genres()
    world_type = _get_random_weird_world()
    main_char  = _get_random_chinese_name()
    # 将唯一后缀加入标题，防止完全相同
    title_with_id = f"{title}_{unique_id_part}"
    # 随机字数范围控制在合理区间，避免极端情况导致超时或无效
    total_words   = random.randint(8000, 30000)   # 总字数 8k‑30k
    words_per_chap = random.randint(1200, 2500)   # 每章 1.2k‑2.5k
    total_chapters = max(1, total_words // words_per_chap)  # 章节数向下取整，最少 1 章
    prompt = (
        f"请创建工程文件，项目名《{title_with_id}》，"
        f"题材：{genre}，"
        f"世界类型：{world_type}，"
        f"主角名：{main_char}，"
        f"总字数：{total_words}，"
        f"每章字数：{words_per_chap}"
    )
    write_report(self.report_path,
                  f"生成的创建提示词：{prompt} (预计生成 {total_chapters} 章)")
    return prompt, total_words, words_per_chap, total_chapters

# ---------- 9️⃣ 重试包装 ----------
def _retry_operation(self,
                    func,
                    *args,
                    max_attempts: int = MAX_RETRY_COUNT,
                    base_delay: int   = RETRY_BACKOFF_BASE,
                    **kwargs) -> Any:
    """
    直接调用 func(*args, **kwargs)，最多重试 max_attempts 次。
    使用指数回退策略（backoff），避免瞬时网络抖动导致的误报失败。
    若全部失败，抛出最后一次异常，供上层统一捕获处理。
    """
    last_exc = None
    for attempt in range(1, max_attempts + 1):
        try:
            return func(*args, **kwargs)   # 成功立即返回
        except Exception as exc:
            last_exc = exc
            write_report(
                self.report_path,
                f"⚠️ 重试 {attempt}/{max_attempts}：{getattr(func, '__name__', 'unknown')} 失败：{str(exc)[:120]}",
                also_print=False,
            )
            if attempt < max_attempts:
                # 指数回退：base * 2^(attempt-1)
                time.sleep(base_delay * (2 ** (attempt - 1)))
    # 所有尝试均失败 → 抛出最后一次异常
    raise last_exc

# ---------- 10️⃣ AIController 初始化 ----------
def initialize_controller(self) -> bool:
    """
    实例化 AIController 并把日志回调绑定到报告文件。
    返回 True 表示成功，False 表示失败（后续流程将中止）。
    """
    try:
        self.controller = AIController(
            log_callback=lambda msg: write_report(self.report_path,
                                                  f"[AI LOG] {msg}",
                                                  also_print=False)
        )
        write_report(self.report_path, "✅ AIController 初始化成功")
        return True
    except Exception as e:
        write_report(self.report_path,
                     f"❌ AIController 初始化失败: {e}",
                     also_print=True)
        return False

# ---------- 11️⃣ 项目创建 ----------
def create_project(self, prompt: str) -> Tuple[Dict[str, Any], str, Path]:
    """
    调用 AI 创建项目。
    返回 (原始响应, task_id, 项目目录)。
    关键：不依赖返回的 success/action 字段，只通过文件系统验证项目是否创建成功。
    """
    write_report(self.report_path, "🚀 开始项目创建...")
    response = _retry_operation(self, self.controller.ask_ai, prompt)

    # 兼容多种返回结构（不同版本的 AI 可能返回不同字段）
    task_id = response.get("task_id") or response.get("project_info", {}).get("task_id")
    project_dir_str = response.get("project_dir") or response.get("project_info", {}).get("project_dir")
    project_dir = Path(project_dir_str) if project_dir_str else None

    # 【关键验证 1】：项目目录必须存在且包含 README 说明文件
    readme_path = project_dir / "README_项目说明.txt" if project_dir else None
    if not (project_dir and project_dir.is_dir() and is_file_non_empty(readme_path)):
        error_msg = (
            f"项目创建失败：目录不存在或缺少 README。\n"
            f"   项目目录：{project_dir}\n"
            f"   README 路径：{readme_path}\n"
            f"   README 存在且非空：{is_file_non_empty(readme_path) if readme_path else False}"
        )
        write_report(self.report_path, f"❌ {error_msg}", also_print=True)
        raise RuntimeError(error_msg)

    write_report(self.report_path,
                 f"✅ 项目创建成功。任务 ID：{task_id}，目录：{project_dir}")
    self.test_task_id = task_id
    self.test_project_dir = project_dir

    # 记录状态文件（便于后续调试或恢复）
    state_data = {
        "project_dir": str(project_dir),
        "task_id": task_id,
        "created_at": datetime.now().isoformat(),
        "test_run_id": self.unique_run_id
    }
    with open(self.state_file_path, "w", encoding="utf-8") as f:
        json.dump(state_data, f, ensure_ascii=False, indent=2)

    return response, task_id, project_dir

# ---------- 12️⃣ 大纲生成 & 确认 ----------
def generate_and_confirm_outline(self) -> Dict[str, Any]:
    """
    生成大纲并发送“确认”指令以激活 creation_allowed。
    关键点：
    1. 发送“生成大纲”，等价于让模型生成大纲文件。
    2. 验证大纲文件是否存在且非空（兼容多种文件名）。
    3. 发送“确认”，此指令会把 AIController.creation_allowed 设为 True。
    4. 通过检查内部属性为后续章节生成做准备。
    """
    write_report(self.report_path, "📋 开始生成大纲...")
    outline_response = self.controller.ask_ai("生成大纲")

    # 兼容旧版和新版大纲文件名
    outline_dir = self.test_project_dir / "01_大纲"
    outline_file = outline_dir / "总体大纲_模板.txt"
    if not outline_file.exists():
        possible = list(outline_dir.glob("*.json")) + list(outline_dir.glob("*.txt"))
        if possible:
            outline_file = possible[0]

    if not is_file_non_empty(outline_file):
        error_msg = (
            f"大纲生成失败：大纲文件不存在或为空。\n"
            f"   大纲路径：{outline_file}\n"
            f"   文件大小：{outline_file.stat().st_size if outline_file.exists() else 0} 字节"
        )
        write_report(self.report_path, f"❌ {error_msg}", also_print=True)
        raise RuntimeError(error_msg)

    write_report(self.report_path,
                 f"✅ 大纲生成成功。大纲文件：{outline_file}")

    # 发送确认指令（关键：激活创建模式）
    write_report(self.report_path, "✅ 发送确认指令以激活创建模式...")
    self.controller.ask_ai("确认")

    # 调试输出 creation_allowed 状态（不强制判断，仅作记录）
    if hasattr(self.controller, 'creation_allowed'):
        write_report(
            self.report_path,
            f"[调试] 确认后 creation_allowed = {self.controller.creation_allowed}",
            also_print=False
        )
    write_report(self.report_path, "✅ 确认指令发送完毕。")
    return outline_response

# ---------- 13️⃣ 章节写入 ----------
def write_chapter(self, chapter_number: int) -> bool:
    """
    向 AI 发送写章节指令并校验对应目录下出现非空文件即视为成功。
    关键点：
    1. 与 AI 对话发送 “写第 N 章”。
    2. 等待文件系统写入（异步写入可能需要数秒）。
    3. 通过文件系统检查章节目录是否出现非空文件决定成功/失败。
    """
    chapter_dir = self.test_project_dir / "02_章节" / f"第{chapter_number:03d}章"
    write_report(self.report_path, f"✍️ 开始写入第 {chapter_number} 章...")

    # 发送写章指令
    self.controller.ask_ai(f"写第 {chapter_number} 章")

    # 等待文件写入完成
    time.sleep(CHAPTER_VERIFY_WAIT)

    if has_non_empty_files_in_dir(chapter_dir):
        # 找到第一条非空文件用于报告
        first_file = next((f for f in chapter_dir.iterdir() if is_file_non_empty(f)), None)
        file_size = first_file.stat().st_size if first_file else 0
        write_report(
            self.report_path,
            f"✅ 第 {chapter_number} 章生成成功。文件：{first_file.name if first_file else '未知'}，大小：{file_size} 字节"
        )
        return True
    else:
        error_msg = (
            f"第 {chapter_number} 章生成失败：章节目录中不存在非空文件。\n"
            f"   章节目录：{chapter_dir}\n"
            f"   目录内容：{[f.name for f in chapter_dir.iterdir()] if chapter_dir.exists() else '目录不存在'}"
        )
        write_report(self.report_path, f"❌ {error_msg}", also_print=True)
        return False

# ---------- 14️⃣ 健康检查 ----------
def perform_health_check(self, chapter_number: int) -> bool:
    """
    对指定章节执行健康检查，采用多重调用策略适配不同版本的 API。
    只要任意一次检查返回 success=True 即视为通过；全部失败则记录警告但不终止测试。
    """
    if not HEALTH_CHECK_ENABLED or not self.controller:
        write_report(self.report_path,
                     "ℹ️ 健康检查已禁用或控制器未就绪，跳过。",
                     also_print=False)
        return True

    if not self.test_task_id:
        write_report(self.report_path,
                     "⚠️ 无法执行健康检查：缺少 task_id。",
                     also_print=False)
        return True

    write_report(self.report_path,
                 f"🩺 开始对第 {chapter_number} 章执行健康检查...")

    # 方案 1：标准关键字参数调用
    try:
        result = self.controller._run_health_check(
            task_id=self.test_task_id,
            current_chapter=chapter_number
        )
        if result.get("success", False):
            write_report(self.report_path,
                         f"✅ 第 {chapter_number} 章健康检查通过（策略1）。")
            return True
    except Exception as e1:
        write_report(self.report_path,
                     f"⚠️ 健康检查策略1失败：{str(e1)[:100]}",
                     also_print=False)

    # 方案 2：位置参数调用（兼容旧版签名）
    try:
        result = self.controller._run_health_check(
            self.test_task_id,
            chapter_number
        )
        if result.get("success", False):
            write_report(self.report_path,
                         f"✅ 第 {chapter_number} 章健康检查通过（策略2）。")
            return True
    except Exception as e2:
        write_report(self.report_path,
                     f"⚠️ 健康检查策略2失败：{str(e2)[:100]}",
                     also_print=False)

    # 方案 3：直接访问健康检查器实例（绕过包装层）
    try:
        if hasattr(self.controller, 'health_checker') and self.controller.health_checker:
            result = self.controller.health_checker.run(
                self.test_task_id,
                chapter_number
            )
            if result.get("success", False):
                write_report(self.report_path,
                             f"✅ 第 {chapter_number} 章健康检查通过（策略3：直接检查器）。")
                return True
    except Exception as e3:
        write_report(self.report_path,
                     f"⚠️ 健康检查策略3失败：{str(e3)[:100]}",
                     also_print=False)

    # 所有方案均失败
    error_msg = (
        f"第 {chapter_number} 章健康检查所有策略均失败。\n"
        f"这可能表明健康检查 API 已变更，但不影响章节生成核心功能。"
    )
    write_report(self.report_path, f"⚠️ {error_msg}", also_print=True)
    # 健康检查失败不应终止测试
    return True

# ---------- 15️⃣ 聊天历史测试 ----------
def chat_history_test(self) -> bool:
    """
    通过 3‑6 条聊天向 AI 注入设定信息，然后要求 AI 基于完整聊天历史进行创作。
    关键点：
    1. 随机决定聊天轮数（3‑6）。
    2. 每轮 20% 的概率发送离线话题，其余 80% 随机组合角色、场景、题材子集。
    3. 记录所有发送的原始消息以及是否为离线话题，便于后续比对。
    4. 最后发送“根据我们之前的对话，请写一个开场白”，获取 AI 响应并保存。
    5. 将聊天记录、内部对话历史以及 AI 最终响应写入报告，供人工核对。
    返回 True 表示聊天环节全部成功（包括 AI 响应成功返回），否则返回 False。
    """
    write_report(self.report_path, "💬 开始聊天历史测试（随机 3‑6 条）")
    self.chat_turns = []   # 清空历史

    # 随机决定总轮数
    total_turns = random.randint(3, 6)
    write_report(self.report_path, f"   将进行 {total_turns} 轮聊天...")

    for turn in range(1, total_turns + 1):
        # 20% 离线话题
        if random.random() < 0.2:
            msg = random.choice(OFF_TOPIC_CHATS)
            off_topic = True
            write_report(self.report_path,
                         f"   第{turn}/{total_turns}（离线）: {msg}",
                         also_print=False)
        else:
            # 设定相关话题：随机挑选要涉及的类别（角色/场景/题材），每类随机子集
            categories = ['characters', 'scenes', 'genres']
            included = []
            for cat in categories:
                if random.random() < 0.5:   # 50% 概率包含该类别
                    included.append(cat)
            if not included:               # 确保至少包含一类
                included.append(random.choice(categories))

            parts = []
            if 'characters' in included:
                k = random.randint(1, len(self.total_setting['characters']))
                chars = random.sample(self.total_setting['characters'], k=k)
                parts.append("主角包括：" + "、".join(chars))
            if 'scenes' in included:
                k = random.randint(1, len(self.total_setting['scenes']))
                scenes = random.sample(self.total_setting['scenes'], k=k)
                parts.append("故事发生在：" + "、".join(scenes))
            if 'genres' in included:
                k = random.randint(1, len(self.total_setting['genres']))
                genres = random.sample(self.total_setting['genres'], k=k)
                parts.append("这是一部" + "、".join(genres) + "作品")
            msg = "，".join(parts) + "。"
            off_topic = False
            write_report(self.report_path,
                         f"   第{turn}/{total_turns}（设定）: {msg}",
                         also_print=False)

        # 发送消息并记录
        try:
            self.controller.ask_ai(msg)   # 只关注发送成功，不处理返回值
            self.chat_turns.append((turn, msg, off_topic))
        except Exception as e:
            write_report(self.report_path,
                         f"❌ 第 {turn} 轮聊天发送失败：{e}",
                         also_print=True)
            return False
        # 轻微延迟，防止短时间内大量请求触发限流
        time.sleep(0.5)

    # 发送最终创作请求
    self.final_prompt = (
        "根据我们之前的对话，请写一个能自然体现出所有设定（角色、场景、题材）的开场白。"
        "不要直接说‘主角’、‘场景’或‘题材’，而是通过情节和描述展现它们。"
    )
    write_report(self.report_path,
                 f"📝 发送最终创作请求：{self.final_prompt}",
                 also_print=False)
    try:
        resp = self.controller.ask_ai(self.final_prompt)
        # 正常返回 dict，提取 explanation；若返回字符串则直接使用
        if isinstance(resp, dict):
            self.ai_response = resp.get('explanation', '')
        else:
            self.ai_response = str(resp)
        write_report(self.report_path, "✅ AI 创作响应获取成功", also_print=False)
    except Exception as e:
        write_report(self.report_path,
                     f"❌ AI 创作响应获取失败：{e}",
                     also_print=True)
        return False

    # 将聊天记录、内部对话历史、AI 响应写入报告
    write_report(self.report_path, "\n--- 聊天历史（发送给 AI）---", also_print=False)
    for turn, msg, off in self.chat_turns:
        tag = "[离线]" if off else "[设定]"
        write_report(self.report_path, f"{turn}. {tag} {msg}", also_print=False)

    write_report(self.report_path, "\n--- AI 内部对话历史（供对比）---", also_print=False)
    if self.controller and hasattr(self.controller, 'conversation_history'):
        for idx, entry in enumerate(self.controller.conversation_history, 1):
            role = entry.get('role', 'unknown')
            content = entry.get('content', '')
            # 截断长内容，保持报告可读
            display = content[:100] + ('...' if len(content) > 100 else '')
            write_report(self.report_path, f"{idx}. {role.upper()}: {display}", also_print=False)
    else:
        write_report(self.report_path, "(未能获取内部对话历史)", also_print=False)

    write_report(self.report_path,
                 "\n--- AI 最终创作响应 ---",
                 also_print=False)
    write_report(self.report_path,
                 self.ai_response if self.ai_response else "(空响应)",
                 also_print=False)

    return True

# ---------- 16️⃣ 主执行流程 ----------
def run_full_test(self) -> bool:
    """
    执行完整的测试流程：
    1. 初始化 AIController
    2. 生成并记录随机总设定（角色/场景/题材 列表）
    3. 进行聊天历史测试（3‑6 条消息注入设定）
    4. 创建项目（使用随机怪诞标题等）
    5. 生成大纲并发送确认（激活 creation_allowed）
    6. 循环写入所有章节（依据总章节数），每章后可选执行健康检查
    7. 生成最终报告，包含所有关键步骤的成功/失败信息
    返回 True 表示所有核心步骤（包括聊天、项目创建、大纲、章节）均成功
            False 表示任意核心步骤失败
    """
    test_passed = True
    core_failures = []

    try:
        # ----- 0️⃣ 初始化 -----
        if not self.initialize_controller():
            core_failures.append("控制器初始化")
            return False

        # ----- 1️⃣ 生成总设定 -----
        self.total_setting = {
            'characters': random.sample(CHARACTERS, k=random.randint(1, 3)),
            'scenes'    : random.sample(SCENES,    k=random.randint(1, 3)),
            'genres'    : random.sample(GENRES,    k=random.randint(1, 2))
        }
        write_report(self.report_path,
                     f"🎯 本次测试的总设定：角色[{', '.join(self.total_setting['characters'])}]、"
                     f"场景[{', '.join(self.total_setting['scenes'])}]、"
                     f"题材[{', '.join(self.total_setting['genres'])}]",
                     also_print=True)

        # ----- 2️⃣ 聊天历史注入测试 -----
        if not self.chat_history_test():
            core_failures.append("聊天历史测试")
            test_passed = False

        # ----- 3️⃣ 项目创建 -----
        prompt, total_words, words_per_chap, total_chapters = generate_unique_prompt(self)
        self.total_chapters = total_chapters          # 记录章节总数供后续循环使用
        try:
            self.create_project(prompt)
        except Exception as e:
            write_report(self.report_path,
                         f"❌ 项目创建失败：{e}",
                         also_print=True)
            core_failures.append("项目创建")
            test_passed = False

        # ----- 4️⃣ 大纲生成 & 确认 -----
        if test_passed:
            try:
                self.generate_and_confirm_outline()
            except Exception as e:
                write_report(self.report_path,
                             f"❌ 大纲生成/确认失败：{e}",
                             also_print=True)
                core_failures.append("大纲生成/确认")
                test_passed = False

        # ----- 5️⃣ 章节循环写入 -----
        if test_passed:
            write_report(self.report_path,
                         f"\n📚 开始循环生成全部 {self.total_chapters} 章...")
            for chap_num in range(1, self.total_chapters + 1):
                write_report(self.report_path,
                             f"\n{'='*20} 第 {chap_num}/{self.total_chapters} 章 {'='*20}")

                if not self.write_chapter(chap_num):
                    core_failures.append(f"第{chap_num}章写入")
                    test_passed = False
                    write_report(self.report_path,
                                 f"❌ 第 {chap_num} 章生成失败，终止后续章节生成。",
                                 also_print=True)
                    break  # 章节失败则中止循环，避免继续无意义写入

                # 可选健康检查
                if HEALTH_CHECK_ENABLED:
                    self.perform_health_check(chap_num)   # 失败不影响主流程

    except Exception as e:
        write_report(self.report_path,
                     f"❌ 测试流程中出现未捕获异常：{e}",
                     also_print=True)
        core_failures.append("未知异常")
        test_passed = False

    # ----- 6️⃣ 生成最终报告 -----
    self.generate_final_report(core_failures)
    return test_passed

# ---------- 17️⃣ 最终报告 ----------
def generate_final_report(self, core_failures: List[str]) -> None:
    """
    生成完整的测试报告，展示：
    - 总设定（角色/场景/题材列表）
    - 聊天历史（发送给 AI 的原始消息）
    - AI 内部存储的对话历史（用于对比是否完整）
    - AI 最终创作响应
    - 项目/大纲/章节生成的成功/失败状态
    - 健康检查执行情况（若启用）
    """
    write_report(self.report_path, "\n" + "=" * 60, also_print=False)
    write_report(self.report_path, "=== 测试最终结果 ===", also_print=False)
    write_report(self.report_path, "=" * 60, also_print=False)

    # ---- 总设定 ----
    write_report(self.report_path, "🎯 总设定（用于对比 AI 是否正确理解）:", also_print=False)
    write_report(self.report_path,
                 f"   角色列表：[{', '.join(self.total_setting['characters'])}]",
                 also_print=False)
    write_report(self.report_path,
                 f"   场景列表：[{', '.join(self.total_setting['scenes'])}]",
                 also_print=False)
    write_report(self.report_path,
                 f"   题材列表：[{', '.join(self.total_setting['genres'])}]",
                 also_print=False)

    # ---- 核心步骤成功情况 ----
    if not core_failures:
        write_report(self.report_path, "✅ 所有核心步骤均成功！", also_print=False)
        write_report(self.report_path, f"   项目创建：成功", also_print=False)
        write_report(self.report_path, f"   大纲生成/确认：成功", also_print=False)
        write_report(self.report_path,
                     f"   全部 {self.total_chapters} 章写入：成功（文件系统验证）",
                     also_print=False)
        if HEALTH_CHECK_ENABLED:
            write_report(self.report_path, "ℹ️ 健康检查已执行（详情见日志）", also_print=False)
    else:
        write_report(self.report_path, "❌ 以下核心步骤出现失败：", also_print=False)
        for item in core_failures:
            write_report(self.report_path, f"   - {item}", also_print=False)

    # ---- 结束时间 & 运行目录 ----
    end_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    write_report(self.report_path, f"\n结束时间：{end_time}", also_print=False)
    write_report(self.report_path,
                 f"测试运行目录：{self.report_path.parent}",
                 also_print=False)
    write_report(self.report_path, "=" * 60, also_print=False)

# ---------- 19️⃣ TestExecutor 类 ----------
class TestExecutor:
    """
    一个轻量包装类，用来把脚本顶部全部以 ``def foo(self, …)`` 形式
    定义的函数动态绑定为实例方法。这样在 ``main()`` 中只需要
    ``executor = TestExecutor(report_path, state_file_path)`` 并
    调用 ``executor.run_full_test()`` 即可执行完整流程。

    核心实现:
    - ``__init__`` 保存测试运行时需要的公共属性（报告路径、状态文件、
      当前 run_id、以及在测试过程中产生的动态属性）。
    - ``__getattr__`` 在实例属性不存在时在全局命名空间查找同名函数，
      若找到可调用对象则返回一个 ``lambda``，在调用时自动把 ``self``
      作为第一个参数传递。这样所有 ``def foo(self, …)`` 的函数
      都可以通过 ``self.foo(...)`` 直接使用，无需再复制到类内部。
    - ``__repr__`` 提供友好的调试信息。
    """

    def __init__(self, report_path: Path, state_file_path: Path):
        # ------------------- 必要属性 -------------------
        # 报告文件（test_report.txt）
        self.report_path: Path = report_path
        # 状态持久化文件（.last_project_info.json）
        self.state_file_path: Path = state_file_path
        # 运行唯一标识：使用报告所在目录的名称（即 test_runs/<timestamp>_xxxx）
        self.unique_run_id: str = report_path.parent.name

        # ------------------- 在测试过程中会动态填充的属性 -------------------
        self.controller = None          # AIController 实例
        self.test_task_id = None        # 当前任务 ID
        self.test_project_dir = None    # 项目根目录（Path 对象）
        self.total_chapters = None      # 章节总数（由创建提示自动计算）
        self.total_setting = {}         # 随机生成的角色/场景/题材列表
        self.chat_turns = []            # 聊天历史（用于后续比对）
        self.final_prompt = ""          # 最后一次创作请求
        self.ai_response = ""           # AI 最终返回的文本

    # -----------------------------------------------------------------
    # 动态方法绑定
    # -----------------------------------------------------------------
    def __getattr__(self, name: str):
        """
        当实例属性未找到时，尝试在全局命名空间（module level）查找同名函数。
        如果找到了可调用对象，则返回一个 ``lambda``，在调用时自动把
        ``self`` 作为第一个参数传进去。这样脚本中所有以
        ``def foo(self, …)`` 定义的函数都可以像实例方法一样使用。
        """
        fn = globals().get(name)
        if callable(fn):
            # 这里使用 lambda 包装确保 ``self`` 在调用时被注入为第一个参数
            return lambda *args, **kwargs: fn(self, *args, **kwargs)
        raise AttributeError(
            f"'{self.__class__.__name__}' object has no attribute '{name}'"
        )

    # -----------------------------------------------------------------
    # 便于调试的表示
    # -----------------------------------------------------------------
    def __repr__(self):
        return (
            f"<TestExecutor run_id={self.unique_run_id} "
            f"report={self.report_path} state={self.state_file_path}>"
        )

# ---------- 18️⃣ 主入口 ----------
def main() -> None:
    """
    主函数：负责生成唯一测试目录、初始化报告、执行完整测试并根据结果返回退出码。
    """
    # 1️⃣ 生成唯一目录
    unique_dir = generate_unique_dir()

    # 2️⃣ 初始化报告文件与状态文件路径
    report_path, state_file_path = setup_test_environment(unique_dir)

    # 3️⃣ 实例化执行器并启动完整测试
    executor = TestExecutor(report_path, state_file_path)
    success = executor.run_full_test()

    # 4️⃣ 根据执行结果退出（0 为成功，1 为失败）
    if success:
        print("\n🎉 测试全部通过！详细信息请查看报告。")
        print(f"   报告路径：{report_path}")
        print(f"   运行目录：{unique_dir}")
        sys.exit(0)
    else:
        print("\n❌ 测试出现错误，请查看报告定位问题。")
        print(f"   报告路径：{report_path}")
        print(f"   运行目录：{unique_dir}")
        sys.exit(1)

if __name__ == "__main__":
    # 运行前的路径检查（友好提示），不影响核心功能
    if not Path("core").exists() and not Path("../core").exists():
        print("⚠️ 警告：未检测到 core 目录。请确保此脚本位于项目根目录中。")
        print("   示例：项目根目录/此脚本.py")
        print(f"   当前工作目录：{Path.cwd()}")
        # 仍继续执行，后续的 import 错误将给出明确提示
    main()

