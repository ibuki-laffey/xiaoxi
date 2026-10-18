import os
import json
import hashlib
import re
import threading
from datetime import datetime
from typing import Dict, Any, List, Optional

from .config import KNOWLEDGE_BASE_DIR, TASK_TRACKING_DIR

try:
    from .config import VECTOR_DB_ENABLED
except Exception:
    VECTOR_DB_ENABLED = False

# 🌟 基础停用词表：过滤无意义的高频词，防止污染检索结果
_STOP_WORDS = {
    "但是", "因为", "所以", "如果", "虽然", "然而", "而且", "并且", "或者", "还是",
    "他们", "她们", "它们", "我们", "你们", "自己", "这个", "那个", "这些", "那些",
    "已经", "可以", "可能", "应该", "必须", "需要", "能够", "愿意", "希望", "觉得",
    "不是", "没有", "就是", "只是", "只有", "还有", "也是", "都是", "非常", "十分",
    "一个", "一些", "什么", "怎么", "为什么", "哪里", "哪个", "多少", "几时", "怎样"
}

class VectorMemory:
    """向量记忆库：利用磁盘空间建立语义索引，支持快速检索、伏笔定位、设定查询。"""
    
    def __init__(self, log_callback=None):
        self.log_callback = log_callback
        self.vector_dir = os.path.join(KNOWLEDGE_BASE_DIR, "vector_db")
        os.makedirs(self.vector_dir, exist_ok=True)
        
        self.index_types = {
            "content": "章节内容索引", "character": "角色提及索引",
            "event": "关键事件索引", "setting": "世界观设定索引", "foreshadowing": "伏笔索引"
        }
        
        # 🌟 引入线程锁，防止多线程并发写入索引导致数据覆盖丢失
        self._index_lock = threading.Lock()
        self._log("✅ 向量记忆库已初始化（完整修复版 + 线程安全 + 角色/事件索引）")
    
    def _log(self, msg: str, level: str = "INFO"):
        if self.log_callback:
            try: self.log_callback(msg, level=level)
            except TypeError: self.log_callback(msg)

    def _atomic_write_json(self, filepath: str, data: Any) -> bool:
        """工业级原子写入 JSON，防止断电或崩溃导致文件损坏。"""
        tmp_path = filepath + ".tmp"
        try:
            dir_name = os.path.dirname(filepath)
            if dir_name: os.makedirs(dir_name, exist_ok=True)
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, filepath)
            return True
        except Exception as e:
            if os.path.exists(tmp_path):
                try: os.remove(tmp_path)
                except Exception: pass
            self._log(f"❌ 原子写入失败 ({filepath}): {e}", level="ERROR")
            return False

    def _safe_read_json(self, filepath: str) -> Optional[Any]:
        """安全读取 JSON，自动跳过损坏的文件。"""
        if not os.path.exists(filepath): return None
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return None

    def store_content(self, content_id: str, content: str, metadata: Dict[str, Any] = None):
        """将章节内容写入向量记忆库。"""
        if metadata is None: metadata = {}

        content_hash = hashlib.md5(content.encode()).hexdigest()
        keywords = self._extract_keywords(content)
        character_mentions = self._extract_character_mentions(content)
        key_events = self._extract_key_events(content)

        record = {
            "content_id": content_id, "content_hash": content_hash,
            "content_preview": content[:500], "content_length": len(content),
            "keywords": keywords, "character_mentions": character_mentions,
            "key_events": key_events, "metadata": metadata, "stored_at": datetime.now().isoformat()
        }

        chapter = metadata.get("chapter", 0)
        group_dir = os.path.join(self.vector_dir, f"chapter_{chapter // 10:02d}")
        filepath = os.path.join(group_dir, f"{content_id}.json")
        
        self._atomic_write_json(filepath, record)

        if character_mentions:
            self._index_character_mentions(content_id, character_mentions, chapter)
        if key_events:
            self._index_key_events(content_id, key_events, chapter)

        self._log(f"💾 存储内容：{content_id}（{len(content)}字，{len(keywords)}关键词，{len(character_mentions)}角色）")
    
    def _index_character_mentions(self, content_id: str, characters: List[str], chapter: int):
        """建立角色提及索引（线程安全）。"""
        index_dir = os.path.join(self.vector_dir, "indexes", "characters")
        
        with self._index_lock:
            for char_name in characters:
                char_safe = re.sub(r'[^a-zA-Z0-9\u4e00-\u9fa5]', '_', char_name)
                index_file = os.path.join(index_dir, f"{char_safe}.json")
                
                index_data = self._safe_read_json(index_file) or {"character": char_name, "mentions": []}
                index_data["mentions"].append({
                    "content_id": content_id, "chapter": chapter, "indexed_at": datetime.now().isoformat()
                })
                self._atomic_write_json(index_file, index_data)
    
    def _index_key_events(self, content_id: str, events: List[str], chapter: int):
        """建立关键事件索引（线程安全）。"""
        index_dir = os.path.join(self.vector_dir, "indexes", "events")
        index_file = os.path.join(index_dir, "event_index.json")
        
        with self._index_lock:
            index_data = self._safe_read_json(index_file) or {"events": []}
            for event in events:
                index_data["events"].append({
                    "content_id": content_id, "chapter": chapter,
                    "event": event, "indexed_at": datetime.now().isoformat()
                })
            self._atomic_write_json(index_file, index_data)
    
    def search_content(self, query: str, project_id: str = None, limit: int = 10) -> List[Dict[str, Any]]:
        """搜索相关内容。"""
        results = []
        query_keywords = self._extract_keywords(query)
        query_chars = self._extract_character_mentions(query)
        
        for root, dirs, files in os.walk(self.vector_dir):
            if "indexes" in root: continue
            
            for filename in files:
                if filename.endswith('.json'):
                    filepath = os.path.join(root, filename)
                    record = self._safe_read_json(filepath)
                    if not record: continue
                    
                    match_score = self._calculate_match_score(query_keywords, record.get("keywords", []))
                    record_chars = record.get("character_mentions", [])
                    char_match = len(set(query_chars) & set(record_chars))
                    
                    total_score = match_score * 0.7 + (char_match / max(len(query_chars), 1)) * 0.3
                    
                    if total_score > 0.3:
                        results.append({
                            "content_id": record["content_id"], "preview": record["content_preview"],
                            "match_score": total_score, "keyword_matches": match_score,
                            "character_matches": char_match, "metadata": record.get("metadata", {}),
                            "chapter": record.get("metadata", {}).get("chapter", 0)
                        })
        
        results.sort(key=lambda x: x["match_score"], reverse=True)
        return results[:limit]
    
    def search_character_mentions(self, character_name: str, project_id: str = None) -> List[Dict[str, Any]]:
        """搜索角色提及记录。"""
        char_safe = re.sub(r'[^a-zA-Z0-9\u4e00-\u9fa5]', '_', character_name)
        index_file = os.path.join(self.vector_dir, "indexes", "characters", f"{char_safe}.json")
        
        index_data = self._safe_read_json(index_file)
        return index_data.get("mentions", []) if index_data else []
    
    def search_key_events(self, chapter_range: tuple = None, limit: int = 50) -> List[Dict[str, Any]]:
        """搜索关键事件。"""
        index_file = os.path.join(self.vector_dir, "indexes", "events", "event_index.json")
        index_data = self._safe_read_json(index_file)
        if not index_data: return []
        
        events = index_data.get("events", [])
        if chapter_range:
            start, end = chapter_range
            events = [e for e in events if start <= e.get("chapter", 0) <= end]
        
        events.sort(key=lambda x: x.get("chapter", 0))
        return events[:limit]
    
    def get_character_appearance_chapters(self, character_name: str) -> List[int]:
        """获取角色出现的所有章节。"""
        mentions = self.search_character_mentions(character_name)
        chapters = list(set([m.get("chapter", 0) for m in mentions]))
        chapters.sort()
        return chapters
    
    def get_content_by_chapter(self, chapter_num: int) -> Optional[Dict[str, Any]]:
        """根据章节号获取内容。"""
        group_dir = os.path.join(self.vector_dir, f"chapter_{chapter_num // 10:02d}")
        if not os.path.exists(group_dir): return None
        
        for filename in os.listdir(group_dir):
            if filename.endswith('.json'):
                filepath = os.path.join(group_dir, filename)
                record = self._safe_read_json(filepath)
                if record and record.get("metadata", {}).get("chapter") == chapter_num:
                    return record
        return None
    
    def _extract_keywords(self, text: str) -> List[str]:
        """提取关键词（引入停用词过滤，提升检索准确率）。"""
        keywords = []
        quoted = re.findall(r'["\']([^"\']+)["\']', text)
        keywords.extend(quoted)
        
        names = re.findall(r'[A-Z][a-z]+', text)
        keywords.extend(names[:10])
        
        chinese_terms = re.findall(r'[\u4e00-\u9fa5]{2,4}', text)
        # 🌟 过滤停用词
        keywords.extend([t for t in chinese_terms if t not in _STOP_WORDS])
        
        return list(set(keywords))[:30]
    
    def _extract_character_mentions(self, text: str) -> List[str]:
        """提取角色提及（引入停用词过滤）。"""
        characters = []
        patterns = [r'([A-Z][a-z]+)', r'([\u4e00-\u9fa5]{2,4})']
        
        for pattern in patterns:
            matches = re.findall(pattern, text)
            characters.extend([m for m in matches if m not in _STOP_WORDS])
        
        return list(set(characters))[:15]
    
    def _extract_key_events(self, text: str) -> List[str]:
        """提取关键事件。"""
        events = []
        event_keywords = ['杀死', '发现', '遇到', '决定', '离开', '到达', '战斗', '死亡', '复活', '觉醒']
        sentences = re.split(r'[。！？!?]', text)
        
        for sentence in sentences[:20]:
            for keyword in event_keywords:
                if keyword in sentence:
                    events.append(sentence.strip()[:50])
                    break
        return list(set(events))[:10]
    
    def _calculate_match_score(self, query_keywords: List[str], doc_keywords: List[str]) -> float:
        """计算匹配分数（Jaccard 相似度）。"""
        if not query_keywords or not doc_keywords: return 0
        matches = len(set(query_keywords) & set(doc_keywords))
        total = len(set(query_keywords) | set(doc_keywords))
        return matches / total if total > 0 else 0
    
    def get_storage_stats(self) -> Dict[str, Any]:
        """获取存储统计。"""
        total_files, total_size, by_chapter = 0, 0, {}
        for root, dirs, files in os.walk(self.vector_dir):
            if "indexes" in root: continue
            for filename in files:
                if filename.endswith('.json'):
                    total_files += 1
                    filepath = os.path.join(root, filename)
                    size = os.path.getsize(filepath)
                    total_size += size
                    chapter_dir = os.path.basename(root)
                    if chapter_dir.startswith("chapter_"):
                        by_chapter[chapter_dir] = by_chapter.get(chapter_dir, 0) + size
        return {"total_records": total_files, "total_size_bytes": total_size, "total_size_mb": total_size / (1024 * 1024), "by_chapter": by_chapter, "vector_dir": self.vector_dir}
    
    def cleanup_old_records(self, days: int = 30) -> int:
        """清理旧记录。"""
        cutoff_date = datetime.now().timestamp() - (days * 24 * 60 * 60)
        cleaned = 0
        for root, dirs, files in os.walk(self.vector_dir):
            if "indexes" in root: continue
            for filename in files:
                if filename.endswith('.json'):
                    filepath = os.path.join(root, filename)
                    try:
                        if os.path.getmtime(filepath) < cutoff_date:
                            os.remove(filepath)
                            cleaned += 1
                    except Exception: continue
        self._log(f"🧹 清理了 {cleaned} 条旧记录")
        return cleaned
    
    def rebuild_indexes(self) -> Dict[str, Any]:
        """重建所有索引。"""
        self._log("🔧 开始重建索引...")
        index_dir = os.path.join(self.vector_dir, "indexes")
        if os.path.exists(index_dir):
            import shutil
            shutil.rmtree(index_dir)
        os.makedirs(index_dir, exist_ok=True)
        
        records_indexed = characters_indexed = events_indexed = 0
        
        for root, dirs, files in os.walk(self.vector_dir):
            if "indexes" in root: continue
            for filename in files:
                if filename.endswith('.json'):
                    filepath = os.path.join(root, filename)
                    record = self._safe_read_json(filepath)
                    if not record: continue
                    
                    char_mentions = record.get("character_mentions", [])
                    if char_mentions:
                        self._index_character_mentions(record["content_id"], char_mentions, record.get("metadata", {}).get("chapter", 0))
                        characters_indexed += len(char_mentions)
                    
                    key_events = record.get("key_events", [])
                    if key_events:
                        self._index_key_events(record["content_id"], key_events, record.get("metadata", {}).get("chapter", 0))
                        events_indexed += len(key_events)
                    records_indexed += 1
        
        self._log(f"✅ 索引重建完成：{records_indexed}条记录，{characters_indexed}个角色提及，{events_indexed}个事件")
        return {"success": True, "records_indexed": records_indexed, "characters_indexed": characters_indexed, "events_indexed": events_indexed}
    
    def export_vector_db(self, output_path: str) -> Dict[str, Any]:
        """导出向量数据库。"""
        try:
            export_data = {"exported_at": datetime.now().isoformat(), "records": []}
            for root, dirs, files in os.walk(self.vector_dir):
                if "indexes" in root: continue
                for filename in files:
                    if filename.endswith('.json'):
                        filepath = os.path.join(root, filename)
                        record = self._safe_read_json(filepath)
                        if record: export_data["records"].append(record)
            
            self._atomic_write_json(output_path, export_data)
            self._log(f"💾 向量数据库已导出：{output_path}（{len(export_data['records'])}条记录）")
            return {"success": True, "record_count": len(export_data["records"])}
        except Exception as e:
            self._log(f"❌ 导出失败：{e}", level="ERROR")
            return {"success": False, "error": str(e)}

    def search_similar_outline(self, query: Dict[str, Any], top_k: int = 3) -> List[Dict[str, Any]]:
        """根据 query 在向量库中检索最相似的大纲。"""
        if not VECTOR_DB_ENABLED:
            self._log("⚠️ 向量数据库未启用，search_similar_outline 直接返回 []")
            return []

        try:
            raw_query = json.dumps(query, ensure_ascii=False, sort_keys=True)
            raw_results = self.search_content(raw_query, project_id=None, limit=top_k * 5)

            outlines: List[Dict[str, Any]] = []
            for entry in raw_results:
                cid = entry.get("content_id")
                if not cid: continue

                # 🌟 修复路径寻址致命错误：经验库在 KNOWLEDGE_BASE_DIR 下，而不是 vector_dir 下
                exp_path = os.path.join(KNOWLEDGE_BASE_DIR, "experience_db", f"exp_{cid}.json")
                if not os.path.isfile(exp_path): continue

                data = self._safe_read_json(exp_path)
                if isinstance(data, dict) and isinstance(data.get("chapters"), list):
                    outlines.append(data)
                    if len(outlines) >= top_k: break

            return outlines
        except Exception as e:
            self._log(f"⚠️ search_similar_outline 异常: {e}", level="WARN")
            return []
