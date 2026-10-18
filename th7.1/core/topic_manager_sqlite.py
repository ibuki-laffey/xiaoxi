import os
import sqlite3
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import List, Dict

from .paths import TOPIC_STORAGE_DIR, CURRENT_TOPIC_META, ensure_path

class SQLiteTopicManager:
    """基于 SQLite 的话题管理（可替代 JSON 版）。"""

    def __init__(self, log_callback=None):
        self.log = log_callback or (lambda m: print(f"[SQLiteTopic] {m}"))
        self._lock = RLock()
        ensure_path(TOPIC_STORAGE_DIR)
        self._db_path = TOPIC_STORAGE_DIR / "topics.db"
        self._ensure_db()

    def _log(self, msg: str, level: str = "INFO"):
        if level == "ERROR": self.log(f"[TopicManager][SQLite][ERROR] {msg}")
        elif level == "WARN": self.log(f"[TopicManager][SQLite][WARN] {msg}")
        else: self.log(f"[TopicManager][SQLite] {msg}")

    def _ensure_db(self):
        with self._lock, sqlite3.connect(str(self._db_path)) as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT, topic TEXT NOT NULL,
                role TEXT CHECK(role IN ('user','assistant')) NOT NULL,
                content TEXT NOT NULL, ts TEXT NOT NULL);""")
            conn.commit()
        self._log("✅ SQLite 数据库初始化完成")

    def get_current(self) -> str:
        if not CURRENT_TOPIC_META.is_file():
            # 🌟 核心修复：增加 flush 和 fsync
            with open(str(CURRENT_TOPIC_META), "w", encoding="utf-8") as f:
                f.write("default")
                f.flush()
                os.fsync(f.fileno())
            return "default"
        try:
            with open(str(CURRENT_TOPIC_META), "r", encoding="utf-8") as f:
                topic = f.read().strip()
                return topic if topic else "default"
        except Exception as e:
            self._log(f"⚠️ 读取当前话题失败，fallback to default: {e}", level="WARN")
            return "default"

    def set_current(self, topic: str) -> bool:
        if not topic:
            self._log("⚠️ set_current 接收到空话题，保持原有状态", level="WARN")
            return False
        try:
            tmp_path = str(CURRENT_TOPIC_META) + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(topic.strip())
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, str(CURRENT_TOPIC_META))
            return True
        except Exception as e:
            self._log(f"❌ 写入当前话题文件失败: {e}", level="ERROR")
            return False

    def save(self, topic: str, history: List[Dict[str, str]]) -> bool:
        if not topic:
            self._log("⚠️ 保存时 topic 为 None/空字符串，放弃写入", level="WARN")
            return False
        try:
            with self._lock, sqlite3.connect(str(self._db_path)) as conn:
                cur = conn.cursor()
                cur.execute("DELETE FROM messages WHERE topic=?", (topic,))
                # 🌟 核心修复：废弃 datetime.utcnow()，防止 Python 3.12+ 弃用警告
                now = datetime.now().isoformat()
                rows = [(topic, rec["role"], rec["content"], now) for rec in history]
                cur.executemany("INSERT INTO messages (topic, role, content, ts) VALUES (?,?,?,?)", rows)
                conn.commit()
            self._log(f"✅ SQLite 话题 [{topic}] 保存成功（{len(history)} 条）")
            return True
        except Exception as e:
            self._log(f"❌ SQLite 保存话题 [{topic}] 失败: {e}", level="ERROR")
            return False

    def load(self, topic: str) -> List[Dict[str, str]]:
        if not topic:
            self._log("⚠️ load 接收到空 topic，返回空列表", level="WARN")
            return []
        try:
            with self._lock, sqlite3.connect(str(self._db_path)) as conn:
                cur = conn.execute("SELECT role, content FROM messages WHERE topic=? ORDER BY id ASC", (topic,))
                rows = [{"role": r[0], "content": r[1]} for r in cur.fetchall()]
            self._log(f"✅ SQLite 读取话题 [{topic}]（{len(rows)} 条）")
            return rows
        except Exception as e:
            self._log(f"❌ SQLite 读取话题 [{topic}] 失败: {e}", level="ERROR")
            return []

    def load_recent(self, topic: str, limit: int = 200) -> List[Dict[str, str]]:
        if not topic: return []
        try:
            with self._lock, sqlite3.connect(str(self._db_path)) as conn:
                cur = conn.execute("SELECT role, content FROM messages WHERE topic=? ORDER BY id DESC LIMIT ?", (topic, limit))
                rows = [{"role": r[0], "content": r[1]} for r in reversed(cur.fetchall())]
            return rows
        except Exception: return []

    def list_topics(self) -> List[str]:
        try:
            with self._lock, sqlite3.connect(str(self._db_path)) as conn:
                cur = conn.execute("SELECT DISTINCT topic FROM messages")
                topics = sorted([r[0] for r in cur.fetchall()])
            self._log(f"✅ SQLite 检测到 {len(topics)} 个话题")
            return topics
        except Exception as e:
            self._log(f"❌ SQLite 列举话题失败: {e}", level="ERROR")
            return []

    def delete_topic(self, topic: str) -> bool:
        if not topic:
            self._log("⚠️ delete_topic 接收到空 topic", level="WARN")
            return False
        try:
            with self._lock, sqlite3.connect(str(self._db_path)) as conn:
                conn.execute("DELETE FROM messages WHERE topic=?", (topic,))
                conn.commit()
            self._log(f"✅ SQLite 删除话题 [{topic}] 的全部记录")
            return True
        except Exception as e:
            self._log(f"❌ SQLite 删除话题 [{topic}] 失败: {e}", level="ERROR")
            return False

    def migrate_legacy(self) -> None:
        # 🌟 核心修复：增加 ImportError 防弹，防止旧模块被删除时崩溃
        try:
            from .topic_manager import _JSONTopicBackend
        except ImportError:
            self._log("⚠️ 未找到旧版 _JSONTopicBackend，跳过迁移", level="WARN")
            return

        json_backend = _JSONTopicBackend(log_callback=self.log)
        json_backend.migrate_legacy()

        for topic in json_backend.list_topics():
            history = json_backend.load(topic)
            if history:
                self.save(topic, history)
                safe_name = f"topic_{topic.replace(os.sep, '_')}.json"
                json_path = os.path.join(str(TOPIC_STORAGE_DIR), safe_name)
                try: os.remove(json_path)
                except Exception: pass
                self._log(f"✅ 将 JSON 话题 [{topic}] 迁移至 SQLite 并删除原文件")

    def cleanup_tmp_files(self) -> None:
        try:
            for entry in TOPIC_STORAGE_DIR.iterdir():
                if entry.is_file() and entry.name.endswith(".tmp"):
                    try: entry.unlink()
                    except Exception: pass
            self._log("✅ SQLite 清理临时文件完成")
        except Exception as e:  # 🌟 核心修复：改为 except Exception
            self._log(f"⚠️ 清理临时文件时出错: {e}", level="WARN")
