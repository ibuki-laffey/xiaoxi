# core/permanent_memory.py
# ==================== 永久记忆模块 ====================

import os
import sqlite3
import json
import hashlib
import re
import threading
from datetime import datetime
from typing import List, Dict, Any, Optional

from .config import (
    KNOWLEDGE_BASE_DIR,
    get_db_connection,
    close_db_connection,
)

# ----------------------------------------------------------------------
# PermanentMemory
# ----------------------------------------------------------------------
class PermanentMemory:
    """
    永久记忆模块

    * 存储用户设定的长期规则（跨话题、跨会话持久化）  
    * 支持增删改查、自动统计、备份/恢复、规则冲突检测等高级功能  
    * 采用统一的 SQLite 连接管理、线程锁，确保多线程环境下安全操作  
    * 在模块首次创建时会 **自检表结构**，若检测到缺失列会使用 `ALTER TABLE` 自动补齐
    """

    # ---------- 初始化 ----------
    def __init__(self, log_callback=None):
        """
        参数
        ----------
        log_callback : Callable[[str], None] | None
            UI/CLI 用来接收日志的回调函数
        """
        self.log_callback = log_callback
        self.db_path = os.path.join(KNOWLEDGE_BASE_DIR, "permanent_rules.db")
        os.makedirs(KNOWLEDGE_BASE_DIR, exist_ok=True)

        # 用于多线程安全的锁
        self._db_lock = threading.Lock()

        # 创建/校验表结构（已经去掉时间列的 DEFAULT）
        self._init_database()
        self._log("✅ 永久记忆模块已初始化")

    # ---------- 日志 ----------
    def _log(self, msg: str, level: str = "INFO") -> None:
        """
        统一日志输出

        若外部传入了 ``log_callback`` 则直接回调，否则静默。
        level 参数仅用于兼容旧代码的调用方式，实际回调的内容保持不变。
        """
        if self.log_callback:
            timestamp = datetime.now().isoformat()
            self.log_callback(f"[{timestamp}] [{level}] {msg}")

    # ---------- 数据库 & 表结构 ----------
    def _init_database(self) -> None:
        """创建表（若不存在）并确保最新的列结构完整（已去掉时间默认值）"""
        try:
            conn = get_db_connection(self.db_path, timeout=30)
            cursor = conn.cursor()

            # 1️⃣ 创建表（如果不存在）——时间列不再使用 DEFAULT CURRENT_TIMESTAMP
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS permanent_rules (
                    id TEXT PRIMARY KEY,
                    rule TEXT NOT NULL,
                    source TEXT,
                    category TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    usage_count INTEGER DEFAULT 0,
                    last_used_at TEXT,
                    is_active INTEGER DEFAULT 1,
                    modified_by_user INTEGER DEFAULT 0
                )
                """
            )

            # 2️⃣ 创建常用索引（如果不存在）
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_category ON permanent_rules(category)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_active ON permanent_rules(is_active)")
            conn.commit()

            # 3️⃣ 检查/迁移旧表结构（避免 “Cannot add a column with non‑constant default” 错误）
            self._ensure_table_schema(conn)

        except sqlite3.OperationalError as e:
            self._log(f"❌ 数据库初始化失败：{e}", level="ERROR")
        except Exception as e:  # pragma: no cover
            self._log(f"❌ 数据库初始化异常：{e}", level="ERROR")
        finally:
            # 交给 config 的统一清理器，保证程序退出时关闭
            close_db_connection(self.db_path)

    def _ensure_table_schema(self, conn: sqlite3.Connection) -> None:
        """
        检查 ``permanent_rules`` 表的列是否完整。

        若检测到缺失列，则使用 ``ALTER TABLE ... ADD COLUMN`` 补齐。
        SQLite 只允许在 ``ALTER`` 时使用 **常量** 默认值，故对时间列不设默认，
        只在插入/更新时显式写入时间戳。
        """
        needed_columns = {
            "id": "TEXT PRIMARY KEY",
            "rule": "TEXT NOT NULL",
            "source": "TEXT",
            "category": "TEXT",
            "created_at": "TEXT",
            "updated_at": "TEXT",
            "usage_count": "INTEGER DEFAULT 0",
            "last_used_at": "TEXT",
            "is_active": "INTEGER DEFAULT 1",
            "modified_by_user": "INTEGER DEFAULT 0",
        }

        with self._db_lock:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(permanent_rules)")
            existing_cols = {row[1] for row in cursor.fetchall()}

            for col_name, col_def in needed_columns.items():
                if col_name in existing_cols:
                    continue

                # 只能在 ALTER 时使用恒定默认值（整数/布尔），时间列不设默认
                default_clause = ""
                if "DEFAULT" in col_def:
                    parts = col_def.split("DEFAULT")
                    col_type = parts[0].strip()
                    default_val = parts[1].strip()
                    if default_val.replace(".", "", 1).isdigit() or default_val in ("1", "0"):
                        default_clause = f"DEFAULT {default_val}"
                    col_def = col_type
                try:
                    cursor.execute(
                        f"ALTER TABLE permanent_rules ADD COLUMN {col_name} {col_def} {default_clause}"
                    )
                    self._log(f"✅ 为 permanent_rules 表添加缺失列 `{col_name}`")
                except Exception as e:  # pragma: no cover
                    self._log(
                        f"⚠️ 添加列 `{col_name}` 失败（可能已存在或不兼容）：{e}",
                        level="WARN",
                    )
            conn.commit()

    # ----------------------------------------------------------------------
    # 基础 CRUD
    # ----------------------------------------------------------------------
    def add_rule(self, rule_text: str, source: str = "user", category: str = "general") -> Dict[str, Any]:
        """向表中插入一条永久规则（显式写入时间戳）"""
        try:
            rule_id = hashlib.md5(f"{rule_text}{datetime.now()}".encode()).hexdigest()[:16]

            with self._db_lock:
                conn = get_db_connection(self.db_path, timeout=30)
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO permanent_rules
                    (id, rule, source, category, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        rule_id,
                        rule_text,
                        source,
                        category,
                        datetime.now().isoformat(),
                        datetime.now().isoformat(),
                    ),
                )
                conn.commit()
                close_db_connection(self.db_path)

            self._log(f"📝 添加永久规则：{rule_text[:50]}...")
            # 自动处理“默认保存路径”之类的指令
            if "默认保存" in rule_text or "默认目录" in rule_text:
                m = re.search(r"([A-Z]:\\[^\\\s]+)", rule_text)
                if m:
                    path = m.group(1)
                    default_path_file = os.path.join(KNOWLEDGE_BASE_DIR, "default_save_path.txt")
                    with open(default_path_file, "w", encoding="utf-8") as f:
                        f.write(path)
                    self._log(f"🔧 已把默认保存路径写入系统：{path}")

            return {"success": True, "rule_id": rule_id}
        except Exception as e:  # pragma: no cover
            self._log(f"❌ 添加规则失败：{e}", level="ERROR")
            return {"success": False, "error": str(e)}

    # ----------------------------------------------------------------------
    def identify_rules_from_text(self, text: str) -> List[str]:
        """基于正则从用户对话中抽取可能的永久规则"""
        patterns = [
            r"(?:记住 | 牢记 | 务必 | 必须 | 一定).{0,50}(?:要 | 得 | 需要).{0,100}",
            r"(?:规则 | 规定 | 要求 | 原则).{0,50}(?:是 | 为 | 叫).{0,100}",
            r"(?:以后 | 今后 | 从此 | 永远).{0,50}(?:都 | 要 | 必须).{0,100}",
            r"(?:不要 | 不能 | 禁止 | 不准).{0,50}(?:做 | 说 | 使用).{0,100}",
            r"(?:我希望 | 我想要 | 我需要).{0,50}(?:你 | 助手).{0,100}",
            r"(?:每次 | 所有 | 任何).{0,50}(?:都 | 要 | 必须).{0,100}",
        ]

        rules = []
        for pattern in patterns:
            matches = re.findall(pattern, text, flags=re.IGNORECASE)
            for match in matches:
                if 10 < len(match) < 500:
                    rules.append(match.strip())
        # 去重
        rules = list(set(rules))

        if rules:
            self._log(f"🔍 从文本中识别出 {len(rules)} 条规则")
        return rules

    # ----------------------------------------------------------------------
    def update_rule(self, rule_id: str, new_rule_text: str) -> Dict[str, Any]:
        """更新已有规则的文本内容（显式写入 updated_at）"""
        try:
            with self._db_lock:
                conn = get_db_connection(self.db_path, timeout=30)
                cursor = conn.cursor()
                cursor.execute(
                    """
                    UPDATE permanent_rules
                    SET rule = ?, updated_at = ?, modified_by_user = 1
                    WHERE id = ?
                    """,
                    (new_rule_text, datetime.now().isoformat(), rule_id),
                )
                conn.commit()
                close_db_connection(self.db_path)

            self._log(f"✏️ 更新规则：{rule_id}")
            return {"success": True}
        except Exception as e:  # pragma: no cover
            self._log(f"❌ 更新规则失败：{e}", level="ERROR")
            return {"success": False, "error": str(e)}

    # ----------------------------------------------------------------------
    def delete_rule(self, rule_id: str) -> Dict[str, Any]:
        """物理删除一条规则"""
        try:
            with self._db_lock:
                conn = get_db_connection(self.db_path, timeout=30)
                cursor = conn.cursor()
                cursor.execute("DELETE FROM permanent_rules WHERE id = ?", (rule_id,))
                conn.commit()
                close_db_connection(self.db_path)

            self._log(f"🗑️ 删除规则：{rule_id}")
            return {"success": True}
        except Exception as e:  # pragma: no cover
            self._log(f"❌ 删除规则失败：{e}", level="ERROR")
            return {"success": False, "error": str(e)}

    # ----------------------------------------------------------------------
    def deactivate_rule(self, rule_id: str) -> Dict[str, Any]:
        """将规则标记为不活跃（软删除）"""
        try:
            with self._db_lock:
                conn = get_db_connection(self.db_path, timeout=30)
                cursor = conn.cursor()
                cursor.execute(
                    """
                    UPDATE permanent_rules
                    SET is_active = 0, updated_at = ?
                    WHERE id = ?
                    """,
                    (datetime.now().isoformat(), rule_id),
                )
                conn.commit()
                close_db_connection(self.db_path)

            self._log(f"⏸️ 停用规则：{rule_id}")
            return {"success": True}
        except Exception as e:  # pragma: no cover
            self._log(f"❌ 停用规则失败：{e}", level="ERROR")
            return {"success": False, "error": str(e)}

    # ----------------------------------------------------------------------
    def get_active_rules(self, category: Optional[str] = None) -> List[Dict[str, Any]]:
        """查询所有处于激活状态的规则（可选按分类过滤）"""
        try:
            with self._db_lock:
                conn = get_db_connection(self.db_path, timeout=30)
                cursor = conn.cursor()
                if category:
                    cursor.execute(
                        """
                        SELECT id, rule, source, category, created_at, usage_count
                        FROM permanent_rules
                        WHERE is_active = 1 AND category = ?
                        ORDER BY created_at DESC
                        """,
                        (category,),
                    )
                else:
                    cursor.execute(
                        """
                        SELECT id, rule, source, category, created_at, usage_count
                        FROM permanent_rules
                        WHERE is_active = 1
                        ORDER BY created_at DESC
                        """
                    )
                rows = cursor.fetchall()
                close_db_connection(self.db_path)

            rules = []
            for row in rows:
                rules.append(
                    {
                        "id": row[0],
                        "rule": row[1],
                        "source": row[2],
                        "category": row[3],
                        "created_at": row[4],
                        "usage_count": row[5],
                    }
                )
            return rules
        except Exception as e:  # pragma: no cover
            self._log(f"❌ 获取活跃规则失败：{e}", level="ERROR")
            return []

    # ----------------------------------------------------------------------
    def get_rules_for_context(self, user_input: str) -> List[Dict[str, Any]]:
        """
        根据用户输入返回可能相关的规则并计数使用次数。

        规则匹配策略（轻量）：
            * 规则文本中出现 2 词以上且词长 > 2 → 认为匹配
            * “general” 分类的规则默认返回
        """
        all_rules = self.get_active_rules()
        relevant_rules = []

        user_lower = user_input.lower()
        for rule in all_rules:
            rule_lower = rule["rule"].lower()

            words = rule_lower.split()
            match_cnt = sum(1 for w in words if len(w) > 2 and w in user_lower)
            if match_cnt >= 2 or rule["category"] == "general":
                relevant_rules.append(rule)
                self._increment_usage(rule["id"])

        return relevant_rules

    # ----------------------------------------------------------------------
    def _increment_usage(self, rule_id: str) -> None:
        """每次被检索到后递增使用计数（显式写入时间戳）"""
        try:
            with self._db_lock:
                conn = get_db_connection(self.db_path, timeout=30)
                cursor = conn.cursor()
                cursor.execute(
                    """
                    UPDATE permanent_rules
                    SET usage_count = usage_count + 1, last_used_at = ?
                    WHERE id = ?
                    """,
                    (datetime.now().isoformat(), rule_id),
                )
                conn.commit()
                close_db_connection(self.db_path)
        except Exception as e:  # pragma: no cover
            self._log(f"⚠️ 增加使用计数失败：{e}", level="WARN")

    # ----------------------------------------------------------------------
    # …（文件其余部分保持原样，只是把所有写入时间的地方改为显式写入 datetime，保持一致）…
    # ----------------------------------------------------------------------
    def search_rules(self, keyword: str) -> List[Dict[str, Any]]:
        """模糊搜索所有激活规则（LIKE %keyword%）"""
        try:
            with self._db_lock:
                conn = get_db_connection(self.db_path, timeout=30)
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT id, rule, source, category, created_at
                    FROM permanent_rules
                    WHERE rule LIKE ? AND is_active = 1
                    ORDER BY created_at DESC
                    """,
                    (f"%{keyword}%",),
                )
                rows = cursor.fetchall()
                close_db_connection(self.db_path)

            return [
                {
                    "id": r[0],
                    "rule": r[1],
                    "source": r[2],
                    "category": r[3],
                    "created_at": r[4],
                }
                for r in rows
            ]
        except Exception as e:  # pragma: no cover
            self._log(f"❌ 搜索规则失败：{e}", level="ERROR")
            return []

    # ----------------------------------------------------------------------
    def export_rules(self, filepath: str) -> Dict[str, Any]:
        """把所有激活规则导出为 JSON 文件"""
        try:
            rules = self.get_active_rules()
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(rules, f, indent=2, ensure_ascii=False)

            self._log(f"📤 导出 {len(rules)} 条规则到：{filepath}")
            return {"success": True, "count": len(rules)}
        except Exception as e:  # pragma: no cover
            self._log(f"❌ 导出规则失败：{e}", level="ERROR")
            return {"success": False, "error": str(e)}

    # ----------------------------------------------------------------------
    def import_rules(self, filepath: str) -> Dict[str, Any]:
        """从 JSON 文件批量导入规则（重复的 rule_id 会被当作普通新建）"""
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                rules = json.load(f)

            count = 0
            for rule in rules:
                self.add_rule(
                    rule.get("rule", ""),
                    source=rule.get("source", "imported"),
                    category=rule.get("category", "general"),
                )
                count += 1

            self._log(f"📥 导入 {count} 条规则")
            return {"success": True, "count": count}
        except Exception as e:  # pragma: no cover
            self._log(f"❌ 导入规则失败：{e}", level="ERROR")
            return {"success": False, "error": str(e)}

    # ----------------------------------------------------------------------
    def get_statistics(self) -> Dict[str, Any]:
        """返回规则数量、分类统计、累计使用次数等概要信息"""
        try:
            with self._db_lock:
                conn = get_db_connection(self.db_path, timeout=30)
                cursor = conn.cursor()

                cursor.execute(
                    "SELECT COUNT(*) FROM permanent_rules WHERE is_active = 1"
                )
                total = cursor.fetchone()[0]

                cursor.execute(
                    "SELECT category, COUNT(*) FROM permanent_rules WHERE is_active = 1 GROUP BY category"
                )
                by_category = dict(cursor.fetchall())

                cursor.execute(
                    "SELECT SUM(usage_count) FROM permanent_rules WHERE is_active = 1"
                )
                total_usage = cursor.fetchone()[0] or 0

                close_db_connection(self.db_path)

            return {
                "total_rules": total,
                "by_category": by_category,
                "total_usage": total_usage,
            }
        except Exception as e:  # pragma: no cover
            self._log(f"❌ 获取统计失败：{e}", level="ERROR")
            return {"total_rules": 0, "by_category": {}, "total_usage": 0}

    # ----------------------------------------------------------------------
    def cleanup_old_rules(self, days: int = 365) -> Dict[str, Any]:
        """
        将长期未被使用且未被触发的规则自动停用。

        参数
        ----
        days : int
            “未使用” 的阈值天数（默认 365 天）
        """
        try:
            cutoff = datetime.now().replace(year=datetime.now().year - (days // 365))
            with self._db_lock:
                conn = get_db_connection(self.db_path, timeout=30)
                cursor = conn.cursor()
                cursor.execute(
                    """
                    UPDATE permanent_rules
                    SET is_active = 0
                    WHERE (last_used_at < ? OR last_used_at IS NULL)
                      AND usage_count = 0
                    """,
                    (cutoff.isoformat(),),
                )
                deleted = cursor.rowcount
                conn.commit()
                close_db_connection(self.db_path)

            if deleted:
                self._log(f"🧹 清理了 {deleted} 条长期未使用的规则")
            return {"success": True, "deleted": deleted}
        except Exception as e:  # pragma: no cover
            self._log(f"❌ 清理旧规则失败：{e}", level="ERROR")
            return {"success": False, "error": str(e)}

    # ----------------------------------------------------------------------
    def apply_rules_to_context(self, user_input: str, ai_response: str) -> str:
        """
        将匹配到的规则强制应用到 AI 的输出上（冲突检测 & 自动修正）。
        若未匹配到任何规则则直接返回原始 ``ai_response``。
        """
        relevant_rules = self.get_rules_for_context(user_input)
        if not relevant_rules:
            return ai_response

        violations = self._check_violations(ai_response, relevant_rules)
        if violations:
            self._log(f"⚠️ 发现 {len(violations)} 条规则被违背", level="WARN")
            return self._correct_response(ai_response, violations)
        return ai_response

    # ----------------------------------------------------------------------
    def _check_violations(self, response: str, rules: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """检测回复是否违背了禁止类或必需类规则（简易实现）"""
        violations = []
        resp_low = response.lower()

        for rule in rules:
            r_low = rule["rule"].lower()

            # 禁止类规则（如 “不要 ...”）
            if any(word in r_low for word in ["不要", "不能", "禁止", "不准", "别"]):
                forbid_pat = r"(?:不要|不能|禁止|不准|别).{0,20}(?:做|说|使用|提).{0,20}"
                for m in re.findall(forbid_pat, r_low):
                    if m in resp_low:
                        violations.append(
                            {
                                "rule_id": rule["id"],
                                "rule": rule["rule"],
                                "violation": m,
                            }
                        )

            # 必须类规则（如 “必须 …”）
            if any(word in r_low for word in ["必须", "务必", "一定", "要"]):
                must_pat = r"(?:必须|务必|一定|要).{0,20}(?:做|说|使用|包括).{0,50}"
                for m in re.findall(must_pat, r_low):
                    if m not in resp_low:
                        violations.append(
                            {
                                "rule_id": rule["id"],
                                "rule": rule["rule"],
                                "violation": f"缺少必需内容：{m}",
                            }
                        )
        return violations

    # ----------------------------------------------------------------------
    def _correct_response(self, response: str, violations: List[Dict[str, Any]]) -> str:
        """在返回文本末尾追加简易的规则修正说明（可自行扩展为更智能的编辑）"""
        corrected = response
        for v in violations:
            corrected += f"\n\n【根据规则修正】{v['rule']}"
        self._log("✏️ 已根据规则修正回复", level="INFO")
        return corrected

    # ----------------------------------------------------------------------
    def auto_update_rules_from_feedback(self, user_feedback: str, original_rule_id: str) -> Dict[str, Any]:
        """
        根据用户的反馈（如 “改成…”“删除这条规则”）自动更新或删除对应规则。
        """
        fb_low = user_feedback.lower()
        # 若表达“改成 …”
        if any(w in fb_low for w in ["不对", "错误", "错了", "修改", "改一下"]):
            self._log(f"📝 用户要求修改规则：{original_rule_id}")
            new_pat = r"(?:应该是|改成|改为|变成).{0,100}"
            m = re.search(new_pat, user_feedback)
            if m:
                new_rule = m.group(0).strip()
                return self.update_rule(original_rule_id, new_rule)
            else:
                # 没有提供新内容则直接停用
                return self.deactivate_rule(original_rule_id)

        # 若表达删除
        if any(w in fb_low for w in ["删除", "去掉", "不要这个"]):
            self.delete_rule(original_rule_id)
            self._log(f"🗑️ 用户要求删除规则：{original_rule_id}")
            return {"success": True, "action": "deleted"}

        return {"success": False, "action": "none"}

    # ----------------------------------------------------------------------
    def merge_duplicate_rules(self) -> Dict[str, Any]:
        """
        通过规则内容的 MD5 哈希检测重复规则，保留最早创建的那条并停用其余。
        """
        rules = self.get_active_rules()
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for rule in rules:
            h = hashlib.md5(rule["rule"].encode()).hexdigest()
            groups.setdefault(h, []).append(rule)

        merged = 0
        for h, dupes in groups.items():
            if len(dupes) <= 1:
                continue
            # 按创建时间升序保留最早的一条
            dupes.sort(key=lambda x: x["created_at"])
            keep = dupes[0]
            for dup in dupes[1:]:
                self.deactivate_rule(dup["id"])
                merged += 1

        if merged:
            self._log(f"🔀 合并了 {merged} 条重复规则")
        return {"success": True, "merged": merged}

    # ----------------------------------------------------------------------
    def validate_rules(self) -> Dict[str, Any]:
        """
        简单校验：长度、空值、是否含有冲突关键词（must & forbid 同时出现）。
        """
        rules = self.get_active_rules()
        invalid = []

        for r in rules:
            txt = r["rule"].strip()
            if not txt or len(txt) < 5:
                invalid.append(r["id"])
                continue
            if len(txt) > 1000:
                invalid.append(r["id"])
                continue
            low = txt.lower()
            if "必须" in low and "不要" in low:
                self._log(f"⚠️ 规则可能冲突：{r['id']}", level="WARN")
                invalid.append(r["id"])

        for rid in invalid:
            self.deactivate_rule(rid)

        if invalid:
            self._log(f"⚠️ 已停用 {len(invalid)} 条无效规则", level="WARN")
        return {
            "success": True,
            "total": len(rules),
            "invalid": len(invalid),
            "invalid_ids": invalid,
        }

    # ----------------------------------------------------------------------
    def get_context_string(self) -> str:
        """返回所有激活规则的可直接拼进去的文本块（用于 Prompt）"""
        rules = self.get_active_rules()
        if not rules:
            return "\n【永久规则】无"

        ctx = "\n【永久规则】（必须遵守）\n"
        for i, r in enumerate(rules, 1):
            ctx += f"{i}. {r['rule']}\n"
        ctx += f"（共 {len(rules)} 条规则）\n"
        return ctx

    # ----------------------------------------------------------------------
    def analyze_rule_usage(self) -> Dict[str, Any]:
        """统计规则的使用频次，区分未使用、常用两类"""
        rules = self.get_active_rules()
        rules.sort(key=lambda x: x["usage_count"], reverse=True)

        unused = [r for r in rules if r["usage_count"] == 0]
        high = [r for r in rules if r["usage_count"] >= 10]

        analysis = {
            "total": len(rules),
            "unused": len(unused),
            "high_usage": len(high),
            "unused_rules": unused[:5],
            "high_usage_rules": high[:5],
        }
        self._log(
            f"📊 规则使用分析：{analysis['unused']} 条未使用，{analysis['high_usage']} 条高频使用",
            level="INFO",
        )
        return analysis

    # ----------------------------------------------------------------------
    def suggest_rule_improvements(self) -> Dict[str, Any]:
        """
        基于 ``analyze_rule_usage`` 与相似度检测给出可执行的改进建议。
        """
        analysis = self.analyze_rule_usage()
        suggestions = []

        if analysis["unused"]:
            suggestions.append(
                f"考虑删除或合并 {analysis['unused']} 条长期未使用的规则"
            )
        if analysis["high_usage"]:
            suggestions.append(
                f"{analysis['high_usage']} 条规则使用频繁，可在文档中强调或细化"
            )

        # 简单相似度检测（相同分类、内容交叉 > 0.7）
        similar_cnt = 0
        rules = self.get_active_rules()
        for i, r1 in enumerate(rules):
            words1 = set(r1["rule"].split())
            for r2 in rules[i + 1 :]:
                if r1["category"] != r2["category"]:
                    continue
                words2 = set(r2["rule"].split())
                if not words1 or not words2:
                    continue
                sim = len(words1 & words2) / len(words1 | words2)
                if sim > 0.7:
                    similar_cnt += 1
        if similar_cnt:
            suggestions.append(f"发现 {similar_cnt} 对相似规则，建议合并")

        return {"success": True, "suggestions": suggestions, "analysis": analysis}

    # ----------------------------------------------------------------------
    def backup_rules(self, backup_path: Optional[str] = None) -> Dict[str, Any]:
        """将规则数据库文件复制为备份（可自定义目标路径）"""
        try:
            if not backup_path:
                backup_dir = os.path.join(KNOWLEDGE_BASE_DIR, "backups")
                os.makedirs(backup_dir, exist_ok=True)
                backup_path = os.path.join(
                    backup_dir,
                    f"permanent_rules_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db",
                )
            import shutil

            shutil.copy2(self.db_path, backup_path)
            self._log(f"💾 规则数据库已备份：{backup_path}")
            return {"success": True, "backup_path": backup_path}
        except Exception as e:  # pragma: no cover
            self._log(f"❌ 备份规则失败：{e}", level="ERROR")
            return {"success": False, "error": str(e)}

    # ----------------------------------------------------------------------
    def restore_rules(self, backup_path: str) -> Dict[str, Any]:
        """从之前的备份文件恢复规则数据库（覆盖现有数据库）"""
        if not os.path.isfile(backup_path):
            return {"success": False, "message": "备份文件不存在"}

        try:
            import shutil

            shutil.copy2(backup_path, self.db_path)
            self._log(f"🔄 规则数据库已恢复：{backup_path}")
            return {"success": True}
        except Exception as e:  # pragma: no cover
            self._log(f"❌ 恢复规则失败：{e}", level="ERROR")
            return {"success": False, "error": str(e)}

    # ----------------------------------------------------------------------
    def reset_all_rules(self, confirm: bool = False) -> Dict[str, Any]:
        """
        完全清空所有规则（危险操作），在执行前会自动备份一次。
        """
        if not confirm:
            return {
                "success": False,
                "message": "需要明确传入 confirm=True 才会执行清空",
            }

        # 先备份
        self.backup_rules()
        try:
            with self._db_lock:
                conn = get_db_connection(self.db_path, timeout=30)
                cursor = conn.cursor()
                cursor.execute("DELETE FROM permanent_rules")
                conn.commit()
                close_db_connection(self.db_path)

            self._log("⚠️ 所有规则已重置（已创建备份）")
            return {"success": True, "message": "所有规则已重置，备份已创建"}
        except Exception as e:  # pragma: no cover
            self._log(f"❌ 重置规则失败：{e}", level="ERROR")
            return {"success": False, "error": str(e)}
