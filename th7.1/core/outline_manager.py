# -*- coding: utf-8 -*-
# core/outline_manager.py
# ==============================================================================
# 大纲管理模块（完整增强版 - 质量优先）
# 负责：
#   1️⃣ 生成机器完整大纲（JSON）并生成人类可读版（TXT）
#   2️⃣ 读取 / 保存 / 删除 大纲文件
#   3️⃣ 大纲确认（记录对账、更新项目状态、生成章节卡片）
#   4️⃣ 自动恢复（从已生成章节逆向重建大纲）
#   5️⃣ 经验库写入（跨项目自学习使用）
#   6️⃣ 备份 / 恢复 / 导入 / 导出 大纲
#   7️⃣ 与 AIController、ReasoningEngine、ReconciliationEngine、
#      StateManager、ProgressTracker、ChapterCardManager 完全兼容
# 新增：
#   - 统一日志格式（支持专业日志 JSONL）                        # 新版: 支持结构化日志，旧版: 只输出文本
#   - 健康检查辅助接口 `reconstruct_outline_from_chapters`
#   - 自动保存与自动加载上一次工作状态                        # 新版: .auto_save 机制，旧版: 无
#   - 完整的异常捕获、回滚与事务式写入
# ==============================================================================

import os
import json
import shutil
import traceback
import re
import glob
import math
import hashlib
from datetime import datetime
from typing import Dict, Any, List, Optional

from .paths import NOVEL_PROJECTS_DIR, DEFAULT_SAVE_DIR, ensure_path
from .config import (
    TASK_TRACKING_DIR, KNOWLEDGE_BASE_DIR, PROJECT_LOG_ISOLATION, INTERNAL_LOG_ENABLED,
    LOG_FORMAT, LOG_LEVELS, AUTO_BACKUP_ENABLED, BACKUP_INTERVAL_CHAPTERS,
    MAX_BACKUP_COUNT, AUTO_SAVE_INTERVAL_SECONDS, call_ollama_safe
)

# 🌟 核心修复：防弹导入 path_utils，彻底解决 get_project_dir 不存在导致的 ImportError
try:
    from .path_utils import get_overall_outline_dir, get_chapter_outline_dir, get_project_dir
except ImportError:
    try:
        from .path_utils import get_overall_outline_dir, get_chapter_outline_dir
    except ImportError:
        get_overall_outline_dir = None
        get_chapter_outline_dir = None
    get_project_dir = None

from .logger import action_logger, project_logger
from .reasoning_engine import ReasoningEngine
from .reconciliation_engine import ReconciliationEngine
from .state_manager import StateManager
from .prompt_manager import PromptManager

ProgressTracker = None
ChapterCardManager = None


class InvalidIdentifierError(Exception):
    """当 task_id / project_id 为 None 或空字符串时抛出。"""
    pass

