# -*- coding: utf-8 -*-
# core/self_learning_engine.py
# ==============================================================
# 自主学习引擎（完整优化版）
# 功能概述
#   1. 真正网络学习（搜索 → 抓取 → LLM 摘要）
#   2. 跨工程经验积累与复用
#   3. 经验验证、置信度管理
#   4. 角色成长、世界观层级学习集成
#   5. 意外需求（错误、异常）学习
#   6. 学习资源质量评估 (GAP‑15)
#   7. 经验置信度增强 (GAP‑11)
# ==============================================================
# 为防止后续维护时误删模型调用，**所有学习相关函数均使用
# `call_ollama_safe`**（已在代码中用显著注释标记）。请务必保持此约束。
# ==============================================================

from __future__ import annotations

import os
import json
import time
import hashlib
import random
import re
import traceback
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

import requests
from bs4 import BeautifulSoup

# ------------------- 项目公共配置 -------------------
# 必须从 config 中统一导入常量和工具函数，以免循环依赖
from .config import (
    KNOWLEDGE_BASE_DIR,
    TASK_TRACKING_DIR,
    PLUGINS_DIR,
    OLLAMA_URL,
    MODEL_NAME,
    MAX_LEARNING_MINUTES,
    MIN_SEARCH_QUALITY,
    CHARACTER_GROWTH_ENABLED,
    WORLD_TRUTH_LEVELS,
    call_ollama_safe,          # ← 强制使用模型的安全封装
    SAFE_TIMEOUT,
    PROFESSIONAL_LOG_ENABLED,  # 用于结构化日志（可选）
    ENABLE_SELF_LEARNING_LLM,
)

# --------------------------------------------------------------
# 辅助正则：从 LLM 原始返回中提取最外层 JSON（或数组）
# --------------------------------------------------------------
def _extract_json(text: str) -> str:
    """
    Ollama 常将 JSON 包裹在代码块中（```json ...```），此函数使用
    正则抽取最外层的大括号或中括号，返回可直接 `json.loads` 的字符串。
    """
    # 首先尝试匹配对象
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        return m.group()
    # 再尝试匹配数组
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if m:
        return m.group()
    # 若均未匹配，返回原文（后续会捕获异常）
    return text.strip()


