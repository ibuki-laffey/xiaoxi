#!/usr/bin/env python
# migrations/migrate_permanent_rules_v2.py
# -------------------------------------------------------------
# 用于一次性把已存在的 permanent_rules.db 中的
#   - CURRENT_TIMESTAMP 默认值删除
#   - 为缺失的时间列添加 (TEXT) 并填充当前时间
# -------------------------------------------------------------
import os, sqlite3
from datetime import datetime
from core.config import KNOWLEDGE_BASE_DIR

DB_PATH = os.path.join(KNOWLEDGE_BASE_DIR, "permanent_rules.db")
if not os.path.isfile(DB_PATH):
    raise SystemExit(f"[迁移] 数据库文件不存在：{DB_PATH}")

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

# ---- 检查是否已有时间列（可能已经是 TEXT）----
cur.execute("PRAGMA table_info(permanent_rules);")
cols = {row[1]: row for row in cur.fetchall()}
need_alter = []

if 'created_at' not in cols:
    need_alter.append(('created_at', 'TEXT'))
if 'updated_at' not in cols:
    need_alter.append(('updated_at', 'TEXT'))
if 'last_used_at' not in cols:
    need_alter.append(('last_used_at', 'TEXT'))

for col_name, col_type in need_alter:
    sql = f"ALTER TABLE permanent_rules ADD COLUMN {col_name} {col_type}"
    print(f"[迁移] 添加列 {col_name} ({col_type}) …")
    cur.execute(sql)

# ---- 为所有已有记录补齐时间戳（若为空则写入当前时间）----
now_iso = datetime.now().isoformat()
for col in ('created_at', 'updated_at', 'last_used_at'):
    cur.execute(f"""
        UPDATE permanent_rules
        SET {col} = ?
        WHERE {col} IS NULL OR {col} = ''
    """, (now_iso,))
    print(f"[迁移] 为列 {col} 填充当前时间 ({now_iso})")

conn.commit()
conn.close()
print("[迁移] 完成。请再次启动项目，确认日志中不再出现 “Cannot add a column with non‑constant default”。")
