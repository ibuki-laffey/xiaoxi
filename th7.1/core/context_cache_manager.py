import os
import pickle
import threading
from collections import OrderedDict
from typing import Any, Optional

class ContextCacheManager:
    """线程安全的 KV 缓存，内部使用 OrderedDict 实现 LRU 淘汰。"""

    def __init__(self, max_entries: int = 0):
        self._store: OrderedDict[str, Any] = OrderedDict()
        self._max = max_entries
        self._lock = threading.RLock()

    def _evict_if_needed(self) -> None:
        """若超过 max_entries，弹出最旧的条目（LRU）。"""
        if self._max > 0:
            while len(self._store) > self._max:
                self._store.popitem(last=False)

    def set(self, key: str, value: Any) -> None:
        """写入或更新键值，并把键标记为最近使用。"""
        with self._lock:
            self._store[key] = value
            self._store.move_to_end(key, last=True)
            self._evict_if_needed()

    def get(self, key: str, default: Optional[Any] = None) -> Any:
        """读取键对应的值，若不存在返回 default。读取成功的键会被标记为最近使用。"""
        with self._lock:
            try:
                value = self._store[key]
                self._store.move_to_end(key, last=True)
                return value
            except KeyError:
                return default

    def delete(self, key: str) -> None:
        """安全删除键（若不存在则无操作）。"""
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        """一次性清空全部缓存。"""
        with self._lock:
            self._store.clear()

    def save(self, file_path: str) -> bool:
        """将当前缓存序列化保存到 file_path（使用 pickle）。成功返回 True，失败返回 False。"""
        try:
            dir_name = os.path.dirname(file_path)
            if dir_name:
                os.makedirs(dir_name, exist_ok=True)
            with open(file_path, "wb") as fh:
                pickle.dump(self._store, fh, protocol=pickle.HIGHEST_PROTOCOL)
            return True
        except Exception:
            return False

    def load(self, file_path: str) -> bool:
        """从 file_path 读取并恢复缓存。若文件不存在或损坏，保留当前缓存并返回 False。"""
        if not os.path.isfile(file_path):
            return False
        try:
            with open(file_path, "rb") as fh:
                data = pickle.load(fh)
            if isinstance(data, OrderedDict):
                with self._lock:
                    self._store = data
                    self._evict_if_needed()
                return True
            return False
        except Exception:
            return False

    def __len__(self) -> int:
        """当前缓存条目数。"""
        return len(self._store)

    def keys(self):
        """返回所有键的快照。"""
        with self._lock:
            return list(self._store.keys())

    def items(self):
        """返回 (key, value) 列表快照。"""
        with self._lock:
            return list(self._store.items())

default_cache = ContextCacheManager(max_entries=0)
