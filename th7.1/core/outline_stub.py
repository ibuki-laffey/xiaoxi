# core/outline_stub.py
"""
存根大纲管理器（去大纲版/Pass-through 版）。
"""

from typing import Dict, Any, List, Optional
import os
import json
import copy
from datetime import datetime

class StubOutlineManager:
    def __init__(self, log_callback=None):
        if log_callback is not None:
            self._log = lambda msg, **kwargs: log_callback(msg)
        else:
            self._log = lambda msg, **kwargs: None

        # 🌟 核心修复：使用 Optional 替代 | None，兼容 Python 3.9
        self._current_outline: Optional[Dict[str, Any]] = None
        self._current_task_id: Optional[str] = None

    def get_outline(self, task_id: str) -> Dict[str, Any]:
        if self._current_outline is not None:
            self._log(f"🪄 StubOutlineManager.get_outline 被调用 → 返回已保存的大纲（task_id={task_id}）")
            return copy.deepcopy(self._current_outline)

        self._log(f"🪄 StubOutlineManager.get_outline 被调用 → 未保存大纲，返回占位结构（task_id={task_id}）")
        return {
            "title": "默认大纲（占位）",
            "genre": "通用",
            "chapters": [],
            "total_chapters": 0,
            "words_per_chapter": 0,
        }

    def generate_outline(self, task_plan: Dict[str, Any]) -> Dict[str, Any]:
        task_id = task_plan.get("task_id", "")

        if self._current_outline is not None:
            self._log(f"🪄 StubOutlineManager.generate_outline 被调用 → 直接返回已保存的大纲（task_id={task_id}）")
            return {
                "success": True,
                "outline": copy.deepcopy(self._current_outline),
                "machine_path": self.get_outline_path(task_id),
                "statistics": {"info": "使用已保存的大纲（stub）"},
            }

        self._log(f"🪄 StubOutlineManager.generate_outline 被调用 → 规则兜底生成占位大纲（task_id={task_id}）")
        fallback = self._fallback_rule_based(task_plan)
        outline = fallback["outline"]
        stats = fallback["statistics"]

        self._current_outline = copy.deepcopy(outline)
        self._current_task_id = task_id

        return {
            "success": True,
            "outline": copy.deepcopy(outline),
            "machine_path": self.get_outline_path(task_id),
            "statistics": stats,
        }

    # 🌟 核心修复：增加 **kwargs 兜底，防止上游传入多余参数导致 TypeError
    def write_outline(self, task_id: str, outline: Dict[str, Any], **kwargs) -> None:
        self._current_outline = copy.deepcopy(outline)
        self._current_task_id = task_id
        self._log(f"🪄 StubOutlineManager.write_outline 被调用 → 已在内存中保存大纲（task_id={task_id}）")

    def confirm_outline(self, task_id: str, **kwargs) -> None:
        self._log(f"🪄 StubOutlineManager.confirm_outline 被调用 → 任务 {task_id} 已标记为确认（stub）")

    def _fallback_rule_based(self, task_plan: Dict[str, Any]) -> Dict[str, Any]:
        self._log(f"🪄 StubOutlineManager._fallback_rule_based 被调用（基于 task_plan 生成占位大纲）")

        title = task_plan.get("title", "未命名项目")
        genre = task_plan.get("genre", "通用")

        total_chapters = task_plan.get("total_chapters")
        if not isinstance(total_chapters, int) or total_chapters <= 0:
            total_chapters = 1

        words_per_chapter = task_plan.get("words_per_chapter", 2500)

        chapters: List[Dict[str, Any]] = []
        for idx in range(total_chapters):
            # 🌟 核心修复：补全 chapter_num 和关键字段，防止下游 ChapterGenerator 报 KeyError
            chapters.append({
                "chapter_num": idx + 1,
                "title": f"第 {idx + 1} 章 标题待定（占位符）",
                "summary": "本章摘要待定（占位符）",
                "keywords": ["占位"],
                "characters_involved": [],
                "key_events": []
            })

        outline = {
            "title": title,
            "genre": genre,
            "chapters": chapters,
            "total_chapters": total_chapters,
            "words_per_chapter": words_per_chapter,
        }

        statistics = {
            "fallback": "rule_based_stub",
            "generated_at": datetime.now().isoformat(),
            "note": "此大纲为占位，仅用于满足结构检查",
        }

        return {"outline": outline, "statistics": statistics}

    def get_outline_path(self, task_id: str) -> str:
        self._log(f"🪄 StubOutlineManager.get_outline_path 被调用 → 为 task_id={task_id} 创建占位路径")
        outline_dir = os.path.abspath(os.path.join(os.getcwd(), "tmp_outlines"))
        os.makedirs(outline_dir, exist_ok=True)
        return os.path.join(outline_dir, f"outline_{task_id}.json")
