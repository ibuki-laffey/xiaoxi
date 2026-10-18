import os
import json
import re
import time
import hashlib
import requests
from datetime import datetime
from typing import Dict, Any, List, Tuple, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import deque

from .config import (
    OLLAMA_URL, SAFE_TIMEOUT, OUTLINE_SUBTASK_SIZE, OUTLINE_MAX_WORKERS,
    OUTLINE_MAX_RPS, OUTLINE_MAX_RETRIES_PER_SUBTASK, OUTLINE_TEMPERATURES,
    OUTLINE_FALLBACK_MODELS, OUTLINE_CACHE_TTL, MODEL_NAME,
)
from .log_mixin import LogMixin
from .disk_cache_manager import DiskCacheManager
from .vector_memory import VectorMemory
from .outline_manager import OutlineManager

class OutlineMultiGenerator(LogMixin):
    """多子任务大纲生成器：通过 ThreadPoolExecutor 并行调度子任务。"""

    def __init__(self, log_callback=None):
        super().__init__(log_callback)
        self.disk_cache = DiskCacheManager(log_callback)
        self.vector_mem = VectorMemory(log_callback)
        self._rps_window = deque(maxlen=OUTLINE_MAX_RPS)
        self._last_subtask_id = None

    def _extract_json(self, text: str) -> Optional[Dict[str, Any]]:
        """🌟 健壮的 JSON 提取：剥离 Markdown，首尾括号定位，清理尾部逗号。"""
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
        text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE).strip()
        start_idx = text.find('{')
        end_idx = text.rfind('}')
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            json_str = text[start_idx:end_idx + 1]
            json_str = re.sub(r",\s*([}\]])", r"\1", json_str)
            try: return json.loads(json_str)
            except json.JSONDecodeError: pass
        return None

    def generate_full_outline(self, task_plan: Dict[str, Any]) -> Dict[str, Any]:
        start_ts = time.time()
        try:
            subtasks = self._split_to_subtasks(task_plan)
            self._log(f"🔧 任务拆分为 {len(subtasks)} 条子任务 (subtask_size={OUTLINE_SUBTASK_SIZE})")
        except Exception as e:
            return {"success": False, "error": f"子任务拆分错误: {e}"}

        subtask_results = self._run_subtasks(subtasks, task_plan)
        merge_res = self._validate_and_merge(subtask_results, task_plan)

        if merge_res.get("success"):
            try:
                om = OutlineManager(log_callback=self._log)
                write_res = om.write_outline(task_plan["task_id"], merge_res["outline"])
                if not write_res.get("success"): raise RuntimeError(write_res.get("error", "未知写盘错误"))
            except Exception as e:
                return {"success": True, "outline": merge_res["outline"], "statistics": self._make_statistics(subtasks, subtask_results, start_ts), "warning": f"写盘异常: {e}"}
        else:
            self._log("⚠️ 合并子任务失败，启用规则兜底（确保返回合法大纲）")
            fallback = self._fallback_rule_based(task_plan)
            om = OutlineManager(log_callback=self._log)
            om.write_outline(task_plan["task_id"], fallback["outline"])
            merge_res = fallback

        stats = self._make_statistics(subtasks, subtask_results, start_ts)
        return {"success": True, "outline": merge_res["outline"], "statistics": stats}

    def _split_to_subtasks(self, task_plan: Dict[str, Any]) -> List[Dict[str, Any]]:
        total_chapters = int(task_plan.get("total_chapters", 1))
        size = max(1, OUTLINE_SUBTASK_SIZE)
        subtasks = []
        for start in range(1, total_chapters + 1, size):
            end = min(start + size - 1, total_chapters)
            sub_id = f"{task_plan['task_id']}_{start}_{end}"
            sub_ctx = {
                "title": task_plan.get("title", "未命名"), "genre": task_plan.get("genre", "通用"),
                "main_character": task_plan.get("main_character", "未知"),
                "words_per_chapter": int(task_plan.get("words_per_chapter", 2500)), "chapter_range": (start, end),
            }
            subtasks.append({"subtask_id": sub_id, "chapter_range": (start, end), "target_word_count": (end - start + 1) * sub_ctx["words_per_chapter"], "prompt_context": sub_ctx})
        return subtasks

    def _run_subtasks(self, subtasks: List[Dict[str, Any]], task_plan: Dict[str, Any]) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
        results = []
        with ThreadPoolExecutor(max_workers=OUTLINE_MAX_WORKERS) as executor:
            future_to_sub = {executor.submit(self._process_single_subtask, sub, task_plan): sub for sub in subtasks}
            for future in as_completed(future_to_sub):
                sub = future_to_sub[future]
                try:
                    res = future.result()
                    results.append((sub, res))
                except Exception as e:
                    results.append((sub, {"success": False, "error": str(e)}))
        return results

    def _process_single_subtask(self, subtask: Dict[str, Any], task_plan: Dict[str, Any]) -> Dict[str, Any]:
        self._last_subtask_id = subtask["subtask_id"]
        cache_key = self._make_cache_key(subtask["subtask_id"], subtask["prompt_context"])
        
        cached = self.disk_cache.get(cache_key)
        if cached: return {"success": True, "outline": cached, "from_cache": True}

        similar = self._vector_lookup(subtask["prompt_context"])
        if similar:
            self.disk_cache.set(cache_key, similar, ttl=OUTLINE_CACHE_TTL)
            return {"success": True, "outline": similar, "from_cache": False}

        attempts, max_attempts = 0, OUTLINE_MAX_RETRIES_PER_SUBTASK
        temperatures = OUTLINE_TEMPERATURES
        fallback_models = list(OUTLINE_FALLBACK_MODELS)
        current_model = MODEL_NAME

        while attempts < max_attempts:
            for temp in temperatures:
                self._throttle()
                try:
                    llm_res = self._call_llm_for_subtask(subtask, task_plan, temperature=temp, model_name=current_model)
                    ok, reason = self._quick_validate_subtask_result(llm_res, subtask)
                    if ok:
                        self.disk_cache.set(cache_key, llm_res, ttl=OUTLINE_CACHE_TTL)
                        self._store_vector(subtask["subtask_id"], llm_res)
                        return {"success": True, "outline": llm_res, "from_cache": False}
                except Exception as e:
                    self._log(f"❌ 子任务 {subtask['subtask_id']} LLM 调用异常: {e}")

            if fallback_models:
                current_model = fallback_models.pop(0)
                attempts += 1
            else:
                break

        return {"success": False, "error": f"子任务 {subtask['subtask_id']} 生成失败"}

    def _make_cache_key(self, subtask_id: str, prompt_context: Dict) -> str:
        raw = json.dumps({"id": subtask_id, "ctx": prompt_context}, sort_keys=True).encode()
        return hashlib.sha256(raw).hexdigest()[:12]

    def _throttle(self) -> None:
        now = time.monotonic()
        self._rps_window.append(now)
        if len(self._rps_window) == self._rps_window.maxlen:
            elapsed = now - self._rps_window[0]
            if elapsed < 1.0: time.sleep(1.0 - elapsed)

    def _call_llm_for_subtask(self, subtask: Dict[str, Any], task_plan: Dict[str, Any], temperature: float, model_name: str = None) -> Dict[str, Any]:
        prompt = self._build_subtask_prompt(subtask, task_plan)
        use_model = model_name or MODEL_NAME
        payload = {"model": use_model, "messages": [{"role": "user", "content": prompt}], "options": {"temperature": temperature, "num_predict": 8192}, "stream": False}
        
        resp = requests.post(OLLAMA_URL, json=payload, timeout=SAFE_TIMEOUT)
        resp.raise_for_status()
        raw = resp.json().get("message", {}).get("content", "")
        
        extracted = self._extract_json(raw)
        if not extracted: raise ValueError("LLM 未返回有效 JSON")
        return extracted

    def _build_subtask_prompt(self, subtask: Dict[str, Any], task_plan: Dict[str, Any]) -> str:
        """🌟 优化 Prompt：释放 LLM 创造力，不再死板限制标题格式和严格字数。"""
        ctx = subtask["prompt_context"]
        start, end = subtask["chapter_range"]
        prompt = f"""请为以下小说项目生成 **{end - start + 1}** 章节的详细大纲（只返回 JSON），**不要任何文字解释**，**不要代码块**：

项目标题      : {ctx["title"]}
题材          : {ctx["genre"]}
主角          : {ctx["main_character"]}

章节范围      : 第 {start} 章 - 第 {end} 章
每章节目标字数: {ctx["words_per_chapter"]}

**输出约束**：
1. JSON 顶层必须包含键 `chapters`，其值为长度恰好等于 {end - start + 1} 的列表；
2. 每个章节对象必须包含：
   - `title`（具有吸引力的章节标题，如“第 {start} 章 初入江湖”）
   - `summary`（详细的剧情摘要，包含核心冲突、人物动作和场景转换，100-400字均可，请根据剧情张力自由发挥）
   - `keywords`（列表，至少 3 条，必须与章节内容、人物或设定相关）
3. 请使用 **中文**，保持自然、流畅的写作风格。

**示例返回**（请严格遵守结构，仅返回 JSON）：
{{"chapters":[{{"title":"第 {start} 章 标题","summary":"...详细剧情...","keywords":["...","...","..."]}}, …]}}

现在请直接返回完整 JSON（不要任何前缀/后缀文字）。"""
        return prompt

    def _quick_validate_subtask_result(self, llm_res: Dict, subtask: Dict) -> Tuple[bool, str]:
        """🌟 放宽校验：兼容 LLM 自由发挥的合理描述字段，释放灵活性。"""
        chapters = llm_res.get("chapters")
        if not isinstance(chapters, list): return False, "缺少 chapters 列表"

        start, end = subtask["chapter_range"]
        expected_cnt = end - start + 1
        if len(chapters) != expected_cnt: return False, f"章节数量不匹配（期望 {expected_cnt}，实际 {len(chapters)}）"

        for idx, chap in enumerate(chapters, start=start):
            if not chap.get("title"): return False, f"第 {idx} 章缺少 title"
            has_desc = any(chap.get(k) for k in ["summary", "description", "plot", "content", "overview"])
            if not has_desc: return False, f"第 {idx} 章缺少剧情描述"
        return True, ""

    def _vector_lookup(self, prompt_context: Dict) -> Optional[Dict]:
        try:
            results = self.vector_mem.search_similar_outline(prompt_context, top_k=1)
            if results and isinstance(results[0], dict) and "chapters" in results[0]: return results[0]
        except Exception: pass
        return None

    def _store_vector(self, subtask_id: str, outline: Dict) -> None:
        try:
            self.vector_mem.add_content(content_id=subtask_id, content=json.dumps(outline, ensure_ascii=False), metadata={"type": "outline_subtask", "generated_at": datetime.now().isoformat()})
        except Exception: pass

    def _validate_and_merge(self, subtask_results: List[Tuple[Dict, Dict]], task_plan: Dict[str, Any]) -> Dict[str, Any]:
        failures = [res for _, res in subtask_results if not res.get("success")]
        if failures: return {"success": False, "error": "子任务生成不完整"}

        ordered = sorted(subtask_results, key=lambda x: x[0]["chapter_range"][0])
        all_chapters = []
        for sub, res in ordered: all_chapters.extend(res["outline"]["chapters"])

        full_outline = {
            "title": task_plan.get("title", "未命名"), "genre": task_plan.get("genre", "通用"),
            "total_chapters": task_plan.get("total_chapters", len(all_chapters)),
            "words_per_chapter": task_plan.get("words_per_chapter", 2500), "chapters": all_chapters,
        }

        from .quality_inspector import QualityInspector
        qi = QualityInspector(log_callback=self._log)
        check_res = qi.inspect_outline(full_outline, task_plan)
        if not check_res.get("passed"): return {"success": False, "error": "全局质量检查未通过"}
        return {"success": True, "outline": full_outline}

    def _fallback_rule_based(self, task_plan: Dict[str, Any]) -> Dict[str, Any]:
        total = int(task_plan.get("total_chapters", 1))
        words_per = int(task_plan.get("words_per_chapter", 2500))
        fallback = {"title": task_plan.get("title", "未命名"), "genre": task_plan.get("genre", "通用"), "total_chapters": total, "words_per_chapter": words_per, "chapters": []}
        for i in range(1, total + 1):
            fallback["chapters"].append({"title": f"第 {i} 章", "summary": f"本章概要（待补充），目标字数约 {words_per}。", "keywords": [task_plan.get("genre", "通用")]})
        return {"success": True, "outline": fallback}

    def _make_statistics(self, subtasks: List[Dict[str, Any]], results: List[Tuple[Dict[str, Any], Dict[str, Any]]], start_ts: float) -> Dict[str, Any]:
        total = len(subtasks)
        success = sum(1 for _, r in results if r.get("success"))
        cache_hits = sum(1 for _, r in results if r.get("from_cache"))
        return {"subtasks_total": total, "subtasks_success": success, "cache_hits": cache_hits, "llm_calls": success - cache_hits, "duration_seconds": round(time.time() - start_ts, 2)}
