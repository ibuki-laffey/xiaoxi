#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
测试脚本：验证章节生成是否实际产生非空文件（ filesystem 为唯一真理源 ）
================================================================================
核心原则：
1. 每次运行生成全局唯一目录（时间戳 + 随机字符串），杜绝文件覆盖
2. 章节生成成功仅当 02_章节/第00X章/ 目录存在且包含非零大小文件
3. 完全不依赖 API 返回值的 success/action 字段进行判断
4. 健康检查采用多重调用策略，适配潜在API变化
5. 明确使用 "确认" 指令设置 creation_allowed，而非假设 "继续" 生效
6. 每个修改点均有三处以上注释解释原因，防止后续误改
7. 零占位符、零省略、零模板化 - 所有函数完整呈现
8. 新增：丰富的随机化元素（标题、题材、世界类型等），以测试模型对奇怪组合的应变能力

运行方式：
    python test_script_complete.py
    （脚本会自动在当前目录下创建 test_runs/ 时间戳_随机串/ 目录存放所有输出）
"""

import os
import sys
import time
import json
import random
import string
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

# 将项目根目录加入路径，确保能导入 core 模块
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

# 导入必要模块（仅在需要时导入，避免早期失败）
try:
    from core.ai_controller import AIController
except ImportError as e:
    print(f"致命错误：无法导入 AIController。请确保脚本位于 project_root 目录下。")
    print(f"原始错误：{e}")
    sys.exit(1)

# ==================== 配置区域（便于后期调整） ====================
MAX_RETRY_COUNT = 3                 # 关键步骤最大重试次数
RETRY_BACKOFF_BASE = 2              # 指数回退基数（秒）
CHAPTER_VERIFY_WAIT = 2             # 发送写章指令后等待文件写入的秒数
MIN_FILE_SIZE_BYTES = 10            # 判定为“非空文件”的最小字节数
HEALTH_CHECK_ENABLED = True         # 是否执行健康检查（非核心步骤，失败不终止测试）
# ============================================================

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
    
    # 确保父目录存在
    unique_dir.mkdir(parents=True, exist_ok=True)
    
    # 在目录内创建明显标记，防止被误认为是项目数据
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

class TestExecutor:
    """
    封装测试执行逻辑，使主流程更清晰。
    所有方法均可独立测试，并有详细注释解释关键设计。
    """
    
    def __init__(self, report_path: Path, state_file_path: Path):
        self.report_path = report_path
        self.state_file_path = state_file_path
        self.controller: Optional[AIController] = None
        self.test_project_dir: Optional[Path] = None
        self.test_task_id: Optional[str] = None
        self.unique_run_id: str = report_path.parent.name  # 例如 "20260525_204607_cdYq"
        
        write_report(self.report_path, f"测试运行ID: {self.unique_run_id}")
    
    # ----------------------------------------------------------------------
    # ① 实例化 AIController（带日志回调）
    # ----------------------------------------------------------------------
    def initialize_controller(self) -> bool:
        """
        初始化 AIController 实例，并设置日志回写到我们的报告。
        返回值：成功则 True，失败则 False。
        """
        try:
            # 使用我们的报告路径作为日志回调
            self.controller = AIController(
                log_callback=lambda msg: write_report(self.report_path, f"[AI LOG] {msg}", also_print=False)
            )
            write_report(self.report_path, "✅ AIController 初始化成功")
            return True
        except Exception as e:
            write_report(self.report_path, f"❌ AIController 初始化失败: {e}", also_print=True)
            return False
    
    def _get_random_weird_genres(self) -> str:
        """
        从怪诞题材池中随机抽取 2–4 项，用 “ + ” 连接。
        题材列表包含克苏鲁、修仙、赛博朋克、古代、蒸汽朋克、末日、丧尸、
        仙侠、星际、奇幻、恐怖、荒诞、洛夫克拉夫特、道教、机甲、魔法少女等，
        目的是产生如 “克苏鲁 + 修仙 + 赛博朋克 + 古代” 这样的奇怪组合，
        以测试模型对极端概念融合的理解和生成能力。
        """
        weird_genres = [
            "克苏鲁", "修仙", "赛博朋克", "古代", "蒸汽朋克", "末日", "丧尸",
            "仙侠", "星际", "奇幻", "恐怖", "荒诞", "洛夫克拉夫特", "道教",
            "机甲", "魔法少女", "赛博修仙", "仙侠赛博", "古代机甲", "丧尸修仙",
            "机械龙", "神话机械", "废土道教", "霓虹仙境", "量子巫术", "暗黑童话"
        ]
        # 随机选择 2–4 个不同的题材
        k = random.randint(2, 4)
        selected = random.sample(weird_genres, k=k)
        return " + ".join(selected)
    
    def _get_random_weird_title(self) -> str:
        """
        从形容词和名词列表中随机组合，生成奇特标题。
        例如：赛博修仙古城、丧尸道宗遗迹、量子巫师废墟等。
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
        # 为了增加变化，有时双形容词
        if random.random() < 0.3:
            adj2 = random.choice(adjectives)
            while adj2 == adj:
                adj2 = random.choice(adjectives)
            return f"{adj}{adj2}{noun}"
        return f"{adj}{noun}"
    
    def _get_random_weird_world(self) -> str:
        """
        从奇特世界类型列表中随选择一个，作为世界类型描述。
        例如：蒸汽朋克+修仙洞天、末日丧尸修仙界、星际古代机甲废墟等。
        """
        weird_worlds = [
            "蒸汽朋克+修仙洞天", "末日丧尸修仙界", "星际古代机甲废墟",
            "克苏鲁深海神庙", "赛博仙山", "量子道场", "暗黑童话王国",
            "机械龙帝国", "废土仙府", "霓虹修真网", "洪荒机甲纪",
            "星际修仙舰队", "末日道宗", "蒸汽魔法学院", "古代机械神域"
        ]
        return random.choice(weird_worlds)
    
    def _get_random_chinese_name(self) -> str:
        """
        生成一个随机的中文人物名字（姓+名），姓氏和名字均来自扩展列表，
        以增加罕见姓氏和名字的出现概率，提升多样性。
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
        # 为了让名字更有趣，加入一些罕见或带有特殊含义的字
        given = [
            "浩","轩","宇","澈","霖","哲","辰","熙","铭","烨","朗","宁",
            "琪","雯","珂","雅","菲","妍","欣","然","炎","魔","神","仙",
            "道","佛","鬼","尸","星","月","日","辰","时","空","宙","界",
            "梦","魂","魄","幽","冥","阳","阴","金","木","水","火","土",
            "雷","电","风","云","霞","霜","雪","雨","电","光","影","声",
            "音","律","韵","曲","歌","舞","剑","刀","枪","斧","钺","弓",
            "甲","乙","丙","丁","戊","己","庚","辛","壬","癸","子","丑",
            "寅","卯","辰","巳","午","未","申","酉","戌","亥","龙","蛇",
            "马","羊","猴","鸡","狗","猪","鼠","牛","虎","兔"
        ]
        return random.choice(surnames) + random.choice(given)

    
    def generate_unique_prompt(self) -> Tuple[str, int, int]:
        """
        生成一个用于创建项目的唯一提示词。
        包含本次运行的唯一 ID，确保即使内容相同，提示词也不同（防止缓存命中）。
        新增：标题、题材、世界类型均采用随机化的怪诞元素，以测试模型对奇怪组合的应变能力。
        返回：(提示词, 总字数, 每章字数)
        """
        # 使用唯一运行ID作为项目名的一部分，确保全局唯一
        unique_id_part = self.unique_run_id.split('_')[-1]  # 取随机部分如 aB3f
        
        # 生成随机标题、题材、世界类型、主角名
        title = self._get_random_weird_title()
        genre = self._get_random_weird_genres()
        world_type = self._get_random_weird_world()
        main_char = self._get_random_chinese_name()
        
        # 标题加入唯一ID后缀，防止完全相同
        title_with_id = f"{title}_{unique_id_part}"
        
        # 字数在合理范围内随机，避免过小导致生成跳过或过大导致超时
        total_words = random.randint(8000, 50000)
        words_per_chap = random.randint(1000, 2500)
        
        prompt = (
            f"请创建工程文件，项目名《{title_with_id}》，"
            f"题材：{genre}，"
            f"世界类型：{world_type}，"
            f"主角名：{main_char}，"
            f"总字数：{total_words}，"
            f"每章字数：{words_per_chap}"
        )
        
        write_report(self.report_path, f"生成的创建提示词：{prompt}")
        return prompt, total_words, words_per_chap

    def _retry_operation(self, func, *args, max_attempts: int = MAX_RETRY_COUNT, base_delay: int = RETRY_BACKOFF_BASE, **kwargs):
        """
        直接执行 ``func(*args, **kwargs)``，最多重试 ``max_attempts`` 次。
        重构为直接执行模式，彻底避免高阶函数闭包导致的参数传递异常。
        """
        last_exc = None
        for attempt in range(1, max_attempts + 1):
            try:
                return func(*args, **kwargs)          # 成功 → 直接返回结果
            except Exception as exc:                  # 失败 → 记录并等待
                last_exc = exc
                write_report(
                    self.report_path,
                    f"⚠️ 重试 {attempt}/{max_attempts}：{getattr(func, '__name__', 'unknown')} 失败：{str(exc)[:120]}",
                    also_print=False,
                )
                if attempt < max_attempts:               # 不是最后一次 → 退避
                    time.sleep(base_delay * (2 ** (attempt - 1)))
        # 所有尝试均失败 → 抛出最后一次异常，让外层捕获
        raise last_exc


    
    def create_project(self, prompt: str) -> Tuple[Dict[str, Any], str, Path]:
        """
        执行项目创建步骤。
        返回：(API响应, task_id, 项目目录Path)
        关键：不依赖API返回的action字段，而是验证项目目录是否存在必需文件。
        """
        write_report(self.report_path, "🚀 开始项目创建...")
        
        # 直接调用重试方法，传入目标函数及其参数（不再使用 lambda 和 wrapper）
        response = self._retry_operation(self.controller.ask_ai, prompt)
        
        # 提取 task_id 和 project_dir（容错处理不同返回结构）
        task_id = response.get("task_id")
        if not task_id:
            task_id = response.get("project_info", {}).get("task_id")
        if not task_id:
            # 尝试从项目目录中推断（虽然不太可靠，但作为后备）
            # 注意：这里我们依赖项目目录的创建，而不是 task_id
            pass  # 将在目录验证中处理
        
        project_dir_str = response.get("project_dir")
        if not project_dir_str:
            project_dir_str = response.get("project_info", {}).get("project_dir")
        
        project_dir = Path(project_dir_str) if project_dir_str else None
        
        # === 关键验证：项目创建成功仅当目录存在且包含 README ===
        readme_path = project_dir / "README_项目说明.txt" if project_dir else None
        if not project_dir or not project_dir.is_dir() or not is_file_non_empty(readme_path):
            error_msg = (
                f"项目创建失败：目录不存在或缺少README。\n"
                f"项目目录：{project_dir}\n"
                f"README路径：{readme_path}\n"
                f"README存在且非空：{is_file_non_empty(readme_path) if readme_path else False}"
            )
            write_report(self.report_path, f"❌ {error_msg}", also_print=True)
            raise RuntimeError(error_msg)
        
        write_report(self.report_path, f"✅ 项目创建成功。任务ID：{task_id}，目录：{project_dir}")
        self.test_task_id = task_id
        self.test_project_dir = project_dir
        
        # 保存状态以便后续可能恢复（虽然此测试不依赖它，但保持习惯）
        state_data = {
            "project_dir": str(project_dir),
            "task_id": task_id,
            "created_at": datetime.now().isoformat(),
            "test_run_id": self.unique_run_id
        }
        with open(self.state_file_path, "w", encoding="utf-8") as f:
            json.dump(state_data, f, ensure_ascii=False, indent=2)
        
        return response, task_id, project_dir

    
    def generate_and_confirm_outline(self) -> Dict[str, Any]:
        """
        生成大纲并确认。
        流程：
        1. 发送“生成大纲”
        2. 验证 01_大纲/总体大纲_模板.txt 是否存在且非空
        3. 发送“确认”  --> 这是关键！用于设置 creation_allowed = True
        4. 验证确认后状态（虽然不强制，但有助于调试）
        返回：大纲生成步骤的API响应
        """
        # --- 步骤1：生成大纲 ---
        write_report(self.report_path, "📋 开始生成大纲...")
        outline_response = self.controller.ask_ai("生成大纲")
        
        # 验证大纲文件是否存在且非空（这是真正的成功标准）
        outline_file = self.test_project_dir / "01_大纲" / "总体大纲_模板.txt"
        if not is_file_non_empty(outline_file):
            error_msg = (
                f"大纲生成失败：大纲文件不存在或为空。\n"
                f"大纲路径：{outline_file}\n"
                f"文件大小：{outline_file.stat().st_size if outline_file.exists() else 0} 字节"
            )
            write_report(self.report_path, f"❌ {error_msg}", also_print=True)
            raise RuntimeError(error_msg)
        
        write_report(self.report_path, f"✅ 大纲生成成功。大纲文件：{outline_file}")
        
        # --- 步骤2：发送“确认”指令（关键！此处设置 creation_allowed） ---
        write_report(self.report_path, "✅ 发送确认指令以激活创建模式...")
        confirm_response = self.controller.ask_ai("确认")
        
        # 验证确认后 creation_allowed 是否被设置（通过检查后续行为间接验证）
        # 直接访问 self.controller.creation_allowed 是可能的，但为了避免依赖内部属性，
        # 我们等待一下后看第一章是否能生成。此处仅作记录。
        if hasattr(self.controller, 'creation_allowed'):
            write_report(
                self.report_path,
                f"[调试] 确认后 creation_allowed = {self.controller.creation_allowed}",
                also_print=False
            )
        
        write_report(self.report_path, f"✅ 确认指令发送完毕。")
        return outline_response
    
    def write_chapter(self, chapter_number: int) -> bool:
        """
        写入指定章节并验证是否产生了非空文件。
        返回：True 表示成功生成了非空文件，False 表示失败。
        关键：不依赖API返回值，纯文件系统验证。
        """
        chapter_dir = self.test_project_dir / "02_章节" / f"第{chapter_number:03d}章"
        write_report(self.report_path, f"✍️ 开始写入第 {chapter_number} 章...")
        
        # 发送写章指令
        write_response = self.controller.ask_ai(f"写第 {chapter_number} 章")
        
        # 等待文件系统更新（异步写入可能需要时间）
        time.sleep(CHAPTER_VERIFY_WAIT)
        
        # === 关键验证：章节成功仅当目录中存在非空文件 ===
        if has_non_empty_files_in_dir(chapter_dir):
            # 查找第一个非空文件用于报告
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
                f"章节目录：{chapter_dir}\n"
                f"目录内容：{[f.name for f in chapter_dir.iterdir()] if chapter_dir.exists() else '目录不存在'}"
            )
            write_report(self.report_path, f"❌ {error_msg}", also_print=True)
            return False
    
    def perform_health_check(self, chapter_number: int) -> bool:
        """
        执行章节健康检查（非核心步骤）。
        采用多重调用策略以适配API变化：
        1. 尝试标准关键字参数调用
        2. 尝试位置参数调用
        3. 尝试直接访问健康检查器
        只要有一次成功即算通过；全部失败则记录警告但不中断测试。
        返回：True 表示检查通过或可跳过，False 表示严重失败（但即使返回False也不终止主流程）。
        """
        if not HEALTH_CHECK_ENABLED or not self.controller:
            write_report(self.report_path, "ℹ️ 健康检查已禁用或控制器未就绪，跳过。", also_print=False)
            return True  # 禁用时视为“通过”以不影响主流程判断
        
        if not self.test_task_id:
            write_report(self.report_path, "⚠️ 无法执行健康检查：缺少 task_id。", also_print=False)
            return True
        
        write_report(self.report_path, f"🩺 开始对第 {chapter_number} 章执行健康检查...")
        
        # 策略1：标准关键字参数调用（假设当前API）
        try:
            health_result = self.controller._run_health_check(
                task_id=self.test_task_id,
                current_chapter=chapter_number
            )
            if health_result.get("success", False):
                write_report(self.report_path, f"✅ 第 {chapter_number} 章健康检查通过（策略1）。")
                return True
        except Exception as e1:
            write_report(
                self.report_path,
                f"⚠️ 健康检查策略1失败：{str(e1)[:100]}",
                also_print=False
            )
        
        # 策略2：位置参数调用（假设API变化为仅(task_id, chapter)）
        try:
            health_result = self.controller._run_health_check(
                self.test_task_id,
                chapter_number
            )
            if health_result.get("success", False):
                write_report(self.report_path, f"✅ 第 {chapter_number} 章健康检查通过（策略2）。")
                return True
        except Exception as e2:
            write_report(
                self.report_path,
                f"⚠️ 健康检查策略2失败：{str(e2)[:100]}",
                also_print=False
            )
        
        # 策略3：直接访问健康检查器（绕过APIController包装）
        try:
            if hasattr(self.controller, 'health_checker') and self.controller.health_checker:
                health_result = self.controller.health_checker.run(
                    self.test_task_id,
                    chapter_number
                )
                if health_result.get("success", False):
                    write_report(self.report_path, f"✅ 第 {chapter_number} 章健康检查通过（策略3：直接检查器）。")
                    return True
        except Exception as e3:
            write_report(
                self.report_path,
                f"⚠️ 健康检查策略3失败：{str(e3)[:100]}",
                also_print=False
            )
        
        # 所有策略失败
        error_msg = (
            f"第 {chapter_number} 章健康检查所有策略均失败。\n"
            f"这可能表明健康检查API已变更，但不影响章节生成核心功能。"
        )
        write_report(self.report_path, f"⚠️ {error_msg}", also_print=True)
        # 健康检查失败不应终止测试，因为它不是核心功能
        return True  # 返回 True 以允许测试继续，但已在报告中警告
    
    def run_full_test(self) -> bool:
        """
        执行完整的测试流程：
        1. 初始化控制器
        2. 生成并创建唯一项目
        3. 生成大纲并确认（关键：使用“确认”而非“继续”）
        4. 写入第1章并验证文件生成
        5. （可选）对第1章执行健康检查
        6. 写入第2章并验证文件生成
        7. （可选）对第2章执行健康检查
        8. 生成最终报告
        
        返回：True 表示所有核心步骤（项目创建、大纲确认、章节生成）均成功
              False 表示任何核心步骤失败
        """
        test_passed = True
        core_steps_failed = []
        
        try:
            # --- 步骤0：初始化 ---
            if not self.initialize_controller():
                core_steps_failed.append("控制器初始化")
                test_passed = False
                return test_passed  # 提前失败
            
            # --- 步骤1：创建项目 ---
            prompt, total_words, words_per_chap = self.generate_unique_prompt()
            try:
                self.create_project(prompt)
            except Exception as e:
                write_report(self.report_path, f"❌ 核心步骤失败 - 项目创建：{e}", also_print=True)
                core_steps_failed.append("项目创建")
                test_passed = False
            
            # --- 步骤2：生成大纲并确认 ---
            if test_passed:  # 仅在前步骤成功时继续
                try:
                    self.generate_and_confirm_outline()
                except Exception as e:
                    write_report(self.report_path, f"❌ 核心步骤失败 - 大纲生成/确认：{e}", also_print=True)
                    core_steps_failed.append("大纲生成/确认")
                    test_passed = False
            
            # --- 步骤3：写入第1章 ---
            if test_passed:
                if not self.write_chapter(1):
                    core_steps_failed.append("第1章写入")
                    test_passed = False
            
            # --- 步骤4：第1章健康检查（可选） ---
            if test_passed and HEALTH_CHECK_ENABLED:
                self.perform_health_check(1)  # 失败不影响test_passed
            
            # --- 步骤5：写入第2章 ---
            if test_passed:
                if not self.write_chapter(2):
                    core_steps_failed.append("第2章写入")
                    test_passed = False
            
            # --- 步骤6：第2章健康检查（可选） ---
            if test_passed and HEALTH_CHECK_ENABLED:
                self.perform_health_check(2)  # 失败不影响test_passed
            
        except Exception as e:
            write_report(self.report_path, f"❌ 测试流程中发生未预期异常：{e}", also_print=True)
            core_steps_failed.append("未预期异常")
            test_passed = False
        
        # --- 生成最终报告 ---
        self.generate_final_report(core_steps_failed)
        
        return test_passed
    
    def generate_final_report(self, core_steps_failed: list) -> None:
        """
        生成测试最终报告，清晰显示哪些核心步骤成功/失败。
        """
        write_report(self.report_path, "\n" + "=" * 60, also_print=False)
        write_report(self.report_path, "=== 测试最终结果 ===", also_print=False)
        write_report(self.report_path, "=" * 60, also_print=False)
        
        if not core_steps_failed:
            write_report(self.report_path, "🎉 所有核心测试步骤均通过！", also_print=False)
            write_report(self.report_path,   "✅ 项目创建：成功", also_print=False)
            write_report(self.report_path,   "✅ 历史发送/确认：成功", also_print=False)
            write_report(self.report_path,   "✅ 第1章写入：成功（文件系统验证）", also_print=False)
            write_report(self.report_path,   "✅ 第2章写入：成功（文件系统验证）", also_print=False)
            if HEALTH_CHECK_ENABLED:
                write_report(self.report_path,   "ℹ️ 健康检查：已执行（详见日志）", also_print=False)
        else:
            write_report(self.report_path, "❌ 以下核心步骤失败：", also_print=False)
            for step in core_steps_failed:
                write_report(self.report_path, f"   - {step}", also_print=False)
            write_report(self.report_path, "\n💡 提示：章节生成成功仅由文件系统中的非空文件决定。", also_print=False)
            write_report(self.report_path,   "   即使API返回成功消息，若无实际文件，亦判定为失败。", also_print=False)
            write_report(self.report_path,   "   内容检查仅作参考，用于观察机器人是否省略了历史中的主题信息。", also_print=False)
        
        end_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        write_report(self.report_path, f"\n结束时间：{end_time}", also_print=False)
        # 使用 self.report_path.parent 获取当前测试目录
        write_report(self.report_path, f"测试运行目录：{self.report_path.parent}", also_print=False)
        write_report(self.report_path, "=" * 60, also_print=False)


def main() -> None:
    """
    主函数：编排整个测试流程。
    确保每次运行都使用全新唯一目录。
    """
    # 生成全局唯一测试目录
    unique_dir = generate_unique_dir()
    
    # 在唯一目录内设置环境
    report_path, state_file_path = setup_test_environment(unique_dir)
    
    # 创建测试执行器并运行
    executor = TestExecutor(report_path, state_file_path)
    success = executor.run_full_test()
    
    # 根据测试结果设置退出码（0表示成功，1表示失败）
    # 注意：即使健康检查失败，只要核心章节生成成功，仍视为测试通过
    if success:
        print(f"\n🎉 测试通过！所有核心功能正常。")
        print(f"   详细报告：{report_path}")
        print(f"   测试目录：{unique_dir}")
        sys.exit(0)
    else:
        print(f"\n❌ 测试失败！请检查报告了解详情。")
        print(f"   详细报告：{report_path}")
        print(f"   测试目录：{unique_dir}")
        sys.exit(1)

if __name__ == "__main__":
    # 确保在项目根目录下的test_runs/中运行
    # 如果脚本被直接双击运行，可能需要调整路径
    if not Path("core").exists() and not Path("../core").exists():
        print("警告：未检测到 core 目录。请确保此脚本位于项目根目录中。")
        print("   例如：项目根目录/此脚本.py")
        print("   当前目录：", Path.cwd())
        # 继续执行，让导入错误在后面捕获
    
    main()
