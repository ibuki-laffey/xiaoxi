import os
import json
import time
import hashlib
import re
import traceback
from datetime import datetime
from typing import Dict, Any, List, Optional

from .config import (
    KNOWLEDGE_BASE_DIR, TASK_TRACKING_DIR, OLLAMA_URL, MODEL_NAME,
    SAFE_TIMEOUT, TOPIC_STORAGE_DIR,
)

class TaskPlanner:
    """任务规划器：解析用户需求，生成任务计划，增强状态管理。"""

    def __init__(self, log_callback: Optional[Any] = None, model_client: Optional[Any] = None):
        self.log_callback = log_callback
        self.model_client = model_client

        self.plans_dir = os.path.join(TASK_TRACKING_DIR, "plans")
        os.makedirs(self.plans_dir, exist_ok=True)

        self.status_enum = {
            "planned": "计划中", "outline_generated": "大纲已生成",
            "outline_confirmed": "大纲已确认", "in_progress": "进行中",
            "paused": "已暂停", "completed": "已完成", "failed": "失败",
        }

        cleaned = self.cleanup_invalid_plans()
        if cleaned: self._log(f"🧹 启动清理完成，移除 {cleaned} 份无效任务计划")

    def _log(self, msg: str, level: str = "INFO") -> None:
        if self.log_callback:
            timestamp = datetime.now().strftime("%H:%M:%S")
            formatted_msg = f"[{timestamp}] [TaskPlanner] [{level}] {msg}"
            try: self.log_callback(formatted_msg, level=level)
            except TypeError: self.log_callback(formatted_msg)

    def _robust_extract_json(self, raw: Any) -> Dict[str, Any]:
        """🌟 工业级 JSON 提取器：剥离 Markdown、首尾括号定位、清理尾部逗号。"""
        if isinstance(raw, dict): return raw
        if not isinstance(raw, str): return {}
        
        text = raw.strip()
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
        text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE).strip()
        
        start_idx = text.find('{')
        end_idx = text.rfind('}')
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            json_str = text[start_idx:end_idx + 1]
            json_str = re.sub(r",\s*([}\]])", r"\1", json_str)
            try: return json.loads(json_str)
            except json.JSONDecodeError: pass
        try: return json.loads(text)
        except json.JSONDecodeError: return {}

    def parse_user_request(self, user_prompt: str) -> Dict[str, Any]:
        """解析用户自然语言指令，生成结构化任务计划。"""
        self._log("🔍 开始解析用户需求...")
        parse_prompt = f"""你是一个任务规划助手，请分析用户的小说创作需求。
用户指令：{user_prompt}

请提取以下信息（严格 JSON，字段请全部返回，绝不输出任何废话、解释或 Markdown 标记）：
{{"task_type": "novel/article/code", "genre": "题材", "total_words": 0, "total_chapters": 1, "words_per_chapter": 2000, "style": "写作风格", "special_requirements": [], "prohibitions": [], "file_naming_preference": "每章一个文件", "generation_mode": "逐章/一次性/分批", "batch_size": 1, "title": "书名", "main_character": "主角名字"}}"""
        try:
            raw_resp = self._call_model(parse_prompt)
            plan_data = self._robust_extract_json(raw_resp)

            defaults = {
                "task_type": "novel", "genre": "通用", "total_words": 0, "total_chapters": 1,
                "words_per_chapter": 2000, "style": "自然流畅", "special_requirements": [],
                "prohibitions": [], "file_naming_preference": "每章一个文件，按章节号排序",
                "generation_mode": "逐章", "batch_size": 1, "title": None, "main_character": None,
            }
            for k, v in defaults.items(): plan_data.setdefault(k, v)

            if not plan_data.get("title"):
                title_match = re.search(r'《([^》]+)》|\"([^\"]+)\"', user_prompt)
                plan_data["title"] = title_match.group(1) or title_match.group(2) if title_match else "未命名"

            if not plan_data.get("main_character"):
                char_match = re.search(r'主角(?:名字)?叫?([\w]+)', user_prompt)
                plan_data["main_character"] = char_match.group(1) if char_match else "未知"

            plan_data["task_id"] = self._generate_task_id(user_prompt)
            now_iso = datetime.now().isoformat()
            plan_data["created_at"] = now_iso
            plan_data["updated_at"] = now_iso
            plan_data["status"] = "planned"
            plan_data["status_verified"] = True
            plan_data["outline_created"] = False
            plan_data["outline_confirmed"] = False

            self._save_plan(plan_data)
            self._log(f"✅ 任务计划已生成：{plan_data['task_id']}")
            return plan_data
        except Exception as exc:
            self._log(f"❌ 任务计划生成失败：{exc}\n{traceback.format_exc()}", level="ERROR")
            return self._create_fallback_plan(user_prompt)

    def get_topic_list(self) -> List[str]:
        """返回已保存的所有话题名称。"""
        try:
            if not os.path.isdir(TOPIC_STORAGE_DIR): return []
            topics = []
            for fname in os.listdir(TOPIC_STORAGE_DIR):
                if fname.startswith("topic_") and fname.endswith(".json"):
                    topics.append(fname.replace("topic_", "").replace(".json", ""))
            return topics
        except Exception as exc:
            self._log(f"⚠️ 获取话题列表时出现异常：{exc}", level="ERROR")
            return []

    def _call_model(self, prompt: str) -> Any:
        """统一模型调用入口。"""
        if self.model_client:
            if hasattr(self.model_client, "_parse_user_request"):
                try: return self.model_client._parse_user_request(prompt)
                except Exception as e: self._log(f"⚠️ model_client._parse_user_request 调用异常: {e}")
            if hasattr(self.model_client, "generate"):
                try: return self.model_client.generate(prompt)
                except Exception as e: self._log(f"⚠️ model_client.generate 调用异常: {e}")
        try: return self._simple_parse(prompt)
        except Exception as e:
            self._log(f"⚠️ fallback 正则解析异常: {e}")
            return {}

    def _simple_parse(self, prompt: str) -> Dict[str, Any]:
        """当模型不可用或返回异常时使用的轻量级正则解析。"""
        prompt_lower = prompt.lower()
        chapter_match = re.search(r'(\d+)\s*[章回]', prompt_lower)
        total_chapters = int(chapter_match.group(1)) if chapter_match else 1

        word_match = re.search(r'(\d+(?:\.\d+)?)\s*万字', prompt_lower)
        total_words = int(float(word_match.group(1)) * 10000) if word_match else 0

        genre = "通用"
        if any(w in prompt_lower for w in ['现代', '都市', '异能', '高武']): genre = "现代都市异能"
        elif any(w in prompt_lower for w in ['奇幻', '魔法', '西方']): genre = "西方奇幻"
        elif any(w in prompt_lower for w in ['科幻', '未来', '太空']): genre = "科幻"
        elif any(w in prompt_lower for w in ['武侠', '修仙', '玄幻']): genre = "玄幻武侠"

        generation_mode = "逐章"
        if "一次性" in prompt_lower: generation_mode = "一次性"
        elif "先生成" in prompt_lower and "看看" in prompt_lower: generation_mode = "分批"

        title = None
        title_match = re.search(r'《([^》]+)》|\"([^\"]+)\"', prompt)
        if title_match: title = title_match.group(1) or title_match.group(2)

        main_character = None
        char_match = re.search(r'主角(?:名字)?叫?([\w]+)', prompt)
        if char_match: main_character = char_match.group(1)

        return {
            "task_type": "novel", "genre": genre, "total_words": total_words,
            "total_chapters": total_chapters, "words_per_chapter": 2000, "style": "自然流畅",
            "special_requirements": [], "prohibitions": [], "file_naming_preference": "每章一个文件，按章节号排序",
            "generation_mode": generation_mode, "batch_size": 1,
            "title": title or "未命名", "main_character": main_character or "未知",
        }

    def _generate_task_id(self, user_prompt: str) -> str:
        timestamp = int(time.time())
        hash_input = f"{user_prompt}{timestamp}"
        return f"task_{hashlib.md5(hash_input.encode()).hexdigest()[:12]}"

    def _save_plan(self, plan_data: Dict[str, Any]):
        """将任务计划写入 JSON 文件（工业级原子写入）。"""
        if not isinstance(plan_data, dict): return
        task_id = plan_data.get('task_id')
        if not task_id or not isinstance(task_id, str): return

        os.makedirs(self.plans_dir, exist_ok=True)
        plan_file = os.path.join(self.plans_dir, f"{task_id}.json")
        tmp_file = plan_file + ".tmp"

        try:
            json_content = json.dumps(plan_data, indent=2, ensure_ascii=False)
            with open(tmp_file, "w", encoding="utf-8") as f:
                f.write(json_content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_file, plan_file)
        except Exception as e:
            if os.path.exists(tmp_file):
                try: os.remove(tmp_file)
                except Exception: pass
            self._log(f"❌ 保存任务计划失败: {e}", level="ERROR")

    def get_plan(self, task_id: str) -> Optional[Dict[str, Any]]:
        """读取指定 task_id 的计划。"""
        plan_file = os.path.join(self.plans_dir, f"{task_id}.json")
        if not os.path.exists(plan_file): return None
        try:
            with open(plan_file, "r", encoding="utf-8") as f: return json.load(f)
        except Exception as e:
            self._log(f"⚠️ 读取任务计划错误（{task_id}）: {e}", level="WARN")
            return None

    def update_plan_status(self, task_id: str, status: str, updates: Optional[Dict[str, Any]] = None, extra_fields: Optional[Dict[str, Any]] = None) -> bool:
        """更新任务计划的 status，并可一次性写入其它键值。"""
        plan_data = self.get_plan(task_id)
        if not plan_data: return False

        if status not in self.status_enum: status = "planned"
        plan_data["status"] = status
        plan_data["updated_at"] = datetime.now().isoformat()

        if updates: plan_data.update(updates)
        if extra_fields: plan_data.update(extra_fields)

        self._save_plan(plan_data)
        self._log(f"📊 任务状态已更新：{task_id} → {status}")
        return True

    def sync_total_chapters(self, task_id: str, total_chapters: int, words_per_chapter: int) -> None:
        """将 total_chapters 与 words_per_chapter 同步到任务计划并持久化。"""
        plan = self.get_plan(task_id)
        if not plan: raise RuntimeError(f"任务计划不存在：{task_id}")
        plan['total_chapters'] = total_chapters
        plan['words_per_chapter'] = words_per_chapter
        self._save_plan(plan)

    def generate_quality_rules(self, plan_data: Dict[str, Any]) -> Dict[str, Any]:
        """根据任务计划生成质量检查规则。"""
        self._log("📋 生成质量检查规则...")
        rule_prompt = f"""你是一个质量检查专家，请为以下小说创作任务生成质量检查规则（严格 JSON，绝不输出任何废话或 Markdown 标记）：
题材：{plan_data.get('genre', '通用')} | 总字数：{plan_data.get('total_words', 0)} | 总章节数：{plan_data.get('total_chapters', 1)}
每章字数：{plan_data.get('words_per_chapter', 2500)} | 写作风格：{plan_data.get('style', '自然流畅')}

返回结构：
{{"word_count": {{"min":1500,"max":2500,"tolerance":3}}, "genre_check": {{"enabled":true,"forbidden_elements":[],"required_elements":[]}}, "style_check": {{"enabled":true,"ai_cliche_words":["仿佛","似乎","无疑","值得注意的是","总而言之","综上所述"],"max_cliche_count":5}}, "consistency_check": {{"enabled":true,"check_items":["角色设定","世界观","时间线"]}}, "max_rewrite_attempts": 3}}"""

        try:
            raw = self._call_model(rule_prompt)
            rules = self._robust_extract_json(raw)

            if not isinstance(rules, dict) or "word_count" not in rules:
                rules = self._get_default_quality_rules()
            else:
                rules.setdefault("word_count", {"min": 1500, "max": 2500, "tolerance": 3})
                rules.setdefault("max_rewrite_attempts", 3)

            task_id = plan_data.get("task_id")
            if task_id:
                cur = self.get_plan(task_id)
                if cur:
                    cur["quality_rules"] = rules
                    self._save_plan(cur)
            return rules
        except Exception as e:
            self._log(f"⚠️ 质量规则生成失败，回退默认：{e}", level="WARN")
            return self._get_default_quality_rules()

    def _get_default_quality_rules(self) -> Dict[str, Any]:
        return {
            "word_count": {"min": 1500, "max": 2500, "tolerance": 3},
            "genre_check": {"enabled": True, "forbidden_elements": [], "required_elements": []},
            "style_check": {"enabled": True, "ai_cliche_words": ["仿佛", "似乎", "无疑", "值得注意的是", "总而言之", "综上所述", "可以看见", "不难发现"], "max_cliche_count": 5},
            "consistency_check": {"enabled": True, "check_items": ["角色设定", "世界观", "时间线"]},
            "max_rewrite_attempts": 3,
        }

    def list_pending_tasks(self) -> List[Dict[str, Any]]:
        """返回所有需要继续处理的任务（按更新时间倒序）。"""
        pending = []
        try:
            valid_statuses = {"planned", "outline_generated", "outline_confirmed", "in_progress", "paused"}
            file_times = []
            for filename in os.listdir(self.plans_dir):
                if not filename.endswith(".json"): continue
                plan_path = os.path.join(self.plans_dir, filename)
                file_times.append((os.path.getmtime(plan_path), plan_path))
            file_times.sort(reverse=True)
            
            for _, plan_path in file_times:
                try:
                    with open(plan_path, "r", encoding="utf-8") as f: plan_data = json.load(f)
                    if plan_data.get("status") in valid_statuses: pending.append(plan_data)
                except Exception: continue
        except Exception as e:
            self._log(f"⚠️ 列出待处理任务时出错: {e}", level="WARN")
        return pending

    def list_completed_tasks(self) -> List[Dict[str, Any]]:
        """返回所有状态为 'completed' 的任务计划列表。"""
        completed = []
        for filename in os.listdir(self.plans_dir):
            if not filename.endswith(".json"): continue
            plan_path = os.path.join(self.plans_dir, filename)
            try:
                with open(plan_path, "r", encoding="utf-8") as f: plan_data = json.load(f)
                if plan_data.get("status") == "completed": completed.append(plan_data)
            except Exception: continue
        return completed

    def get_task_status_string(self, task_id: str) -> str:
        plan = self.get_plan(task_id)
        if not plan: return "未知"
        return self.status_enum.get(plan.get("status", "unknown"), "未知")

    def list_all_tasks(self, status_filter: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """get_all_tasks 的别名：ai_controller 三处 hasattr('list_all_tasks') 反查链
        （_purify_current_task_id / _build_intent_context / _handle_outline_generation）
        依赖此方法名，缺失时 hasattr 静默为 False，task_id/project_id 反查全部失效。"""
        return self.get_all_tasks(status_filter)

    def get_all_tasks(self, status_filter: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """返回所有任务，可通过 status_filter 进行筛选。"""
        all_tasks = []
        for filename in os.listdir(self.plans_dir):
            if not filename.endswith(".json"): continue
            plan_path = os.path.join(self.plans_dir, filename)
            try:
                with open(plan_path, "r", encoding="utf-8") as f: plan_data = json.load(f)
                if status_filter is None or plan_data.get("status") in status_filter: all_tasks.append(plan_data)
            except Exception: continue
        return all_tasks

    def delete_task(self, task_id: str) -> bool:
        plan_file = os.path.join(self.plans_dir, f"{task_id}.json")
        if not os.path.exists(plan_file): return False
        try:
            os.remove(plan_file)
            self._log(f"🗑️ 任务计划已删除：{task_id}")
            return True
        except Exception as e:
            self._log(f"❌ 删除任务失败（{task_id}）: {e}", level="ERROR")
            return False

    def _create_fallback_plan(self, user_prompt: str) -> Dict[str, Any]:
        """当所有解析手段均失败时返回的最保守计划。"""
        now_iso = datetime.now().isoformat()
        fallback_task_id = self._generate_task_id(user_prompt)
        fallback = {
            "task_id": fallback_task_id, "task_type": "novel", "title": "未命名", "genre": "通用", "main_character": "未知",
            "total_words": 0, "total_chapters": 1, "words_per_chapter": 2000, "style": "自然流畅",
            "special_requirements": [], "prohibitions": [], "file_naming_preference": "每章一个文件，按章节号排序",
            "generation_mode": "逐章", "batch_size": 1, "created_at": now_iso, "updated_at": now_iso,
            "status": "planned", "status_verified": True, "outline_created": False, "outline_confirmed": False,
            "project_id": f"proj_{fallback_task_id[:8]}",
        }
        self._save_plan(fallback)
        self._log("⚠️ 使用 fallback 任务计划（全部默认值）", level="WARN")
        return fallback

    def _detect_continuation_task(self, user_prompt: str) -> Dict[str, Any]:
        continuation_keywords = ["续写", "继续写", "接着写", "后续", "第二部", "番外", "后传"]
        file_keywords = ["文件", "txt", "小说", "这本书", "这本"]
        if any(k in user_prompt for k in continuation_keywords) and any(k in user_prompt for k in file_keywords):
            extract_prompt = f"""用户想要续写已有小说，请提取以下信息（严格 JSON，绝不输出废话）：
用户指令：{user_prompt}
返回：{{"is_continuation": true, "source_file": "原作文件完整路径", "continuation_type": "续写类型", "target_words": 0, "target_chapters": 0, "keep_style": true, "chapter_numbering": "1,2,3"}}"""
            raw = self._call_model(extract_prompt)
            return self._robust_extract_json(raw)
        return {"is_continuation": False}

    def adapt_plan_for_unexpected_demand(self, task_id: str, new_demand: Dict[str, Any]) -> Dict[str, Any]:
        """当用户提出意外需求时，自动在已有计划上追加/修改对应字段。"""
        plan_data = self.get_plan(task_id)
        if not plan_data: return {"success": False, "error": "任务计划不存在"}

        plan_data.setdefault("special_requirements", [])
        if new_demand.get("requirement"): plan_data["special_requirements"].append(new_demand["requirement"])

        plan_data.setdefault("prohibitions", [])
        if new_demand.get("prohibition"): plan_data["prohibitions"].append(new_demand["prohibition"])

        plan_data["status"] = "planned"
        plan_data["updated_at"] = datetime.now().isoformat()

        plan_data.setdefault("adaptation_log", [])
        plan_data["adaptation_log"].append({"timestamp": datetime.now().isoformat(), "demand": new_demand, "changes_made": ["special_requirements", "prohibitions", "status"]})

        self._save_plan(plan_data)
        self._log(f"✅ 任务计划已适配意外需求：{task_id}")
        return {"success": True, "plan": plan_data}

    # 🌟 核心修复：兼容 fallback 关键字参数
    def ensure_project_id(self, task_id: str, fallback_project_id: str = None, **kwargs) -> bool:
        # 兼容旧代码传入的 fallback=xxx
        if fallback_project_id is None and 'fallback' in kwargs:
            fallback_project_id = kwargs['fallback']
            
        plan = self.get_plan(task_id)
        if not plan:
            self._log(f"⚠️ ensure_project_id 失败：未找到 task_id={task_id}")
            return False

        existing = plan.get("project_id")
        if existing and not existing.startswith("proj_task_"):
            return True

        if fallback_project_id:
            plan["project_id"] = fallback_project_id
            plan.setdefault("status", "planned")
            self._save_plan(plan)
            self._log(f"✅ 已为 task_id={task_id} 补全 project_id={fallback_project_id}")
            return True
        return False


    def cleanup_invalid_plans(self) -> int:
        """扫描并删除无效的计划文件（放宽 project_id 校验，防止误删刚创建的计划）。"""
        removed = 0
        for filename in os.listdir(self.plans_dir):
            if not filename.endswith(".json"): continue
            plan_path = os.path.join(self.plans_dir, filename)
            try:
                with open(plan_path, "r", encoding="utf-8") as f: plan = json.load(f)
            except Exception:
                try: os.remove(plan_path); removed += 1
                except Exception: pass
                continue

            task_id = plan.get("task_id")
            status = plan.get("status")

            # 🌟 核心修复：只删除无 task_id 或状态非法的文件，允许 project_id 为空或占位
            if (not task_id) or (status and status not in self.status_enum):
                try: os.remove(plan_path); removed += 1
                except Exception: pass
        return removed
