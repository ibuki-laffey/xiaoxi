# core/knowledge_manager.py
# ==================== 知识管理器 ====================
# 功能概述：
#   - 统一 SQLite 存储、统一 DB 连接管理
#   - 置信度、权重、版本、时间衰减、自动清理
#   - 多源抓取、LLM 一致性审查、实践验证
#   - 使用历史审计、跨项目经验检索等
#   - 完全向后兼容原有接口

import os
import sqlite3
import json
import hashlib
import re
import requests
import time
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

from .config import (
    KNOWLEDGE_BASE_DIR,
    get_db_connection,
    call_ollama_safe,
    SAFE_TIMEOUT,
    PROFESSIONAL_LOG_ENABLED,
)

# -----------------------------------------------------------------
# KnowledgeManager
# -----------------------------------------------------------------
class KnowledgeManager:
    """
    知识管理器
    负责：
    1️⃣ 添加/检索知识（带置信度、权重、版本）
    2️⃣ 多源验证、实践验证、LLM 一致性检查
    3️⃣ 权重/置信度动态调节、时间衰减
    4️⃣ 自动清理（重复、低置信、过期）
    5️⃣ 使用历史审计（knowledge_usage 表）
    """
    def __init__(self, log_callback=None):
        self.log_callback = log_callback
        self.db_path = os.path.join(KNOWLEDGE_BASE_DIR, "knowledge_base.db")
        os.makedirs(KNOWLEDGE_BASE_DIR, exist_ok=True)
        self._init_database()
        self._log("✅ 知识管理器已初始化")

    # -----------------------------------------------------------------
    # 日志
    # -----------------------------------------------------------------
    def _log(self, msg: str, level: str = "INFO"):
        if self.log_callback:
            self.log_callback(msg)
        elif PROFESSIONAL_LOG_ENABLED:
            entry = {
                "timestamp": datetime.utcnow().isoformat(),
                "level": level,
                "module": "KnowledgeManager",
                "message": msg,
            }
            print(json.dumps(entry, ensure_ascii=False))
        else:
            print(msg)

    # -----------------------------------------------------------------
    # 数据库初始化（带迁移：version、retrieved_at、knowledge_usage）
    # -----------------------------------------------------------------
    def _init_database(self):
        """初始化 SQLite 数据库以及必要的迁移"""
        try:
            conn = get_db_connection(self.db_path, timeout=30)
            cursor = conn.cursor()

            # 主知识表（若已存在则不变）
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS knowledge (
                    id TEXT PRIMARY KEY,
                    topic TEXT,
                    subtopic TEXT,
                    content TEXT NOT NULL,
                    content_type TEXT,
                    source TEXT,
                    source_url TEXT,
                    confidence REAL DEFAULT 0.5,
                    weight REAL DEFAULT 1.0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_verified TIMESTAMP,
                    verification_count INTEGER DEFAULT 0,
                    success_count INTEGER DEFAULT 0,
                    fail_count INTEGER DEFAULT 0,
                    is_verified BOOLEAN DEFAULT 0,
                    tags TEXT,
                    version INTEGER DEFAULT 1,
                    retrieved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # 若是旧库，补上 version、retrieved_at 两列
            cursor.execute("PRAGMA table_info(knowledge)")
            cols = [row[1] for row in cursor.fetchall()]
            if "version" not in cols:
                cursor.execute('ALTER TABLE knowledge ADD COLUMN version INTEGER DEFAULT 1')
            if "retrieved_at" not in cols:
                cursor.execute('ALTER TABLE knowledge ADD COLUMN retrieved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP')

            # 权重表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS knowledge_weights (
                    id TEXT PRIMARY KEY,
                    knowledge_id TEXT,
                    current_weight REAL DEFAULT 1.0,
                    decay_rate REAL DEFAULT 0.1,
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    next_verification TIMESTAMP,
                    FOREIGN KEY (knowledge_id) REFERENCES knowledge(id)
                )
            ''')

            # 默认学习资源表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS learning_resources (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    type TEXT,
                    url TEXT,
                    description TEXT,
                    quality_score REAL DEFAULT 0.5,
                    verified BOOLEAN DEFAULT 0,
                    usage_count INTEGER DEFAULT 0,
                    success_count INTEGER DEFAULT 0,
                    fail_count INTEGER DEFAULT 0,
                    last_used TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # 使用历史审计表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS knowledge_usage (
                    id TEXT PRIMARY KEY,
                    knowledge_id TEXT,
                    used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    context_type TEXT,    -- 如 "chapter", "plugin", "prompt"
                    context_id TEXT,      -- 章节号、插件名等
                    success INTEGER,      -- 1 / 0
                    FOREIGN KEY (knowledge_id) REFERENCES knowledge(id)
                )
            ''')

            # 索引
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_topic ON knowledge(topic)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_verified ON knowledge(is_verified)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_weight ON knowledge(weight)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_resource_type ON learning_resources(type)')

            conn.commit()
            self._init_default_resources()
        except sqlite3.OperationalError as e:
            self._log(f"❌ 数据库初始化失败：{e}", level="ERROR")
        except Exception as e:
            self._log(f"❌ 数据库初始化异常：{e}", level="ERROR")

    # -----------------------------------------------------------------
    # 默认资源初始化
    # -----------------------------------------------------------------
    def _init_default_resources(self):
        """初始化默认学习资源（百度、GitHub、SO、官方文档、菜鸟）"""
        default_resources = [
            {"id":"res_001","name":"百度搜索","type":"search_engine",
             "url":"https://www.baidu.com/s?wd={query}",
             "description":"通用搜索引擎","quality_score":0.7,"verified":1},
            {"id":"res_002","name":"GitHub","type":"code_repository",
             "url":"https://github.com/search?q={query}",
             "description":"代码仓库","quality_score":0.8,"verified":1},
            {"id":"res_003","name":"Stack Overflow","type":"qa_forum",
             "url":"https://stackoverflow.com/search?q={query}",
             "description":"编程问答社区","quality_score":0.85,"verified":1},
            {"id":"res_004","name":"Python 官方文档","type":"documentation",
             "url":"https://docs.python.org/3/search.html?q={query}",
             "description":"Python 官方文档","quality_score":0.95,"verified":1},
            {"id":"res_005","name":"菜鸟教程","type":"tutorial",
             "url":"https://www.runoob.com/","description":"中文编程教程",
             "quality_score":0.7,"verified":1}
        ]
        try:
            conn = get_db_connection(self.db_path, timeout=30)
            cursor = conn.cursor()
            for r in default_resources:
                cursor.execute('''
                    INSERT OR IGNORE INTO learning_resources
                    (id, name, type, url, description, quality_score, verified)
                    VALUES (?,?,?,?,?,?,?)
                ''', (r["id"], r["name"], r["type"], r["url"],
                      r["description"], r["quality_score"], r["verified"]))
            conn.commit()
        except Exception as e:
            self._log(f"⚠️ 初始化默认资源失败：{e}", level="WARN")

    # -----------------------------------------------------------------
    # 初始置信度计算（基于来源映射）
    # -----------------------------------------------------------------
    def _calculate_initial_confidence(self, source: str, source_url: Optional[str]) -> float:
        mapping = {
            "official_documentation": 0.95,
            "github": 0.8,
            "stack_overflow": 0.85,
            "search_engine": 0.6,
            "tutorial": 0.7,
            "blog": 0.5,
            "unknown": 0.5
        }
        base = mapping.get(source, 0.5)
        if source_url:
            official = ['python.org','github.com','stackoverflow.com','microsoft.com','google.com']
            if any(d in source_url for d in official):
                base = min(base + 0.1, 1.0)
        return base

    # -----------------------------------------------------------------
    # 添加知识（自动递增 version、记录 retrieved_at）
    # -----------------------------------------------------------------
    def add_knowledge(self,
                     topic: str,
                     content: str,
                     content_type: str = "text",
                     source: str = "unknown",
                     source_url: Optional[str] = None,
                     tags: Optional[List[str]] = None) -> Dict[str, Any]:
        """加入新知识并返回 id、置信度、版本"""
        try:
            conn = get_db_connection(self.db_path, timeout=30)
            cursor = conn.cursor()

            # 计算新版本号
            cursor.execute('SELECT MAX(version) FROM knowledge WHERE topic = ?', (topic,))
            max_ver = cursor.fetchone()[0] or 0
            version = max_ver + 1

            knowledge_id = hashlib.md5(f"{topic}{content}{datetime.now()}".encode()).hexdigest()[:16]
            confidence = self._calculate_initial_confidence(source, source_url)

            cursor.execute('''
                INSERT INTO knowledge
                (id, topic, content, content_type, source, source_url,
                 confidence, weight, created_at, last_verified,
                 tags, version, retrieved_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            ''', (knowledge_id, topic, content, content_type,
                  source, source_url, confidence, 1.0,
                  datetime.now().isoformat(),
                  datetime.now().isoformat(),
                  json.dumps(tags) if tags else None,
                  version, datetime.now().isoformat()))

            # 对应的 weight 记录
            next_ver = datetime.now() + timedelta(days=7)
            cursor.execute('''
                INSERT INTO knowledge_weights
                (id, knowledge_id, current_weight, next_verification)
                VALUES (?,?,?,?)
            ''', (knowledge_id, knowledge_id, 1.0, next_ver.isoformat()))

            conn.commit()
            self._log(f"📚 添加知识：{topic[:30]}…（v{version}，置信度 {confidence:.2f}）")
            return {"success": True,
                    "knowledge_id": knowledge_id,
                    "confidence": confidence,
                    "version": version}
        except Exception as e:
            self._log(f"❌ 添加知识异常：{e}", level="ERROR")
            return {"success": False, "error": str(e)}

    # -----------------------------------------------------------------
    # 单条获取（包含权重、版本等信息）
    # -----------------------------------------------------------------
    def get_knowledge(self, knowledge_id: str) -> Optional[Dict[str, Any]]:
        try:
            conn = get_db_connection(self.db_path, timeout=30)
            cursor = conn.cursor()
            cursor.execute('''
                SELECT k.*, w.current_weight, w.next_verification
                FROM knowledge k
                LEFT JOIN knowledge_weights w ON k.id = w.knowledge_id
                WHERE k.id = ?
            ''', (knowledge_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return {
                "id": row[0], "topic": row[1], "subtopic": row[2],
                "content": row[3], "content_type": row[4],
                "source": row[5], "source_url": row[6],
                "confidence": row[7], "weight": row[8],
                "created_at": row[9], "last_verified": row[10],
                "verification_count": row[11], "success_count": row[12],
                "fail_count": row[13], "is_verified": bool(row[14]),
                "tags": row[15], "version": row[16],
                "retrieved_at": row[17],
                "current_weight": row[18], "next_verification": row[19]
            }
        except Exception as e:
            self._log(f"❌ 获取知识失败：{e}", level="ERROR")
            return None

    # -----------------------------------------------------------------
    # 知识搜索（支持 topic、版本、置信度排序）
    # -----------------------------------------------------------------
    def search_knowledge(self,
                         keyword: str,
                         topic: Optional[str] = None,
                         min_confidence: float = 0.5) -> List[Dict[str, Any]]:
        try:
            conn = get_db_connection(self.db_path, timeout=30)
            cursor = conn.cursor()
            sql = '''
                SELECT k.*, w.current_weight
                FROM knowledge k
                LEFT JOIN knowledge_weights w ON k.id = w.knowledge_id
                WHERE k.content LIKE ?
                AND k.confidence >= ?
            '''
            params = [f"%{keyword}%", min_confidence]

            if topic:
                sql += " AND k.topic = ?"
                params.append(topic)

            sql += " ORDER BY k.version DESC, k.confidence DESC, w.current_weight DESC LIMIT 20"

            cursor.execute(sql, tuple(params))
            results = []
            for row in cursor.fetchall():
                results.append({
                    "id": row[0],
                    "topic": row[1],
                    "content": row[3],
                    "confidence": row[7],
                    "weight": row[18],
                    "is_verified": bool(row[14]),
                    "version": row[16]
                })
            return results
        except Exception as e:
            self._log(f"❌ 搜索知识失败：{e}", level="ERROR")
            return []

    # -----------------------------------------------------------------
    # 为上下文获取关联知识（强化版）
    # -----------------------------------------------------------------
    def get_knowledge_for_context(self, user_input: str) -> List[Dict[str, Any]]:
        """返回与用户输入最相关的 ≤8 条知识（优先 solution、置信度、权重、版本）"""
        solution_knowledge = self._search_solutions(user_input)
        if solution_knowledge:
            return solution_knowledge

        keywords = [kw for kw in user_input.split() if len(kw) > 2]
        all_hits: List[Dict[str, Any]] = []
        for kw in keywords:
            hits = self.search_knowledge(kw, min_confidence=0.4)
            # 优先 solution 标签
            sol_hits = [h for h in hits if "solution" in (h.get("tags") or [])]
            all_hits.extend(sol_hits if sol_hits else hits)

        # 去重 + 排序
        uniq: Dict[str, Dict] = {}
        for k in all_hits:
            if k["id"] not in uniq:
                uniq[k["id"]] = k
        lst = list(uniq.values())
        lst.sort(key=lambda x: (x["confidence"] * x.get("weight", 1)), reverse=True)
        return lst[:8]

    # -----------------------------------------------------------------
    # 专门搜索 solutions（tag 中含有 solution）
    # -----------------------------------------------------------------
    def _search_solutions(self, user_input: str) -> List[Dict[str, Any]]:
        try:
            conn = get_db_connection(self.db_path, timeout=30)
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, topic, content, confidence, weight, tags
                FROM knowledge
                WHERE tags LIKE ? AND confidence >= ?
                ORDER BY confidence DESC, weight DESC LIMIT 5
            ''', ('%solution%', 0.5))
            solutions = []
            for row in cursor.fetchall():
                solutions.append({
                    "id": row[0],
                    "topic": row[1],
                    "content": row[2],
                    "confidence": row[3],
                    "weight": row[4],
                    "tags": json.loads(row[5]) if row[5] else [],
                    "is_solution": True
                })
            return solutions
        except Exception as e:
            self._log(f"❌ 搜索解决方案失败：{e}", level="ERROR")
            return []

    # -----------------------------------------------------------------
    # LLM 一致性检查（私有工具函数）
    # -----------------------------------------------------------------
    def _llm_consistency_check(self,
                               base_text: str,
                               candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
        """使用 ollama（gemma4）对多源文本做语义一致性审查，返回 {'consistent':bool,'score':float,'remarks':str}"""
        if not candidates:
            return {"consistent": True, "score": 1.0, "remarks": ""}

        prompt = (
            "请判断以下几段中文文本是否在描述同一概念或事实，给出一致性评分（0‑1），"
            "如果不一致请指出冲突点并给出简短说明。仅返回 JSON，格式如下：\n"
            "{'consistent': true/false, 'score': <0-1>, 'remarks': '...'}\n\n"
            f"基准文本（截取前 2000 字）：\n{base_text[:2000]}\n\n"
        )
        for i, cand in enumerate(candidates, 1):
            prompt += f"候选{i}（截取前 2000 字）：\n{cand.get('content','')[:2000]}\n\n"

        resp = call_ollama_safe(
            [{"role": "system", "content": prompt}],
            timeout=SAFE_TIMEOUT,
            retry=2
        )
        if not resp.get("success"):
            return {"consistent": False, "score": 0.0, "remarks": "LLM 调用失败"}

        raw = resp.get("content", "")
        # 使用 SuperGuardian 进行工业级 JSON 自愈
        from .super_guardian import guardian
        try:
            data = guardian.heal_json(raw, expected_type=dict)
            return {
                "consistent": bool(data.get("consistent", False)),
                "score": float(data.get("score", 0.0)),
                "remarks": data.get("remarks", "")
            }
        except ValueError:
            # 若自愈失败，回退到原有的正则提取方式
            try:
                json_str = re.search(r"\{.*\}", raw, re.S).group()
                data = json.loads(json_str)
                return {
                    "consistent": bool(data.get("consistent", False)),
                    "score": float(data.get("score", 0.0)),
                    "remarks": data.get("remarks", "")
                }
            except Exception:
                return {"consistent": False, "score": 0.0, "remarks": "解析失败"}


    # -----------------------------------------------------------------
    # 验证知识（多源比对 → URL 检验 → LLM 一致性 → 实践执行）
    # -----------------------------------------------------------------
    def verify_knowledge(self, knowledge_id: str) -> Dict[str, Any]:
        """返回验证结果、更新置信度/权重，返回结构见函数体文档"""
        knowledge = self.get_knowledge(knowledge_id)
        if not knowledge:
            return {"success": False, "message": "知识不存在"}

        result = {
            "knowledge_id": knowledge_id,
            "verified_at": datetime.now().isoformat(),
            "methods_used": [],
            "success": False,
            "new_confidence": knowledge["confidence"],
            "new_weight": knowledge["weight"]
        }

        # 1️⃣ 多源比对（相似知识）
        if knowledge["content_type"] in ("text", "code", "api"):
            # 使用知识的唯一 ID 排除自身
            similar = self._find_similar_knowledge(knowledge["topic"], knowledge["id"])
            if len(similar) >= 2:
                consistency = self._check_consistency(knowledge["content"], similar)
                

                if consistency >= 0.66:
                    result["methods_used"].append("multi_source")
                    result["new_confidence"] = min(1.0, result["new_confidence"] + 0.1)
                else:
                    # LLM 再次检查
                    llm_res = self._llm_consistency_check(knowledge["content"], similar)
                    if llm_res["consistent"]:
                        result["methods_used"].append("llm_consistency")
                        result["new_confidence"] = min(1.0, result["new_confidence"] + llm_res["score"] * 0.2)
                    else:
                        result["methods_used"].append("llm_conflict")
                        result["new_confidence"] = max(0.0, result["new_confidence"] - 0.1)

        # 2️⃣ URL 可用性检查
        if knowledge["source_url"]:
            if self._verify_url_still_valid(knowledge["source_url"]):
                result["methods_used"].append("url_check")
                result["new_confidence"] = min(1.0, result["new_confidence"] + 0.05)

        # 3️⃣ 实践验证（代码执行）
        if knowledge["content_type"] == "code":
            try:
                from .code_executor import CodeExecutor
                exec_res = CodeExecutor(self.log_callback).execute(knowledge["content"], "学习验证")
                if exec_res.get("success"):
                    result["methods_used"].append("practice")
                    result["new_confidence"] = min(1.0, result["new_confidence"] + 0.2)
            except Exception:
                pass

        # 完成后写回 DB
        result["new_confidence"] = round(min(1.0, max(0.0, result["new_confidence"])), 3)
        self._update_knowledge_verification(
            knowledge_id,
            result["new_confidence"],
            bool(result["methods_used"])
        )
        result["success"] = True
        return result

    # -----------------------------------------------------------------
    # 查找相似知识（同 topic、已验证）
    # -----------------------------------------------------------------
    def _find_similar_knowledge(self, topic: str, knowledge_id: str, limit: int = 5) -> List[Dict[str, Any]]:
        """
        在同一主题下查找已验证的相似知识（排除自身）。
        参数
        ----------
        topic : str
            知识所属主题。
        knowledge_id : str
            当前知识的唯一 ID（用于在查询中排除自身）。
        limit : int
            返回的相似条目上限，默认 5 条。
        """
        try:
            conn = get_db_connection(self.db_path, timeout=30)
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, topic, content, confidence
                FROM knowledge
                WHERE topic = ? AND is_verified = 1 AND id != ?
                ORDER BY confidence DESC
                LIMIT ?
            ''', (topic, knowledge_id, limit))
            rows = [
                {"id": r[0], "topic": r[1], "content": r[2], "confidence": r[3]}
                for r in cursor.fetchall()
            ]
            return rows
        except Exception as e:
            self._log(f"❌ 查找相似知识失败：{e}", level="WARN")
            return []


    # -----------------------------------------------------------------
    # 简易文本相似度（词集合交集比）
    # -----------------------------------------------------------------
    def _check_consistency(self, base: str, similar: List[Dict[str, Any]]) -> float:
        if not similar:
            return 0.0
        stop = {'的','了','是','在','和','与','或','等','一个','这个','我们','你们','他们'}
        base_set = set(w for w in base.lower().split() if w not in stop)
        scores = []
        for k in similar:
            other_set = set(w for w in k["content"].lower().split() if w not in stop)
            if not base_set or not other_set:
                continue
            inter = len(base_set & other_set)
            union = len(base_set | other_set)
            scores.append(inter / union if union else 0)
        return sum(scores) / len(scores) if scores else 0.0

    # -----------------------------------------------------------------
    # URL 有效性检查（HEAD）
    # -----------------------------------------------------------------
    def _verify_url_still_valid(self, url: str) -> bool:
        try:
            resp = requests.head(url, timeout=10, allow_redirects=True)
            return resp.status_code == 200
        except Exception:
            return False

    # -----------------------------------------------------------------
    # 更新知识的置信度、验证状态、权重等
    # -----------------------------------------------------------------
    def _update_knowledge_verification(self,
                                      knowledge_id: str,
                                      new_confidence: float,
                                      is_verified: bool):
        try:
            conn = get_db_connection(self.db_path, timeout=30)
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE knowledge
                SET confidence = ?, is_verified = ?, last_verified = ?, verification_count = verification_count + 1
                WHERE id = ?
            ''', (new_confidence, 1 if is_verified else 0,
                  datetime.now().isoformat(), knowledge_id))

            # 更新权重表的 next_verification
            next_v = datetime.now() + timedelta(days=30)
            cursor.execute('''
                UPDATE knowledge_weights
                SET current_weight = ?, last_updated = ?, next_verification = ?
                WHERE knowledge_id = ?
            ''', (new_confidence, datetime.now().isoformat(),
                  next_v.isoformat(), knowledge_id))
            conn.commit()
        except Exception as e:
            self._log(f"❌ 更新知识验证失败：{e}", level="WARN")

    # -----------------------------------------------------------------
    # 权重调节（基于使用成功/失败）
    # -----------------------------------------------------------------
    def update_knowledge_weight(self, knowledge_id: str, success: bool = True):
        if not knowledge_id:
            return
        try:
            conn = get_db_connection(self.db_path, timeout=30)
            cursor = conn.cursor()
            if success:
                cursor.execute('''
                    UPDATE knowledge
                    SET success_count = success_count + 1,
                        confidence = MIN(confidence + 0.05, 1.0)
                    WHERE id = ?
                ''', (knowledge_id,))
            else:
                cursor.execute('''
                    UPDATE knowledge
                    SET fail_count = fail_count + 1,
                        confidence = MAX(confidence - 0.1, 0.0)
                    WHERE id = ?
                ''', (knowledge_id,))
            cursor.execute('''
                UPDATE knowledge_weights
                SET current_weight = current_weight * 0.9,
                    last_updated = CURRENT_TIMESTAMP
                WHERE knowledge_id = ?
            ''', (knowledge_id,))
            conn.commit()
        except Exception as e:
            self._log(f"❌ 更新知识权重失败：{e}", level="WARN")

    # -----------------------------------------------------------------
    # 时间衰减（每 30 天 weight *0.9）
    # -----------------------------------------------------------------
    def apply_time_decay(self) -> Dict[str, Any]:
        try:
            conn = get_db_connection(self.db_path, timeout=30)
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE knowledge_weights
                SET current_weight = current_weight * POWER(0.9, (JULIANDAY('now') - JULIANDAY(last_updated))/30),
                    last_updated = CURRENT_TIMESTAMP
            ''')
            updated = cursor.rowcount
            conn.commit()
            if updated:
                self._log(f"⏳ 已对 {updated} 条知识应用时间衰减")
            return {"success": True, "updated": updated}
        except Exception as e:
            self._log(f"❌ 应用时间衰减失败：{e}", level="ERROR")
            return {"success": False, "error": str(e)}

    # -----------------------------------------------------------------
    # 统计信息
    # -----------------------------------------------------------------
    def get_statistics(self) -> Dict[str, Any]:
        try:
            conn = get_db_connection(self.db_path, timeout=30)
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) FROM knowledge')
            total = cursor.fetchone()[0]
            cursor.execute('SELECT COUNT(*) FROM knowledge WHERE is_verified = 1')
            verified = cursor.fetchone()[0]
            cursor.execute('SELECT AVG(confidence) FROM knowledge')
            avg_conf = cursor.fetchone()[0] or 0.0
            cursor.execute('''
                SELECT topic, COUNT(*) FROM knowledge
                GROUP BY topic ORDER BY COUNT(*) DESC LIMIT 10
            ''')
            by_topic = dict(cursor.fetchall())
            return {
                "total": total,
                "verified": verified,
                "verified_rate": verified / total if total else 0,
                "avg_confidence": avg_conf,
                "by_topic": by_topic
            }
        except Exception as e:
            self._log(f"❌ 获取统计失败：{e}", level="ERROR")
            return {"total":0,"verified":0,"avg_confidence":0,"by_topic":{}}

    # -----------------------------------------------------------------
    # 导出 / 导入
    # -----------------------------------------------------------------
    def export_knowledge(self, filepath: str, topic: Optional[str] = None) -> Dict[str, Any]:
        try:
            conn = get_db_connection(self.db_path, timeout=30)
            cursor = conn.cursor()
            if topic:
                cursor.execute('SELECT * FROM knowledge WHERE topic = ?', (topic,))
            else:
                cursor.execute('SELECT * FROM knowledge')
            rows = cursor.fetchall()
            col_names = [desc[0] for desc in cursor.description]
            data = [dict(zip(col_names, r)) for r in rows]
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            return {"success": True, "count": len(data)}
        except Exception as e:
            self._log(f"❌ 导出知识失败：{e}", level="ERROR")
            return {"success": False, "error": str(e)}

    def import_knowledge(self, filepath: str) -> Dict[str, Any]:
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                items = json.load(f)
            added = 0
            for item in items:
                res = self.add_knowledge(
                    topic=item.get("topic", "imported"),
                    content=item.get("content", ""),
                    content_type=item.get("content_type", "text"),
                    source=item.get("source", "imported"),
                    source_url=item.get("source_url"),
                    tags=item.get("tags")
                )
                if res.get("success"):
                    added += 1
            return {"success": True, "count": added}
        except Exception as e:
            self._log(f"❌ 导入知识失败：{e}", level="ERROR")
            return {"success": False, "error": str(e)}

    # -----------------------------------------------------------------
    # 待验证 / 低置信度查询
    # -----------------------------------------------------------------
    def get_pending_verification(self,
                                 min_confidence: float = 0.6,
                                 limit: int = 10) -> List[Dict[str, Any]]:
        try:
            conn = get_db_connection(self.db_path, timeout=30)
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, topic, content, confidence, last_verified
                FROM knowledge
                WHERE (confidence < ? OR last_verified IS NULL OR
                       julianday('now') - julianday(last_verified) > 30)
                ORDER BY confidence ASC, last_verified ASC
                LIMIT ?
            ''', (min_confidence, limit))
            pending = []
            for row in cursor.fetchall():
                pending.append({
                    "id": row[0],
                    "topic": row[1],
                    "content": row[2][:100] + "..." if len(row[2]) > 100 else row[2],
                    "confidence": row[3],
                    "last_verified": row[4]
                })
            return pending
        except Exception as e:
            self._log(f"❌ 获取待验证知识失败：{e}", level="WARN")
            return []

    def get_low_confidence_knowledge(self,
                                    threshold: float = 0.5,
                                    limit: int = 20) -> List[Dict[str, Any]]:
        """兼容旧接口（直接调用 get_pending_verification）"""
        return self.get_pending_verification(threshold, limit)

    # -----------------------------------------------------------------
    # 自动优化（去重、低置信度、过期）
    # -----------------------------------------------------------------
    def auto_optimize_knowledge_base(self) -> Dict[str, Any]:
        optim = {"duplicates_removed": 0,
                 "low_confidence_removed": 0,
                 "outdated_removed": 0}
        try:
            optim["duplicates_removed"] = self._remove_duplicate_knowledge()
            optim["low_confidence_removed"] = self._remove_low_confidence_knowledge(0.3)
            optim["outdated_removed"] = self._remove_outdated_knowledge(365)
            self._log(f"✅ 知识库优化完成：{optim}")
            return {"success": True, "optimizations": optim}
        except Exception as e:
            self._log(f"❌ 知识库优化失败：{e}", level="ERROR")
            return {"success": False, "error": str(e)}

    def _remove_duplicate_knowledge(self) -> int:
        try:
            conn = get_db_connection(self.db_path, timeout=30)
            cursor = conn.cursor()
            cursor.execute('''
                SELECT content, COUNT(*) as cnt, MIN(id) as keep_id
                FROM knowledge
                GROUP BY content
                HAVING cnt > 1
            ''')
            duplicates = cursor.fetchall()
            removed = 0
            for content, cnt, keep_id in duplicates:
                cursor.execute('DELETE FROM knowledge WHERE content = ? AND id != ?', (content, keep_id))
                removed += cnt - 1
            conn.commit()
            return removed
        except Exception as e:
            self._log(f"❌ 移除重复知识失败：{e}", level="WARN")
            return 0

    def _remove_low_confidence_knowledge(self, threshold: float) -> int:
        try:
            conn = get_db_connection(self.db_path, timeout=30)
            cursor = conn.cursor()
            cursor.execute('DELETE FROM knowledge WHERE confidence < ?', (threshold,))
            removed = cursor.rowcount
            conn.commit()
            return removed
        except Exception as e:
            self._log(f"❌ 移除低置信度知识失败：{e}", level="WARN")
            return 0

    def _remove_outdated_knowledge(self, days: int) -> int:
        try:
            conn = get_db_connection(self.db_path, timeout=30)
            cursor = conn.cursor()
            cursor.execute('''
                DELETE FROM knowledge
                WHERE last_verified IS NOT NULL
                AND julianday('now') - julianday(last_verified) > ?
            ''', (days,))
            removed = cursor.rowcount
            conn.commit()
            return removed
        except Exception as e:
            self._log(f"❌ 移除过时知识失败：{e}", level="WARN")
            return 0

    # -----------------------------------------------------------------
    # 使用历史记录（审计）
    # -----------------------------------------------------------------
    def log_knowledge_usage(self,
                            knowledge_id: str,
                            context_type: str,
                            context_id: str,
                            success: bool = True) -> None:
        """
        记录一次知识被使用的情境（章节、插件、普通 Prompt 等）。
        便于事后审计、置信度调节或经验复用。
        """
        try:
            conn = get_db_connection(self.db_path, timeout=30)
            cursor = conn.cursor()
            uid = hashlib.md5(f"{knowledge_id}{context_id}{time.time()}".encode()).hexdigest()[:16]
            cursor.execute('''
                INSERT INTO knowledge_usage
                (id, knowledge_id, context_type, context_id, success)
                VALUES (?,?,?,?,?)
            ''', (uid, knowledge_id, context_type, context_id, int(success)))
            conn.commit()
        except Exception as e:
            self._log(f"⚠️ 记录知识使用失败：{e}", level="WARN")

    # -----------------------------------------------------------------
    # 兼容旧接口（如学习管理器的旧调用）
    # -----------------------------------------------------------------
    # 这些方法保留旧名称，仅内部转发
    def generate_first_run_learning_goals(self):
        """向后兼容：返回首次学习目标列表（已在 SelfLearningEngine 中实现）"""
        from .self_learning_engine import SelfLearningEngine
        return SelfLearningEngine().get_initial_learning_goals()

    def execute_first_run_learning(self, time_budget_minutes: int = 15):
        """向后兼容：执行首次学习（已在 SelfLearningEngine 中实现）"""
        from .self_learning_engine import SelfLearningEngine
        return SelfLearningEngine().perform_initial_learning(time_budget_minutes)

# ==================== 文件结束 ====================
