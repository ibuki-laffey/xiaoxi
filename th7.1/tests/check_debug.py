# check_debug.py
import os, json, sqlite3, sys
from pathlib import Path
from datetime import datetime

# 1️⃣ 检查 PermanentMemory DB 表结构
from core.config import KNOWLEDGE_BASE_DIR
db_path = os.path.join(KNOWLEDGE_BASE_DIR, "permanent_rules.db")
print("\n--- PermanentMemory DB 检查 ---")
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(permanent_rules);")
    rows = cur.fetchall()
    for r in rows:
        print(r)  # (cid, name, type, notnull, dflt_value, pk)
    conn.close()
else:
    print("DB 不存在:", db_path)

# 2️⃣ 检查 StateManager 话题恢复
from core.state_manager import StateManager
print("\n--- StateManager 话题检查 ---")
sm = StateManager()
topic = sm.load_last_active_topic()
print("last topic ->", repr(topic))

# 3️⃣ 检查 AIController 意图分析阈值
from core.ai_controller import AIController
from core.config import USE_ONLY_AI_INTENT
print("\n--- AIController 意图阈值检查 ---")
print("USE_ONLY_AI_INTENT =", USE_ONLY_AI_INTENT)

# 4️⃣ 检查日志回调是否已是 JSON
def dummy_log(msg):
    print("LOG CALLBACK RECEIVED:", msg[:120], "...")

controller = AIController(log_callback=dummy_log)
print("\n--- 日志回调示例（一次调用）---")
controller._log("测试结构化日志")
# check_debug.py 部分
def _demo_ai_controller():
    try:
        from core.ai_controller import AIController
        controller = AIController(log_callback=dummy_log)
        controller._log("测试结构化日志")
    except Exception as e:
        print("⚠️ AIController 示例创建失败:", e)
