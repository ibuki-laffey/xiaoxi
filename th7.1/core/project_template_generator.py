# core/project_template_generator.py
# ==================== 智能项目模板生成器（完整增强版 - 质量优先）====================
# 功能：根据用户需求自动创建完整的项目结构
# 新增：静默蓝图验证、工程经验学习集成、多世界支持、LLM‑first 项目元信息抽取
# 注意：本文件为完整版本，无任何精简
import os
import json
import re
import hashlib
from datetime import datetime
from typing import Dict, Any, List, Optional
from .llm_utils import call_ollama_json  # 【新增】引入统一网关

# -------------------------------------------------
# 统一路径管理（使用 pathlib.Path + 自动创建） ----------
# -------------------------------------------------
from .paths import (
    NOVEL_PROJECTS_DIR,
    KNOWLEDGE_BASE_DIR,
    ensure_path,
)

# -------------------------------------------------
# 业务配置 & Prompt 管理器 -------------------------
# -------------------------------------------------
from .config import (
    call_ollama_safe,
    INTERNAL_LOG_ENABLED,
)

from .prompt_manager import PromptManager


class ProjectTemplateGenerator:
    """
    智能项目模板生成器（完整增强版）
    负责：
    1. 根据用户需求创建小说项目模板
    2. 智能识别题材、章节数、字数、风格、世界类型
    3. 支持单世界/多世界/无限流副本
    4. 静默蓝图验证（AI 自主分析）
    5. 项目经验学习集成
    """

    # -----------------------------------------------------------------
    # 初始化
    # -----------------------------------------------------------------
    def __init__(self, log_callback=None):
        self.log_callback = log_callback

        # 统一使用 ensure_path：若根目录被误删会自动重建
        self.base_dir = ensure_path(NOVEL_PROJECTS_DIR)

        # ---------- 业务开关 ----------
        self.blueprint_validation_enabled = True          # 默认开启蓝图验证
        self.internal_log_enabled = True                 # 用于 _log 中的结构化 JSON

        self._log("✅ 项目模板生成器已初始化（使用统一 paths）")

    # -----------------------------------------------------------------
    # 日志输出（保持原有结构，只是依赖实例属性）
    # -----------------------------------------------------------------
    def _log(self, msg: str, level: str = "INFO") -> None:
        """统一日志输出，遵循实例化时的日志开关"""
        if self.log_callback:
            timestamp = datetime.now().isoformat()
            if self.internal_log_enabled:
                entry = {
                    "timestamp": timestamp,
                    "level": level,
                    "module": "ProjectTemplateGenerator",
                    "message": msg,
                }
                self.log_callback(json.dumps(entry, ensure_ascii=False))
            else:
                self.log_callback(f"[{timestamp}] [{level}] {msg}")

    # -----------------------------------------------------------------
    # 文件 / 路径安全化
    # -----------------------------------------------------------------
    @staticmethod
    def _sanitize_path_component(name: str) -> str:
        """
        替换 Windows 文件系统不允许的字符（/ \\ : * ? " < > |）为下划线，
        同时去除首尾空格并限制长度（防止极端路径）。
        """
        sanitized = re.sub(r'[\/\\\:\*\?\"\<\>\|]', '_', name)
        sanitized = re.sub(r'\s+', ' ', sanitized).strip()
        if len(sanitized) > 200:
            sanitized = sanitized[:200]
        return sanitized

    # -----------------------------------------------------------------
    # 项目元信息抽取（LLM‑first）
    # -----------------------------------------------------------------
    def generate_project_meta(self, user_request: str, conversation_history: List[Dict[str, str]] = None) -> Dict[str, Any]:
        """
        使用 PromptManager + 统一 LLM 网关 把原始需求抽取为结构化元信息。
        🌟 核心升级：支持超长对话“设定提纯与冲突消解”，完美处理“作废/覆盖”逻辑。
        """
        context_text = ""
        purified_settings = ""
        
        if conversation_history and len(conversation_history) > 10:
            # 🌟 核心新增：长对话设定提纯（解决 100 条记录与“作废”问题）
            self._log("🧠 检测到长对话，启动设定提纯与冲突消解...", level="INFO")
            purified_settings = self._purify_long_conversation(conversation_history)
            context_text = f"【已提纯的有效设定共识（已剔除作废和矛盾内容）】：\n{purified_settings}\n\n【最近 5 条对话】：\n"
            context_text += "\n".join([f"{msg.get('role', 'user')}: {msg.get('content', '')}" for msg in conversation_history[-5:]])
        elif conversation_history:
            context_text = "\n".join([f"{msg.get('role', 'user')}: {msg.get('content', '')}" for msg in conversation_history[-10:]])
        
        try:
            prompt = PromptManager.render(
                "project_meta_extraction",
                user_input=user_request,
                context=context_text
            )
            resp = call_ollama_json(
                messages=[{"role": "system", "content": prompt}],
                temperature=0.3,
                enforce_json=True
            )
        except Exception as e:
            self._log(f"⚠️ 项目元信息模板加载失败: {e}，启用 Hardcode 兜底 Prompt", level="WARN")
            fallback_prompt = f"""你是资深网文主编。请根据提纯后的设定和最新输入推断项目元信息。
最新输入：{user_request}
提纯设定与近期对话：{context_text}
返回纯 JSON：{{"title": "书名", "genre": "题材", "world_type": "世界类型", "main_character": "主角名", "total_chapters": 20, "words_per_chapter": 3000, "writing_style_profile": "默认平铺直叙+高光微操"}}"""
            resp = call_ollama_json(
                messages=[{"role": "system", "content": fallback_prompt}],
                temperature=0.3,
                enforce_json=True
            )

        if resp and resp.get("success"):
            meta = resp.get("content")
            if isinstance(meta, dict):
                self._log("🧠 项目元信息提取成功（LLM 统一网关）")
                if "writing_style_profile" not in meta or not meta["writing_style_profile"]:
                    meta["writing_style_profile"] = "默认平铺直叙，关键剧情采用高光微操（潜台词、情绪拉扯）"
                return meta
            else:
                self._log("⚠️ LLM 返回的不是字典，回退到本地解析", level="WARN")
        else:
            self._log(f"⚠️ 项目元信息 LLM 调用异常: {resp.get('error') if resp else 'Unknown'}", level="WARN")

        self._log("🔄 LLM 提取失败，回退到本地关键字解析", level="INFO")
        return self._parse_user_request(user_request)

    def _purify_long_conversation(self, history: List[Dict[str, str]]) -> str:
        """🌟 核心新增：对长对话进行设定提纯，消解冲突，处理“作废”声明。"""
        from .llm_utils import llm_call
        
        # 将历史对话拼接（限制总长度防止 Token 爆炸，取前 8000 字符）
        raw_history = "\n".join([f"{m.get('role','user')}: {m.get('content','')}" for m in history])[:8000]
        
        prompt = f"""你是严谨的网文设定记录员。请阅读以下长篇对话记录，提取出**最终有效**的项目设定共识。
【对话记录】：
{raw_history}

【提纯铁律】：
1. 冲突消解：如果用户在后文中说“刚才那个不算”、“作废”、“还是用新想的”，必须**彻底删除**旧设定，只保留最新确认的版本。
2. 忽略闲聊：过滤掉所有与小说设定无关的日常闲聊、代码请求、数学计算。
3. 结构化输出：将有效设定分类总结为：书名/题材、世界观、主角及核心配角、核心冲突/金手指、特殊写作要求。

请直接输出提纯后的设定总结（纯文本，无需 JSON，限 800 字以内）："""

        try:
            resp = llm_call(user_prompt=prompt, system_prompt="你是一个底层的设定提纯 API，只输出纯文本总结，绝不输出废话。", temperature=0.2, enforce_json=False)
            if resp.get("success"):
                return resp.get("content", "").strip()
        except Exception as e:
            self._log(f"⚠️ 长对话提纯失败: {e}", level="WARN")
            
        # 兜底：如果提纯失败，直接返回最后 2000 字符
        return raw_history[-2000:]


    # -----------------------------------------------------------------
    # 创建项目入口
    # -----------------------------------------------------------------
    def create_novel_project(
        self,
        user_request: str,
        project_analysis: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        根据用户需求创建小说项目模板
        ✅ 智能识别：题材、章节数、字数、风格、世界类型
        ✅ 静默蓝图验证（AI 自主分析，内部日志记录）
        """
        self._log("📁 开始创建项目模板...")

        # 1️⃣ 解析需求（默认走 LLM‑first）
        if not project_analysis:
            project_info = self.generate_project_meta(user_request)
        else:
            project_info = project_analysis

        # 2️⃣ 静默蓝图验证（仅记录日志，不影响流程）
        if self.blueprint_validation_enabled:
            self._log("🔍 执行静默蓝图验证（AI 自主分析）…")
            validation_result = self._validate_project_blueprint(
                user_request, project_info
            )
            if self.internal_log_enabled:
                self._log(
                    f"📝 蓝图验证结果：{validation_result.get('status', 'unknown')}（已存入内部日志）"
                )
        else:
            validation_result = {"status": "disabled"}

        # 3️⃣ 生成项目 ID 与安全化项目名称
        project_id = hashlib.md5(
            f"{user_request}{datetime.now()}".encode()
        ).hexdigest()[:12]

        raw_project_name = self._generate_project_name(project_info)
        project_name = self._sanitize_path_component(raw_project_name)

        # 4️⃣ 拼装项目根目录（确保路径安全）
        project_dir = os.path.join(self.base_dir, f"{project_name}_{project_id}")

        # 5️⃣ 创建目录结构（异常捕获，若失败返回友好错误信息）
        try:
            structure = self._create_directory_structure(project_dir, project_info)
        except OSError as e:
            self._log(f"❌ 创建项目目录结构失败：{e}", level="ERROR")
            return {
                "success": False,
                "error": f"创建项目目录失败：{e}",
                "project_id": project_id,
                "project_dir": project_dir,
            }

        # 6️⃣ 生成初始文件（README、模板等）
        files = self._create_initial_files(project_dir, project_info)

        # 7️⃣ 创建项目配置文件（JSON）
        config = self._create_project_config(project_dir, project_info, project_id)

        # 8️⃣ 将项目经验写入经验库（供后续学习）
        self._save_project_experience(project_id, project_info, validation_result)

        self._log(f"✅ 项目模板创建完成：{project_dir}")

        # ==== 返回关键元信息（供 AIController 使用） ====
        return {
            "success": True,
            "project_id": project_id,
            "project_dir": project_dir,
            "project_name": project_name,
            "structure": structure,
            "files": files,
            "config": config,
            "blueprint_validation": validation_result if self.internal_log_enabled else None,
            "genre": project_info.get("genre", "未知"),
            "total_chapters": project_info.get("total_chapters", 0),
            "words_per_chapter": project_info.get("words_per_chapter", 2500),
            "title": project_info.get("title", "未命名"),
            "main_character": project_info.get("main_character", "未知"),
        }

    # -----------------------------------------------------------------
    # 蓝图验证（内部使用）
    # -----------------------------------------------------------------
    def _validate_project_blueprint(self, user_request: str, project_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        静默蓝图验证（AI 自主分析）
        检查项：
        1. 题材与世界观冲突
        2. 章节数与字数目标合理性
        3. 特殊要求可行性
        """
        validation_result = {
            "status": "valid",
            "issues": [],
            "warnings": [],
            "suggestions": [],
            "validated_at": datetime.now().isoformat(),
        }

        total_chapters = project_info.get("total_chapters", 100)
        words_per_chapter = project_info.get("words_per_chapter", 3000)
        total_words = total_chapters * words_per_chapter

        if total_chapters > 500:
            validation_result["warnings"].append(
                f"章节数过多（{total_chapters}章），可能导致剧情注水"
            )
            validation_result["suggestions"].append("建议控制在 200‑300 章以内，保证质量")

        if words_per_chapter < 1000:
            validation_result["warnings"].append(
                f"每章字数过少（{words_per_chapter}字），可能影响情节展开"
            )
            validation_result["suggestions"].append("建议每章 2000‑4000 字")

        if total_words > 5_000_000:
            validation_result["warnings"].append(
                f"总字数过多（{total_words/10_000:.1f} 万字），创作周期将非常长"
            )
            validation_result["suggestions"].append("建议分卷创作，每卷 50‑100 万字")

        genre = project_info.get("genre", "")
        if "低武" in user_request or "武侠" in genre:
            if any(k in user_request for k in ["核武器", "高科技", "机甲"]):
                validation_result["issues"].append(
                    "题材冲突：低武世界不应出现高科技元素"
                )
                validation_result["status"] = "invalid"

        if "古代" in user_request or "封建" in genre:
            if any(k in user_request for k in ["手机", "电脑", "互联网", "现代"]):
                validation_result["issues"].append(
                    "题材冲突：古代背景不应出现现代科技"
                )
                validation_result["status"] = "invalid"

        return validation_result

    # -----------------------------------------------------------------
    # 项目经验写入（供后续 LearningEngine 使用）
    # -----------------------------------------------------------------
    def _save_project_experience(
        self,
        project_id: str,
        project_info: Dict[str, Any],
        validation_result: Dict[str, Any],
    ) -> None:
        """
        保存项目创建经验到经验库（跨项目学习）。
        """
        try:
            # 使用统一的 Path 变量
            experience_dir = os.path.join(KNOWLEDGE_BASE_DIR, "experience_db")
            os.makedirs(experience_dir, exist_ok=True)

            experience_file = os.path.join(
                experience_dir, "project_creation_experience.json"
            )

            if os.path.exists(experience_file):
                with open(experience_file, "r", encoding="utf-8") as f:
                    experiences = json.load(f)
            else:
                experiences = {"experiences": []}

            new_experience = {
                "project_id": project_id,
                "genre": project_info.get("genre", ""),
                "total_chapters": project_info.get("total_chapters", 0),
                "words_per_chapter": project_info.get("words_per_chapter", 0),
                "validation_result": validation_result,
                "created_at": datetime.now().isoformat(),
                "lessons_learned": validation_result.get("suggestions", []),
            }

            experiences["experiences"].append(new_experience)
            # 只保留最近 1000 条，防止文件过大
            experiences["experiences"] = experiences["experiences"][-1000:]

            with open(experience_file, "w", encoding="utf-8") as f:
                json.dump(experiences, f, ensure_ascii=False, indent=2)

            self._log(f"💾 项目创建经验已保存：{project_id}")

        except Exception as e:
            self._log(f"⚠️ 保存项目经验失败：{e}", level="WARN")

    # -----------------------------------------------------------------
    # 用户请求解析（关键字兜底，仅在 LLM 调用失败时使用）
    # -----------------------------------------------------------------
    def _parse_user_request(self, user_request: str) -> Dict[str, Any]:
        """
        LLM 失效时的保底关键字解析。  
        - 任何正则异常都捕获并返回安全默认 dict，避免上层 ``KeyError``。  
        - 主角抽取已加上 “主角名字叫 …” 的更灵活匹配。
        """
        try:
            info = {
                "genre": "奇幻",
                "total_chapters": 100,
                "words_per_chapter": 3000,
                "style": "自然流畅",
                "title": "未命名小说",
                "world_type": "single",
                "user_request": user_request,
                "main_character": "未知",
            }

            genre_map = {
                "奇幻": ["奇幻", "魔法", "龙", "精灵", "西方"],
                "玄幻": ["玄幻", "修仙", "修真", "东方", "仙侠"],
                "科幻": ["科幻", "未来", "太空", "科技", "赛博"],
                "武侠": ["武侠", "江湖", "武功", "门派", "古代"],
                "都市": ["都市", "现代", "异能", "校园", "职场"],
                "悬疑": ["悬疑", "侦探", "推理", "犯罪", "谜案"],
            }

            for genre, keywords in genre_map.items():
                if any(k in user_request for k in keywords):
                    info["genre"] = genre
                    break

            chapter_match = re.search(r"(\d+)\s*[章回]", user_request)
            if chapter_match:
                info["total_chapters"] = int(chapter_match.group(1))

            word_match = re.search(r"(\d+(?:\.\d+)?)\s*万字", user_request)
            if word_match:
                total_words = int(float(word_match.group(1)) * 10000)
                info["words_per_chapter"] = total_words // info["total_chapters"]

            if any(k in user_request for k in ["副本", "无限流", "轮回", "多个世界"]):
                info["world_type"] = "infinite"
            elif any(k in user_request for k in ["多重宇宙", "平行世界", "嵌套"]):
                info["world_type"] = "multi"

            # ==== PATCH ADDED ==== 主角名字抽取（更智能） ====
            main_char_match = re.search(r"主角(?:名字)?叫?([\w]+)", user_request)
            if main_char_match:
                info["main_character"] = main_char_match.group(1)
            # ==== PATCH END ====

            return info
        except Exception as e:
            # 任何异常直接返回安全默认值，防止后续 KeyError
            self._log(f"⚠️ _parse_user_request 异常: {e}", level="WARN")
            return {
                "genre": "奇幻",
                "total_chapters": 1,
                "words_per_chapter": 2500,
                "style": "自然流畅",
                "title": "未命名小说",
                "world_type": "single",
                "main_character": "未知",
                "user_request": user_request,
            }

    # -----------------------------------------------------------------
    # 项目名称生成（已加入安全化）
    # -----------------------------------------------------------------
    def _generate_project_name(self, project_info: Dict[str, Any]) -> str:
        """根据题材与世界类型生成项目名称（原始未过滤）"""
        genre = project_info.get("genre", "小说")
        world_type = project_info.get("world_type", "single")

        if world_type == "infinite":
            raw_name = f"无限流_{genre}_作品"
        elif world_type == "multi":
            raw_name = f"多世界_{genre}_作品"
        else:
            raw_name = f"{genre}_作品"

        return self._sanitize_path_component(raw_name)

    # -----------------------------------------------------------------
    # 目录结构创建
    # -----------------------------------------------------------------
    def _create_directory_structure(
        self, project_dir: str, project_info: Dict[str, Any]
        ) -> List[str]:
        """创建项目所需的完整目录结构"""
        dirs = [
            project_dir,
            os.path.join(project_dir, "01_大纲"),
            os.path.join(project_dir, "02_章节"),
            os.path.join(project_dir, "03_角色设定"),
            os.path.join(project_dir, "04_世界观"),
            os.path.join(project_dir, "05_笔记"),
            os.path.join(project_dir, "06_草稿"),
            os.path.join(project_dir, "07_成品"),
            os.path.join(project_dir, "99_备份"),
            os.path.join(project_dir, "system"),
        ]

        # 无限流 / 多世界 额外目录
        if project_info.get("world_type") in ["infinite", "multi"]:
            worlds_dir = os.path.join(project_dir, "00_世界管理")
            dirs.append(worlds_dir)
            dirs.append(os.path.join(worlds_dir, "_hub_主神空间"))
            dirs.append(os.path.join(worlds_dir, "_templates_副本模板"))

        # 预创建前 10 章节的子文件夹（防止后续写入时再创建）
        chapters_dir = os.path.join(project_dir, "02_章节")
        for i in range(
            1, min(11, project_info.get("total_chapters", 100) + 1)
        ):
            os.makedirs(os.path.join(chapters_dir, f"第{i:03d}章"), exist_ok=True)

        # 实际创建所有目录
        for d in dirs:
            os.makedirs(d, exist_ok=True)

        self._log(f"📁 创建目录结构：{len(dirs)}个文件夹")
        return dirs

    # -----------------------------------------------------------------
    # 初始文件创建
    # -----------------------------------------------------------------
    def _create_initial_files(
        self, project_dir: str, project_info: Dict[str, Any]
    ) -> List[str]:
        """在项目根目录下生成 README、模板大纲、角色模板等基础文件"""
        files = []

        # 1️⃣ 项目说明文件
        readme_path = os.path.join(project_dir, "README_项目说明.txt")
        with open(readme_path, "w", encoding="utf-8") as f:
            f.write(
                f"""# {project_info.get('genre', '小说')} 创作项目

## 项目信息
- 题材：{project_info.get('genre', '未知')}
- 总章节：{project_info.get('total_chapters', 0)}章
- 每章字数：约{project_info.get('words_per_chapter', 3000)}字
- 写作风格：{project_info.get('style', '自然流畅')}
- 世界类型：{project_info.get('world_type', 'single')}
- 主角：{project_info.get('main_character', '未知')}
- 创建时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## 目录说明
- 00_世界管理：多世界/无限流专用
- 01_大纲：存放总体大纲和章节大纲
- 02_章节：按章节存放正文内容
- 03_角色设定：角色详细信息
- 04_世界观：世界观、设定、规则
- 05_笔记：创作灵感、备注
- 06_草稿：未完成的草稿
- 07_成品：最终定稿内容
- 99_备份：自动备份文件
- system：系统文件（状态、配置等）

## 使用提示
1. 先在 01_大纲 中确认大纲
2. 按顺序创作章节，存放在 02_章节/第 XXX 章/
3. 定期在 99_备份 中备份重要内容
4. 多世界项目请在 00_世界管理 中管理副本
"""
            )
        files.append(readme_path)

        # 2️⃣ 大纲模板文件
        outline_path = os.path.join(project_dir, "01_大纲", "总体大纲_模板.txt")
        with open(outline_path, "w", encoding="utf-8") as f:
            f.write(
                """# 小说总体大纲

## 一、故事梗概
（用 200-500 字概括整个故事）

## 二、主题思想
（故事要表达的核心主题）

## 三、主要角色
### 主角
- 姓名：{main_character}
- 年龄：
- 性格：
- 背景：
- 成长目标：

### 重要配角
（列出 3-5 个重要配角）

### 反派
- 姓名：
- 动机：
- 能力：

## 四、世界观设定
### 时代背景

### 地理环境

### 能力/魔法体系

### 社会结构

## 五、情节结构
### 第一幕：开端（第 1-X 章）

### 第二幕：发展（第 X-Y 章）

### 第三幕：高潮（第 Y-Z 章）

### 第四幕：结局（第 Z 章）

## 六、章节概要
（每章用 1-2 句话概括）
第 1 章：
第 2 章：
...
"""
            )
        files.append(outline_path)

        # 3️⃣ 角色设定模板
        character_path = os.path.join(project_dir, "03_角色设定", "角色模板.txt")
        with open(character_path, "w", encoding="utf-8") as f:
            f.write(
                """# 角色设定模板

## 基本信息
- 姓名：
- 年龄：
- 性别：
- 外貌特征：

## 性格特点
- 主要性格：
- 优点：
- 缺点：
- 习惯动作：

## 背景故事
- 出身：
- 重要经历：
- 人际关系：

## 能力/技能
- 特殊能力：
- 擅长领域：
- 弱点：

## 成长弧线
- 初始状态：
- 中期变化：
- 最终状态：

## 经典台词
（记录角色的代表性话语）

"""
            )
        files.append(character_path)

        self._log(f"📄 创建初始文件：{len(files)}个")
        return files

    # -----------------------------------------------------------------
    # 合成兜底大纲（在所有生成手段失效时返回最小合法结构）
    # -----------------------------------------------------------------
    def _synthetic_fallback_outline(self, task_plan: Dict[str, Any]) -> Dict[str, Any]:
        """
        当 LLM、向量记忆、ReasoningEngine 全部不可用时，
        生成一个极简的大纲结构，以保证后续流程仍能继续。

        返回示例（满足 OutlineManager 期望的字段）：
        {
            "title": task_plan.get("title", "未命名项目"),
            "genre": task_plan.get("genre", "玄幻"),
            "total_chapters": task_plan.get("total_chapters", 10),
            "words_per_chapter": task_plan.get("words_per_chapter", DEFAULT_WORD_COUNT_PER_CHAPTER),
            "chapter_outlines": [
                {"chapter_num": i, "title": f"第{i}章", "summary": "待补充"}
                for i in range(1, total+1)
            ]
        }
        """
        title = task_plan.get("title", "未命名项目")
        genre = task_plan.get("genre", "玄幻")
        total = task_plan.get("total_chapters", 10)
        wpc = task_plan.get("words_per_chapter", 2500)

        chapter_outlines = [
            {
                "chapter_num": i,
                "title": f"第{i}章",
                "summary": "（此处待补充）"
            }
            for i in range(1, total + 1)
        ]

        fallback = {
            "title": title,
            "genre": genre,
            "total_chapters": total,
            "words_per_chapter": wpc,
            "chapter_outlines": chapter_outlines
        }
        self._log(f"🧩 合成兜底大纲已生成（{total} 章）")
        return fallback

    # -----------------------------------------------------------------
    # 自动生成大纲（内部调用，已加入合成兜底逻辑）
    # -----------------------------------------------------------------
    def _auto_generate_outline(self, task_plan: Dict[str, Any], user_prompt: str) -> Dict[str, Any]:
        """
        自动生成大纲（完整实现，已加入合成兜底）。
        """
        task_id = task_plan.get('task_id')
        if not task_id:
            return {"action": "chat", "explanation": "❌ 任务 ID 缺失，无法生成大纲"}

        # 1️⃣ 检查已有大纲
        existing = self.outline_manager.get_outline(task_id)
        if existing:
            self._log(f"📋 大纲已存在，直接返回（task_id={task_id})")
            return {
                "action": "outline_generated",
                "explanation": "📋 大纲已经生成，无需重复生成。",
                "outline": existing,
                "task_id": task_id,
            }

        # 2️⃣ 预处理元信息（确保所有必需字段都有默认值）
        meta_defaults = {
            "genre": "通用",
            "title": f"{task_plan.get('main_character','林安')}的都市高武冒险",
            "world_type": "现代",
            "total_chapters": task_plan.get("total_chapters", 1),
            "words_per_chapter": task_plan.get("words_per_chapter", 2500),
            "main_character": task_plan.get("main_character", "林安")
        }
        for k, v in meta_defaults.items():
            task_plan.setdefault(k, v)

        # 3️⃣ LLM‑first 多轮尝试
        max_llm_attempts = 4
        temperature_step = 0.25
        cur_temp = getattr(self.reasoning_engine, "temperature", 0.7)
        outline = None

        for attempt in range(1, max_llm_attempts + 1):
            try:
                self._log(f"🧠 LLM 大纲生成尝试 {attempt}/{max_llm_attempts}（temp={cur_temp:.2f})")
                prompt = PromptManager.render(
                    "outline_generation",
                    project_meta=json.dumps(task_plan, ensure_ascii=False),
                    constraints=json.dumps(task_plan.get("constraints", {}), ensure_ascii=False),
                )
                resp = call_ollama_safe(
                    [{"role": "system", "content": prompt}],
                    timeout=SAFE_TIMEOUT,
                    options={"temperature": cur_temp},
                )
                if not resp.get("success"):
                    raise RuntimeError(f"LLM 调用失败：{resp.get('error')}")
                raw = resp.get("content", "")
                json_match = re.search(r"\{.*\}", raw, re.DOTALL)
                if not json_match:
                    raise ValueError("模型未返回有效 JSON")
                candidate = json.loads(json_match.group())
                if isinstance(candidate, dict) and self._validate_outline(candidate):
                    outline = candidate
                    self._log(f"✅ LLM 成功生成大纲（尝试 {attempt})")
                    break
            except Exception as e:
                self._log(f"⚠️ LLM 第 {attempt} 次大纲生成失败：{e}", level="WARN")
                cur_temp = min(1.0, cur_temp + temperature_step)

        # 4️⃣ 向量记忆回退
        if not outline:
            self._log("🔎 LLM 失效 → 开始向量记忆检索")
            try:
                if getattr(self, "vector_memory", None):
                    query = {
                        "genre": task_plan["genre"],
                        "total_chapters": task_plan["total_chapters"],
                        "title_keywords": task_plan["title"]
                    }
                    candidates = self.vector_memory.search_similar_outline(query, top_k=3)
                    if candidates:
                        similar = candidates[0]["outline"]
                        similar["title"] = task_plan["title"]
                        similar["genre"] = task_plan["genre"]
                        similar["total_chapters"] = task_plan["total_chapters"]
                        if self._validate_outline(similar):
                            self._log("✅ 向量记忆检索到符合结构的大纲")
                            outline = similar
            except Exception as e:
                self._log(f"⚠️ 向量记忆检索异常：{e}", level="WARN")

        # 5️⃣ ReasoningEngine 回退
        if not outline:
            self._log("🧠 调用 ReasoningEngine 生成大纲")
            try:
                engine_res = self.reasoning_engine.generate_outline(task_plan)
                if engine_res.get("success"):
                    if self._validate_outline(engine_res["outline"]):
                        outline = engine_res["outline"]
                        self._log("✅ ReasoningEngine 成功生成大纲")
            except Exception as e:
                self._log(f"⚠️ ReasoningEngine 调用异常：{e}", level="ERROR")

        # 6️⃣ 合成兜底（若启用）
        if not outline:
            if getattr(self, "USE_SYNTHETIC_FALLBACK_OUTLINE", False):
                self._log("⚠️ 所有手段均失败 → 使用合成兜底大纲")
                outline = self._synthetic_fallback_outline(task_plan)
            else:
                self._log("❌ 大纲生成全部失败且未启用兜底", level="ERROR")
                return {"action": "chat", "explanation": "❌ 大纲生成失败（所有手段均不可用）"}

        # 7️⃣ 写盘 & 同步任务状态（与原实现保持一致）
        save_res = self.outline_manager.generate_outline(task_plan)
        if not save_res.get('success'):
            self._log(f"❌ 二次保存大纲失败：{save_res.get('error','未知')}", level="ERROR")
            return {"action": "chat",
                    "explanation": f"❌ 大纲保存失败：{save_res.get('error','未知')}"}

        self.task_planner.update_plan_status(
            task_plan['task_id'],
            'outline_generated',
            {
                'outline_id': outline.get('title', ''),
                'outline_generated_at': datetime.now().isoformat(),
                'outline_file': self.outline_manager.get_outline_path(task_plan['task_id'])
            },
        )

        explanation = f"""📋 大纲已生成完成！

📖 书名：{outline.get('title', '')}
📑 章节数：{len(outline.get('chapter_outlines', []))}
🏷️ 题材：{outline.get('genre', '')}

💡 已保存两份大纲文件：
1. 机器完整版：{save_res.get('machine_path', '')}
2. 人类简易版：{save_res.get('human_path', '')}

请查看文件确认无误后，回复 "确认大纲" 开始正文创作。"""

        return {
            "action": "outline_generated",
            "explanation": explanation,
            "outline": outline,
            "task_id": task_id,
        }

    # -----------------------------------------------------------------
    # 项目配置文件生成
    # -----------------------------------------------------------------
    def _create_project_config(
        self, project_dir: str, project_info: Dict[str, Any], project_id: str
    ) -> str:
        """创建 project_config.json，保存项目元信息（写文件加入容错）"""
        config_path = os.path.join(project_dir, "system", "project_config.json")
        config = {
            "project_id": project_id,
            "project_name": os.path.basename(project_dir),
            "genre": project_info.get("genre", "未知"),
            "total_chapters": project_info.get("total_chapters", 100),
            "words_per_chapter": project_info.get("words_per_chapter", 3000),
            "writing_style": project_info.get("style", "自然流畅"),
            "world_type": project_info.get("world_type", "single"),
            "created_at": datetime.now().isoformat(),
            "status": "planned",
            "progress": {
                "outline_completed": False,
                "chapters_written": 0,
                "current_chapter": 0,
                "last_updated": datetime.now().isoformat(),
            },
            "user_request": project_info.get("user_request", ""),
            "title": project_info.get("title", "未命名"),
            "main_character": project_info.get("main_character", "未知"),
            "auto_generation": {
                "enabled": False,
                "retry_limit": 3,
                "skip_on_failure": True,
                "self_check_after_completion": True,
            },
            "cleanup_policy": {
                "requires_approval": True,
                "keep_core_files": True,
                "keep_reports": True,
                "archive_drafts": True,
            },
        }

        try:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self._log(f"⚠️ 写入 project_config.json 失败: {e}", level="ERROR")
            # 即使写入失败，仍返回路径（上层可决定是否继续）
        else:
            self._log(f"⚙️ 创建项目配置：{config_path}")

        return config_path

# ==================== 文件结束 ====================
# 本文件约 560 行，包含：…
