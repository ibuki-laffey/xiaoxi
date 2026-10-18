# core/topic_manager.py
# ==================== 话题持久化管理器（统一入口） =====================
# 功能：
#   1. 为每个“话题”提供独立的持久化（JSON 文件 或 SQLite）；
#   2. 统一记录当前活跃的话题（current_topic.txt）；
#   3. 支持旧版 conversation_*.json 的自动迁移；
#   4. 所有写入采用原子操作，防止并发竞争；
#   5. 错误统一走 log_callback（若未提供则直接 print）。
#
#   ——— 重要提示 ———
#   本文件的每个 def 必须完整、四空格缩进，切勿删改任何注释文字，
#   否则后续自动化检查会误报缺失。

import json
import os
import threading
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional


# -------------------------------------------------
# 统一路径（使用 core.paths 中的常量）
# -------------------------------------------------
from .paths import (
    DATA_CONFIG_DIR,
    TOPIC_STORAGE_DIR,
    CURRENT_TOPIC_META,
    ensure_path,
)

# -------------------------------------------------
# 可配置的全局开关（在 core/config 中可覆盖）
# -------------------------------------------------
try:  # pragma: no cover
    from .config import (
        # 话题后端：'json'（默认）或 'sqlite'
        TOPIC_BACKEND,
        # 备份相关
        MAX_BACKUP_VERSIONS,
        BACKUP_SUFFIX_FMT,
    )
except Exception:  # 若用户未在 config 中声明，使用默认值
    TOPIC_BACKEND = "json"                        # 默认使用 JSON 文件
    MAX_BACKUP_VERSIONS = 3                       # 保留最近 3 版
    BACKUP_SUFFIX_FMT = "_v{ver}.json"            # 例：topic_x_v1.json

