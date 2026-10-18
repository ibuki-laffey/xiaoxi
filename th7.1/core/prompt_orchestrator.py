import os
import json
import threading
import time
import shutil
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from .config import (
        ENABLE_PROMPT_DUMP,
        PROMPT_DUMP_DIR,
        PROMPT_BACKUP_INTERVAL,
    )
except Exception:
    ENABLE_PROMPT_DUMP = False
    PROMPT_DUMP_DIR = Path("prompt_dump")
    PROMPT_BACKUP_INTERVAL = 30

class PromptOrchestrator:
    """负责把所有 Prompt / Response 落盘、备份、以及提供快速读取的工具类。"""

    def __init__(self, project_root: Any, log_callback: Optional[Any] = None):
        self.project_root = Path(project_root)
        self.log_callback = log_callback

        self.base_dir = self.project_root / "prompt_history"
        self.subtasks_dir = self.base_dir / "subtasks"
        self.backup_dir = self.base_dir / ".bak"

        for d in (self.base_dir, self.subtasks_dir, self.backup_dir):
            d.mkdir(parents=True, exist_ok=True)

        self._counter = 0
        self._lock = threading.RLock()
        self._stop_backup = threading.Event()
        self._backup_thread = threading.Thread(target=self._auto_backup_worker, daemon=True)
        self._backup_thread.start()

    def _log(self, msg: str, level: str = "INFO"):
        if self.log_callback:
            try: self.log_callback(f"[PromptOrchestrator][{level}] {msg}")
            except Exception: print(f"[PromptOrchestrator][{level}] {msg}")
        else:
            print(f"[PromptOrchestrator][{level}] {msg}")

    def _atomic_write(self, dst: Path, data: str):
        """🌟 工业级原子写入：加入 flush 和 fsync 防止断电数据丢失。"""
        tmp = dst.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, dst)
        except Exception:
            if tmp.exists():
                try: tmp.unlink(missing_ok=True)
                except Exception: pass
            raise

    def record(self, step_name: str, data: Any, is_json: bool = False) -> Optional[Path]:
        """把 data 写入 <base_dir>/<step_name>_<timestamp>[_<cnt>].{'json'|'txt'}。"""
        if not ENABLE_PROMPT_DUMP: return None

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        ext = "json" if is_json else "txt"

        with self._lock:
            self._counter += 1
            counter = self._counter

        filename = f"{step_name}_{timestamp}_{counter:03d}.{ext}"
        dst = self.base_dir / filename

        content = json.dumps(data, ensure_ascii=False, indent=2) if is_json else str(data)
        self._atomic_write(dst, content)
        self._log(f"recorded `{step_name}` → {dst}")
        return dst

    def record_subtask(self, sub_id: str, prompt: str, response: Any, from_cache: bool = False) -> Optional[Dict[str, Path]]:
        """为子任务创建目录并写入 prompt/response/meta。"""
        if not ENABLE_PROMPT_DUMP: return None

        task_dir = self.subtasks_dir / sub_id
        task_dir.mkdir(parents=True, exist_ok=True)

        prompt_path = task_dir / "prompt.txt"
        response_path = task_dir / "response.json"
        meta_path = task_dir / "meta.json"

        self._atomic_write(prompt_path, prompt)
        self._atomic_write(response_path, json.dumps(response, ensure_ascii=False, indent=2))
        self._atomic_write(meta_path, json.dumps({"from_cache": from_cache}, ensure_ascii=False, indent=2))

        self._log(f"subtask `{sub_id}` saved (prompt/response/meta)")
        return {"prompt": prompt_path, "response": response_path, "meta": meta_path}

    def _auto_backup_worker(self):
        """自动备份线程，过滤 .tmp 文件并防弹处理 Windows 文件占用。"""
        while not self._stop_backup.is_set():
            try:
                for src in self.base_dir.iterdir():
                    # 🌟 核心修复：过滤 .tmp 文件，防止备份半写的临时文件
                    if src.is_file() and not src.name.endswith(".tmp"):
                        dst = self.backup_dir / src.name
                        try: shutil.copy2(src, dst)
                        except (PermissionError, OSError): pass  # 防弹：跳过被占用的文件
                        
                for sub_dir in self.subtasks_dir.iterdir():
                    if sub_dir.is_dir():
                        bak_sub = self.backup_dir / "subtasks" / sub_dir.name
                        bak_sub.mkdir(parents=True, exist_ok=True)
                        for f in sub_dir.iterdir():
                            if f.is_file() and not f.name.endswith(".tmp"):
                                try: shutil.copy2(f, bak_sub / f.name)
                                except (PermissionError, OSError): pass
                self._log("自动备份完成")
            except Exception as e:
                self._log(f"自动备份出错: {e}", level="WARN")
            finally:
                self._stop_backup.wait(PROMPT_BACKUP_INTERVAL)

    def load_latest(self, step_name: str) -> Optional[Any]:
        """按时间倒排查找最新文件并返回其内容，过滤 .tmp 文件。"""
        # 🌟 核心修复：过滤 .tmp 文件，确保只加载完整的文件
        candidates = sorted(
            [p for p in self.base_dir.glob(f"{step_name}_*") if not p.name.endswith(".tmp")],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not candidates: return None
        
        latest = candidates[0]
        try:
            if latest.suffix == ".json":
                with open(latest, "r", encoding="utf-8") as f: return json.load(f)
            else:
                with open(latest, "r", encoding="utf-8") as f: return f.read()
        except Exception:
            return None

    def shutdown(self):
        self._stop_backup.set()
        self._backup_thread.join(timeout=5)
        self._log("backup thread stopped")