# ==============================================================
# SelfLearningEngine 主类
# ==============================================================
class SelfLearningEngine:
    """
    自主学习引擎（完整优化版）。
    所有公开方法均 **必须** 通过 LLM（`call_ollama_safe`）完成核心学习步骤，
    任何仅使用本地规则的改动都将导致后续功能失效。
    """

    # ------------------- 初始化 -------------------
    def __init__(self, log_callback: Optional[Any] = None,
                 knowledge_manager: Optional[Any] = None,
                 plugin_manager: Optional[Any] = None):
        self.log_callback = log_callback
        self.knowledge_manager = knowledge_manager
        self.plugin_manager = plugin_manager

        # ------------------- 目录与文件 -------------------
        self.learning_log_dir = os.path.join(TASK_TRACKING_DIR, "learning_logs")
        self.auto_plugins_dir = os.path.join(PLUGINS_DIR, "auto")
        self.experience_db_dir = os.path.join(KNOWLEDGE_BASE_DIR, "experience_db")

        os.makedirs(self.learning_log_dir, exist_ok=True)
        os.makedirs(self.auto_plugins_dir, exist_ok=True)
        os.makedirs(self.experience_db_dir, exist_ok=True)

        self.learning_log_file = os.path.join(self.learning_log_dir, "learning_history.json")
        self.strategy_file = os.path.join(self.learning_log_dir, "learning_strategies.json")
        self.first_run_file = os.path.join(KNOWLEDGE_BASE_DIR, "first_run_done.json")
        self.experience_db_file = os.path.join(self.experience_db_dir, "experience_index.json")

        # ------------------- 状态 -------------------
        self.is_online = self._check_internet_connection()
        self.cross_project_learning_enabled = True
        self.experience_confidence_threshold = 0.6

        # ------------------- 运行时缓存 -------------------
        self.learning_logs: List[Dict[str, Any]] = []
        self.strategies: List[Dict[str, Any]] = []

        # ------------------- 初始化文件/策略 -------------------
        self._init_files()
        self._load_strategies()
        self._load_learning_history()

        self._log(f"✅ SelfLearningEngine 初始化完成（{'在线' if self.is_online else '离线'}模式）")

    # ==========================================================
    # 读取已有学习日志（私有实现，供 __init__ 调用）
    # ==========================================================
    def _load_learning_history(self) -> None:
        """
        从磁盘读取 `learning_history.json`（如果存在），
        把日志恢复到 `self.learning_logs` 中供后续增量写入使用。
        若文件缺失或读取异常则返回空列表。
        """
        try:
            if os.path.isfile(self.learning_log_file):
                with open(self.learning_log_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.learning_logs = data.get("logs", [])
                self._log(f"🔄 已加载 {len(self.learning_logs)} 条历史学习日志")
            else:
                self.learning_logs = []
        except Exception as e:
            self._log(f"⚠️ 加载学习日志失败：{e}", level="WARN")
            self.learning_logs = []

    # ==========================================================
    # 公开的加载入口（供 AIController 调用）
    # ==========================================================
    def load_learning_history(self) -> None:
        """
        对外公开的加载方法，内部直接委托给私有实现
        （保持向后兼容，外部无需关心实现细节）。
        """
        self._load_learning_history()

    # ==========================================================
    # 日志打印（统一格式，可被外部回调接管）
    # ==========================================================
    def _log(self, msg: str, level: str = "INFO") -> None:
        """
        统一日志输出。若外部提供 `log_callback` 则调用；否则直接 `print`。
        当 `PROFESSIONAL_LOG_ENABLED` 为 True 时会以 JSON 结构化日志输出。
        """
        timestamp = datetime.utcnow().isoformat()
        if PROFESSIONAL_LOG_ENABLED:
            entry = {
                "timestamp": timestamp,
                "level": level,
                "module": "SelfLearningEngine",
                "message": msg,
            }
            formatted = json.dumps(entry, ensure_ascii=False)
        else:
            formatted = f"[{timestamp}] [{level}] {msg}"
        if self.log_callback:
            self.log_callback(formatted)
        else:
            print(formatted)

    # ==========================================================
    # 1️⃣ 判断是否已经完成首次学习（避免重复）
    # ==========================================================
    def is_first_run_completed(self) -> bool:
        """检查 `first_run_done.json` 是否存在，表示首次学习已经结束。"""
        return os.path.isfile(self.first_run_file)

    def mark_first_run_done(self) -> None:
        """
        在首次学习全部成功后写入标记文件，防止后续启动再次执行。
        文件结构示例：
        {
            "completed_at": "2026-05-08T20:15:30.123456"
        }
        """
        try:
            with open(self.first_run_file, "w", encoding="utf-8") as f:
                json.dump(
                    {"completed_at": datetime.utcnow().isoformat()},
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
            self._log("✅ 首次学习标记已写入，后续启动将直接跳过首次学习")
        except Exception as e:
            self._log(f"⚠️ 写入首次学习标记失败：{e}", level="WARN")

    # ==========================================================
    # 2️⃣ 首次学习目标（系统第一次启动时要学习的内容）
    # ==========================================================
    def get_initial_learning_goals(self) -> List[Dict[str, Any]]:
        """
        返回一组 **首次学习目标**（按照优先级排序），帮助系统在第一次运行时
        快速建立插件、基础能力、错误处理、用户偏好、知识库、跨工程经验等。
        """
        return [
            {
                "id": "goal_001",
                "type": "plugin_discovery",
                "description": "扫描已安装插件并学习其主要功能",
                "priority": 1,
            },
            {
                "id": "goal_002",
                "type": "basic_capabilities",
                "description": "学习时间查询、文件读取、网页打开等基础能力",
                "priority": 1,
            },
            {
                "id": "goal_003",
                "type": "error_handling",
                "description": "学习常见错误的诊断与修复方法",
                "priority": 2,
            },
            {
                "id": "goal_004",
                "type": "user_preferences",
                "description": "从已有对话中提取用户兴趣与偏好",
                "priority": 2,
            },
            {
                "id": "goal_005",
                "type": "knowledge_base_init",
                "description": "写入系统自我认知与核心概念至知识库",
                "priority": 1,
            },
            {
                "id": "goal_006",
                "type": "cross_project_experience",
                "description": "初始化跨工程经验库，为后续经验复用做准备",
                "priority": 2,
            },
        ]

    # ==========================================================
    # 3️⃣ 执行完整的首次学习流程
    # ==========================================================
    def perform_initial_learning(self, time_budget_minutes: int = MAX_LEARNING_MINUTES) -> Dict[str, Any]:
        """
        按照 `get_initial_learning_goals` 的顺序逐一完成学习任务，直至：
        - 用尽预算时间
        - 所有目标完成（或出现致命错误）
        完成后写入 `first_run_done.json`，以后启动会直接跳过。
        """
        # ── 新版实现 ──
        # 1️⃣ 若已有标记文件则直接返回（行为保持不变）
        if self.is_first_run_completed():
            return {"status": "already_done", "message": "首次学习已完成"}

        self._log(f"🚀 开始首次学习（预算 {time_budget_minutes} 分钟）")
        start_ts = time.time()

        # 2️⃣ 为每个目标准备 LLM‑Prompt‑模板（可在 config 中统一开启/关闭）
        from .config import ENABLE_SELF_LEARNING_LLM  # 新增的全局开关
        results = {
            "goals_total": 0,
            "goals_completed": 0,
            "goals_failed": 0,
            "knowledge_gained": 0,
            "plugins_discovered": 0,
            "time_spent_minutes": 0,
            "success": False,
            "llm_summaries": [],          # 收集每个目标的 LLM 摘要，供报告使用
        }

        goals = self.get_initial_learning_goals()
        results["goals_total"] = len(goals)

        for goal in goals:
            # 3️⃣ 超时检测（与旧实现保持一致）
            elapsed = (time.time() - start_ts) / 60
            if elapsed >= time_budget_minutes:
                self._log(
                    f"⌛ 时间预算已耗尽，停止后续学习（已完成 {results['goals_completed']} / {results['goals_total']}）"
                )
                break

            # 4️⃣ 先执行原有的内部实现（保持原有插件/知识库等功能）
            sub_res = {}
            try:
                if goal["type"] == "plugin_discovery":
                    sub_res = self._learn_plugin_discovery()
                    results["plugins_discovered"] += sub_res.get("count", 0)
                elif goal["type"] == "basic_capabilities":
                    sub_res = self._learn_basic_capabilities()
                    results["knowledge_gained"] += sub_res.get("count", 0)
                elif goal["type"] == "error_handling":
                    sub_res = self._learn_error_handling()
                    results["knowledge_gained"] += sub_res.get("count", 0)
                elif goal["type"] == "user_preferences":
                    sub_res = self._learn_user_preferences()
                    results["knowledge_gained"] += sub_res.get("count", 0)
                elif goal["type"] == "knowledge_base_init":
                    sub_res = self._init_knowledge_base()
                    results["knowledge_gained"] += sub_res.get("count", 0)
                elif goal["type"] == "cross_project_experience":
                    sub_res = self._init_cross_project_experience()
                    results["knowledge_gained"] += sub_res.get("count", 0)
                else:
                    self._log(f"⚠️ 未识别的学习目标类型：{goal['type']}")
                    sub_res = {"success": False}
            except Exception as exc:
                self._log(f"❌ 学习目标 `{goal['id']}` 执行异常：{exc}")
                sub_res = {"success": False}

            # 5️⃣ 根据子任务成功与否更新计数
            if sub_res.get("success"):
                results["goals_completed"] += 1
            else:
                results["goals_failed"] += 1

            # 6️⃣ **可选的 LLM 摘要**（在配置 ENABLE_SELF_LEARNING_LLM 为 True 时激活）
            if ENABLE_SELF_LEARNING_LLM:
                # 生成一个简洁的中文摘要，告诉系统“我刚完成了 X 目标，得到 Y 条新知识 …”
                # 为防止 Prompt 过长，仅传递目标描述与子任务返回的可读信息
                goal_desc = goal.get("description", "")
                sub_detail = sub_res.get("detail") or sub_res.get("message") or ""
                llm_prompt = f"""请根据以下信息用 **中文** 写一段不超过 120 字的学习摘要，包含：
1. 本次学习的目标描述（{goal_desc}）；
2. 子任务执行的主要结果（{sub_detail[:200]}）；
3. 若有新知识或插件被发现，请标注「发现 X 条/个」；
请只返回纯文本，不要出现任何前缀、代码块或 markdown。"""

                try:
                    resp = call_ollama_safe(
                        [{"role": "system", "content": llm_prompt}],
                        timeout=SAFE_TIMEOUT,
                        retry=2,
                    )
                    summary = resp.get("content", "").strip() if resp.get("success") else ""
                except Exception as e:
                    self._log(f"⚠️ LLM 摘要调用异常（目标 {goal['id']}）: {e}", level="WARN")
                    summary = ""

                if summary:
                    results["llm_summaries"].append(
                        {"goal_id": goal["id"], "summary": summary}
                    )
                    # 同时将摘要写入学习日志（便于后续报告）
                    self.learning_logs.append(
                        {
                            "timestamp": datetime.utcnow().isoformat(),
                            "type": "llm_summary",
                            "goal_id": goal["id"],
                            "summary": summary,
                        }
                    )

        # 7️⃣ 计算总耗时、整体成功标记
        results["time_spent_minutes"] = round((time.time() - start_ts) / 60, 2)
        results["success"] = results["goals_completed"] > 0

        # 8️⃣ 若整体成功则写入标记文件
        if results["success"]:
            self.mark_first_run_done()
            self._log("✅ 首次学习全部完成，已写入标记文件")
        else:
            self._log(
                "⚠️ 首次学习未全部完成，标记文件保持未写入，以便下次继续尝试"
            )

        # 9️⃣ 持久化一次完整的学习日志（包括 LLM 摘要）
        self._save_learning_log(
            {
                "type": "initial_learning",
                "timestamp": datetime.utcnow().isoformat(),
                "result": results,
                "goals": goals,
                "llm_summaries": results.get("llm_summaries", []),
            }
        )
        # ── 新版实现结束 ──
        return results


    # ==========================================================
    # 4️⃣ 真正的网络学习入口（网络抓取 → LLM 摘要 → 知识库写入）
    # ==========================================================
    def fetch_online_knowledge(self, query: str, max_results: int = 5) -> List[Dict[str, Any]]:
        """
        **必须使用模型进行学习**（通过 `call_ollama_safe` 生成摘要）。
        1️⃣ 使用百度搜索（或其它搜索引擎）获取前 `max_results` 条结果页面 URL。  
        2️⃣ 抓取每个页面正文（最多 2 000 字），去除噪声。  
        3️⃣ 调用 LLM 对正文进行 **摘要**（200~400 字左右），并写入 `knowledge_manager`。  
        4️⃣ 返回写入成功的知识记录（用于后续学习链路）。
        """
        if not self.is_online:
            self._log("⚠️ 当前离线，无法进行网络抓取", level="WARN")
            return []

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/112.0 Safari/537.36"
            )
        }

        fetched: List[Dict[str, Any]] = []

        # -------------------------------------------------
        # ① 生成搜索 URL（国内优先使用百度，若百度失效则回退 Bing CN）
        # -------------------------------------------------
        # 为了统一管理，使用内部小工具函数（后文实现）
        def _search_urls(keyword: str) -> List[str]:
            """返回关键字在百度/必应（CN）上搜索得到的前 max_results 条真实 URL 列表"""
            # 先尝试百度
            try:
                baidu_url = f"https://www.baidu.com/s?wd={keyword}"
                resp = requests.get(baidu_url, headers=headers, timeout=10)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "lxml")
                    # 百度搜索结果面板 class 可能随时间变化，这里使用较宽松的 selector
                    items = soup.select('.result.c-container')[:max_results]
                    urls = []
                    for it in items:
                        a = it.select_one('a')
                        if a and a.has_attr('href'):
                            urls.append(a['href'])
                    if urls:
                        return urls
            except Exception as e:
                self._log(f"⚠️ 百度搜索异常（{keyword}）：{e}", level="WARN")

            # 若百度未返回有效 URL，使用 Bing CN（需手动拼接参数）
            try:
                bing_url = f"https://cn.bing.com/search?q={keyword}"
                resp = requests.get(bing_url, headers=headers, timeout=10)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "lxml")
                    items = soup.select('li.b_algo')[:max_results]
                    urls = []
                    for it in items:
                        a = it.select_one('h2 a')
                        if a and a.has_attr('href'):
                            urls.append(a['href'])
                    return urls
            except Exception as e:
                self._log(f"⚠️ Bing CN 搜索异常（{keyword}）：{e}", level="WARN")
            return []

        # -------------------------------------------------
        # ② 为每个关键字搜索并抓取页面
        # -------------------------------------------------
        search_keywords = [query] + self._extract_keywords(query)[:2]

        for kw in search_keywords:
            try:
                urls = _search_urls(kw)
                if not urls:
                    self._log(f"⚠️ 未能获得任何搜索结果（关键词：{kw}）", level="WARN")
                    continue

                for url in urls:
                    # 抓取正文（限制字数防止超时）
                    page_text = self._download_page_text(url, headers)
                    if not page_text:
                        continue

                    # LLM 生成摘要（必须使用模型！）
                    summary = self._summarize_with_llm(page_text[:3000])
                    if not summary:
                        continue

                    # 写入知识库（如果有 knowledge_manager）
                    if self.knowledge_manager:
                        add_res = self.knowledge_manager.add_knowledge(
                            topic=kw,
                            content=summary,
                            content_type="text",
                            source="web",
                            tags=["web_search", kw],
                        )
                        if add_res.get("success"):
                            fetched.append(
                                {
                                    "title": kw,
                                    "url": url,
                                    "summary": summary,
                                    "knowledge_id": add_res.get("knowledge_id"),
                                }
                            )
            except Exception as e:
                self._log(f"⚠️ 网络抓取异常（关键词：{kw}）：{e}", level="WARN")
                continue

        self._log(f"✅ 完成网络抓取，得到 {len(fetched)} 条有效知识")
        return fetched


    # -----------------------------------------------------------------
    # LLM 摘要实现（统一使用 call_ollama_safe）
    # -----------------------------------------------------------------
    def _summarize_with_llm(self, raw_text: str) -> str:
        """
        将一段较长文本交给 Ollama 生成 250~400 字的中文摘要。
        若模型调用失败则返回截断的前 250 字（兜底）。
        **务必使用模型进行学习**（此函数是唯一的 LLM 调用点）。
        """
        if not raw_text:
            return ""

        prompt = (
            "请对以下内容进行精炼摘要，长度控制在 250~400 字之间，"
            "保留关键信息与结论，去掉冗余描述。仅返回摘要正文，不要出现任何前缀、说明或代码块。"
            "\n--- 正文开始 ---\n"
            f"{raw_text[:3000]}\n"
            "--- 正文结束 ---"
        )
        try:
            resp = call_ollama_safe(
                [{"role": "system", "content": prompt}], timeout=SAFE_TIMEOUT, retry=2
            )
            if resp.get("success"):
                return resp.get("content", "").strip()
        except Exception as e:
            self._log(f"⚠️ LLM 摘要调用异常：{e}", level="WARN")
        # 兜底：返回原文前 250 字
        return raw_text[:250].strip() + "..."

    # -----------------------------------------------------------------
    # 页面正文抓取（带长度限制，去噪）
    # -----------------------------------------------------------------
    def _download_page_text(self, url: str, headers: Dict[str, str]) -> str:
        """
        抓取网页正文，最多返回 2 000 字。
        对常见噪声标签（script、style、header、footer、nav、noscript）进行剔除。
        """
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code != 200:
                return ""
            soup = BeautifulSoup(resp.text, "lxml")
            for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
                tag.decompose()
            text = soup.get_text(separator="\n", strip=True)
            # 合并多余空行
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            clean = "\n".join(lines)
            return clean[:2000]  # 截断至 2 000 字
        except Exception as e:
            self._log(f"⚠️ 下载页面失败 {url}: {e}", level="WARN")
            return ""

    # -----------------------------------------------------------------
    # 关键词抽取（供网络搜索使用）
    # -----------------------------------------------------------------
    def _extract_keywords(self, text: str) -> List[str]:
        """
        从文本中抽取技术关键字或常用词，以提高搜索命中率。
        """
        tech_terms = [
            "python", "api", "文件", "网页", "天气", "时间", "代码", "插件",
            "数据库", "网络", "系统", "功能", "方法", "类", "模块",
            "b站", "bilibili", "视频", "热点", "排行榜",
        ]
        low = text.lower()
        kw = [t for t in tech_terms if t in low]
        if not kw:
            kw = [w for w in text.split() if len(w) > 1][:3]
        return kw if kw else [text[:20]]

    # ==========================================================
    # 5️⃣ 学习资源获取（组合本地 KB、网络、跨项目经验）
    # ==========================================================
    def find_learning_resources(self, knowledge_gap: Dict[str, Any], max_resources: int = 10) -> List[Dict[str, Any]]:
        """
        根据 `knowledge_gap` 综合检索学习资源。
        - 本地知识库（`_find_existing_resources`）  
        - 网络（如果在线则调用 `_search_web_resources`）  
        - 跨工程经验（如果启用）  
        返回统一结构的资源列表（后续会统一评估质量）。
        """
        keywords = self._extract_keywords(knowledge_gap.get("user_request", ""))
        resources: List[Dict[str, Any]] = []

        # 1️⃣ 本地知识库
        resources.extend(self._find_existing_resources(keywords))

        # 2️⃣ 网络抓取（仅在在线模式下）
        if self.is_online:
            resources.extend(self._search_web_resources(keywords))

        # 3️⃣ 跨工程经验（可选）
        if self.cross_project_learning_enabled:
            resources.extend(self._search_cross_project_experience(keywords))

        # 对所有资源进行质量评估并按得分排序
        for r in resources:
            r["quality_score"] = self._evaluate_resource_quality(r)
        resources.sort(key=lambda x: x["quality_score"], reverse=True)

        # 截断至上限
        return resources[:max_resources]

    # -----------------------------------------------------------------
    # 本地知识库检索（基于 knowledge_manager）
    # -----------------------------------------------------------------
    def _find_existing_resources(self, keywords: List[str]) -> List[Dict[str, Any]]:
        """
        在本地 `knowledge_manager` 中搜索匹配的知识条目。
        返回结构如：
        {
            "type": "knowledge_base",
            "title": "...",
            "content": "...",
            "source": "internal",
            "confidence": 0.87,
            ...
        }
        """
        if not self.knowledge_manager:
            return []
        results: List[Dict[str, Any]] = []
        for kw in keywords:
            hits = self.knowledge_manager.search_knowledge(
                kw, min_confidence=MIN_SEARCH_QUALITY
            )
            for k in hits:
                results.append(
                    {
                        "type": "knowledge_base",
                        "title": k.get("topic", "未命名"),
                        "content": k.get("content", ""),
                        "source": "knowledge_base",
                        "confidence": k.get("confidence", 0.5),
                    }
                )
        return results

    # -----------------------------------------------------------------
    # 网络搜索（统一调用 `fetch_online_knowledge`）
    # -----------------------------------------------------------------
    def _search_web_resources(self, keywords: List[str]) -> List[Dict[str, Any]]:
        """
        通过 `fetch_online_knowledge` 为每个关键字进行网络抓取，
        合并结果后返回统一结构（已包含 `quality_score` 待后续评估）。
        """
        fetched: List[Dict[str, Any]] = []
        for kw in keywords[:3]:  # 只取前 3 个关键词防止爆炸
            fetched.extend(self.fetch_online_knowledge(kw, max_results=3))
        # 统一结构
        resources = []
        for f in fetched:
            resources.append(
                {
                    "type": "web",
                    "title": f.get("title", "未命名"),
                    "summary": f.get("summary", ""),
                    "url": f.get("url", ""),
                    "source": "web_search",
                }
            )
        return resources

    # --------------------------------------------------------------
    # 私有实现：检索跨项目经验（类内部实现）
    # --------------------------------------------------------------
    def _search_cross_project_experience(self, keywords: List[str]) -> List[Dict[str, Any]]:
        """
        私有实现：在本地经验库（experience_index.json）中检索包含关键字的经验。
        返回结构与网络/知识库资源统一，示例：
        {
            "type": "cross_project_experience",
            "title": "...",
            "summary": "...",
            "source": "cross_project",
            "confidence": 0.7,
        }
        """
        exp_file = os.path.join(self.experience_db_dir, "experience_index.json")
        if not os.path.isfile(exp_file):
            return []

        try:
            with open(exp_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            experiences = data.get("experiences", [])
        except Exception:
            return []

        matched: List[Dict[str, Any]] = []
        for exp in experiences:
            join_str = f"{exp.get('topic', '')} {exp.get('solution', '')}"
            if any(kw.lower() in join_str.lower() for kw in keywords):
                matched.append(
                    {
                        "type": "cross_project_experience",
                        "title": exp.get("topic", "未命名"),
                        "summary": exp.get("solution", ""),
                        "source": "cross_project",
                        "confidence": exp.get("confidence", 0.5),
                    }
                )
        return matched


    # -----------------------------------------------------------------
    # 资源质量评估（GAP‑15）
    # -----------------------------------------------------------------
    def _evaluate_resource_quality(self, resource: Dict[str, Any]) -> float:
        """
        根据资源来源、置信度、是否已验证等因素返回 0~1 的质量分数。
        该分数用于后续资源排序与学习策略决策。
        """
        base_score = 0.5
        source_weights = {
            "official_documentation": 0.95,
            "github": 0.85,
            "stack_overflow": 0.90,
            "knowledge_base": 0.70,
            "search_engine": 0.60,
            "blog": 0.55,
            "baidu": 0.50,
            "cross_project": 0.80,
        }
        src = resource.get("source", "").lower()
        for key, weight in source_weights.items():
            if key in src:
                base_score = weight
                break

        # 若资源已经带有置信度 / 质量分数则融合
        if "confidence" in resource:
            base_score = (base_score + float(resource["confidence"])) / 2
        if resource.get("verified"):
            base_score = min(1.0, base_score + 0.10)

        return round(min(1.0, max(0.0, base_score)), 3)

    # ==========================================================
    # 6️⃣ 学习方法选择（依据目标类型与资源质量）
    # ==========================================================
    def select_learning_method(self, knowledge_gap: Dict[str, Any], resources: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        根据 `knowledge_gap` 的类型和可用资源质量选择适合的学习策略（strategy）。
        每个策略都有 `id`、`name`、`steps`、`success_rate` 等字段。
        只返回 **已启用且成功率 >= 0.6** 的策略中最合适的那一个。
        """
        gap_type = knowledge_gap.get("gap_type", "unknown")
        # 预定义的策略映射（简化示例，可自行扩展）
        mapping = {
            "missing_plugin": ["strategy_004", "strategy_002"],
            "api_knowledge": ["strategy_003", "strategy_001"],
            "code_knowledge": ["strategy_002", "strategy_004"],
            "capability_gap": ["strategy_005", "strategy_001"],
            "execution_error": ["strategy_002", "strategy_004"],
            "unknown": ["strategy_005"],
        }
        # 若资源中出现跨工程经验，提升对应策略
        if any(r["type"] == "cross_project_experience" for r in resources):
            mapping["capability_gap"] = ["strategy_006", "strategy_005"]
            mapping["execution_error"] = ["strategy_006", "strategy_002"]

        candidates = mapping.get(gap_type, ["strategy_005"])
        selected = None
        for sid in candidates:
            strat = next((s for s in self.strategies if s["id"] == sid), None)
            if strat and strat.get("success_rate", 0) >= 0.6:
                selected = strat
                break
        # 若仍未匹配，则选成功率最高的策略
        if not selected:
            selected = max(self.strategies, key=lambda s: s.get("success_rate", 0))
        self._log(f"📖 选定学习方法：{selected['name']}（ID={selected['id']})")
        # 计数 + 持久化
        selected["usage_count"] = selected.get("usage_count", 0) + 1
        self._save_strategies()
        return selected

    # ==========================================================
    # 7️⃣ 统一学习执行入口（核心流程）
    # ==========================================================
    def execute_learning(self, knowledge_gap: Dict[str, Any],
                         method: Dict[str, Any],
                         resources: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        执行一次完整的学习任务。
        步骤概览：
        1️⃣ 按 `method["steps"]` 逐步执行（内部通过 `_execute_learning_step`）。
        2️⃣ 记录每一步耗时、成功与返回结果。
        3️⃣ 根据成功情况写入知识库或跨项目经验库。
        4️⃣ 将完整日志保存到 `learning_history.json`（调用 `_save_learning_log`）。
        返回值结构与原实现保持一致，供 `AIController` 调用。
        """
        log_entry: Dict[str, Any] = {
            "id": hashlib.md5(
                f"{knowledge_gap.get('user_request', '')}{time.time()}".encode()
            ).hexdigest()[:16],
            "start_time": datetime.utcnow().isoformat(),
            "end_time": None,
            "knowledge_gap": knowledge_gap,
            "method_id": method.get("id", "unknown"),
            "method_name": method.get("name", "unknown"),
            "steps_completed": [],
            "errors": [],
            "knowledge_gained": [],
            "experience_saved": None,
            "success": False,
            "time_spent_minutes": 0,
        }

        try:
            for idx, step_desc in enumerate(method.get("steps", []), 1):
                self._log(f"📝 执行学习步骤 {idx}/{len(method['steps'])}: {step_desc}")
                step_res = self._execute_learning_step(
                    step_desc, idx, knowledge_gap, resources
                )
                step_res["step_time"] = datetime.utcnow().isoformat()
                log_entry["steps_completed"].append(
                    {
                        "step": idx,
                        "description": step_desc,
                        "success": step_res.get("success", False),
                        "result": step_res.get("result", ""),
                        "method": step_res.get("method", "original"),
                    }
                )
                if not step_res.get("success"):
                    # 记录错误但继续执行后续步骤（除非致命错误在 step 中自行抛出）
                    log_entry["errors"].append(step_res.get("error", "未知错误"))
            # 完成所有步骤后统计
            log_entry["end_time"] = datetime.utcnow().isoformat()
            start = datetime.fromisoformat(log_entry["start_time"])
            end = datetime.fromisoformat(log_entry["end_time"])
            log_entry["time_spent_minutes"] = max(
                (end - start).total_seconds() / 60, 0.5
            )
            # 达到 60% 步骤成功即视为整体成功
            success_cnt = sum(1 for s in log_entry["steps_completed"] if s["success"])
            log_entry["success"] = success_cnt >= len(method["steps"]) * 0.6

            # ------------------- 7.1 写入知识库（若成功） -------------------
            if log_entry["success"] and self.knowledge_manager:
                solution_text = self._compile_learning_solutions(
                    knowledge_gap, log_entry, resources
                )
                if solution_text:
                    add_res = self.knowledge_manager.add_knowledge(
                        topic=f"如何：{knowledge_gap.get('user_request','')[:30]}",
                        content=solution_text,
                        content_type="solution",
                        source="auto_learning",
                        tags=[knowledge_gap.get("gap_type", "unknown"), "solution"],
                    )
                    if add_res.get("success"):
                        log_entry["knowledge_gained"].append(
                            add_res.get("knowledge_id")
                        )
            # ------------------- 7.2 保存跨项目经验（若涉及执行错误） -------------------
            if (
                log_entry["success"]
                and knowledge_gap.get("gap_type") in ["execution_error", "capability_gap"]
            ):
                exp_id = self.save_cross_project_experience(
                    project_id="global",
                    topic=knowledge_gap.get("user_request", "")[:100],
                    solution=self._compile_learning_solutions(
                        knowledge_gap, log_entry, resources
                    ),
                    confidence=0.7,
                    tags=[knowledge_gap.get("gap_type", "unknown"), "solution"],
                )
                log_entry["experience_saved"] = exp_id

            # ------------------- 7.3 持久化学习日志 -------------------
            self._save_learning_log(log_entry)
            self._log(
                f"✅ 学习完成：{'成功' if log_entry['success'] else '失败'}，耗时 {log_entry['time_spent_minutes']:.2f} 分钟"
            )
            return log_entry
        except Exception as exc:
            log_entry["errors"].append(str(exc))
            log_entry["end_time"] = datetime.utcnow().isoformat()
            self._save_learning_log(log_entry)
            self._log(f"❌ 学习异常：{exc}", level="ERROR")
            return log_entry

    # -----------------------------------------------------------------
    # 单步学习执行（内部调度，各步骤实现集中在此）
    # -----------------------------------------------------------------
    def _execute_learning_step(self, step: str, step_num: int,
                               knowledge_gap: Dict[str, Any],
                               resources: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        根据步骤描述调用对应内部实现。
        步骤示例：
        - “搜索资源”
        - “对比内容”
        - “实践验证”
        - “存储知识”
        若出现未实现的步骤则返回通用成功（保持流程向前）。
        """
        try:
            if step_num == 1 and ("搜索" in step or "获取" in step):
                return self._step_search_resources(knowledge_gap, resources)
            if step_num == 2 and ("对比" in step or "分析" in step):
                return self._step_analyze_content(knowledge_gap, resources)
            if step_num == 3 and ("实践" in step or "验证" in step):
                return self._step_practice_verification(knowledge_gap, resources)
            if step_num == 4 and ("存储" in step or "记录" in step):
                return self._step_store_knowledge(knowledge_gap, resources)
            # 默认通用步骤（可做额外扩展）
            time.sleep(1)  # 模拟耗时
            return {"success": True, "result": f"通用步骤完成：{step}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # -----------------------------------------------------------------
    # 步骤实现：搜索资源（使用网络抓取或跨项目经验）
    # -----------------------------------------------------------------
    def _step_search_resources(self, knowledge_gap: Dict[str, Any],
                               resources: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        搜索资源的实现：
        - 若已提供 `resources`（如跨项目经验、网络抓取），直接返回成功。
        - 若资源为空且在线，则尝试一次网络抓取（兜底）。
        """
        if resources:
            return {"success": True, "result": f"已有 {len(resources)} 条资源", "resources": resources}
        if self.is_online:
            try:
                keywords = self._extract_keywords(knowledge_gap.get("user_request", ""))
                extra = self.fetch_online_knowledge(" ".join(keywords), max_results=3)
                if extra:
                    return {"success": True, "result": f"网络搜索得到 {len(extra)} 条资源", "resources": extra}
            except Exception as e:
                return {"success": False, "error": f"网络搜索异常：{e}"}
        return {"success": False, "error": "未找到资源"}

    # -----------------------------------------------------------------
    # 步骤实现：内容对比/一致性分析
    # -----------------------------------------------------------------
    def _step_analyze_content(self, knowledge_gap: Dict[str, Any],
                              resources: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        对资源进行内容一致性分析（计算标题相似度平均值）。
        若资源数量不足则直接返回成功。
        """
        if len(resources) < 2:
            return {"success": True, "result": "资源数量不足，跳过对比"}

        titles = [r.get("title", "").lower() for r in resources if r.get("title")]
        if len(titles) < 2:
            return {"success": True, "result": "标题信息不足，跳过对比"}

        sims = []
        for i in range(len(titles)):
            for j in range(i + 1, len(titles)):
                set_i = set(titles[i].split())
                set_j = set(titles[j].split())
                inter = len(set_i & set_j)
                union = len(set_i | set_j)
                if union:
                    sims.append(inter / union)
        avg_sim = sum(sims) / len(sims) if sims else 0.0
        return {
            "success": True,
            "result": f"内容一致性平均相似度 {avg_sim:.2f}",
            "consistency": {"score": avg_sim},
        }

    # -----------------------------------------------------------------
    # 步骤实现：实践验证（执行代码片段或调用插件）
    # -----------------------------------------------------------------
    def _step_practice_verification(self, knowledge_gap: Dict[str, Any],
                                    resources: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        若资源中包含代码类知识（`content_type == 'code'`），使用
        `core.code_executor.CodeExecutor` 实际执行一次，以验证有效性。
        """
        if not self.knowledge_manager:
            return {"success": True, "result": "未配置 KnowledgeManager，跳过实践"}

        # 取前 3 条可能的代码片段进行尝试
        candidates = self.knowledge_manager.search_knowledge(
            knowledge_gap.get("user_request", ""), min_confidence=0.4, limit=3
        )
        for cand in candidates:
            if cand.get("content_type") == "code":
                try:
                    from .code_executor import CodeExecutor
                    exec_res = CodeExecutor(self.log_callback).execute(
                        cand["content"], "学习验证"
                    )
                    if exec_res.get("success"):
                        return {
                            "success": True,
                            "result": "代码实践验证成功",
                            "verification": exec_res,
                        }
                except Exception as e:
                    continue
        return {"success": True, "result": "未发现可执行代码，跳过实践"}

    # -----------------------------------------------------------------
    # 步骤实现：写入知识库（将资源摘要或自生成的解决方案写入 KB）
    # -----------------------------------------------------------------
    def _step_store_knowledge(self, knowledge_gap: Dict[str, Any],
                              resources: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        将资源中的关键信息或自动生成的解决方案写入 KnowledgeManager。
        若均失败则返回错误。
        """
        if not self.knowledge_manager:
            return {"success": False, "error": "未配置 KnowledgeManager"}

        # 优先使用资源的摘要（如果有）
        for r in resources:
            content = r.get("summary") or r.get("snippet") or r.get("content")
            if content:
                add_res = self.knowledge_manager.add_knowledge(
                    topic=knowledge_gap.get("user_request", "")[:30],
                    content=content,
                    content_type="text",
                    source=r.get("source", "auto_learning"),
                    tags=[knowledge_gap.get("gap_type", "unknown")],
                )
                if add_res.get("success"):
                    return {
                        "success": True,
                        "result": f"知识已写入（ID={add_res.get('knowledge_id')})",
                        "knowledge_id": add_res.get("knowledge_id"),
                    }

        # 若资源均无可写入内容，则尝试模型直接生成
        prompt = (
            f"请根据以下需求提供一个简洁的中文解决方案（约 300 字）：\n"
            f"{knowledge_gap.get('user_request', '')}"
        )
        resp = call_ollama_safe(
            [{"role": "system", "content": prompt}], timeout=SAFE_TIMEOUT
        )
        if resp.get("success"):
            solution = resp.get("content", "").strip()
            add_res = self.knowledge_manager.add_knowledge(
                topic=knowledge_gap.get("user_request", "")[:30],
                content=solution,
                content_type="solution",
                source="auto_learning",
                tags=[knowledge_gap.get("gap_type", "unknown")],
            )
            if add_res.get("success"):
                return {
                    "success": True,
                    "result": f"模型直接生成的解决方案已写入（ID={add_res.get('knowledge_id')})",
                    "knowledge_id": add_res.get("knowledge_id"),
                }
        return {"success": False, "error": "未能写入任何知识"}

    # -----------------------------------------------------------------
    # 8️⃣ 编译学习阶段产生的解决方案（用于写入 KnowledgeManager）
    # -----------------------------------------------------------------
    def _compile_learning_solutions(self, knowledge_gap: Dict[str, Any],
                                   learning_log: Dict[str, Any],
                                   resources: List[Dict[str, Any]]) -> Optional[str]:
        """
        将学习过程中的关键信息组织成一段完整的中文解决方案文本，
        供后续写入知识库或经验库使用。
        """
        lines = [f"学习需求：{knowledge_gap.get('user_request', '')}"]
        lines.append(f"学习时间：{learning_log.get('start_time', '')}")
        lines.append(f"使用方法：{learning_log.get('method_name', '')}")

        # 收集每一步的成功摘要
        for step in learning_log.get("steps_completed", []):
            if step.get("success") and step.get("result"):
                lines.append(f"- {step['description']}: {step['result']}")

        # 如果有资源摘要，也加入
        if resources:
            lines.append("参考资源摘要：")
            for r in resources[:3]:
                title = r.get("title") or r.get("url", "未命名")
                summary = r.get("summary") or r.get("snippet") or ""
                lines.append(f"* {title}: {summary[:200]}...")

        solution = "\n".join(lines)
        return solution if solution else None

    # ==========================================================
    # 9️⃣ 跨工程经验库相关（保存、更新置信度、查询）
    # ==========================================================
    def save_cross_project_experience(self,
                                     project_id: str,
                                     topic: str,
                                     solution: str,
                                     chapter_range: str = "",
                                     confidence: float = 0.7,
                                     tags: Optional[List[str]] = None) -> str:
        """
        将一次学习所得的经验保存至跨工程经验库（JSON 文件）。
        若库文件不存在则自动创建。返回生成的经验 ID。
        """
        exp_file = os.path.join(self.experience_db_dir, "experience_index.json")
        if os.path.isfile(exp_file):
            with open(exp_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = {"experiences": []}

        new_exp = {
            "id": hashlib.md5(f"{project_id}{topic}{datetime.utcnow()}".encode()).hexdigest()[:16],
            "project_id": project_id,
            "topic": topic,
            "solution": solution,
            "chapter_range": chapter_range,
            "confidence": confidence,
            "tags": tags or [],
            "saved_at": datetime.utcnow().isoformat(),
            "usage_count": 0,
            "success_count": 0,
            "fail_count": 0,
        }
        data["experiences"].append(new_exp)
        # 只保留最近 1000 条经验（防止文件无限膨胀）
        data["experiences"] = data["experiences"][-1000:]

        try:
            with open(exp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self._log(f"💾 跨工程经验已写入（ID={new_exp['id']})")
            return new_exp["id"]
        except Exception as e:
            self._log(f"⚠️ 写入跨工程经验失败：{e}", level="ERROR")
            return ""

    def _search_cross_project_experience(self, keywords: List[str]) -> List[Dict[str, Any]]:
        """
        内部调用：仅在 `find_learning_resources` 中使用，已实现同名公开方法。
        """
        return self._search_cross_project_experience(keywords)

    def update_experience_confidence(self, experience_id: str, success: bool) -> None:
        """
        根据一次使用结果更新经验的置信度（GAP‑11）。
        计算方式：成功率 + 使用次数权重 + 时间衰减。
        """
        exp_file = os.path.join(self.experience_db_dir, "experience_index.json")
        if not os.path.isfile(exp_file):
            return
        try:
            with open(exp_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            for exp in data.get("experiences", []):
                if exp["id"] == experience_id:
                    exp["usage_count"] = exp.get("usage_count", 0) + 1
                    if success:
                        exp["success_count"] = exp.get("success_count", 0) + 1
                    else:
                        exp["fail_count"] = exp.get("fail_count", 0) + 1

                    total = exp["success_count"] + exp["fail_count"]
                    if total > 0:
                        base = exp["success_count"] / total
                        usage_weight = min(total / 10, 1.0)
                        # 时间衰减：最近使用的经验置信度更高（最多 100 天）
                        if "last_used" in exp:
                            days = (datetime.utcnow() - datetime.fromisoformat(exp["last_used"])).days
                            time_decay = max(0.8, 1.0 - days / 100)
                        else:
                            time_decay = 1.0
                        new_conf = base * usage_weight * time_decay
                        exp["confidence"] = round(max(0.1, min(0.95, new_conf)), 3)
                    exp["last_used"] = datetime.utcnow().isoformat()
                    break
            with open(exp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self._log(f"⚠️ 更新经验置信度失败：{e}", level="WARN")

    # ==========================================================
    # 10️⃣ 学习日志持久化（统一结构，供 AIController 调用）
    # ==========================================================
    def _save_learning_log(self, log_entry: Dict[str, Any]) -> None:
        """
        将一次学习过程的完整日志追加写入 `learning_history.json`。
        若文件不存在则自动创建。日志结构示例：
        {
            "type": "initial_learning" | "learning",
            "timestamp": "2026-05-08T19:45:12.123456",
            "result": {...},
            "goals": [...]
        }
        """
        try:
            # 更新内存缓存
            self.learning_logs.append(log_entry)

            # 读取已有文件
            if os.path.isfile(self.learning_log_file):
                with open(self.learning_log_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
            else:
                data = {"logs": []}
            data["logs"].append(log_entry)

            # 写回磁盘
            with open(self.learning_log_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self._log(f"🗒 学习日志已写入（当前累计 {len(data['logs'])} 条）")
        except Exception as e:
            self._log(f"⚠️ 保存学习日志失败：{e}", level="ERROR")

    def save_learning_history(self) -> None:
        """
        手动持久化当前内存中的学习日志（主要在程序关闭时由
        `AIController` 调用）。此函数相当于 `_save_learning_log` 的
        只写入一次完整列表版本。
        """
        try:
            with open(self.learning_log_file, "w", encoding="utf-8") as f:
                json.dump({"logs": self.learning_logs}, f, ensure_ascii=False, indent=2)
            self._log("✅ 学习历史已完整持久化")
        except Exception as e:
            self._log(f"⚠️ 持久化学习历史失败：{e}", level="ERROR")

    # ==========================================================
    # 11️⃣ 统计信息（供系统状态报告）
    # ==========================================================
    def get_learning_statistics(self) -> Dict[str, Any]:
        """
        读取 `learning_history.json` 并返回统计信息：
        - total_learning_sessions
        - success_rate
        - avg_time_minutes
        - by_method（每种学习方法的成功/总次数）
        - cross_project_experiences（经验库中条目数）
        """
        try:
            if os.path.isfile(self.learning_log_file):
                with open(self.learning_log_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                logs = data.get("logs", [])
            else:
                logs = []

            total = len(logs)
            success = sum(1 for l in logs if l.get("success"))
            avg_time = (
                sum(l.get("time_spent_minutes", 0) for l in logs) / total
                if total else 0
            )
            by_method: Dict[str, Dict[str, int]] = {}
            for l in logs:
                m = l.get("method_name", "unknown")
                by_method.setdefault(m, {"total": 0, "success": 0})
                by_method[m]["total"] += 1
                if l.get("success"):
                    by_method[m]["success"] += 1

            cross_exp_cnt = self._count_cross_project_experiences()
            return {
                "total_learning_sessions": total,
                "success_rate": success / total if total else 0,
                "avg_time_minutes": round(avg_time, 2),
                "by_method": by_method,
                "cross_project_experiences": cross_exp_cnt,
            }
        except Exception as e:
            return {"error": str(e)}

    def _count_cross_project_experiences(self) -> int:
        """统计经验库中的经验数量（最多 1000 条）"""
        exp_file = os.path.join(self.experience_db_dir, "experience_index.json")
        if not os.path.isfile(exp_file):
            return 0
        try:
            with open(exp_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return len(data.get("experiences", []))
        except Exception:
            return 0

    # ==========================================================
    # 12️⃣ 记录学习/执行失败（供错误库 / 经验采集）
    # ==========================================================
    def record_failure(self, user_request: str, error_reason: str,
                       solution_attempted: str) -> None:
        """
        将一次失败记录到学习日志中，格式示例：
        {
            "timestamp": "...",
            "type": "failure",
            "user_request": "...",
            "error_reason": "...",
            "solution_attempted": "..."
        }
        """
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "type": "failure",
            "user_request": user_request,
            "error_reason": error_reason,
            "solution_attempted": solution_attempted,
        }
        self.learning_logs.append(entry)
        # 立即写入磁盘，防止程序异常退出导致丢失
        self.save_learning_history()

    # ==========================================================
    # 13️⃣ 兼容旧接口（供 AIController 旧代码调用）
    # ==========================================================
    # 这些方法保持与旧版本同名，仅内部转发到新实现，避免破坏向后兼容。
    def generate_first_run_learning_goals(self):
        """兼容旧调用：返回首次学习目标列表"""
        return self.get_initial_learning_goals()

    def execute_first_run_learning(self, time_budget_minutes: int = MAX_LEARNING_MINUTES):
        """兼容旧调用：执行首次学习"""
        return self.perform_initial_learning(time_budget_minutes)

    # ==========================================================
    # 14️⃣ 文件/目录初始化（私有）
    # ==========================================================
    def _init_files(self):
        """确保所有必要的文件/目录存在，若不存在则创建空结构。"""
        for fp in [self.learning_log_file, self.strategy_file, self.first_run_file]:
            dirname = os.path.dirname(fp)
            os.makedirs(dirname, exist_ok=True)
            if not os.path.isfile(fp):
                with open(fp, "w", encoding="utf-8") as f:
                    if fp == self.strategy_file:
                        json.dump({"strategies": self._default_strategies()}, f, indent=2, ensure_ascii=False)
                    else:
                        json.dump({} if fp == self.first_run_file else {"logs": []}, f, indent=2, ensure_ascii=False)

    # -----------------------------------------------------------------
    # 默认学习策略（与原实现保持一致，仅作数据结构）
    # -----------------------------------------------------------------
    def _default_strategies(self) -> List[Dict[str, Any]]:
        return [
            {
                "id": "strategy_001",
                "name": "多源对比学习",
                "type": "multi_source",
                "steps": [
                    "1. 搜索至少 3 个独立来源",
                    "2. 对比内容一致性",
                    "3. 评估来源权威度",
                    "4. 提取共同知识点",
                    "5. 存储高置信度知识",
                ],
                "suitable_for": ["事实性知识", "API 用法", "技术概念"],
                "success_rate": 0.85,
                "avg_time_minutes": 2,
                "usage_count": 0,
            },
            {
                "id": "strategy_002",
                "name": "实践验证学习",
                "type": "practice",
                "steps": [
                    "1. 获取知识内容",
                    "2. 编写测试代码",
                    "3. 执行测试",
                    "4. 分析结果",
                    "5. 记录成功/失败",
                ],
                "suitable_for": ["代码知识", "命令用法", "功能实现"],
                "success_rate": 0.9,
                "avg_time_minutes": 3,
                "usage_count": 0,
            },
            {
                "id": "strategy_003",
                "name": "文档深度学习",
                "type": "documentation",
                "steps": [
                    "1. 找到官方文档",
                    "2. 阅读相关章节",
                    "3. 提取关键信息",
                    "4. 整理知识结构",
                    "5. 存储到知识库",
                ],
                "suitable_for": ["API 文档", "库的使用", "规范标准"],
                "success_rate": 0.95,
                "avg_time_minutes": 5,
                "usage_count": 0,
            },
            {
                "id": "strategy_004",
                "name": "试错学习",
                "type": "trial_error",
                "steps": [
                    "1. 根据已有知识尝试",
                    "2. 记录错误信息",
                    "3. 分析错误原因",
                    "4. 调整方案重试",
                    "5. 成功后记录方案",
                ],
                "suitable_for": ["探索性任务", "未知问题", "创新需求"],
                "success_rate": 0.6,
                "avg_time_minutes": 5,
                "usage_count": 0,
            },
            {
                "id": "strategy_005",
                "name": "网络搜索学习",
                "type": "web_search",
                "steps": [
                    "1. 生成搜索关键词",
                    "2. 执行网络搜索",
                    "3. 筛选高质量结果",
                    "4. 提取有用信息",
                    "5. 验证信息准确性",
                ],
                "suitable_for": ["新知识", "最新信息", "问题解决方案"],
                "success_rate": 0.75,
                "avg_time_minutes": 2,
                "usage_count": 0,
            },
            {
                "id": "strategy_006",
                "name": "跨工程经验学习",
                "type": "cross_project",
                "steps": [
                    "1. 检索历史工程经验",
                    "2. 评估经验适用性",
                    "3. 调整经验适配新工程",
                    "4. 应用经验解决问题",
                    "5. 记录新经验",
                ],
                "suitable_for": [
                    "工程配置",
                    "质量检查",
                    "角色成长",
                    "世界观揭露",
                ],
                "success_rate": 0.8,
                "avg_time_minutes": 3,
                "usage_count": 0,
            },
        ]

    def _load_strategies(self):
        """读取 `learning_strategies.json`，若不存在则使用默认策略。"""
        try:
            with open(self.strategy_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.strategies = data.get("strategies", self._default_strategies())
        except Exception:
            self.strategies = self._default_strategies()

    def _save_strategies(self):
        """持久化学习策略（更新使用计数等信息）。"""
        try:
            with open(self.strategy_file, "w", encoding="utf-8") as f:
                json.dump({"strategies": self.strategies}, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self._log(f"⚠️ 保存学习策略失败：{e}", level="WARN")

    # --------------------------------------------------------------
    # 检测网络连通性（私有）
    # --------------------------------------------------------------
    def _check_internet_connection(self) -> bool:
        try:
            requests.get("https://www.baidu.com", timeout=15)
            return True
        except Exception:
            self._log("⚠️ 网络连接不可用，将使用离线模式", level="WARN")
            return False

    # ==============================================================
    #  END OF SelfLearningEngine
    # ==============================================================
