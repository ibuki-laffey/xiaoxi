import os
import sys
import json
import time
import hashlib
import pickle
import gzip
import threading
import shutil
from datetime import datetime, timedelta
from typing import Any, Optional, Dict, List
from .config import KNOWLEDGE_BASE_DIR, TASK_TRACKING_DIR, CACHE_LOCK_TIMEOUT, DATA_ARCHIVE_DIR, DATA_ACTIVE_DIR, MAX_DISK_CACHE_INDEX

class DiskCacheManager:
    """磁盘缓存管理器：大对象磁盘存储、自动清理、压缩、归档。"""
    
    def __init__(self, log_callback=None):
        self.log_callback = log_callback
        self.cache_dir = DATA_ACTIVE_DIR
        self.archive_dir = DATA_ARCHIVE_DIR
        self.index_file = os.path.join(self.cache_dir, "cache_index.bin")
        os.makedirs(self.cache_dir, exist_ok=True)
        os.makedirs(self.archive_dir, exist_ok=True)
        
        self.cache_index: Dict[str, Dict] = {}
        self._cache_lock = threading.Lock()
        self._lock_timeout = CACHE_LOCK_TIMEOUT
        self._load_index()
        self._log("✅ 磁盘缓存管理器已初始化 (二进制 + 归档)")
    
    def _log(self, msg: str, level: str = "INFO") -> None:
        if self.log_callback: self.log_callback(msg)
    
    def _load_index(self) -> None:
        try:
            if os.path.exists(self.index_file):
                with open(self.index_file, 'rb') as f:
                    self.cache_index = pickle.load(f)
                self._log(f"📚 已加载 {len(self.cache_index)} 条缓存索引")
        except Exception as e:
            self._log(f"⚠️ 加载缓存索引失败，已重置：{e}")
            self.cache_index = {}
    
    def _save_index(self) -> None:
        try:
            if len(self.cache_index) > MAX_DISK_CACHE_INDEX:
                sorted_items = sorted(self.cache_index.items(), key=lambda x: x[1].get('last_accessed', ''), reverse=True)[:MAX_DISK_CACHE_INDEX]
                self.cache_index = dict(sorted_items)
            
            tmp_file = self.index_file + ".tmp"
            with open(tmp_file, 'wb') as f:
                pickle.dump(self.cache_index, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_file, self.index_file)
        except Exception as e:
            self._log(f"⚠️ 保存缓存索引失败：{e}")
    
    def _generate_cache_key(self, key: str) -> str:
        return hashlib.md5(key.encode()).hexdigest()
    
    def set(self, key: str, value: Any, compress: bool = True, ttl_seconds: int = None) -> bool:
        with self._cache_lock:
            try:
                cache_key = self._generate_cache_key(key)
                filepath = os.path.join(self.cache_dir, f"{cache_key}.cache")
                
                if compress:
                    data = pickle.dumps(value)
                    compressed_data = gzip.compress(data)
                    with open(filepath, 'wb') as f: f.write(compressed_data)
                    file_size = len(compressed_data)
                else:
                    with open(filepath, 'wb') as f: pickle.dump(value, f)
                    file_size = os.path.getsize(filepath)
                
                self.cache_index[key] = {
                    'filepath': filepath, 'size_bytes': file_size, 'compressed': compress,
                    'created_at': datetime.now().isoformat(), 'last_accessed': datetime.now().isoformat(),
                    'ttl_seconds': ttl_seconds, 'expires_at': (time.time() + ttl_seconds if ttl_seconds else None)
                }
                self._save_index()
                return True
            except Exception as e:
                self._log(f"❌ 写入缓存失败：{e}")
                return False
    
    def get(self, key: str) -> Optional[Any]:
        cache_key = self._generate_cache_key(key)
        cache_path = os.path.join(self.cache_dir, f"{cache_key}.cache")
        
        # 更新访问时间
        with self._cache_lock:
            if key in self.cache_index:
                self.cache_index[key]['last_accessed'] = datetime.now().isoformat()
                
        if not os.path.isfile(cache_path): return None
        
        # TTL 检查
        with self._cache_lock:
            expires_at = self.cache_index.get(key, {}).get('expires_at')
            if expires_at and time.time() > expires_at:
                self.delete(key)
                return None

        try:
            with open(cache_path, "rb") as f: raw = f.read()
            if raw[:2] == b'\x1f\x8b':
                return pickle.loads(gzip.decompress(raw))
            else:
                return pickle.loads(raw)
        except Exception as e:
            self._log(f"⚠️ 读取缓存 {key} 异常: {e}", level="WARN")
            return None
    
    def delete(self, key: str) -> bool:
        with self._cache_lock:
            try:
                if key not in self.cache_index: return False
                filepath = self.cache_index[key].get('filepath', '')
                if os.path.exists(filepath): os.remove(filepath)
                del self.cache_index[key]
                self._save_index()
                return True
            except Exception as e:
                self._log(f"❌ 删除缓存失败：{e}")
                return False
    
    def clear(self) -> int:
        with self._cache_lock:
            count = 0
            for key in list(self.cache_index.keys()):
                if self.delete(key): count += 1
            for filename in os.listdir(self.cache_dir):
                if filename.endswith('.cache'):
                    try: os.remove(os.path.join(self.cache_dir, filename)); count += 1
                    except Exception: pass
            return count
    
    def get_stats(self) -> Dict[str, Any]:
        try:
            total_size = sum(info.get('size_bytes', 0) for info in self.cache_index.values())
            expired_count = sum(1 for info in self.cache_index.values() if info.get('expires_at') and time.time() > info['expires_at'])
            return {'total_items': len(self.cache_index), 'total_size_mb': total_size / (1024 * 1024), 'expired_items': expired_count}
        except Exception: return {}
    
    def cleanup_expired(self) -> int:
        with self._cache_lock:
            cleaned, archived = 0, 0
            current_time = time.time()
            archive_cutoff = current_time - (30 * 24 * 3600)
            
            for key in list(self.cache_index.keys()):
                info = self.cache_index[key]
                if info.get('expires_at') and current_time > info['expires_at']:
                    if self.delete(key): cleaned += 1
                else:
                    try:
                        created_ts = datetime.fromisoformat(info.get('created_at', datetime.now().isoformat())).timestamp()
                        if created_ts < archive_cutoff:
                            filepath = info.get('filepath', '')
                            if os.path.exists(filepath):
                                archive_path = os.path.join(self.archive_dir, datetime.now().strftime("%Y-%m"), os.path.basename(filepath))
                                os.makedirs(os.path.dirname(archive_path), exist_ok=True)
                                shutil.move(filepath, archive_path)
                                self.delete(key)
                                archived += 1
                    except Exception: pass
            return cleaned + archived
    
    def save_large_object(self, key: str, obj: Any, description: str = "") -> bool:
        try:
            # 🌟 防 OOM 修复：避免对大对象进行全量 pickle.dumps 估算
            if isinstance(obj, str):
                estimated_size = len(obj.encode('utf-8'))
            elif isinstance(obj, (list, dict)):
                estimated_size = len(json.dumps(obj, ensure_ascii=False).encode('utf-8'))
            else:
                estimated_size = sys.getsizeof(obj)
                
            size_mb = estimated_size / (1024 * 1024)
            
            if size_mb > 10: compress, ttl = True, 7 * 24 * 3600
            elif size_mb > 1: compress, ttl = True, 30 * 24 * 3600
            else: compress, ttl = False, None
            
            return self.set(key, obj, compress=compress, ttl_seconds=ttl)
        except Exception as e:
            self._log(f"❌ 保存大对象失败：{e}")
            return False

