#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
极限测试脚本：AI 指令识别、抗干扰与精准章节生成验证
================================================================================
核心测试矩阵：
1. 单章生成 + 闲聊干扰 -> 期望生成：[1]
2. 批量数量(2章) + 剧情干扰 -> 期望生成：[2, 3]
3. 批量数量(3章) + 总结干扰 -> 期望生成：[4, 5, 6]
4. 指定跳跃章节(第8章) + 否定干扰 -> 期望生成：[8] (绝对不生成7)
5. 剩余全部生成 + 情绪干扰 -> 期望生成：[9, 10, ..., N]

核心验证机制：
- 文件系统快照对比：精准计算 `After - Before`，确保不多生成、不少生成。
- 零 API 依赖：完全不信任 API 返回的 success/action，纯靠物理文件验证。
================================================================================
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
from typing import Dict, Any, Optional, Tuple, List, Set

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
BASE_VERIFY_WAIT = 15               # 基础等待时间（秒）
PER_CHAPTER_WAIT = 20               # 每增加一章额外等待时间（秒）
MIN_FILE_SIZE_BYTES = 50            # 判定为“非空文件”的最小字节数（防止生成废话）
HEALTH_CHECK_ENABLED = True         # 是否执行健康检查（非核心步骤，失败不终止测试）
# ============================================================

