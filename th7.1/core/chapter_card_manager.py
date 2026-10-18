import os
import json
import re
from datetime import datetime
from typing import Dict, Any, List, Optional

from .config import (
    TASK_TRACKING_DIR, CHAPTER_CARDS_DIR, CHAPTER_CARD_ENABLED,
    CHAPTER_CARD_FORMAT, CHAPTER_CARD_FIELDS, PROFESSIONAL_LOG_ENABLED,
    OLLAMA_URL, MODEL_NAME, CHAPTER_TIMEOUT
)

class ChapterCardManager:
    """章节卡片管理器：为每章生成结构化上下文卡片，支持磁盘级索引。"""
    
    def __init__(self, log_callback=None):
        self.log_callback = log_callback
        self.cards_dir = CHAPTER_CARDS_DIR
        os.makedirs(self.cards_dir, exist_ok=True)
        
        self.enabled = CHAPTER_CARD_ENABLED
        self.card_format = CHAPTER_CARD_FORMAT
        self.card_fields = CHAPTER_CARD_FIELDS
        
        self._log("✅ 章节卡片管理器已初始化")
    
    def _log(self, msg: str, level: str = "INFO") -> None:
        if self.log_callback:
            try: self.log_callback(msg, level=level)
            except TypeError: self.log_callback(msg)

    def _robust_extract_json(self, text: str) -> Optional[dict]:
        """🌟 工业级 JSON 提取器：剥离 Markdown、首尾括号定位、清理尾部逗号。"""
        if not text: return None
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
        except json.JSONDecodeError: return None
    
    def create_chapter_card(
        self, task_id: str, chapter_num: int, chapter_content: str,
        state_changes: Dict[str, Any] = None, foreshadowing: List[Dict[str, Any]] = None,
        new_entities: List[str] = None
    ) -> Dict[str, Any]:
        """创建章节卡片（在章节保存后自动调用）。"""
        if not self.enabled: return {"success": False, "error": "章节卡片功能已禁用"}
        
        self._log(f"📇 正在生成第{chapter_num}章卡片...")
        card_data = self._generate_card_content(chapter_content, chapter_num, state_changes, foreshadowing, new_entities)
        
        card_data["task_id"] = task_id
        card_data["chapter_num"] = chapter_num
        card_data["created_at"] = datetime.now().isoformat()
        card_data["card_version"] = "1.0"
        
        card_file = self._get_card_filepath(task_id, chapter_num)
        self._save_card(card_data, card_file)
        self._log(f"✅ 第{chapter_num}章卡片已生成：{card_file}")
        return {"success": True, "card_data": card_data, "filepath": card_file}
    
    def _generate_card_content(
        self, content: str, chapter_num: int, state_changes: Dict[str, Any] = None,
        foreshadowing: List[Dict[str, Any]] = None, new_entities: List[str] = None
    ) -> Dict[str, Any]:
        """调用 AI 生成卡片内容。"""
        prompt = f"""请分析以下小说章节内容，生成结构化上下文卡片。
第{chapter_num}章内容：
{content[:15000]}...

请自主思考并提取以下信息（严格 JSON，绝不输出任何废话或 Markdown 标记）：
{{"plot_summary": "本章剧情简述", "state_changes": {{"entity": "变化前→变化后"}}, "new_entities": ["新实体"], "foreshadowing": [{{"type": "埋下/回收", "content": "内容", "related_chapter": 0}}], "emotional_tone": "基调", "key_quotes": ["摘录"], "character_appearances": ["角色"], "location_changes": ["地点"], "time_progression": "时间推进", "conflict_summary": "核心冲突", "resolution": "解决方式", "cliffhanger": false, "word_count": 0}}"""
        
        try:
            card_data = self._call_ai_model(prompt)
            if state_changes: card_data["state_changes"] = state_changes
            if foreshadowing: card_data["foreshadowing"] = foreshadowing
            if new_entities: card_data["new_entities"] = new_entities
            card_data["word_count"] = len(content)
            return card_data
        except Exception as e:
            self._log(f"⚠️ 卡片生成失败：{e}，使用基础模板", level="WARN")
            return self._create_fallback_card(content, chapter_num, state_changes, foreshadowing, new_entities)
    
    def _create_fallback_card(self, content: str, chapter_num: int, state_changes: Dict[str, Any] = None, foreshadowing: List[Dict[str, Any]] = None, new_entities: List[str] = None) -> Dict[str, Any]:
        """创建基础卡片（AI 生成失败时的备用方案）。"""
        return {
            "plot_summary": content[:500] + "..." if len(content) > 500 else content,
            "state_changes": state_changes or {}, "new_entities": new_entities or [],
            "foreshadowing": foreshadowing or [], "emotional_tone": "未知", "key_quotes": [],
            "character_appearances": [], "location_changes": [], "time_progression": "未知",
            "conflict_summary": "未知", "resolution": "未知", "cliffhanger": False, "word_count": len(content)
        }
    
    def _get_card_filepath(self, task_id: str, chapter_num: int) -> str:
        task_dir = os.path.join(self.cards_dir, task_id)
        os.makedirs(task_dir, exist_ok=True)
        return os.path.join(task_dir, f"chapter_{chapter_num:03d}_card.{self.card_format}")
    
    def _save_card(self, card_data: Dict[str, Any], filepath: str) -> None:
        """🌟 工业级原子保存卡片文件，防止断电损坏。"""
        tmp_filepath = filepath + '.tmp'
        try:
            with open(tmp_filepath, 'w', encoding='utf-8') as f:
                json.dump(card_data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_filepath, filepath)
        except Exception as e:
            if os.path.exists(tmp_filepath):
                try: os.remove(tmp_filepath)
                except Exception: pass
            self._log(f"❌ 卡片保存失败：{e}", level="ERROR")
            raise
    
    def load_card(self, task_id: str, chapter_num: int) -> Optional[Dict[str, Any]]:
        """加载指定章节卡片。"""
        filepath = self._get_card_filepath(task_id, chapter_num)
        if not os.path.exists(filepath): return None
        try:
            with open(filepath, 'r', encoding='utf-8') as f: return json.load(f)
        except Exception as e:
            self._log(f"❌ 卡片读取失败：{e}", level="ERROR")
            return None
    
    def load_cards_range(self, task_id: str, start_chapter: int, end_chapter: int) -> List[Dict[str, Any]]:
        cards = []
        for chapter_num in range(start_chapter, end_chapter + 1):
            card = self.load_card(task_id, chapter_num)
            if card: cards.append(card)
        return cards
    
    def load_recent_cards(self, task_id: str, count: int = 10) -> List[Dict[str, Any]]:
        latest_chapter = self._get_latest_chapter(task_id)
        if latest_chapter is None: return []
        start_chapter = max(1, latest_chapter - count + 1)
        return self.load_cards_range(task_id, start_chapter, latest_chapter)
    
    def _get_latest_chapter(self, task_id: str) -> Optional[int]:
        task_dir = os.path.join(self.cards_dir, task_id)
        if not os.path.exists(task_dir): return None
        latest = 0
        for filename in os.listdir(task_dir):
            if filename.startswith('chapter_') and filename.endswith(f'.{self.card_format}'):
                try:
                    chapter_num = int(filename.split('_')[1])
                    latest = max(latest, chapter_num)
                except Exception: continue
        return latest if latest > 0 else None
    
    def search_cards(self, task_id: str, keyword: str) -> List[Dict[str, Any]]:
        results = []
        task_dir = os.path.join(self.cards_dir, task_id)
        if not os.path.exists(task_dir): return results
        for filename in os.listdir(task_dir):
            if filename.startswith('chapter_') and filename.endswith(f'.{self.card_format}'):
                filepath = os.path.join(task_dir, filename)
                try:
                    with open(filepath, 'r', encoding='utf-8') as f: card_data = json.load(f)
                    if keyword in json.dumps(card_data, ensure_ascii=False): results.append(card_data)
                except Exception: continue
        return results
    
    def get_state_changes_summary(self, task_id: str, up_to_chapter: int) -> Dict[str, Any]:
        summary = {"characters": {}, "items": {}, "locations": {}, "relationships": {}}
        for chapter_num in range(1, up_to_chapter + 1):
            card = self.load_card(task_id, chapter_num)
            if card and card.get("state_changes"):
                for entity, change in card["state_changes"].items():
                    if "->" in str(change):
                        latest_state = str(change).split("->")[-1]
                        if entity.startswith("char_"): summary["characters"][entity] = latest_state
                        elif entity.startswith("item_"): summary["items"][entity] = latest_state
                        elif entity.startswith("loc_"): summary["locations"][entity] = latest_state
        return summary
    
    def get_foreshadowing_ledger(self, task_id: str) -> Dict[str, Any]:
        ledger = {"pending": [], "resolved": [], "all": []}
        task_dir = os.path.join(self.cards_dir, task_id)
        if not os.path.exists(task_dir): return ledger
        for filename in sorted(os.listdir(task_dir)):
            if filename.startswith('chapter_') and filename.endswith(f'.{self.card_format}'):
                filepath = os.path.join(task_dir, filename)
                try:
                    with open(filepath, 'r', encoding='utf-8') as f: card_data = json.load(f)
                    chapter_num = card_data.get("chapter_num", 0)
                    for fb in card_data.get("foreshadowing", []):
                        fb_record = {"chapter": chapter_num, "type": fb.get("type", "unknown"), "content": fb.get("content", ""), "related_chapter": fb.get("related_chapter")}
                        ledger["all"].append(fb_record)
                        if fb.get("type") == "埋下": ledger["pending"].append(fb_record)
                        elif fb.get("type") == "回收": ledger["resolved"].append(fb_record)
                except Exception: continue
        return ledger
    
    def delete_card(self, task_id: str, chapter_num: int) -> bool:
        filepath = self._get_card_filepath(task_id, chapter_num)
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
                self._log(f"🗑️ 已删除章节卡片：{chapter_num}")
                return True
            except Exception as e:
                self._log(f"❌ 删除卡片失败：{e}", level="ERROR")
        return False
    
    def rebuild_all_cards(self, task_id: str, chapters_data: List[Dict[str, Any]]) -> int:
        rebuilt_count = 0
        for chapter_data in chapters_data:
            try:
                self.create_chapter_card(
                    task_id=task_id, chapter_num=chapter_data["chapter_num"],
                    chapter_content=chapter_data["content"], state_changes=chapter_data.get("state_changes"),
                    foreshadowing=chapter_data.get("foreshadowing"), new_entities=chapter_data.get("new_entities")
                )
                rebuilt_count += 1
            except Exception as e:
                self._log(f"⚠️ 重建第{chapter_data['chapter_num']}章卡片失败：{e}", level="WARN")
        self._log(f"✅ 已重建 {rebuilt_count} 张章节卡片")
        return rebuilt_count
    
    def export_cards(self, task_id: str, output_path: str) -> Dict[str, Any]:
        """导出所有卡片为单个文件（工业级原子写入）。"""
        task_dir = os.path.join(self.cards_dir, task_id)
        if not os.path.exists(task_dir): return {"success": False, "error": "任务卡片目录不存在"}
        
        all_cards = []
        for filename in sorted(os.listdir(task_dir)):
            if filename.startswith('chapter_') and filename.endswith(f'.{self.card_format}'):
                filepath = os.path.join(task_dir, filename)
                try:
                    with open(filepath, 'r', encoding='utf-8') as f: all_cards.append(json.load(f))
                except Exception: continue
        
        tmp_path = output_path + ".tmp"
        try:
            dir_name = os.path.dirname(output_path)
            if dir_name: os.makedirs(dir_name, exist_ok=True)
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(all_cards, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, output_path)
            return {"success": True, "count": len(all_cards), "filepath": output_path}
        except Exception as e:
            if os.path.exists(tmp_path):
                try: os.remove(tmp_path)
                except Exception: pass
            return {"success": False, "error": str(e)}
    
    def _call_ai_model(self, prompt: str) -> Dict[str, Any]:
        """🌟 核心修复：调用 AI 模型，强制 JSON 模式 + 防弹提取。"""
        try:
            import requests
            resp = requests.post(OLLAMA_URL, json={
                "model": MODEL_NAME,
                "messages": [
                    {"role": "system", "content": "你是一个底层的 JSON 数据审查 API。你只输出合法的、结构严谨的纯 JSON 字符串，绝不输出任何废话、解释或 Markdown 标记。"},
                    {"role": "user", "content": prompt}
                ],
                "stream": False,
                "options": {"temperature": 0.3, "num_predict": 16000, "format": "json"}
            }, timeout=CHAPTER_TIMEOUT)
            
            if resp.status_code == 200:
                content = resp.json().get('message', {}).get('content', '').strip()
                parsed = self._robust_extract_json(content)
                if parsed: return parsed
            return {}
        except Exception as e:
            self._log(f"❌ AI 调用失败：{e}", level="ERROR")
            return {}
