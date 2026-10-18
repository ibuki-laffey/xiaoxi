import threading
from typing import Callable, Dict, List

class _EventBus:
    """内部实现类，全局唯一的事件总线。"""
    def __init__(self) -> None:
        self._subscribers: Dict[str, List[Callable]] = {}
        self._lock = threading.RLock()

    def subscribe(self, event_name: str, callback: Callable) -> None:
        with self._lock:
            self._subscribers.setdefault(event_name, []).append(callback)

    def unsubscribe(self, event_name: str, callback: Callable) -> None:
        with self._lock:
            cbs = self._subscribers.get(event_name)
            if not cbs: return
            try: cbs.remove(callback)
            except ValueError: pass
            if not cbs: self._subscribers.pop(event_name, None)

    def publish(self, event_name: str, **kwargs) -> None:
        with self._lock:
            callbacks = list(self._subscribers.get(event_name, []))
        for cb in callbacks:
            try: cb(**kwargs)
            except Exception as e:
                print(f"[EventBus] Error in handler for '{event_name}': {e}")

EventBus = _EventBus()