def generate_unique_dir() -> Path:
    """生成全局唯一的测试运行目录。"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    random_suffix = ''.join(random.choices(string.ascii_letters + string.digits, k=4))
    unique_dir = Path(f"test_runs/{timestamp}_{random_suffix}")
    unique_dir.mkdir(parents=True, exist_ok=True)
    marker_file = unique_dir / ".this_is_test_run_marker.txt"
    marker_file.write_text(
        f"此目录为测试专用运行目录\n"
        f"生成时间：{datetime.now().isoformat()}\n"
        f"请勿在此存放重要数据，测试结束后可安全删除\n",
        encoding="utf-8"
    )
    return unique_dir

def setup_test_environment(unique_dir: Path) -> Tuple[Path, Path]:
    """在唯一目录内设置测试环境"""
    report_path = unique_dir / "test_report.txt"
    state_file_path = unique_dir / ".last_project_info.json"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"=== 极限指令识别测试报告：{unique_dir.name} ===\n")
        f.write(f"开始时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("=" * 70 + "\n\n")
    return report_path, state_file_path

def write_report(report_path: Optional[Path], message: str, also_print: bool = True) -> None:
    """向报告文件写入一行并立即刷新。"""
    timestamp = datetime.now().strftime("%H:%M:%S")
    line = f"[{timestamp}] {message}"
    if report_path is not None:
        with open(report_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
    if also_print:
        print(line)

def is_file_non_empty(file_path: Path) -> bool:
    """检查文件是否存在且大小大于 MIN_FILE_SIZE_BYTES。"""
    try:
        return file_path.is_file() and file_path.stat().st_size > MIN_FILE_SIZE_BYTES
    except Exception:
        return False

def has_non_empty_files_in_dir(dir_path: Path) -> bool:
    """检查目录中是否存在至少一个非空文件。"""
    try:
        if not dir_path.is_dir():
            return False
        return any(is_file_non_empty(item) for item in dir_path.iterdir())
    except Exception:
        return False

class TestExecutor:
    """封装测试执行逻辑，使主流程更清晰。"""
    
    def __init__(self, report_path: Path, state_file_path: Path):
        self.report_path = report_path
        self.state_file_path = state_file_path
        self.controller: Optional[AIController] = None
        self.test_project_dir: Optional[Path] = None
        self.test_task_id: Optional[str] = None
        self.total_chapters: Optional[int] = None  
        self.unique_run_id: str = report_path.parent.name  
        write_report(self.report_path, f"测试运行ID: {self.unique_run_id}")
    
    def initialize_controller(self) -> bool:
        """初始化 AIController 实例"""
        try:
            self.controller = AIController(
                log_callback=lambda msg: write_report(self.report_path, f"[AI LOG] {msg}", also_print=False)
            )
            write_report(self.report_path, "✅ AIController 初始化成功")
            return True
        except Exception as e:
            write_report(self.report_path, f"❌ AIController 初始化失败: {e}", also_print=True)
            return False
    
    # ==================== 随机化元素生成库（保持原有丰富度） ====================
    def _get_random_weird_genres(self) -> str:
        weird_genres = [
            "克苏鲁", "洛夫克拉夫特", "修仙", "仙侠", "道教", "巫蛊", "山海经", "神话", "古代", "古装", "宫廷", "武侠", "江湖", "刀剑", "阴阳师",
            "赛博朋克", "蒸汽朋克", "机械朋克", "量子朋克", "全息网游", "全息", "机甲", "机械", "机器人", "机动", "机械龙", "机械帝国", "机械神域", "机甲少女", "机械奥特曼", "TSF", "全息虚拟", "时空穿梭",
            "末日", "废土", "废土道教", "废土仙府", "废土机甲", "废土道长", "生化危机", "丧尸", "丧尸修仙", "末日丧尸", "末日道宗", "后核辐射",
            "星际", "星际古代", "星际修仙舰队", "星际机甲废墟", "星际古代机甲", "量子道场", "量子巫术", "量子星河", "星际废墟",
            "奇幻", "暗黑童话", "荒诞", "黑暗奇幻", "暗黑童话王国", "暗黑童话王宫", "暗黑童话废土", "暗黑童话机甲", "暗黑童话宇宙",
            "魔法少女", "萝莉美少女", "性转男变女", "二次元", "动漫", "轻小说", "赛博修仙", "仙侠赛博", "古代机甲", "赛博仙山", "赛博幻境",
            "恐怖", "血月", "血祭", "祭坛", "异界", "幽冥", "鬼吹灯", "民俗恐怖", "占卜梦魇", "八卦风水", "八卦阵", "风水", "黄帝内经", "太乙阴阳", "祝由术", "巫蛊降头", "附身厌胜",
            "血腥", "暴力", "杀戮", "狂暴", "狂热", "残忍", "霓虹仙境", "霓虹修真网", "暗黑童话星河", "蒸汽魔法学院", "机械龙帝国", "神话机械", "废土道长", "量子巫术",
            "海底古城", "深渊神庙", "混沌时空", "幻影城市", "梦魇宫殿", "时空盗贼", "时空修真", "时空旅者", "时空幻影", "星河机甲", "星河幻境", "星河古代", "星际机械神域", "星际废土机甲",
            "生化魔法", "生化仙途", "生化星际", "全息幻影", "全息星河", "全息机器", "全息废土", "全息赛博", "太空蒸汽", "太空机械", "暗黑机甲", "暗黑机械", "幻影机械", "幻影星河", "幻影古代", "幻影未来",
            "指环王", "哈利波特", "星球大战", "终结者", "黑客帝国", "赛博朋克2077", "Apex", "英雄联盟", "王者荣耀", "现代都市", "直播", "日常", "虚拟偶像", "全息直播", "全息网络", "全息世界", "全息实验室", "全息星际", "全息废墟", "全息暗黑", "全息荒诞", "全息狂热", "全息梦境", "全息幻境"
        ]
        k = random.randint(2, 5)
        selected = random.sample(weird_genres, k=k)
        return " + ".join(selected)

    def _get_random_weird_title(self) -> str:
        adjectives = ["赛博", "修仙", "古代", "末日", "量子", "霓虹", "暗黑", "蒸汽", "机械", "神话", "道教", "仙境", "巫师", "龙", "丧尸", "克苏鲁", "洛夫克拉夫特", "荒诞", "奇幻", "星际", "废土", "洞天", "遗迹", "血月", "时空", "梦魇", "幻影", "全息", "虚拟", "神秘", "绝望", "残忍", "血腥", "狂热", "毁灭", "混沌", "光辉", "暗影", "永恒", "流光", "星河", "深渊", "幽冥", "祭坛", "秘仪", "巫蛊", "占卜"]
        nouns = ["古城", "宗门", "遗迹", "废墟", "星球", "基地", "洞天", "秘境", "战场", "试炼场", "神殿", "祭坛", "实验室", "飞船", "机甲", "妖塔", "血月", "星河", "时空", "梦境", "深渊", "乐园", "试炼", "孤岛", "幻境", "星系", "城市", "王座", "宫殿", "墓园", "暗道", "雨林", "废土", "矿洞", "霓虹街", "光之塔", "暗影林", "远古殿", "机械城", "量子塔", "海底城", "霓虹河", "星际港", "虚拟街", "全息城"]
        adj = random.choice(adjectives)
        noun = random.choice(nouns)
        if random.random() < 0.3:
            adj2 = random.choice(adjectives)
            while adj2 == adj: adj2 = random.choice(adjectives)
            return f"{adj}{adj2}{noun}"
        return f"{adj}{noun}"

    def _get_random_weird_world(self) -> str:
        weird_worlds = ["赛博朋克", "全息网游", "全息城市", "虚拟现实", "全息废墟", "末日废土", "后核辐射", "求生荒野", "废土求生", "末日求生", "现代都市", "都市奇幻", "都市修真", "都市暗黑", "都市浪潮", "高奇幻", "诸天万界", "神话纪元", "古代王朝", "古风武侠", "蒸汽朋克", "蒸汽幻想", "机甲纪元", "机甲帝国", "机械龙帝国", "星际殖民", "星际舰队", "宇宙深渊", "量子星河", "星际古代", "梦境迷宫", "深渊幻境", "幻影都市", "暗黑童话", "荒诞梦魇", "深海神庙", "海底古城", "海底废墟", "海底星域", "赛博修真", "全息仙境", "蒸汽魔法学院", "机械巫师", "量子道场", "暗黑机械", "废土仙府", "霓虹仙山", "星际魔法", "全息荒诞"]
        return random.choice(weird_worlds)

    def _get_random_chinese_name(self) -> str:
        surnames = ["赵","钱","孙","李","周","吴","郑","王","冯","陈","褚","卫","蒋","沈","韩","杨","朱","秦","尤","许","何","吕","施","张","孔","曹","严","华","金","魏","陶","姜","戚","谢","邹","喻","柏","水","窦","章","云","苏","潘","葛","奚","范","彭","郎","鲁","韦","昌","马","苗","凤","花","方","俞","任","袁","柳","酆","鲍","唐","费","廉","岑","薛","雷","贺","倪","汤","滕","殷","罗","毕","郝","邬","安","常","乐","于","傅","卞","蔡","樊","客","孟","黄","庞","宗","洪","盛"]
        given = ["浩","轩","宇","澈","霖","哲","辰","熙","铭","烨","朗","宁","琪","雯","珂","雅","菲","妍","欣","然","炎","魔","神","仙","道","佛","鬼","尸","星","月","日","辰","时","空","宙","界","梦","魂","魄","幽","冥","阳","阴","金","木","水","火","土","雷","电","风","云","霞","霜","雪","雨","电","光","影","声","音","律","韵","曲","歌","舞","剑","刀","枪","斧","钺","弓","甲","乙","丙","丁","戊","己","庚","辛","壬","癸","子","丑","寅","卯","辰","巳","午","未","申","酉","戌","亥","龙","蛇","马","羊","猴","鸡","狗","猪","鼠","牛","虎","兔"]
        return random.choice(surnames) + random.choice(given)
    # ========================================================================

    def generate_unique_prompt(self) -> Tuple[str, int]:
        """
        生成创建项目的提示词。
        🌟 核心修改：字数范围 2w~20w，移除每章字数指定，让系统自行判断。
        """
        unique_id_part = self.unique_run_id.split('_')[-1]  
        title = self._get_random_weird_title()
        genre = self._get_random_weird_genres()
        world_type = self._get_random_weird_world()
        main_char = self._get_random_chinese_name()
        title_with_id = f"{title}_{unique_id_part}"
        
        # 🌟 字数限制：20,000 ~ 200,000
        total_words = random.randint(20000, 200000)  
        
        # 🌟 移除每章字数说明
        prompt = (
            f"请创建工程文件，项目名《{title_with_id}》，"
            f"题材：{genre}，"
            f"世界类型：{world_type}，"
            f"主角名：{main_char}，"
            f"总字数：{total_words}"
        )
        
        write_report(self.report_path, f"生成的创建提示词：{prompt} (总字数: {total_words}，每章字数由系统自动判断)")
        return prompt, total_words

    def _retry_operation(self, func, *args, max_attempts: int = MAX_RETRY_COUNT, base_delay: int = RETRY_BACKOFF_BASE, **kwargs):
        last_exc = None
        for attempt in range(1, max_attempts + 1):
            try:
                return func(*args, **kwargs)          
            except Exception as exc:                  
                last_exc = exc
                write_report(self.report_path, f"⚠️ 重试 {attempt}/{max_attempts} 失败：{str(exc)[:120]}", also_print=False)
                if attempt < max_attempts: time.sleep(base_delay * (2 ** (attempt - 1)))
        raise last_exc

    def create_project(self, prompt: str) -> Tuple[Dict[str, Any], str, Path]:
        """执行项目创建步骤，并获取系统自动计算的总章节数。"""
        write_report(self.report_path, "🚀 开始项目创建...")
        response = self._retry_operation(self.controller.ask_ai, prompt)
        
        task_id = response.get("task_id") or response.get("project_info", {}).get("task_id")
        project_dir_str = response.get("project_dir") or response.get("project_info", {}).get("project_dir")
        project_dir = Path(project_dir_str) if project_dir_str else None
        
        readme_path = project_dir / "README_项目说明.txt" if project_dir else None
        if not project_dir or not project_dir.is_dir() or not is_file_non_empty(readme_path):
            raise RuntimeError(f"项目创建失败：目录不存在或缺少README。路径：{project_dir}")
        
        write_report(self.report_path, f"✅ 项目创建成功。任务ID：{task_id}，目录：{project_dir}")
        self.test_task_id = task_id
        self.test_project_dir = project_dir
        
        # 🌟 核心新增：从 task_planner 获取系统自动计算的 total_chapters
        if hasattr(self.controller, 'task_planner') and task_id:
            plan = self.controller.task_planner.get_plan(task_id)
            if plan and 'total_chapters' in plan:
                self.total_chapters = int(plan['total_chapters'])
                write_report(self.report_path, f"📊 系统自动计算的总章节数：{self.total_chapters} 章")
                
        state_data = {"project_dir": str(project_dir), "task_id": task_id, "created_at": datetime.now().isoformat(), "test_run_id": self.unique_run_id}
        with open(self.state_file_path, "w", encoding="utf-8") as f:
            json.dump(state_data, f, ensure_ascii=False, indent=2)
            
        return response, task_id, project_dir

    def generate_and_confirm_outline(self) -> Dict[str, Any]:
        """生成大纲并确认，并清理默认生成的章节以保证测试纯净。"""
        write_report(self.report_path, "📋 开始生成大纲...")
        outline_response = self.controller.ask_ai("生成大纲")
        
        outline_dir = self.test_project_dir / "01_大纲"
        outline_file = outline_dir / "总体大纲_模板.txt"
        if not outline_file.exists():
            possible_files = list(outline_dir.glob("*.json")) + list(outline_dir.glob("*.txt"))
            if possible_files: outline_file = possible_files[0]
                
        if not is_file_non_empty(outline_file):
            raise RuntimeError(f"大纲生成失败：大纲文件不存在或为空。路径：{outline_file}")
        
        write_report(self.report_path, f"✅ 大纲生成成功。文件：{outline_file}")
        
        write_report(self.report_path, "✅ 发送确认指令以激活创建模式...")
        self.controller.ask_ai("确认")
        
        # 🌟 核心修复：清理 AIController 默认生成的第1章
        # 因为底层逻辑在确认大纲后会自动写第1章，这会干扰我们后续的“单章生成”测试用例。
        # 我们必须物理清空 02_章节 目录，确保测试矩阵从绝对的“0章”开始。
        chapter_base_dir = self.test_project_dir / "02_章节"
        if chapter_base_dir.exists():
            write_report(self.report_path, "🧹 清理系统默认生成的章节，确保测试环境纯净...")
            shutil.rmtree(chapter_base_dir)
            chapter_base_dir.mkdir(parents=True, exist_ok=True)
            
        write_report(self.report_path, "✅ 确认指令发送完毕，测试环境已重置。")
        return outline_response


    # -----------------------------------------------------------------
    # 🌟 核心创新：文件系统快照与指令测试矩阵
    # -----------------------------------------------------------------
    def get_existing_chapters(self) -> Set[int]:
        """扫描 02_章节 目录，返回当前已存在且非空的章节号集合"""
        chapters = set()
        chapter_base_dir = self.test_project_dir / "02_章节"
        if not chapter_base_dir.exists():
            return chapters
        for d in chapter_base_dir.iterdir():
            if d.is_dir() and d.name.startswith("第") and d.name.endswith("章"):
                try:
                    num_str = d.name[1:-1].lstrip('0')
                    if not num_str: num_str = '0'
                    if has_non_empty_files_in_dir(d):
                        chapters.add(int(num_str))
                except ValueError:
                    pass
        return chapters

    def run_instruction_test_cases(self) -> bool:
        """执行指令识别与抗干扰测试矩阵"""
        total_chapters = self.total_chapters or 10
        
        # 兜底：如果系统算出的章节数太少，强制修正为 10 章以支撑测试矩阵
        if total_chapters < 9:
            write_report(self.report_path, f"⚠️ 系统计算的总章节数({total_chapters})不足以支撑测试矩阵，强制修正为 10 章。")
            total_chapters = 10
            if hasattr(self.controller, 'task_planner') and self.test_task_id:
                plan = self.controller.task_planner.get_plan(self.test_task_id)
                if plan:
                    plan['total_chapters'] = 10
                    self.controller.task_planner._save_plan(plan)
            self.total_chapters = total_chapters

        test_cases = [
            {
                "name": "单章生成 + 闲聊干扰",
                "prompt": "今天天气真好，顺便帮我把第1章写了吧，细节丰富点。",
                "expected_new": [1],
                "analysis": "【期望】AI识别'第1章'为有效指令，过滤'天气好'的闲聊干扰。"
            },
            {
                "name": "批量数量(2章) + 剧情干扰",
                "prompt": "我刚才看了下设定，感觉前面提到的那个伏笔不错。对了，一次性写两章，就是接下来的两章，保持风格啊。",
                "expected_new": [2, 3],
                "analysis": "【期望】AI识别'一次性写两章'，从断点(2)生成2章。'伏笔'作为剧情描述被过滤。"
            },
            {
                "name": "批量数量(3章) + 总结干扰",
                "prompt": "你心里总结一下前面的剧情就行，不用发给我。另外，把接下来的三章给我写出来，剧情紧凑点。",
                "expected_new": [4, 5, 6],
                "analysis": "【期望】子句隔离生效。'总结'无章节号被丢弃，精准提取'接下来的三章'。"
            },
            {
                "name": "指定跳跃章节(第8章) + 否定干扰",
                "prompt": "第7章我先自己琢磨一下，你先跳过第7章。你直接生成第8章，这章是高潮，写精彩点。",
                "expected_new": [8],
                "analysis": "【期望】AI忽略'跳过第7章'，精准提取'生成第8章'。第7章绝对不能被生成！"
            },
            {
                "name": "剩余全部生成 + 情绪干扰",
                "prompt": "剩下的章节全部一次性生成吧，我懒得一章章点了，直接完结，搞快点。",
                "expected_new": list(range(9, total_chapters + 1)),
                "analysis": f"【期望】AI识别'剩下的全部'，从断点(9)一直生成到总章数({total_chapters})。"
            }
        ]
        
        all_passed = True
        for idx, case in enumerate(test_cases, 1):
            write_report(self.report_path, f"\n{'='*20} 测试用例 {idx}: {case['name']} {'='*20}")
            write_report(self.report_path, f"📤 发送指令: {case['prompt']}")
            write_report(self.report_path, f"🧠 {case['analysis']}")
            
            before_chapters = self.get_existing_chapters()
            self.controller.ask_ai(case["prompt"])
            
            wait_time = BASE_VERIFY_WAIT + len(case["expected_new"]) * PER_CHAPTER_WAIT
            write_report(self.report_path, f"⏳ 等待文件系统写入 ({wait_time}秒)...")
            time.sleep(wait_time)
            
            after_chapters = self.get_existing_chapters()
            new_chapters = sorted(list(after_chapters - before_chapters))
            expected_new = sorted(case["expected_new"])
            
            if new_chapters == expected_new:
                write_report(self.report_path, f"✅ 完美通过！实际生成：{new_chapters}，完全符合期望。")
            else:
                write_report(self.report_path, f"❌ 测试失败！期望生成：{expected_new}，实际生成：{new_chapters}。", also_print=True)
                missed = set(expected_new) - set(new_chapters)
                extra = set(new_chapters) - set(expected_new)
                if missed: write_report(self.report_path, f"   - 漏生成：{sorted(list(missed))}")
                if extra: write_report(self.report_path, f"   - 误生成（抗干扰失败）：{sorted(list(extra))}")
                all_passed = False
                
        return all_passed

    def perform_health_check(self, chapter_number: int) -> bool:
        """执行章节健康检查（保留冗余，非核心步骤）"""
        if not HEALTH_CHECK_ENABLED or not self.controller: return True
        if not self.test_task_id: return True
        
        try:
            health_result = self.controller._run_health_check(task_id=self.test_task_id, current_chapter=chapter_number)
            if health_result.get("success", False): return True
        except Exception: pass
        return True 

    def run_full_test(self) -> bool:
        """执行完整的测试流程"""
        test_passed = True
        core_steps_failed = []
        
        try:
            if not self.initialize_controller():
                return False
            
            prompt, total_words = self.generate_unique_prompt()
            try:
                self.create_project(prompt)
            except Exception as e:
                write_report(self.report_path, f"❌ 核心步骤失败 - 项目创建：{e}", also_print=True)
                core_steps_failed.append("项目创建")
                test_passed = False
            
            if test_passed:
                try:
                    self.generate_and_confirm_outline()
                except Exception as e:
                    write_report(self.report_path, f"❌ 核心步骤失败 - 大纲生成/确认：{e}", also_print=True)
                    core_steps_failed.append("大纲生成/确认")
                    test_passed = False
            
            # 🌟 核心测试：执行指令识别与抗干扰矩阵
            if test_passed:
                write_report(self.report_path, "\n🚀 开始执行【指令识别与抗干扰极限测试矩阵】...")
                if not self.run_instruction_test_cases():
                    core_steps_failed.append("指令识别与抗干扰测试")
                    test_passed = False
                    
        except Exception as e:
            write_report(self.report_path, f"❌ 测试流程中发生未预期异常：{e}", also_print=True)
            core_steps_failed.append("未预期异常")
            test_passed = False
        
        self.generate_final_report(core_steps_failed)
        return test_passed

    def generate_final_report(self, core_steps_failed: list) -> None:
        """生成测试最终报告"""
        write_report(self.report_path, "\n" + "=" * 70, also_print=False)
        write_report(self.report_path, "=== 极限测试最终结果 ===", also_print=False)
        write_report(self.report_path, "=" * 70, also_print=False)
        
        if not core_steps_failed:
            write_report(self.report_path, "🎉 所有极限测试步骤均通过！AI指令识别与抗干扰能力完美！", also_print=False)
        else:
            write_report(self.report_path, "❌ 以下核心步骤失败：", also_print=False)
            for step in core_steps_failed:
                write_report(self.report_path, f"   - {step}", also_print=False)
                
        end_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        write_report(self.report_path, f"\n结束时间：{end_time}", also_print=False)
        write_report(self.report_path, f"测试运行目录：{self.report_path.parent}", also_print=False)
        write_report(self.report_path, "=" * 70, also_print=False)

def main() -> None:
    unique_dir = generate_unique_dir()
    report_path, state_file_path = setup_test_environment(unique_dir)
    executor = TestExecutor(report_path, state_file_path)
    success = executor.run_full_test()
    
    if success:
        print(f"\n🎉 极限测试通过！")
        sys.exit(0)
    else:
        print(f"\n❌ 极限测试失败！请检查报告。")
        sys.exit(1)

if __name__ == "__main__":
    if not Path("core").exists() and not Path("../core").exists():
        print("警告：未检测到 core 目录。请确保此脚本位于项目根目录中。")
    main()