def save_conversation_to_disk(topic: str, conversation_history: List[Dict], cache_manager: DiskCacheManager) -> bool:
    return cache_manager.save_large_object(f"conversation_{topic}", conversation_history, f"对话历史：{topic}")

def load_conversation_from_disk(topic: str, cache_manager: DiskCacheManager, default: List = None) -> List:
    return cache_manager.get(f"conversation_{topic}") or (default or [])

def save_chapter_to_disk(task_id: str, chapter_num: int, content: str, cache_manager: DiskCacheManager) -> bool:
    return cache_manager.save_large_object(f"chapter_{task_id}_{chapter_num}", {'task_id': task_id, 'chapter': chapter_num, 'content': content, 'saved_at': datetime.now().isoformat()}, f"章节：{task_id}_第{chapter_num}章")

def load_chapter_from_disk(task_id: str, chapter_num: int, cache_manager: DiskCacheManager) -> Optional[Dict]:
    return cache_manager.get(f"chapter_{task_id}_{chapter_num}")

_disk_cache_instance: Optional[DiskCacheManager] = None
def get_disk_cache_manager(log_callback=None) -> DiskCacheManager:
    global _disk_cache_instance
    if _disk_cache_instance is None: _disk_cache_instance = DiskCacheManager(log_callback)
    return _disk_cache_instance
