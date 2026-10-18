# core/milestone_tracker.py
"""
里程碑追踪器 (Milestone Tracker)
解决 50 万字以上长线伏笔回收与全局上下文遗忘问题。
通过提取极度浓缩的“里程碑锚点”，实现长线记忆的完美融合。
"""
import os
import json
import re
from typing import Dict, Any, List, Optional
from .config import call_ollama_safe

class MilestoneTracker:
    def __init__(self, project_dir: str, log_callback=None):
        self.project_dir = project_dir
        self._log = log_callback if callable(log_callback) else print
        self.milestones_dir = os.path.join(project_dir, "03_设定", "milestones")
        os.makedirs(self.milestones_dir, exist_ok=True)
        self.milestones_file = os.path.join(self.milestones_dir, "milestones.json")
        self.milestones = self._load_milestones()

    def _load_milestones(self) -> List[Dict[str, Any]]:
        if not os.path.exists(self.milestones_file): return []
        try:
            with open(self.milestones_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception: return []

    def _save_milestones(self):
        tmp = self.milestones_file + ".tmp"
        try:
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(self.milestones, f, ensure_ascii=False, indent=2)
                f.flush(); os.fsync(f.fileno())
            os.replace(tmp, self.milestones_file)
        except Exception as e:
            self._log(f"⚠️ 里程碑保存失败: {e}", level="WARN")

    def _robust_extract_json(self, text: str) -> Optional[Dict]:
        if not text: return None
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
        text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE).strip()
        try: return json.loads(text)
        except json.JSONDecodeError: pass
        start, end = text.find('{'), text.rfind('}')
        if start != -1 and end > start:
            try: return json.loads(re.sub(r",\s*([}\]])", r"\1", text[start:end+1]))
            except: pass
        return None

    def evaluate_and_record(self, chapter_num: int, chapter_content: str, chapter_outline: Dict):
        """评估本章是否为里程碑，并记录锚点。"""
        prompt = f"""你是严谨的网文架构师。请判断以下第{chapter_num}章是否属于“核心里程碑事件”（如：大高潮、重要伏笔埋下/回收、核心人物死亡/觉醒/重大转变、世界观颠覆）。
【本章大纲】：{json.dumps(chapter_outline, ensure_ascii=False)}
【本章正文摘要】：{chapter_content[:2000]}...

如果是里程碑，请提取极度浓缩的“记忆锚点”（必须包含因果逻辑，限150字以内）。
如果不是，请返回 is_milestone 为 false。
返回纯 JSON：{{"is_milestone": true/false, "anchor_text": "浓缩锚点文本(限150字)", "linked_hooks": ["关联的伏笔名称"]}}"""

        try:
            resp = call_ollama_safe([{"role": "user", "content": prompt}], options={"temperature": 0.2, "num_predict": 512, "format": "json"})
            raw = resp.get("content", "") if isinstance(resp, dict) else str(resp)
            data = self._robust_extract_json(raw)
            
            if data and data.get("is_milestone") and data.get("anchor_text"):
                # 防重：如果该章已有里程碑，则更新
                self.milestones = [m for m in self.milestones if m.get("chapter") != chapter_num]
                self.milestones.append({
                    "chapter": chapter_num,
                    "anchor_text": str(data["anchor_text"])[:200], # 强制截断防 Token 爆炸
                    "linked_hooks": data.get("linked_hooks", [])
                })
                # 限制最多保留 100 个里程碑（约 1.5 万字，绝对安全）
                self.milestones = sorted(self.milestones, key=lambda x: x["chapter"])[-100:]
                self._save_milestones()
                self._log(f"🏆 [Milestone] 第{chapter_num}章被记录为核心里程碑！")
        except Exception as e:
            self._log(f"⚠️ 里程碑评估异常: {e}", level="WARN")

    def get_context_anchors(self, current_chapter: int) -> str:
        """获取所有已发生的里程碑锚点，用于注入上下文。"""
        past_milestones = [m for m in self.milestones if m.get("chapter", 0) < current_chapter]
        if not past_milestones: return ""
        
        text = "【📜 核心里程碑记忆锚点（长线因果与伏笔追踪）】\n"
        for m in past_milestones:
            text += f"- 第{m['chapter']}章: {m['anchor_text']}\n"
        return text