# =================================================
# ① JSON 版实现（保留原有行为并加上备份/回滚）
# =================================================
class _JSONTopicBackend:
    """
    使用单个 JSON 文件保存每个话题历史的后端实现。
    自动完成增量备份（最多保留 MAX_BACKUP_VERSIONS 版）以及
    读取失败时的备份回滚。
    """
    def __init__(self, log_callback=None):
        """
        参数
        ----------
        log_callback: callable(msg: str) | None
            用来把内部日志统一输出到 UI / 文件。若为 None，则直接 ``print``。
        """
        # 统一的外层日志入口
        self._outer_log = log_callback or (lambda m: print(f"[TopicManager] {m}"))
        # 🌟 核心修复：绑定日志回调，兼容 self.log 和 self._log 的调用
        self.log = log_callback if log_callback else print
        self._log = log_callback if log_callback else print
        
        # 🌟 致命修复：补全缺失的线程锁，防止 set_current 崩溃
        self._lock = threading.RLock()
        
        # 🚨 致命清理：彻底删除了原本在这里的 `if TOPIC_BACKEND == "sqlite": ... else: self._backend = _JSONTopicBackend(...)` 
        # JSON 后端就是最底层的实现，绝对不能再套娃实例化自己，否则会导致无限递归(RecursionError)！

    # ---------- 内部日志 ----------
    def _log(self, msg: str, level: str = "INFO"):
        """
        统一日志入口，支持不同日志等级。

        参数
        ----------
        msg : str
            日志内容。
        level : str
            INFO / WARN / ERROR 等（默认 INFO），仅用作前缀标记。
        """
        if level == "ERROR":
            self.log(f"[TopicManager][JSON][ERROR] {msg}")
        elif level == "WARN":
            self.log(f"[TopicManager][JSON][WARN] {msg}")
        else:
            self.log(f"[TopicManager][JSON] {msg}")
    

    # ---------- 当前话题文件 ----------
    def _write_current_topic(self, topic: str) -> bool:
        """
        将当前活跃话题写入 ``current_topic.txt``（原子写入）。
        """
        try:
            tmp_path = str(CURRENT_TOPIC_META) + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(topic.strip())
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, str(CURRENT_TOPIC_META))
            return True
        except Exception as e:  # pragma: no cover
            self._log(f"❌ 写入当前话题文件失败: {e}", level="ERROR")
            return False


    def get_current(self) -> str:
        """
        读取 ``current_topic.txt``，若文件不存在或读取异常返回 ``default``。
        """
        try:
            with open(str(CURRENT_TOPIC_META), "r", encoding="utf-8") as f:
                topic = f.read().strip()
                return topic if topic else "default"
        except Exception as e:  # pragma: no cover
            self._log(
                f"⚠️ 读取当前话题失败，回退到 default: {e}",
                level="WARN",
            )
            return "default"


    def set_current(self, topic: str) -> bool:
        """
        设置当前活跃话题并写入 ``current_topic.txt``（原子写入）。
        """
        if not topic:
            self._log(
                "⚠️ set_current 接收到空话题，保持原有状态",
                level="WARN",
            )
            return False
        with self._lock:
            return self._write_current_topic(topic)


    # ---------- 备份与回滚 ----------
    def _backup_existing(self, file_path: str) -> None:
        base = os.path.splitext(file_path)[0]  # 去掉 .json
        for ver in range(MAX_BACKUP_VERSIONS, 0, -1):
            src = f"{base}{BACKUP_SUFFIX_FMT.format(ver=ver)}"
            if os.path.exists(src):
                if ver == MAX_BACKUP_VERSIONS:
                    try:
                        os.remove(src)
                    except OSError:
                        pass
                else:
                    dst = f"{base}{BACKUP_SUFFIX_FMT.format(ver=ver + 1)}"
                    try:
                        os.replace(src, dst)
                    except OSError:
                        pass
        if os.path.isfile(file_path):
            try:
                os.replace(file_path, f"{base}{BACKUP_SUFFIX_FMT.format(ver=1)}")
            except OSError:
                pass

    def _recover_from_backup(self, file_path: str) -> List[Dict[str, str]]:
        base = os.path.splitext(file_path)[0]
        for ver in range(1, MAX_BACKUP_VERSIONS + 1):
            bak = f"{base}{BACKUP_SUFFIX_FMT.format(ver=ver)}"
            if not os.path.isfile(bak):
                continue
            try:
                with open(bak, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        self._log(
                            f"✅ 从备份 {os.path.basename(bak)} 恢复话题历史"
                        )
                        return data
            except Exception:
                continue
        self._log(
            "⚠️ 所有备份均不可用，返回空历史",
            level="WARN",
        )
        return []

    # ---------- 保存 ----------
    def save(self, topic: str, history: List[Dict[str, str]]) -> bool:
        """
        将 ``history`` 完整写入对应话题的 JSON 文件，并进行备份。
        """
        if not topic:
            self._log(
                "⚠️ 保存时 topic 为 None/空字符串，放弃写入",
                level="WARN",
            )
            return False
        safe_name = f"topic_{topic.replace(os.sep, '_')}.json"
        file_path = os.path.join(str(TOPIC_STORAGE_DIR), safe_name)

        try:
            with self._lock:
                self._backup_existing(file_path)
                tmp_path = file_path + ".tmp"
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(history, f, ensure_ascii=False, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, file_path)
            self._log(
                f"✅ 话题 [{topic}] 的对话已成功保存 ({len(history)} 条) 并完成备份"
            )
            return True
        except Exception as e:  # pragma: no cover
            self._log(f"❌ 保存话题 [{topic}] 失败: {e}", level="ERROR")
            return False

    # ---------- 读取 ----------
    def load(self, topic: str) -> List[Dict[str, str]]:
        """
        读取指定话题的完整历史记录（若文件缺失会尝试从备份恢复）。
        """
        if not topic:
            self._log(
                "⚠️ load 接收到空 topic，返回空列表",
                level="WARN",
            )
            return []
        safe_name = f"topic_{topic.replace(os.sep, '_')}.json"
        file_path = os.path.join(str(TOPIC_STORAGE_DIR), safe_name)

        if not os.path.isfile(file_path):
            self._log(
                f"ℹ️ 话题文件不存在，返回空历史: {file_path}",
                level="INFO",
            )
            return []

        try:
            with self._lock:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            if isinstance(data, list):
                self._log(
                    f"✅ 成功读取话题 [{topic}]（{len(data)} 条记录）"
                )
                return data
            raise ValueError("JSON 结构非 list")
        except Exception as e:  # pragma: no cover
            self._log(
                f"⚠️ 加载话题 [{topic}] 失败: {e}",
                level="WARN",
            )
            return self._recover_from_backup(file_path)


    # ---------- 列举 ----------
    def list_topics(self) -> List[str]:
        """
        列举所有已保存的话题名称（不含 ``topic_`` 前缀与 ``.json`` 后缀）。
        """
        topics = []
        try:
            for fname in os.listdir(str(TOPIC_STORAGE_DIR)):
                if not fname.lower().endswith(".json"):
                    continue
                name_part = fname[:-5]  # 去掉 .json
                if name_part.startswith("topic_"):
                    topics.append(name_part[6:])
            topics.sort()
            self._log(f"✅ 检测到 {len(topics)} 个已保存的话题")
            return topics
        except Exception as e:  # pragma: no cover
            self._log(f"❌ 列举话题失败: {e}", level="ERROR")
            return []

    # ---------- 迁移旧版 ----------
    def migrate_legacy(self) -> None:
        """
        将旧版 ``knowledge_base/conversation_*.json`` 文件迁移至新目录 ``topic_*.json``。
        若新目录已存在同名文件则跳过迁移并删除旧文件。
        """
        old_dir = os.path.join(os.path.dirname(str(TOPIC_STORAGE_DIR)), "knowledge_base")
        if not os.path.isdir(old_dir):
            self._log("ℹ️ 旧版 conversation 目录不存在，跳过迁移")
            return
    
        for fname in os.listdir(old_dir):
            if not fname.lower().startswith("conversation_") or not fname.lower().endswith(".json"):
                continue
            old_path = os.path.join(old_dir, fname)
            name = fname[len("conversation_"):-5]  # 去掉前后缀
            new_name = f"topic_{name}.json"
            new_path = os.path.join(str(TOPIC_STORAGE_DIR), new_name)
    
            if os.path.exists(new_path):
                self._log(f"⚠️ 新目录已存在同名话题文件 {new_name}，跳过迁移")
                try:
                    os.remove(old_path)
                except Exception:
                    pass
                continue
    
            try:
                os.replace(old_path, new_path)
                self._log(f"✅ 迁移旧话题文件 {fname} → {new_name}")
            except Exception as e:  # pragma: no cover
                self._log(f"❌ 迁移旧话题 {fname} 失败: {e}", level="ERROR")
    
    # ---------- 清理临时文件 ----------
    def cleanup_tmp_files(self) -> None:
        """
        删除 ``TOPIC_STORAGE_DIR`` 中残留的 ``*.tmp`` 临时文件，防止磁盘膨胀。
        """
        try:
            for fname in os.listdir(str(TOPIC_STORAGE_DIR)):
                if fname.endswith(".tmp"):
                    try:
                        os.remove(os.path.join(str(TOPIC_STORAGE_DIR), fname))
                    except Exception:
                        pass
            self._log("✅ 清理话题目录临时文件完成")
        except Exception as e:  # pragma: no cover
            self._log(f"⚠️ 清理临时文件时出错: {e}", level="WARN")

# =================================================
# ② SQLite 版实现（跨进程安全）
# =================================================
class _SQLiteTopicBackend:
    """
    基于 SQLite 的话题持久化后端。每条对话是一行记录，支持：
    * 原子事务写入（多进程安全）；
    * 按 topic、role、content、时间戳存储；
    * 按需读取全部或最近 N 条；
    * 话题列表查询与整表删除。
    """
    def __init__(self, log_callback=None):
        """
        初始化 SQLite 话题后端。

        参数
        ----------
        log_callback : callable | None
            日志回调函数，若为 None 则直接 ``print``。
        """
        self.log = log_callback or (lambda m: print(f"[SQLiteTopic] {m}"))
        self._lock = threading.RLock()
        # 确保存储目录存在
        ensure_path(TOPIC_STORAGE_DIR)
        self._db_path = TOPIC_STORAGE_DIR / "topics.db"
        self._ensure_db()


    def _log(self, msg: str, level: str = "INFO"):
        """
        统一日志入口，保持与 JSON 版相同的日志前缀结构。
        """
        if level == "ERROR":
            self.log(f"[TopicManager][SQLite][ERROR] {msg}")
        elif level == "WARN":
            self.log(f"[TopicManager][SQLite][WARN] {msg}")
        else:
            self.log(f"[TopicManager][SQLite] {msg}")


    def _ensure_db(self):
        """首次打开时创建表格（若不存在）"""
        with self._lock, sqlite3.connect(str(self._db_path)) as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic TEXT NOT NULL,
                    role TEXT CHECK(role IN ('user','assistant')) NOT NULL,
                    content TEXT NOT NULL,
                    ts TEXT NOT NULL
                );"""
            )
            conn.commit()
        self._log("✅ SQLite 数据库初始化完成")

    # ---------- 当前话题（使用同 JSON 文件记录） ----------
    def get_current(self) -> str:
        """
        读取 ``current_topic.txt``，若文件不存在返回 ``default``。
        与 JSON 实现保持兼容。
        """
        if not CURRENT_TOPIC_META.is_file():
            # 创建默认文件
            with open(str(CURRENT_TOPIC_META), "w", encoding="utf-8") as f:
                f.write("default")
            return "default"

        try:
            with open(str(CURRENT_TOPIC_META), "r", encoding="utf-8") as f:
                topic = f.read().strip()
                return topic if topic else "default"
        except Exception as e:
            self._log(
                f"⚠️ 读取当前话题失败，fallback to default: {e}",
                level="WARN",
            )
            return "default"

    def set_current(self, topic: str) -> bool:
        """
        将 ``topic`` 写入 ``current_topic.txt``（原子写入），
        与 JSON 版保持行为一致。
        """
        if not topic:
            self._log(
                "⚠️ set_current 接收到空话题，保持原有状态",
               level="WARN",
            )
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

    # ---------- 写入（追加） ----------
    def save(self, topic: str, history: List[Dict[str, str]]) -> bool:
        """
        采用“先清空再批量写入”方式实现完整覆盖。
        由于 SQLite 在同一事务内写入能够确保原子性，
        所以这里不再做文件级备份。
        """
        if not topic:
            self._log(
                "⚠️ 保存时 topic 为 None/空字符串，放弃写入",
                level="WARN",
            )
            return False
        try:
            with self._lock, sqlite3.connect(str(self._db_path)) as conn:
                cur = conn.cursor()
                # 先删除该话题的全部旧记录
                cur.execute("DELETE FROM messages WHERE topic=?", (topic,))
                # 批量插入新记录
                now = datetime.now().isoformat()
                rows = [(topic, rec["role"], rec["content"], now) for rec in history]
                cur.executemany(
                    "INSERT INTO messages (topic, role, content, ts) VALUES (?,?,?,?)",
                    rows,
                )
                conn.commit()
            self._log(f"✅ SQLite 话题 [{topic}] 保存成功（{len(history)} 条）")
            return True
        except Exception as e:
            self._log(f"❌ SQLite 保存话题 [{topic}] 失败: {e}", level="ERROR")
            return False

    # ---------- 读取全部 ----------
    def load(self, topic: str) -> List[Dict[str, str]]:
        """
        读取指定 ``topic`` 的完整历史（按写入顺序）。
        """
        if not topic:
            self._log(
                "⚠️ load 接收到空 topic，返回空列表",
                level="WARN",
            )
            return []

        try:
            with self._lock, sqlite3.connect(str(self._db_path)) as conn:
                cur = conn.execute(
                    "SELECT role, content FROM messages WHERE topic=? ORDER BY id ASC",
                    (topic,),
                )
                rows = [{"role": r[0], "content": r[1]} for r in cur.fetchall()]
            self._log(f"✅ SQLite 读取话题 [{topic}]（{len(rows)} 条）")
            return rows
        except Exception as e:
            self._log(f"❌ SQLite 读取话题 [{topic}] 失败: {e}", level="ERROR")
            return []


    # ---------- 最近 N 条（窗口读取） ----------
    def load_recent(self, topic: str, limit: int = 200) -> List[Dict[str, str]]:
        if not topic:
            return []
        try:
            with self._lock, sqlite3.connect(str(self._db_path)) as conn:
                cur = conn.execute(
                    "SELECT role, content FROM messages WHERE topic=? ORDER BY id DESC LIMIT ?",
                    (topic, limit),
                )
                rows = [{"role": r[0], "content": r[1]} for r in reversed(cur.fetchall())]
            return rows
        except Exception:
            return []

    # ---------- 话题列表 ----------
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

    # ---------- 删除话题 ----------
    def delete_topic(self, topic: str) -> bool:
        """
        删除 ``topic`` 的全部记录，成功返回 True。
        """
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


    # ---------- 迁移旧版（JSON 迁移到 SQLite） ----------
    def migrate_legacy(self) -> None:
        """
        将旧版 JSON 话题文件迁移到 SQLite 表中。
        迁移后会删除原 JSON 文件，保持磁盘整洁。
        """
        # 使用内部 JSON 后端完成文件搬迁（若仍有残余 JSON）
        from .topic_manager import _JSONTopicBackend  # 延迟导入避免循环

        json_backend = _JSONTopicBackend(log_callback=self.log)
        json_backend.migrate_legacy()  # 把旧目录搬到 JSON 目录（如果还有残留）

        # 再把所有 JSON 文件读取后写入 SQLite
        for topic in json_backend.list_topics():
            history = json_backend.load(topic)
            if history:
                self.save(topic, history)
                # 删除已写入的 JSON 文件
                safe_name = f"topic_{topic.replace(os.sep, '_')}.json"
                json_path = os.path.join(str(TOPIC_STORAGE_DIR), safe_name)
                try:
                    os.remove(json_path)
                except Exception:
                    pass
                self._log(f"✅ 将 JSON 话题 [{topic}] 迁移至 SQLite 并删除原文件")


    # ---------- 清理临时文件（与 JSON 版保持兼容） ----------
    def cleanup_tmp_files(self) -> None:
        try:
            for fname in os.listdir(str(TOPIC_STORAGE_DIR)):
                if fname.endswith(".tmp"):
                    try:
                        os.remove(os.path.join(str(TOPIC_STORAGE_DIR), fname))
                    except Exception:
                        pass
            self._log("✅ SQLite 清理临时文件完成")
        except Exception as e:  # pragma: no cover
            self._log(f"⚠️ 清理临时文件时出错: {e}", level="WARN")


# =================================================
# ③ 统一外层包装：TopicManager（向上兼容原 API）
# =================================================
class TopicManager:
    """
    对外统一的 TopicManager，内部根据全局配置 ``TOPIC_BACKEND``
    动态实例化 JSON 或 SQLite 后端，保持原有 API 不变。
    """
    def __init__(self, log_callback=None):
        """
        参数
        ----------
        log_callback: callable(msg: str) | None
            用来把内部日志统一输出到 UI / 文件。若为 None，则直接 ``print``。
        """
        # 统一的外层日志入口
        self._outer_log = log_callback or (lambda m: print(f"[TopicManager] {m}"))

        # 根据配置实例化具体后端
        if TOPIC_BACKEND == "sqlite":
            self._backend = _SQLiteTopicBackend(log_callback=self._outer_log)
        else:   # 默认使用 JSON
            self._backend = _JSONTopicBackend(log_callback=self._outer_log)

    # ---------- 内部转发 ----------
    def _log(self, msg: str, level: str = "INFO"):
        self._outer_log(f"[TopicManager] {msg}")

    # ---------- API 直接委托 ----------
    def get_current(self) -> str:
        return self._backend.get_current()

    def set_current(self, topic: str) -> bool:
        return self._backend.set_current(topic)

    def save(self, topic: str, history: List[Dict[str, str]]) -> bool:
        return self._backend.save(topic, history)

    def load(self, topic: str) -> List[Dict[str, str]]:
        return self._backend.load(topic)

    def list_topics(self) -> List[str]:
        return self._backend.list_topics()

    def migrate_legacy(self) -> None:
        """
        将旧版 JSON 话题迁移到当前后端（如果后端是 SQLite，则会
        把 JSON 内容全部写入 SQLite 并删除原文件；如果后端是 JSON，
        只完成目录结构的搬迁）。
        """
        self._backend.migrate_legacy()

    def cleanup_tmp_files(self) -> None:
        self._backend.cleanup_tmp_files()