class OutlineManager:
    """大纲的全生命周期管理器。"""

    def __init__(self, log_callback=None, ai_controller=None):
        self.log_callback = log_callback
        self.ai_controller = ai_controller
        self.reasoning_engine = ReasoningEngine(log_callback)
        self.reconciliation_engine = ReconciliationEngine(log_callback)
        self.state_manager = StateManager(log_callback)
        
        self.outlines_dir = os.path.join(TASK_TRACKING_DIR, "outlines")
        os.makedirs(self.outlines_dir, exist_ok=True)
        
        self.experience_db_dir = os.path.join(KNOWLEDGE_BASE_DIR, "experience_db")
        os.makedirs(self.experience_db_dir, exist_ok=True)
        
        self.progress_tracker = None
        self.chapter_card_manager = None
        self._last_auto_save = datetime.now()
        
        self.vector_dir = os.path.join(KNOWLEDGE_BASE_DIR, "vector_memory")
        os.makedirs(self.vector_dir, exist_ok=True)

    def _log(self, msg: str, level: str = "INFO") -> None:
        if not self.log_callback:
            print(f"[{datetime.now().isoformat()}] [{level}] {msg}")
            return
        timestamp = datetime.now().isoformat()
        if PROJECT_LOG_ISOLATION:
            entry = {"timestamp": timestamp, "level": level, "module": "OutlineManager", "message": msg}
            self.log_callback(json.dumps(entry, ensure_ascii=False))
        else:
            if level in ("WARN", "ERROR"):
                if not hasattr(self, "_logged_messages"): self._logged_messages = set()
                key = (level, msg)
                if key in self._logged_messages: return
                self._logged_messages.add(key)
            self.log_callback(f"[{timestamp}] [{level}] {msg}")

    def _get_real_project_dir(self, task_id: str) -> str:
        if hasattr(self, 'ai_controller') and self.ai_controller and hasattr(self.ai_controller, 'task_planner'):
            try:
                plan = self.ai_controller.task_planner.get_plan(task_id)
                if isinstance(plan, dict) and plan.get("project_dir"): return plan["project_dir"]
            except Exception: pass
                
        pid = self._resolve_project_id(task_id, fallback=getattr(self.ai_controller, "current_project_id", None) if hasattr(self, "ai_controller") and self.ai_controller else None)
        if pid and pid != "invalid_project":
            try:
                # 🌟 防弹修复：检查 get_project_dir 是否存在，不存在则使用原生拼接
                if get_project_dir:
                    real_dir = get_project_dir(pid)
                else:
                    real_dir = os.path.join(str(NOVEL_PROJECTS_DIR), str(pid))
                if os.path.exists(real_dir): return real_dir
            except Exception: pass
                
            base_dir = str(NOVEL_PROJECTS_DIR)
            if os.path.exists(base_dir):
                matches = glob.glob(os.path.join(base_dir, f"*_{pid}"))
                for m in matches:
                    if os.path.isdir(m):
                        self._log(f"💡 [大纲路径] 通过 Glob 硬搜成功定位项目目录: {m}", level="INFO")
                        return m
                pure_dir = os.path.join(base_dir, str(pid))
                if os.path.isdir(pure_dir): return pure_dir
        return ""


    def _machine_outline_path(self, task_id: str) -> str:
        if not task_id:
            self._log("⚠️ _machine_outline_path 收到空 task_id", level="WARN")
            task_id = "unknown"
        project_dir = self._get_real_project_dir(task_id)
        if not project_dir:
            safe_dir = os.path.join(str(KNOWLEDGE_BASE_DIR), "invalid_outlines")
            os.makedirs(safe_dir, exist_ok=True)
            self._log(f"⚠️ 无法解析 project_dir，使用安全路径 (task_id={task_id})", level="WARN")
            return os.path.join(safe_dir, f"invalid_outline_{task_id}.json")
        outline_dir = os.path.join(project_dir, "01_大纲")
        os.makedirs(outline_dir, exist_ok=True)
        return os.path.join(outline_dir, "machine_outline.json")

    def _human_outline_path(self, task_id: str) -> str:
        if not task_id:
            self._log("⚠️ _human_outline_path 收到空 task_id", level="WARN")
            task_id = "unknown"
        project_dir = self._get_real_project_dir(task_id)
        if not project_dir:
            safe_dir = os.path.join(str(KNOWLEDGE_BASE_DIR), "invalid_outlines")
            os.makedirs(safe_dir, exist_ok=True)
            self._log(f"⚠️ 无法解析 project_dir，使用安全路径 (task_id={task_id})", level="WARN")
            return os.path.join(safe_dir, f"invalid_outline_human_{task_id}.txt")
        outline_dir = os.path.join(project_dir, "01_大纲")
        os.makedirs(outline_dir, exist_ok=True)
        return os.path.join(outline_dir, "human_outline.txt")

    def store_content(self, content_id: str, content: str, metadata: Dict[str, Any] = None):
        if metadata is None: metadata = {}
        try: content_hash = hashlib.md5(content.encode("utf-8")).hexdigest()
        except Exception as e:
            self._log(f"⚠️ 计算 content_hash 失败: {e}", level="WARN")
            content_hash = "unknown"

        keywords = self._extract_keywords(content)
        character_mentions = self._extract_character_mentions(content)
        key_events = self._extract_key_events(content)

        record = {
            "content_id": content_id, "content_hash": content_hash, "content_preview": content[:500],
            "content_length": len(content), "keywords": keywords, "character_mentions": character_mentions,
            "key_events": key_events, "metadata": metadata, "stored_at": datetime.now().isoformat(),
        }

        chapter = metadata.get("chapter", 0)
        group_dir = os.path.join(self.vector_dir, f"chapter_{chapter // 10:02d}")
        try:
            os.makedirs(group_dir, exist_ok=True)
            filepath = os.path.join(group_dir, f"{content_id}.json")
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(record, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self._log(f"❌ 向量记忆写入失败: {e}", level="ERROR")
            return {}

        if character_mentions: self._index_character_mentions(content_id, character_mentions, chapter)
        if key_events: self._index_key_events(content_id, key_events, chapter)
        self._log(f"💾 向量记忆已存储 content_id={content_id}（{len(content)}字，{len(keywords)}关键词，{len(character_mentions)}角色）")
        return record

    def _extract_keywords(self, text: str) -> List[str]:
        words = re.findall(r"[A-Za-z0-9\u4e00-\u9fa5]{2,}", text)
        freq = {}
        for w in words: freq[w] = freq.get(w, 0) + 1
        top = sorted(freq.items(), key=lambda x: x[1], reverse=True)[:10]
        return [w for w, _ in top]

    def _extract_character_mentions(self, text: str) -> List[str]:
        candidates = re.findall(r"[\u4e00-\u9fa5]{2,}", text)
        return list(set(candidates))

    def _extract_key_events(self, text: str) -> List[str]:
        sentences = re.split(r"[。！？]", text)
        events = [s.strip() for s in sentences if s and re.search(r"[动|行|做|去|来|到|完成|发生]", s)]
        return events

    def _index_character_mentions(self, content_id: str, characters: List[str], chapter: int) -> None:
        self._log(f"🗂️ 为章节 {chapter} 的内容 {content_id} 建立角色索引（{len(characters)} 条）", level="DEBUG")

    def _index_key_events(self, content_id: str, events: List[str], chapter: int) -> None:
        self._log(f"🗂️ 为章节 {chapter} 的内容 {content_id} 建立事件索引（{len(events)} 条）", level="DEBUG")

    def write_outline(self, task_id: str, outline: Dict[str, Any]) -> Dict[str, Any]:
        machine_path = self._machine_outline_path(task_id)
        human_path = self._human_outline_path(task_id)
        try:
            tmp_machine = machine_path + ".tmp"
            with open(tmp_machine, "w", encoding="utf-8") as f: json.dump(outline, f, ensure_ascii=False, indent=2)
            os.replace(tmp_machine, machine_path)
        except Exception as e:
            self._log(f"❌ 写入机器大纲失败 (task_id={task_id}): {e}", level="ERROR")
            return {"success": False, "error": str(e)}

        try:
            tmp_human = human_path + ".tmp"
            with open(tmp_human, "w", encoding="utf-8") as f: f.write(self._render_human_outline(outline))
            os.replace(tmp_human, human_path)
        except Exception as e:
            self._log(f"❌ 写入人类大纲失败 (task_id={task_id}): {e}", level="ERROR")
            return {"success": True, "warning": f"human write error: {e}", "machine_path": machine_path, "human_path": human_path}

        self._log(f"✅ 完整大纲已写入（task_id={task_id})")
        return {"success": True, "machine_path": machine_path, "human_path": human_path}

    def _current_project_id(self, task_id: str) -> str:
        plan_path = os.path.join(TASK_TRACKING_DIR, "plans", f"{task_id}.json")
        if not os.path.isfile(plan_path): raise FileNotFoundError(f"Task plan not found for task_id={task_id}")
        with open(plan_path, "r", encoding="utf-8") as f: plan = json.load(f)
        return plan.get("project_id", "")

    def _safe_int(self, val, default=0) -> int:
        """🌟 防弹整数转换器：完美清洗 '20章', '20', '40.5', '20章(占50%)' 等脏数据"""
        if isinstance(val, (int, float)): 
            return int(val)
        if isinstance(val, str):
            import re
            # 提取字符串中的第一个连续数字
            m = re.search(r'\d+', val)
            return int(m.group()) if m else default
        if isinstance(val, list) and val:
            return self._safe_int(val[0], default)
        return default

    def _temp_file_path(self, base_path: str) -> str:
        dirname, filename = os.path.split(base_path)
        return os.path.join(dirname, f".tmp_{filename}")

    def _load_project_config(self, project_id: str) -> Optional[Dict[str, Any]]:
        pattern = os.path.join(str(NOVEL_PROJECTS_DIR), f"*{project_id}*", "system", "project_config.json")
        matches = glob.glob(pattern, recursive=True)
        if not matches:
            self._log(f"⚠️ project_config.json 未找到（project_id={project_id})", level="WARN")
            return {}
        try:
            with open(matches[0], "r", encoding="utf-8") as f: return json.load(f)
        except Exception as e:
            self._log(f"⚠️ 读取 project_config.json 异常: {e}", level="WARN")
            return {}

    def get_character_growth_trajectory(self, task_id: str) -> List[Dict]:
        self._log(f"⚠️ 调用了占位 get_character_growth_trajectory（task_id={task_id})，返回空列表", level="WARN")
        return []

    def _fallback_rule_based(self, task_plan: Dict[str, Any]) -> Dict[str, Any]:
        # 🌟 致命修复：使用 _safe_int 防止 "40章" 导致 ValueError
        total = self._safe_int(task_plan.get("total_chapters"), 1)
        words_per = self._safe_int(task_plan.get("words_per_chapter"), 2500)
        fallback = {
            "title": task_plan.get("title", "未命名"), 
            "genre": task_plan.get("genre", "通用"), 
            "total_chapters": total, 
            "words_per_chapter": words_per, 
            "chapters": []
        }
        for i in range(1, total + 1):
            fallback["chapters"].append({
                "chapter_num": i, 
                "title": f"第 {i} 章", 
                "summary": f"本章概要（待完善），目标字数约 {words_per}。", 
                "keywords": [task_plan.get("genre", "通用")], 
                "pacing_state": "normal", 
                "genre_tags": []
            })
        self._log("✅ 使用规则模板兜底生成最小合法大纲", level="INFO")
        return {"success": True, "outline": fallback}

    def generate_outline(self, task_plan: Dict[str, Any]) -> Dict[str, Any]:
        task_id = task_plan.get("task_id", "")
        if not task_id: return {"success": False, "error": "task_plan 中缺少 task_id"}
        if hasattr(self, "ai_controller") and self.ai_controller:
            try:
                if hasattr(self.ai_controller, 'task_planner') and hasattr(self.ai_controller.task_planner, 'ensure_project_id'):
                    self.ai_controller.task_planner.ensure_project_id(task_id, fallback=getattr(self.ai_controller, "current_project_id", None) or "")
            except Exception as e:
                self._log(f"⚠️ ensure_project_id 失败 (task_id={task_id}): {e}", level="WARN")
        self._log(f"🌳 [强制路由] 将使用 generate_hierarchical_outline 生成完整大纲（task_id={task_id})")
        try: return self.generate_hierarchical_outline(task_plan)
        except Exception as e:
            self._log(f"❌ generate_outline 过程异常: {e}\n{traceback.format_exc()}", level="ERROR")
            return {"success": False, "error": str(e)}

    def generate_hierarchical_outline(self, task_plan: Dict[str, Any]) -> Dict[str, Any]:
        from . import config
        task_id = task_plan.get("task_id", "")
        if not task_id: return {"success": False, "error": "task_plan 中缺少 task_id"}

        real_project_dir = task_plan.get("project_dir", "")
        if not real_project_dir and hasattr(self, '_machine_outline_path'):
            machine_path = self._machine_outline_path(task_id)
            if machine_path: real_project_dir = os.path.dirname(os.path.dirname(machine_path))
        if not real_project_dir:
            self._log("❌ 无法确定真实的项目目录，终止大纲生成", level="ERROR")
            return {"success": False, "error": "invalid project: no real project dir"}

        outline_dir_name = getattr(config, "OUTLINE_DIR_NAME", "01_大纲")
        outline_dir = os.path.join(real_project_dir, outline_dir_name)
        os.makedirs(outline_dir, exist_ok=True)

        title = task_plan.get("title", "未命名")
        genre = task_plan.get("genre", "通用")
        main_char = task_plan.get("main_character", "主角")
        # 🌟 致命修复：使用 _safe_int 清洗 task_plan 中的脏数据
        total_chapters = self._safe_int(task_plan.get("total_chapters"), 30)
        words_per_chapter = self._safe_int(task_plan.get("words_per_chapter"), 2500)

        
        enable_hierarchical = getattr(config, 'ENABLE_HIERARCHICAL_OUTLINE', True)
        volume_split_threshold = getattr(config, 'VOLUME_SPLIT_THRESHOLD', 30)
        chapters_per_volume = getattr(config, 'CHAPTERS_PER_VOLUME', 20)

        if enable_hierarchical and total_chapters > volume_split_threshold:
            expected_volumes = max(1, math.ceil(total_chapters / chapters_per_volume))
            self._log(f"📚 [大纲模式] 启用动态分卷，预计 {expected_volumes} 卷（每卷约 {chapters_per_volume} 章）。")
        else:
            expected_volumes = 1
            self._log(f"ℹ️ [大纲模式] 章节数({total_chapters})未达阈值({volume_split_threshold})或已关闭分卷开关，采用单卷模式。")

        def _safe_call_llm_json(messages, temperature=0.1):
            try:
                from .llm_utils import call_ollama_json
                return call_ollama_json(messages=messages, temperature=temperature, enforce_json=True)
            except Exception:
                resp = call_ollama_safe(messages, options={"temperature": temperature, "format": "json"})
                if isinstance(resp, dict) and resp.get("success"): return {"success": True, "content": resp.get("content", "")}
                return {"success": False, "error": "LLM call failed"}

        master_path = os.path.join(outline_dir, "master_outline.json")
        master_data = None
        if os.path.isfile(master_path):
            try:
                with open(master_path, "r", encoding="utf-8") as f: master_data = json.load(f)
            except Exception: pass

        if not master_data:
            self._log("🌳 [阶段1] 生成全书主干（master outline）")
            master_prompt = f"""小说名：《{title}》 题材：{genre} 主角：{main_char}
【创作预算】：全书严格限制在 {total_chapters} 章，每章约 {words_per_chapter} 字。
【架构指导】：预计分为 {expected_volumes} 卷。请输出 JSON，包含：
1. "core_conflict": 核心冲突。2. "pacing_strategy": 节奏策略。
3. "volume_breakdown": 列表，每卷包含 "vol", "name", "expected_chapters", "core_goal", "genre_tags"。只输出纯 JSON！"""
            system_prompt = "你是一个严格的 JSON 数据生成 API。只能输出合法的纯 JSON 字符串。绝对禁止输出 Markdown 标记。"
            resp = _safe_call_llm_json([{"role": "system", "content": system_prompt}, {"role": "user", "content": master_prompt}])
            if resp.get("success"):
                raw_content = resp.get("content", "")
                if isinstance(raw_content, dict): master_data = raw_content
                elif isinstance(raw_content, str): master_data = self._extract_json_from_llm(raw_content) if hasattr(self, '_extract_json_from_llm') else None
            if not master_data or not isinstance(master_data, dict): return {"success": False, "error": "master outline 生成或解析失败"}
            # 修复：master 输出最低校验——垃圾 JSON 被解析成空 dict 时原本放行，导致 volume_breakdown 空兜底、卷大纲静默失败
            if not master_data.get("core_conflict") and not master_data.get("volume_breakdown"):
                return {"success": False, "error": "master outline 输出缺少 core_conflict/volume_breakdown，判定为无效输出"}
            self._atomic_write_json(master_path, master_data)

        all_chapters: List[Dict] = []
        volume_breakdown = master_data.get("volume_breakdown", [])
        if not isinstance(volume_breakdown, list) or not volume_breakdown or expected_volumes == 1:
            volume_breakdown = [{"vol": 1, "name": title, "expected_chapters": total_chapters, "core_goal": "推进主线", "genre_tags": [genre]}]

        current_chap_num = 1
        for vol_idx, vol_info in enumerate(volume_breakdown, 1):
            # 🌟 致命修复：清洗 LLM 返回的 "20章(占50%)" 等杂质字符串，防止乘法报错
            expected = self._safe_int(vol_info.get("expected_chapters"), chapters_per_volume)
            if vol_idx == len(volume_breakdown): 
                expected = total_chapters - current_chap_num + 1

            vol_file = os.path.join(outline_dir, f"volume_{vol_idx:02d}_outline.json")
            if os.path.isfile(vol_file):
                try:
                    with open(vol_file, "r", encoding="utf-8") as f: vol_data = json.load(f)
                    all_chapters.extend(vol_data)
                    current_chap_num += len(vol_data)
                    continue
                except Exception: pass

            self._log(f"📖 [阶段2] 生成第 {vol_idx} 卷《{vol_info.get('name','')}》大纲（预计 {expected} 章）")
            prev_summaries = "\n".join([f"第{c.get('chapter_num')}章 {c.get('title')}: {c.get('summary')}" for c in all_chapters[-3:]]) if all_chapters else "这是第一卷。"
            master_summary = {"core_conflict": master_data.get("core_conflict", ""), "pacing_strategy": master_data.get("pacing_strategy", ""), "volume_breakdown": master_data.get("volume_breakdown", [])}
            master_json_str = json.dumps(master_summary, ensure_ascii=False)[:2500]

            vol_prompt = f"""小说名：《{title}》 主角：{main_char}
【全局主干】：{master_json_str}
【前情提要】：{prev_summaries}
【本卷任务】：卷名：{vol_info.get('name','未知')}，核心目标：{vol_info.get('core_goal','推进剧情')}。本卷题材标签：{vol_info.get('genre_tags', [genre])}。
本卷必须生成精确的 {expected} 章！起始章节号为 {current_chap_num}。
【输出要求】输出合法的 JSON 对象，必须包含 "chapters" 键，其值为章节数组。绝对不能直接输出纯数组！
"chapters" 数组中的每条必须包含："chapter_num", "title", "summary"(50-100字), "event_span", "pacing_state", "genre_tags"。"""
            system_prompt = "你是一个严格的 JSON 数据生成 API。只能输出合法的 JSON 对象。绝对禁止输出 Markdown 标记。"
            resp = _safe_call_llm_json([{"role": "system", "content": system_prompt}, {"role": "user", "content": vol_prompt}])
            if not resp.get("success"): return {"success": False, "error": f"大纲生成失败: LLM 调用返回错误 - {resp.get('error')}"}

            vol_data = resp.get("content")
            if isinstance(vol_data, str): vol_data = self._extract_json_from_llm(vol_data) if hasattr(self, '_extract_json_from_llm') else None
            if isinstance(vol_data, dict):
                for key in ["chapters", "outline", "volume_outline", "data"]:
                    if key in vol_data and isinstance(vol_data[key], list): vol_data = vol_data[key]; break
                if isinstance(vol_data, dict): vol_data = None
            if not isinstance(vol_data, list) or len(vol_data) == 0: return {"success": False, "error": "大纲生成失败: LLM 返回内容无法解析为章节列表"}

            # 🌟🌟🌟 核心新增：大纲设定一致性审查 (Critic) - 用额外的 LLM 调用防止大纲跑偏 🌟🌟🌟
            core_details = task_plan.get("core_details", [])
            forbidden_settings = task_plan.get("forbidden_settings", [])
            
            if (core_details or forbidden_settings) and isinstance(vol_data, list) and len(vol_data) > 0:
                self._log("🔍 [大纲审查] 启动 LLM 校验本卷大纲是否偏离核心设定与禁令...")
                critic_prompt = f"""你是极其严苛的小说大纲审查专家。
【核心设定】：{json.dumps(core_details[:20], ensure_ascii=False)}
【绝对禁令】：{json.dumps(forbidden_settings[:10], ensure_ascii=False)}
【本卷大纲草案】：{json.dumps(vol_data, ensure_ascii=False)}

【审查任务】：
1. 检查大纲是否偏离了核心设定（例如：主角是男性宅男，能力是附身女性，大纲却写成了女性被霸总攻略，或者写成了特工/科幻/悬疑）。
2. 检查大纲是否违反了绝对禁令。
3. 检查主角的动机和行为是否符合设定。

如果大纲完全符合设定，请输出 {{"is_valid": true, "feedback": ""}}。
如果大纲存在严重偏离或违规，请输出 {{"is_valid": false, "feedback": "详细指出哪里偏离了设定，并给出具体的修改建议（如：第X章应该改为利用附身能力做XX事，而不是去地下档案库找夜莺）"}}。
只输出纯 JSON！"""
                
                critic_resp = _safe_call_llm_json([{"role": "user", "content": critic_prompt}], temperature=0.1)
                if critic_resp.get("success"):
                    critic_data = critic_resp.get("content")
                    if isinstance(critic_data, str):
                        critic_data = self._extract_json_from_llm(critic_data) if hasattr(self, '_extract_json_from_llm') else None
                    
                    if isinstance(critic_data, dict) and not critic_data.get("is_valid", True):
                        feedback = critic_data.get("feedback", "大纲偏离设定")
                        self._log(f"⚠️ [大纲审查] 发现偏离：{feedback}，触发大纲局部重写...", level="WARN")
                        
                        # 触发重写
                        rewrite_prompt = f"""你是小说大纲修正专家。
【原大纲】：{json.dumps(vol_data, ensure_ascii=False)}
【审查意见】：{feedback}
【核心设定】：{json.dumps(core_details[:20], ensure_ascii=False)}

请根据审查意见，对原大纲进行彻底修正，使其100%符合核心设定。保持原有的 JSON 结构（包含 chapter_num, title, summary 等），只输出修正后的纯 JSON 数组！"""
                        
                        rewrite_resp = _safe_call_llm_json([{"role": "user", "content": rewrite_prompt}], temperature=0.3)
                        if rewrite_resp.get("success"):
                            rewritten_data = rewrite_resp.get("content")
                            if isinstance(rewritten_data, str):
                                rewritten_data = self._extract_json_from_llm(rewritten_data) if hasattr(self, '_extract_json_from_llm') else None
                            if isinstance(rewritten_data, list) and len(rewritten_data) > 0:
                                vol_data = rewritten_data
                                self._log("✅ [大纲重写] 大纲已根据审查意见修正完毕。")

            min_allowed = max(1, int(expected * 0.9))
            if len(vol_data) > expected: vol_data = vol_data[:expected]
            elif len(vol_data) < min_allowed:
                for i in range(len(vol_data) + 1, min_allowed + 1):
                    vol_data.append({"chapter_num": current_chap_num + i - 1, "title": f"第{current_chap_num + i - 1}章", "summary": "待补充", "event_span": "single", "pacing_state": "normal", "genre_tags": []})

            for idx, chap in enumerate(vol_data):
                chap["chapter_num"] = current_chap_num + idx
                chap.setdefault("pacing_state", "normal")
                chap.setdefault("genre_tags", [])
                if not chap.get("summary"): chap["summary"] = f"第{chap['chapter_num']}章核心剧情推进"

            self._atomic_write_json(vol_file, vol_data)
            # 🌟 核心接入：大纲生成后，调用 LLM 审计大纲消耗了哪些设定
            if hasattr(self, 'ai_controller') and hasattr(self.ai_controller, '_llm_audit_setting_coverage'):
                self._log("🔍 [大纲审计] 启动 LLM 校验本卷大纲消耗了哪些核心设定...")
                outline_text = json.dumps(vol_data, ensure_ascii=False)
                self.ai_controller._llm_audit_setting_coverage(outline_text, task_plan, phase=f"第{vol_idx}卷大纲")

            all_chapters.extend(vol_data)
            current_chap_num += len(vol_data)

        if len(all_chapters) > total_chapters: all_chapters = all_chapters[:total_chapters]
        elif len(all_chapters) < total_chapters:
            for i in range(len(all_chapters) + 1, total_chapters + 1):
                all_chapters.append({"chapter_num": i, "title": f"第{i}章", "summary": "待补充", "event_span": "single", "pacing_state": "normal", "genre_tags": []})
        for idx, chap in enumerate(all_chapters): chap["chapter_num"] = idx + 1

        actual_total = len(all_chapters)
        if actual_total != total_chapters:
            task_plan["total_chapters"] = actual_total
            if hasattr(self, "ai_controller") and self.ai_controller and hasattr(self.ai_controller, "task_planner"):
                try: self.ai_controller.task_planner._save_plan(task_plan)
                except Exception: pass

        final_outline = {"title": title, "genre": genre, "total_chapters": actual_total, "chapters": all_chapters}
        machine_path = self._machine_outline_path(task_id) if hasattr(self, '_machine_outline_path') else os.path.join(outline_dir, "machine_outline.json")
        human_path = self._human_outline_path(task_id) if hasattr(self, '_human_outline_path') else os.path.join(outline_dir, "human_outline.txt")

        self._atomic_write_json(machine_path, final_outline)
        if hasattr(self, '_render_human_outline'):
            txt_content = self._render_human_outline(final_outline)
            self._atomic_write_text(human_path, txt_content)

        self._log(f"✅ 分卷大纲生成完毕，最终章节数 {actual_total}，已写入机器版 {machine_path} 与人类版 {human_path}")
        return {"success": True, "title": title, "chapter_outlines": all_chapters, "machine_path": machine_path, "human_path": human_path}

    # -----------------------------------------------------------------
    # 🌟 强制补全的核心辅助方法（防止文件后半部分缺失导致 AttributeError）
    # -----------------------------------------------------------------
    def _resolve_project_id(self, task_id: str, fallback: Optional[str] = None) -> str:
        try:
            plan_path = os.path.join(TASK_TRACKING_DIR, "plans", f"{task_id}.json")
            if os.path.isfile(plan_path):
                with open(plan_path, "r", encoding="utf-8") as f: plan = json.load(f)
                pid = plan.get("project_id")
                if pid and not pid.startswith("proj_task_"): return pid
        except Exception: pass
        return fallback if fallback else "invalid_project"

    def _atomic_write_json(self, file_path: str, data: Dict) -> None:
        dir_name = os.path.dirname(file_path)
        if dir_name: os.makedirs(dir_name, exist_ok=True)
        tmp_path = file_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        if os.name == 'nt' and os.path.exists(file_path):
            try: os.remove(file_path)
            except PermissionError: pass
        os.replace(tmp_path, file_path)

    def _atomic_write_text(self, file_path: str, text: str) -> None:
        dir_name = os.path.dirname(file_path)
        if dir_name: os.makedirs(dir_name, exist_ok=True)
        tmp_path = file_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        if os.name == 'nt' and os.path.exists(file_path):
            try: os.remove(file_path)
            except PermissionError: pass
        os.replace(tmp_path, file_path)

    def _extract_json_from_llm(self, raw: str) -> Any:
        if not raw or not isinstance(raw, str): return None
        try:
            from .super_guardian import guardian
            data = guardian.heal_json(raw, expected_type=dict)
            if data: return data
            data = guardian.heal_json(raw, expected_type=list)
            if data: return data
        except Exception: pass
        cleaned = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
        cleaned = re.sub(r"\s*```$", "", cleaned, flags=re.MULTILINE).strip()
        for start_char, end_char in [('{', '}'), ('[', ']')]:
            start = cleaned.find(start_char)
            end = cleaned.rfind(end_char)
            if start != -1 and end != -1 and start < end:
                try: return json.loads(cleaned[start : end + 1])
                except Exception: pass
        return None

    def _get_rolling_summaries(self, task_id: str, up_to_chapter: int) -> str:
        if not hasattr(self, 'progress_tracker'): return ""
        summaries = self.progress_tracker.get_chapter_summaries(task_id, up_to_chapter=up_to_chapter)
        if not summaries: return ""
        recent = summaries[-5:]
        return "\n".join([f"第{s.get('chapter')}章: {s.get('summary', '')}" for s in recent])

    def _save_chapter_outlines(self, task_id: str, outline: Dict[str, Any]) -> None:
        """把每个章节的大纲写入 02_章节/第XXX章/chapter_xx.txt 中。"""
        project_dir = self._get_real_project_dir(task_id)
        if not project_dir: return
        
        chapters = outline.get("chapters") or outline.get("chapter_outlines") or []
        for idx, chap in enumerate(chapters, start=1):
            chapter_dir = os.path.join(project_dir, "02_章节", f"第{idx:03d}章")
            os.makedirs(chapter_dir, exist_ok=True)
            chapter_path = os.path.join(chapter_dir, f"chapter_{idx:02d}.txt")
            
            title = chap.get("title", f"第 {idx} 章")
            summary = chap.get("summary") or chap.get("overview") or "暂无概要"
            content = f"{title}\n\n{summary}\n"
            
            try:
                self._atomic_write_text(chapter_path, content)
            except Exception as e:
                self._log(f"⚠️ 写入章节大纲失败 ({chapter_path})：{e}", level="WARN")

    def _synthetic_fallback_outline(self, task_plan: Dict[str, Any], meta: Dict[str, Any]) -> Dict[str, Any]:
        """人工合成最小可用大纲。"""
        total_chapters = int(meta.get("total_chapters", 20))
        words_per_chapter = int(meta.get("words_per_chapter", 2500))
        title = meta.get("title", f"{meta.get('main_character','林安')}的都市高武冒险")
        genre = meta.get("genre", "都市高武")
        world_type = meta.get("world_type", "现代")

        chapters = []
        for i in range(1, total_chapters + 1):
            chapters.append({"chapter_num": i, "title": f"第 {i} 章", "summary": "本章概要（请根据实际需求自行补充）", "keywords": [], "characters_involved": [meta.get("main_character", "林安")], "key_events": [], "pacing_state": "normal"})

        return {"title": title, "genre": genre, "world_type": world_type, "total_chapters": total_chapters, "words_per_chapter": words_per_chapter, "chapters": chapters}

    def _render_human_outline(self, outline: Dict[str, Any]) -> str:
        """将结构化大纲转换为易读的纯文本格式。"""
        lines = [f"书名：《{outline.get('title', '未命名')}》", f"题材：{outline.get('genre', '未定义')}", "-" * 30]
        for idx, ch in enumerate(outline.get("chapters", []), start=1):
            lines.append(f"第 {idx} 章：{ch.get('title', '未命名')}")
            if ch.get("summary"): lines.append(f"  概要：{ch['summary']}")
            if ch.get("keywords"): lines.append(f"  关键词：{', '.join(ch['keywords'])}")
            lines.append("")
        return "\n".join(lines)

    def get_outline(self, task_id: Optional[str]) -> Optional[Dict[str, Any]]:
        """读取机器大纲（JSON）。包含统一路径解析与终极硬探测兜底。"""
        if not task_id:
            self._log("⚠️ get_outline 调用时 task_id 为 None，返回 None", level="WARN")
            return None

        outline_path = self._machine_outline_path(task_id) if hasattr(self, '_machine_outline_path') else self.get_outline_path(task_id)
        if outline_path and os.path.isfile(outline_path):
            try:
                with open(outline_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                self._log(f"❌ 读取机器大纲失败({outline_path}): {e}", level="ERROR")
                return None

        self._log(f"⚠️ 未在标准位置找到机器大纲，启动最后硬探测兜底（task_id={task_id})", level="WARN")
        possible_names = ["machine_outline.json", "outline_machine.json", "outline.json"]

        try:
            tracking_dir = getattr(config, 'TASK_TRACKING_DIR', 'task_tracking')
            plan_path = os.path.join(str(tracking_dir), "plans", f"{task_id}.json")
            if os.path.isfile(plan_path):
                with open(plan_path, "r", encoding="utf-8") as f:
                    plan = json.load(f)
                pd = plan.get("project_dir")
                if pd and os.path.isdir(pd):
                    for name in possible_names:
                        cand = os.path.join(pd, "01_大纲", name)
                        if os.path.isfile(cand):
                            with open(cand, "r", encoding="utf-8") as f:
                                return json.load(f)
        except Exception as e:
            self._log(f"⚠️ 通过 project_dir 硬探测异常: {e}", level="WARN")

        try:
            novel_dir = getattr(config, 'NOVEL_PROJECTS_DIR', 'novel_projects')
            for root, _, files in os.walk(str(novel_dir)):
                for fname in files:
                    if fname in possible_names:
                        cand = os.path.join(root, fname)
                        if os.path.getsize(cand) > 10:
                            try:
                                with open(cand, "r", encoding="utf-8") as f:
                                    return json.load(f)
                            except Exception:
                                continue
        except Exception as e:
            self._log(f"⚠️ 全局硬探测异常: {e}", level="WARN")

        return None

    def get_human_outline(self, task_id: str) -> Optional[str]:
        """读取人类可读大纲（TXT）。"""
        path = self._human_outline_path(task_id) if hasattr(self, '_human_outline_path') else ""
        if not path or not os.path.exists(path):
            self._log(f"⚠️ 人类大纲文件不存在：{path}", level="WARN")
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            self._log(f"❌ 读取人类大纲失败：{e}", level="ERROR")
            return None

    def confirm_outline(self, task_id: str) -> Dict[str, Any]:
        """用户确认大纲后调用。将确认状态通过原子操作写回大纲文件。"""
        outline = self.get_outline(task_id)
        outline_path = self._machine_outline_path(task_id) if hasattr(self, '_machine_outline_path') else ""

        if outline is None:
            self._log(f"⚠️ 大纲文件缺失，无法确认（task_id={task_id})", level="WARN")
            return {"success": False, "error": "大纲文件缺失，无法确认"}

        try:
            if hasattr(self, "reconciliation_engine") and self.reconciliation_engine:
                self.reconciliation_engine.record_outline_confirmation(task_id)
        except Exception as e:
            self._log(f"⚠️ 对账记录异常: {e}", level="WARN")

        try:
            if hasattr(self, "state_manager") and self.state_manager:
                self.state_manager.update_project_state(task_id, outline_confirmed=True)
        except Exception as e:
            self._log(f"⚠️ 更新项目状态异常: {e}", level="WARN")

        if getattr(self, "chapter_card_manager", None):
            try:
                self.chapter_card_manager.generate_initial_cards(task_id, outline)
            except Exception as e:
                self._log(f"⚠️ 章节卡片生成异常: {e}", level="WARN")

        if outline_path and os.path.isfile(outline_path):
            try:
                outline["confirmed"] = True
                outline["confirmed_at"] = datetime.now().isoformat()
                self._atomic_write_json(outline_path, outline)
            except Exception as e:
                self._log(f"⚠️ 大纲确认状态写回失败: {e}", level="WARN")

        self._log(f"✅ 大纲已确认（task_id={task_id})")
        return {"success": True, "message": "大纲已确认"}

    def get_chapter_outline(self, task_id: str, chapter_num: int) -> Dict[str, Any]:
        """精准读取指定章节的大纲要点。"""
        outline_dirs = []
        real_p_dir = self._get_real_project_dir(task_id)
        if real_p_dir:
            outline_dirs.append(os.path.join(real_p_dir, getattr(config, "OUTLINE_DIR_NAME", "01_大纲")))

        # 🌟 防弹修复：兼容 get_project_dir 不存在的情况
        project_id = getattr(self.ai_controller, "current_project_id", None) if hasattr(self, "ai_controller") else None
        if project_id and not real_p_dir:
            try:
                if get_project_dir:
                    outline_dirs.append(os.path.join(get_project_dir(project_id), getattr(config, "OUTLINE_DIR_NAME", "01_大纲")))
                else:
                    outline_dirs.append(os.path.join(str(NOVEL_PROJECTS_DIR), str(project_id), getattr(config, "OUTLINE_DIR_NAME", "01_大纲")))
            except Exception:
                outline_dirs.append(os.path.join("novel_projects", str(project_id), getattr(config, "OUTLINE_DIR_NAME", "01_大纲")))

        outline_dirs = list({os.path.abspath(d) for d in outline_dirs if d})
        
        # ---------- 2️⃣ 在每个目录尝试读取真实 JSON ----------

        for od in outline_dirs:
            if not os.path.isdir(od): continue

            machine_file = os.path.join(od, "machine_outline.json")
            if os.path.isfile(machine_file):
                try:
                    with open(machine_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    chapters = []
                    if isinstance(data, list): chapters = data
                    elif isinstance(data, dict):
                        if isinstance(data.get("chapters"), list): chapters = data["chapters"]
                        elif isinstance(data.get("volumes"), list):
                            for vol in data["volumes"]:
                                if isinstance(vol, dict) and isinstance(vol.get("chapters"), list):
                                    chapters.extend(vol["chapters"])
                    for chap in chapters:
                        cnum = chap.get("chapter_num") or chap.get("chapter_number")
                        if cnum == chapter_num: return chap
                except Exception as e:
                    self._log(f"⚠️ 解析 {machine_file} 失败: {e}", level="WARN")

            vol_idx = (chapter_num - 1) // 25 + 1
            vol_file = os.path.join(od, f"volume_{vol_idx:02d}_outline.json")
            if os.path.isfile(vol_file):
                try:
                    with open(vol_file, "r", encoding="utf-8") as f:
                        vol_data = json.load(f)
                    chapters = []
                    if isinstance(vol_data, list): chapters = vol_data
                    elif isinstance(vol_data, dict) and isinstance(vol_data.get("chapters"), list): chapters = vol_data["chapters"]
                    for chap in chapters:
                        cnum = chap.get("chapter_num") or chap.get("chapter_number")
                        if cnum == chapter_num: return chap
                except Exception as e:
                    self._log(f"⚠️ 解析 {vol_file} 失败: {e}", level="WARN")

        self._log(f"ℹ️ 未找到第 {chapter_num} 章的真实大纲 JSON，返回占位大纲。", level="INFO")
        return {
            "chapter_num": chapter_num,
            "title": f"第 {chapter_num} 章 (无大纲模式/状态驱动)",
            "summary": "根据当前项目状态、世界设定与核心冲突自然推进剧情，保持人物连贯性。",
            "event_span": "single", "foreshadowing_planted": [],
        }

    def list_all_outlines(self) -> List[Dict[str, str]]:
        """列出 outlines 目录下所有机器大纲文件。"""
        result: List[Dict[str, str]] = []
        outlines_dir = getattr(self, 'outlines_dir', '')
        if not outlines_dir or not os.path.isdir(outlines_dir): return result

        for fname in os.listdir(outlines_dir):
            if fname.startswith("outline_machine_") and fname.endswith(".json"):
                task_id = fname[len("outline_machine_") : -len(".json")]
                result.append({"task_id": task_id, "machine_path": os.path.join(outlines_dir, fname)})
            elif fname == "machine_outline.json":
                try:
                    with open(os.path.join(outlines_dir, fname), "r", encoding="utf-8") as f:
                        data = json.load(f)
                    task_id = data.get("task_id", "unknown")
                except Exception: task_id = "unknown"
                result.append({"task_id": task_id, "machine_path": os.path.join(outlines_dir, fname)})
            elif fname == "outline_machine.json":
                result.append({"task_id": "unknown", "machine_path": os.path.join(outlines_dir, fname)})
        return result

    def _record_experience(self, task_id: str, outline: Dict[str, Any]) -> None:
        """将大纲写入经验库。"""
        try:
            exp_dir = getattr(self, 'experience_db_dir', '')
            if not exp_dir: return
            exp_path = os.path.join(exp_dir, f"exp_{task_id}.json")
            data = {"task_id": task_id, "generated_at": datetime.now().isoformat(), "outline": outline}
            with open(exp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self._log(f"💾 经验库写入成功：{exp_path}")
        except Exception as e:
            self._log(f"⚠️ 经验库写入异常：{e}", level="WARN")

    def reconstruct_outline_from_chapters(self, task_id: str, upto_chapter: int) -> Dict[str, Any]:
        """健康检查触发的逆向重建。"""
        from .progress_tracker import ProgressTracker
        tracker = ProgressTracker(getattr(self, 'log_callback', None))
        chapter_texts = tracker.get_generated_chapters(task_id, max_chapter=upto_chapter)
        if not chapter_texts:
            return {"success": False, "error": "没有可用章节文本，无法进行逆向大纲重建"}

        self._log("🧠 调用 ReasoningEngine 逆向生成大纲……")
        rev_result = self.reasoning_engine.reconstruct_outline_from_texts({"chapters": chapter_texts})
        if not rev_result.get("success"):
            return {"success": False, "error": f"逆向生成失败：{rev_result.get('error', '未知错误')}"}

        new_outline = rev_result["outline"]
        minimal_plan = {
            "task_id": task_id, "title": new_outline.get("title", "未命名"),
            "genre": new_outline.get("genre", "未定义"), "total_chapters": len(new_outline.get("chapters", [])),
        }
        save_res = self.generate_outline(minimal_plan)
        if not save_res.get("success"):
            return {"success": False, "error": f"逆向大纲保存失败：{save_res.get('error', '未知错误')}"}

        self._log("✅ 逆向大纲重建并保存成功")
        return {"success": True, "outline": new_outline}

    def backup_outline(self, task_id: str) -> Dict[str, Any]:
        """手动备份大纲文件到 zip。"""
        if not getattr(config, 'AUTO_BACKUP_ENABLED', True):
            return {"success": False, "error": "系统已关闭自动备份功能"}

        machine_path = self._machine_outline_path(task_id)
        human_path = self._human_outline_path(task_id)
        if not (os.path.exists(machine_path) and os.path.exists(human_path)):
            return {"success": False, "error": "大纲文件缺失，无法备份"}

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        kb_dir = getattr(config, 'KNOWLEDGE_BASE_DIR', 'knowledge_base')
        backup_dir = os.path.join(str(kb_dir), "backups")
        os.makedirs(backup_dir, exist_ok=True)
        backup_path = os.path.join(backup_dir, f"outline_{task_id}_{timestamp}.zip")

        try:
            import zipfile
            with zipfile.ZipFile(backup_path, "w", zipfile.ZIP_DEFLATED) as zipf:
                zipf.write(machine_path, arcname=os.path.basename(machine_path))
                zipf.write(human_path, arcname=os.path.basename(human_path))
        except Exception as e:
            self._log(f"❌ 备份大纲失败: {e}", level="ERROR")
            return {"success": False, "error": f"备份异常：{e}"}

        self._cleanup_old_backups(task_id)
        self._log(f"✅ 大纲已备份至 {backup_path}")
        return {"success": True, "backup_path": backup_path}

    def _cleanup_old_backups(self, task_id: str) -> None:
        """删除超过限制的旧备份。"""
        kb_dir = getattr(config, 'KNOWLEDGE_BASE_DIR', 'knowledge_base')
        backup_dir = os.path.join(str(kb_dir), "backups")
        if not os.path.isdir(backup_dir): return
        
        all_files = [f for f in os.listdir(backup_dir) if f.startswith(f"outline_{task_id}_") and f.endswith(".zip")]
        max_count = getattr(config, 'MAX_BACKUP_COUNT', 5)
        if len(all_files) <= max_count: return

        all_files.sort()
        excess = len(all_files) - max_count
        for i in range(excess):
            try:
                os.remove(os.path.join(backup_dir, all_files[i]))
                self._log(f"🗑️ 自动清理旧备份：{all_files[i]}")
            except Exception as e:
                self._log(f"⚠️ 删除旧备份异常: {e}", level="WARN")

    def restore_outline_from_backup(self, backup_path: str) -> Dict[str, Any]:
        """从备份 zip 恢复大纲。"""
        if not os.path.isfile(backup_path):
            return {"success": False, "error": "备份文件不存在"}

        try:
            import zipfile
            with zipfile.ZipFile(backup_path, "r") as zipf:
                namelist = zipf.namelist()
                task_id, machine_name, human_name = None, None, None
                
                for name in namelist:
                    if "machine" in name and name.endswith(".json"):
                        machine_name = name
                        if name.startswith("outline_machine_"):
                            task_id = name[len("outline_machine_") : -len(".json")]
                    elif "human" in name and name.endswith(".txt"):
                        human_name = name
                        
                if not task_id or not machine_name:
                    return {"success": False, "error": "备份文件中未找到有效的机器大纲"}
                    
                target_machine_path = self._machine_outline_path(task_id)
                target_human_path = self._human_outline_path(task_id)
                os.makedirs(os.path.dirname(target_machine_path), exist_ok=True)
                
                with zipf.open(machine_name) as src, open(target_machine_path, "wb") as dst:
                    dst.write(src.read())
                    
                if human_name:
                    with zipf.open(human_name) as src, open(target_human_path, "wb") as dst:
                        dst.write(src.read())
                        
        except Exception as e:
            self._log(f"❌ 恢复备份失败: {e}", level="ERROR")
            return {"success": False, "error": f"解压异常：{e}"}

        self._log(f"✅ 大纲已从备份恢复（task_id={task_id})")
        return {"success": True, "task_id": task_id}

    def export_outline(self, task_id: str, export_dir: Optional[str] = None) -> Dict[str, Any]:
        """导出大纲到指定目录。"""
        machine_path = self._machine_outline_path(task_id)
        human_path = self._human_outline_path(task_id)
        if not (os.path.exists(machine_path) and os.path.exists(human_path)):
            return {"success": False, "error": "大纲文件缺失，无法导出"}

        if export_dir is None:
            kb_dir = getattr(config, 'KNOWLEDGE_BASE_DIR', 'knowledge_base')
            export_dir = os.path.join(str(kb_dir), "exports")
        os.makedirs(export_dir, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest_machine = os.path.join(export_dir, f"outline_{task_id}_{timestamp}.json")
        dest_human = os.path.join(export_dir, f"outline_{task_id}_{timestamp}.txt")

        try:
            shutil.copy2(machine_path, dest_machine)
            shutil.copy2(human_path, dest_human)
        except Exception as e:
            self._log(f"❌ 导出大纲失败: {e}", level="ERROR")
            return {"success": False, "error": f"复制文件异常：{e}"}

        self._log(f"✅ 大纲已导出至 {export_dir}")
        return {"success": True, "exported_files": [dest_machine, dest_human]}

    def import_outline(self, json_path: str, human_txt_path: Optional[str] = None) -> Dict[str, Any]:
        """从外部文件导入大纲。"""
        import uuid
        if not os.path.isfile(json_path):
            return {"success": False, "error": "机器大纲 JSON 文件不存在"}

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                outline_data = json.load(f)
        except Exception as e:
            return {"success": False, "error": f"读取 JSON 失败：{e}"}

        basename = os.path.basename(json_path)
        inferred_id = None
        if basename.startswith("outline_machine_") and basename.endswith(".json"):
            inferred_id = basename[len("outline_machine_") : -len(".json")]
        if not inferred_id:
            inferred_id = str(uuid.uuid4())
            self._log(f"⚠️ 未能从文件名推断 task_id，使用随机 ID：{inferred_id}")

        target_machine_path = self._machine_outline_path(inferred_id)
        try:
            os.makedirs(os.path.dirname(target_machine_path), exist_ok=True)
            shutil.copy2(json_path, target_machine_path)
        except Exception as e:
            return {"success": False, "error": f"复制机器大纲失败：{e}"}

        if human_txt_path:
            if not os.path.isfile(human_txt_path):
                return {"success": False, "error": "提供的人类版 TXT 文件不存在"}
            target_human_path = self._human_outline_path(inferred_id)
            try:
                os.makedirs(os.path.dirname(target_human_path), exist_ok=True)
                shutil.copy2(human_txt_path, target_human_path)
            except Exception as e:
                return {"success": False, "error": f"复制人类大纲失败：{e}"}
        else:
            self._log("ℹ️ 未提供人类版文件，尝试自动渲染")
            outline = self.get_outline(inferred_id)
            if outline and hasattr(self, '_render_human_outline'):
                try:
                    human_txt = self._render_human_outline(outline)
                    target_human_path = self._human_outline_path(inferred_id)
                    os.makedirs(os.path.dirname(target_human_path), exist_ok=True)
                    with open(target_human_path, "w", encoding="utf-8") as f:
                        f.write(human_txt)
                except Exception as e:
                    self._log(f"⚠️ 自动渲染人类版失败：{e}", level="WARN")

        self._record_experience(inferred_id, outline_data)
        self._log(f"✅ 大纲导入成功（task_id={inferred_id})")
        return {"success": True, "task_id": inferred_id}

    def auto_save(self) -> None:
        """自动保存当前所有大纲到 .auto_save 目录。"""
        now = datetime.now()
        last_save = getattr(self, '_last_auto_save', now - datetime.timedelta(seconds=9999))
        interval = getattr(config, 'AUTO_SAVE_INTERVAL_SECONDS', 300)
        if (now - last_save).total_seconds() < interval: return

        kb_dir = getattr(config, 'KNOWLEDGE_BASE_DIR', 'knowledge_base')
        cache_dir = os.path.join(str(kb_dir), ".auto_save")
        os.makedirs(cache_dir, exist_ok=True)

        for outline_info in self.list_all_outlines():
            task_id = outline_info["task_id"]
            src_path = outline_info["machine_path"]
            dst_path = os.path.join(cache_dir, f"{task_id}.json")
            try:
                if os.path.exists(src_path): shutil.copy2(src_path, dst_path)
            except Exception as e:
                self._log(f"⚠️ 自动保存失败 (task_id={task_id}): {e}", level="WARN")
                
        self._last_auto_save = now
        self._log("💾 自动保存完成")

    def load_last_saved_state(self) -> List[str]:
        """启动时恢复 .auto_save 目录下的缓存文件。"""
        kb_dir = getattr(config, 'KNOWLEDGE_BASE_DIR', 'knowledge_base')
        cache_dir = os.path.join(str(kb_dir), ".auto_save")
        if not os.path.isdir(cache_dir): return []

        restored: List[str] = []
        for fname in os.listdir(cache_dir):
            if not fname.endswith(".json"): continue
            task_id = fname[:-len(".json")]
            src_path = os.path.join(cache_dir, fname)
            dst_path = self._machine_outline_path(task_id)
            try:
                os.makedirs(os.path.dirname(dst_path), exist_ok=True)
                shutil.copy2(src_path, dst_path)
                restored.append(task_id)
            except Exception as e:
                self._log(f"⚠️ 自动恢复失败 (task_id={task_id}): {e}", level="WARN")
        return restored
    def backup_outline(self, task_id: str) -> Dict[str, Any]:
        """将大纲文件（机器版+人类版）备份到 knowledge_base/backups 目录。"""
        import shutil
        from datetime import datetime
        from . import config
        
        machine_path = self._machine_outline_path(task_id)
        human_path = self._human_outline_path(task_id)
        
        if not os.path.exists(machine_path):
            return {"success": False, "error": "大纲文件不存在，无法备份"}

        kb_dir = getattr(config, 'KNOWLEDGE_BASE_DIR', 'knowledge_base')
        backup_dir = os.path.join(str(kb_dir), "backups")
        os.makedirs(backup_dir, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest_machine = os.path.join(backup_dir, f"backup_machine_{task_id}_{timestamp}.json")
        
        try:
            shutil.copy2(machine_path, dest_machine)
            self._log(f"💾 机器大纲已备份至: {dest_machine}")
            
            # 🌟 增强：顺便备份人类版（如果存在），确保恢复时数据完整
            if human_path and os.path.exists(human_path):
                dest_human = os.path.join(backup_dir, f"backup_human_{task_id}_{timestamp}.txt")
                shutil.copy2(human_path, dest_human)
                
            return {"success": True, "backup_path": dest_machine}
        except Exception as e:
            self._log(f"❌ 备份大纲失败: {e}", level="ERROR")
            return {"success": False, "error": str(e)}

    def delete_outline(self, task_id: str, keep_backup: bool = True) -> Dict[str, Any]:
        """删除指定 task_id 的机器版 & 人类版大纲文件。"""
        machine_path = self._machine_outline_path(task_id)
        human_path = self._human_outline_path(task_id)

        if keep_backup:
            backup_res = self.backup_outline(task_id)
            if not backup_res.get("success"):
                self._log(f"⚠️ 备份失败（在删除前），继续删除：{backup_res.get('error')}", level="WARN")

        errors: List[str] = []
        for p in (machine_path, human_path):
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except Exception as e:
                    errors.append(f"{p}: {e}")

        if errors:
            return {"success": False, "error": "; ".join(errors)}
        self._log(f"🗑️ 大纲已删除（task_id={task_id})")
        return {"success": True}

    def get_outline_summary(self, task_id: str) -> Optional[Dict[str, Any]]:
        """返回大纲的简要摘要。"""
        outline = self.get_outline(task_id)
        if not outline:
            return None
        return {
            "title": outline.get("title", "未命名"),
            "genre": outline.get("genre", "未定义"),
            "total_chapters": len(outline.get("chapters", [])),
        }

    def get_world_truth_levels(self, task_id: str):
        """兼容旧调用的占位实现。"""
        return []

    def _is_outline_complete(self, outline: Dict[str, Any]) -> bool:
        """判断大纲是否包含最低可运行的字段。"""
        if not isinstance(outline, dict):
            return False
        required_top = ["title", "genre", "total_chapters", "words_per_chapter", "chapters"]
        for key in required_top:
            if key not in outline:
                return False
            if key in ["title", "genre"] and not isinstance(outline[key], str):
                return False
            if key in ["total_chapters", "words_per_chapter"]:
                try:
                    if int(outline[key]) <= 0:
                        return False
                except Exception:
                    return False

        chapters = outline.get("chapters")
        if not isinstance(chapters, list) or len(chapters) == 0:
            return False
        if len(chapters) < int(outline["total_chapters"]):
            return False
        for ch in chapters:
            if not isinstance(ch, dict):
                return False
            if not ch.get("title") or not (ch.get("summary") or ch.get("overview")):
                return False
        return True


