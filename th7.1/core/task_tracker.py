import os
import json
import time
from datetime import datetime
from typing import Dict, Any, List, Optional

from .config import TASK_TRACKING_DIR, KNOWLEDGE_BASE_DIR, NOVEL_PROJECTS_DIR, LEARNING_PLANS_DIR

class TaskTracker:
    """任务追踪系统（磁盘缓存优化版）：管理活跃任务、已完成任务、世界观和学习计划。"""

    def __init__(self, log_callback=None):
        self.log_callback = log_callback
        self.tasks_file = os.path.join(TASK_TRACKING_DIR, "active_tasks.json")
        self.completed_file = os.path.join(TASK_TRACKING_DIR, "completed_tasks.json")
        self.worldview_file = os.path.join(TASK_TRACKING_DIR, "worldviews.json")
        self.learning_plans_file = os.path.join(LEARNING_PLANS_DIR, "learning_plans.json")
        self.current_task = None
        self.disk_cache = None  # 由 ai_controller 注入
        
        self._init_files()

    def _log(self, msg: str, level: str = "INFO"):
        if self.log_callback:
            try: self.log_callback(msg, level=level)
            except TypeError: self.log_callback(msg)

    def _safe_read_json(self, filepath: str, default: dict) -> dict:
        """🌟 安全读取 JSON，自动处理空文件和损坏文件。"""
        if not os.path.exists(filepath):
            return default.copy()
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                file_content = f.read().strip()
                if not file_content:
                    self._log(f"🔧 发现空文件，已重置：{filepath}", level="WARN")
                    return default.copy()
                return json.loads(file_content)
        except json.JSONDecodeError as e:
            self._log(f"🔧 修复损坏的 JSON 文件：{filepath} - {e}", level="WARN")
            return default.copy()
        except Exception as e:
            self._log(f"⚠️ 读取文件异常 {filepath}: {e}", level="ERROR")
            return default.copy()

    def _atomic_write_json(self, filepath: str, data: dict) -> None:
        """🌟 工业级原子写入 JSON，防止断电数据丢失。"""
        dir_name = os.path.dirname(filepath)
        if dir_name: os.makedirs(dir_name, exist_ok=True)
        
        tmp_path = filepath + ".tmp"
        try:
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, filepath)
        except Exception as e:
            if os.path.exists(tmp_path):
                try: os.remove(tmp_path)
                except Exception: pass
            self._log(f"❌ 原子写入失败 ({filepath}): {e}", level="ERROR")

    def _init_files(self):
        """初始化所有必需的 JSON 文件。"""
        os.makedirs(TASK_TRACKING_DIR, exist_ok=True)
        os.makedirs(NOVEL_PROJECTS_DIR, exist_ok=True)
        os.makedirs(LEARNING_PLANS_DIR, exist_ok=True)
        
        files_defaults = [
            (self.tasks_file, {"active_tasks": []}),
            (self.completed_file, {"completed_tasks": []}),
            (self.worldview_file, {"worldviews": []}),
            (self.learning_plans_file, {"learning_plans": []})
        ]
        
        for f_path, default in files_defaults:
            data = self._safe_read_json(f_path, default)
            if not os.path.exists(f_path) or os.path.getsize(f_path) == 0:
                self._atomic_write_json(f_path, data)

    # ==================== 任务生命周期管理 ====================

    def start_task(self, task_name, task_type="novel", total_chapters=1, worldview_id=None):
        task = {
            "id": f"task_{int(time.time())}", "name": task_name, "type": task_type,
            "status": "in_progress", "total_chapters": total_chapters, "current_chapter": 1,
            "created_at": datetime.now().isoformat(), "updated_at": datetime.now().isoformat(),
            "worldview_id": worldview_id, "chapters": [], "worldview_summary": "",
            "characters": [], "settings": {}, "notes": "", "inspiration_log": []
        }
        
        data = self._safe_read_json(self.tasks_file, {"active_tasks": []})
        data["active_tasks"].append(task)
        self._atomic_write_json(self.tasks_file, data)
        
        self.current_task = task
        self._log(f"📝 开始任务：{task_name}（共{total_chapters}章）")
        return task

    def update_chapter(self, chapter_num, content, save_path=None):
        if not self.current_task:
            return {"success": False, "message": "没有活跃任务"}
        
        # 🌟 核心修复：原代码中 content 变量被文件读取的 content 覆盖，导致写入错误和字数计算错误
        if not content or len(content.strip()) == 0:
            return {"success": False, "message": "章节内容为空"}
        
        if save_path:
            os.makedirs(save_path, exist_ok=True)
            chapter_file = os.path.join(save_path, f"chapter_{chapter_num:03d}.txt")
            tmp_chapter = chapter_file + ".tmp"
            try:
                with open(tmp_chapter, 'w', encoding='utf-8') as f:
                    f.write(content)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_chapter, chapter_file)
            except Exception as e:
                self._log(f"❌ 保存章节文件失败: {e}", level="ERROR")
        
        chapter_record = {
            "chapter": chapter_num, "content_length": len(content),
            "saved_at": save_path, "completed_at": datetime.now().isoformat()
        }
        
        data = self._safe_read_json(self.tasks_file, {"active_tasks": []})
        for task in data["active_tasks"]:
            if task["id"] == self.current_task["id"]:
                existing = False
                for ch in task["chapters"]:
                    if ch["chapter"] == chapter_num:
                        ch.update(chapter_record)
                        existing = True
                        break
                if not existing:
                    task["chapters"].append(chapter_record)
                task["current_chapter"] = max(task["current_chapter"], chapter_num + 1)
                task["updated_at"] = datetime.now().isoformat()
                self.current_task = task
                break
        
        self._atomic_write_json(self.tasks_file, data)
        self._log(f"✅ 第{chapter_num}章已保存（{len(content)}字）")
        return {"success": True, "chapter": chapter_num, "length": len(content)}

    def add_inspiration(self, inspiration_text, category="worldview"):
        if not self.current_task: return False
        inspiration = {
            "id": f"insp_{int(time.time())}", "content": inspiration_text,
            "category": category, "timestamp": datetime.now().isoformat()
        }
        
        data = self._safe_read_json(self.tasks_file, {"active_tasks": []})
        for task in data["active_tasks"]:
            if task["id"] == self.current_task["id"]:
                task["inspiration_log"].append(inspiration)
                task["updated_at"] = datetime.now().isoformat()
                self.current_task = task
                break
        self._atomic_write_json(self.tasks_file, data)
        return True

    def update_worldview(self, worldview_summary, characters=None, settings=None):
        if not self.current_task: return False
        
        data = self._safe_read_json(self.tasks_file, {"active_tasks": []})
        for task in data["active_tasks"]:
            if task["id"] == self.current_task["id"]:
                task["worldview_summary"] = worldview_summary
                if characters: task["characters"] = characters
                if settings: task["settings"] = settings
                task["updated_at"] = datetime.now().isoformat()
                self.current_task = task
                break
        self._atomic_write_json(self.tasks_file, data)
        self._save_worldview()
        return True

    def _save_worldview(self):
        if not self.current_task: return
        worldview_id = self.current_task.get("worldview_id", f"world_{int(time.time())}")
        
        data = self._safe_read_json(self.worldview_file, {"worldviews": []})
        existing = False
        for wv in data["worldviews"]:
            if wv["id"] == worldview_id:
                wv.update({
                    "summary": self.current_task.get("worldview_summary", ""),
                    "characters": self.current_task.get("characters", []),
                    "settings": self.current_task.get("settings", {}),
                    "updated_at": datetime.now().isoformat(),
                    "related_works": list(set(wv.get("related_works", []) + [self.current_task["name"]]))
                })
                existing = True
                break
        if not existing:
            data["worldviews"].append({
                "id": worldview_id, "name": self.current_task["name"],
                "summary": self.current_task.get("worldview_summary", ""),
                "characters": self.current_task.get("characters", []),
                "settings": self.current_task.get("settings", {}),
                "created_at": datetime.now().isoformat(),
                "related_works": [self.current_task["name"]]
            })
        self._atomic_write_json(self.worldview_file, data)

    def get_worldview(self, worldview_id):
        data = self._safe_read_json(self.worldview_file, {"worldviews": []})
        for wv in data["worldviews"]:
            if wv["id"] == worldview_id: return wv
        return None

    def list_worldviews(self):
        return self._safe_read_json(self.worldview_file, {"worldviews": []})["worldviews"]

    def get_task_context(self):
        if not self.current_task: return "\n【任务状态】无活跃任务"
        completed = len([c for c in self.current_task["chapters"] if c.get("completed_at")])
        total = self.current_task["total_chapters"]
        recent_inspirations = self.current_task.get("inspiration_log", [])[-5:]
        inspiration_text = "\n【最近灵感交流】\n" + "\n".join([f"- {i['content'][:100]}..." for i in recent_inspirations]) if recent_inspirations else ""
        return f"""
【当前任务】{self.current_task["name"]}
【进度】已完成 {completed}/{total} 章
【当前章节】第 {self.current_task["current_chapter"]} 章
【任务类型】{self.current_task["type"]}
【世界观】{self.current_task.get("worldview_summary", "未设定")[:200]}
{inspiration_text}
【注意事项】保持角色一致性、情节连贯性、文风统一
"""

    def complete_task(self):
        if not self.current_task: return {"success": False, "message": "没有活跃任务"}
        
        data = self._safe_read_json(self.tasks_file, {"active_tasks": []})
        completed_data = self._safe_read_json(self.completed_file, {"completed_tasks": []})
        
        for task in data["active_tasks"]:
            if task["id"] == self.current_task["id"]:
                task["status"] = "completed"
                task["completed_at"] = datetime.now().isoformat()
                completed_data["completed_tasks"].append(task)
                data["active_tasks"].remove(task)
                break
                
        self._atomic_write_json(self.tasks_file, data)
        self._atomic_write_json(self.completed_file, completed_data)
        self._log(f"🎉 任务完成：{self.current_task['name']}")
        self.current_task = None

    def get_active_tasks(self):
        return self._safe_read_json(self.tasks_file, {"active_tasks": []})["active_tasks"]

    def switch_task(self, task_id):
        data = self._safe_read_json(self.tasks_file, {"active_tasks": []})
        for task in data["active_tasks"]:
            if task["id"] == task_id:
                self.current_task = task
                self._log(f"🔄 切换到任务：{task['name']}")
                return True
        return False

    def create_novel_project(self, title, worldview_id=None):
        project_dir = os.path.join(NOVEL_PROJECTS_DIR, f"{title}_{datetime.now().strftime('%Y%m%d')}")
        os.makedirs(os.path.join(project_dir, "chapters"), exist_ok=True)
        os.makedirs(os.path.join(project_dir, "notes"), exist_ok=True)
        os.makedirs(os.path.join(project_dir, "characters"), exist_ok=True)
        
        metadata = {
            "title": title, "worldview_id": worldview_id,
            "created_at": datetime.now().isoformat(), "project_path": project_dir
        }
        self._atomic_write_json(os.path.join(project_dir, "metadata.json"), metadata)
        return project_dir

    def save_learning_plan(self, plan):
        data = self._safe_read_json(self.learning_plans_file, {"learning_plans": []})
        data["learning_plans"].append(plan)
        data["learning_plans"] = data["learning_plans"][-50:]  # 保留最近 50 条
        self._atomic_write_json(self.learning_plans_file, data)

    def get_learning_history(self):
        return self._safe_read_json(self.learning_plans_file, {"learning_plans": []})["learning_plans"]

    def save_chapter_summary(self, chapter_num, summary, key_events, characters_involved):
        """保存章节摘要和关键信息。"""
        if not self.current_task: return {"success": False, "message": "没有活跃任务"}
        
        summary_record = {
            "chapter": chapter_num, "summary": summary[:500],
            "key_events": key_events[:5], "characters_involved": characters_involved[:10],
            "saved_at": datetime.now().isoformat()
        }
        
        data = self._safe_read_json(self.tasks_file, {"active_tasks": []})
        for task in data["active_tasks"]:
            if task["id"] == self.current_task["id"]:
                if "chapter_summaries" not in task: task["chapter_summaries"] = []
                
                existing = False
                for s in task["chapter_summaries"]:
                    if s["chapter"] == chapter_num:
                        s.update(summary_record)
                        existing = True
                        break
                if not existing:
                    task["chapter_summaries"].append(summary_record)
                
                task["updated_at"] = datetime.now().isoformat()
                self.current_task = task
                break
                
        self._atomic_write_json(self.tasks_file, data)
        self._log(f"📝 已保存第{chapter_num}章摘要记忆")
        return {"success": True}

    def get_previous_chapters_summary(self, current_chapter, max_chapters=3):
        """获取前 N 章的摘要记忆。"""
        if not self.current_task: return []
        summaries = [s for s in self.current_task.get("chapter_summaries", []) if s["chapter"] < current_chapter]
        summaries = sorted(summaries, key=lambda x: x["chapter"], reverse=True)[:max_chapters]
        return sorted(summaries, key=lambda x: x["chapter"])

    def get_chapter_context_string(self, current_chapter):
        """生成章节上下文字符串。"""
        previous_summaries = self.get_previous_chapters_summary(current_chapter)
        if not previous_summaries: return "（无前情提要，这是第一章）"
        
        context = "\n【前情提要】\n"
        for s in previous_summaries:
            context += f"\n第{s['chapter']}章概要：\n  - 主要情节：{s['summary']}\n"
            if s.get('key_events'): context += f"  - 关键事件：{', '.join(s['key_events'])}\n"
            if s.get('characters_involved'): context += f"  - 登场角色：{', '.join(s['characters_involved'])}\n"
        
        context += "\n【创作要求】\n  - 保持与上述情节的连贯性\n  - 角色性格和行为保持一致\n  - 可适当呼应前文伏笔\n"
        return context
