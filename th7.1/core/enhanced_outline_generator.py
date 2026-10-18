import json
import re
import os
from datetime import datetime
from typing import Any, Dict, Optional

from .paths import ensure_path
from .prompt_manager import PromptManager
from .config import OUTLINE_TEMPERATURES, SAFE_TIMEOUT, call_ollama_safe, VECTOR_DB_ENABLED, STRICT_JSON
from core.prompt_builder import get_prompt_builder
from .quality_inspector import QualityInspector

class EnhancedOutlineGenerator:
    """大纲生成增强器：多温度 LLM -> 向量记忆 -> ReasoningEngine -> 学习循环。"""

    def __init__(self, controller):
        self.controller = controller

    def _log(self, msg: str, level: str = "INFO"):
        try:
            if hasattr(self.controller, "_log"): self.controller._log(msg, level=level)
            else: print(f"[EnhancedOutlineGenerator][{level}] {msg}")
        except Exception:
            print(f"[EnhancedOutlineGenerator][{level}] {msg}")

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

    def _safe_replace_placeholders(self, template: str, task_plan: dict) -> str:
        """🌟 模糊替换占位符，兼容 {}, {{}}, <> 等 LLM 可能输出的格式。"""
        meta_str = json.dumps(task_plan, ensure_ascii=False, indent=2)
        constraints_str = json.dumps(task_plan.get("constraints", {}), ensure_ascii=False, indent=2)
        template = re.sub(r"\{?\{?project_meta\}?\}?", meta_str, template)
        template = re.sub(r"\{?\{?constraints\}?\}?", constraints_str, template)
        return template

    def _build_prompt(self, task_plan: dict, user_prompt: str) -> str:
        pb = get_prompt_builder(controller=self.controller)
        return pb.render(
            "outline_generation",
            project_meta=json.dumps(task_plan, ensure_ascii=False, indent=2),
            constraints=json.dumps(task_plan.get("constraints", {}), ensure_ascii=False, indent=2),
        )

    def generate(self, task_plan: Dict[str, Any], user_prompt: str) -> Dict[str, Any]:
        task_id = task_plan.get("task_id")
        if not task_id: return {"action": "chat", "explanation": "❌ 任务 ID 缺失，无法生成大纲"}

        existing = self.controller.outline_manager.get_outline(task_id)
        if existing:
            return {"action": "outline_generated", "explanation": "📋 大纲已生成，直接返回。", "outline": existing, "task_id": task_id}

        outline = self._llm_multi_temp(task_plan)
        if outline: return self._finalize_success(task_plan, outline, task_id)

        outline = self._vector_fallback(task_plan)
        if outline: return self._finalize_success(task_plan, outline, task_id)

        outline = self._reasoning_fallback(task_plan)
        if outline: return self._finalize_success(task_plan, outline, task_id)

        outline = self._learning_cycle(task_plan, user_prompt)
        if outline: return self._finalize_success(task_plan, outline, task_id)

        self._log("❌ 所有大纲生成手段均失败", level="ERROR")
        if hasattr(self.controller, "learning_engine"):
            self.controller.learning_engine.save_cross_project_experience(
                project_id=self.controller.current_project_id, topic="outline_generation_failure",
                solution="所有手段均未产出合法大纲", tags=["outline", "failure"]
            )
        return {"action": "chat", "explanation": "❌ 大纲生成失败，请检查提示或手动编写大纲。", "task_id": task_id}

    def _llm_multi_temp(self, task_plan: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        pb = get_prompt_builder(controller=self.controller)
        base_prompt = pb.render(
            "outline_generation",
            project_meta=json.dumps(task_plan, ensure_ascii=False, indent=2),
            constraints=json.dumps(task_plan.get("constraints", {}), ensure_ascii=False, indent=2),
            strict_json=True,
        )

        best_candidate, best_score = None, -1.0
        for temp in OUTLINE_TEMPERATURES:
            self._log(f"🔧 大纲生成尝试（temperature={temp})")
            try:
                resp = call_ollama_safe([{"role": "system", "content": base_prompt}], timeout=SAFE_TIMEOUT, retry=2, options={"temperature": temp})
                if not resp.get("success"): continue
                
                candidate = self._extract_json(resp.get("content", ""))
                if not candidate: continue
                
                if not self._validate_outline(candidate): continue
                
                inspector = QualityInspector(log_callback=self.controller._log)
                score = inspector.score_outline(candidate)
                self._log(f"✅ LLM 生成合法大纲（temp={temp})，评分 {score:.1f}")
                
                if score > best_score: best_score, best_candidate = score, candidate
                if score >= 70: return candidate
            except Exception as e:
                self._log(f"⚠️ LLM 第 {temp} 次尝试异常：{e}", level="WARN")

        if best_candidate and best_score > 0: return best_candidate
        return None

    def _vector_fallback(self, task_plan: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not getattr(self.controller, "vector_memory", None): return None
        try:
            query = {"genre": task_plan.get("genre", "通用"), "total_chapters": task_plan.get("total_chapters", 1), "title_keywords": task_plan.get("title", "")}
            candidates = self.controller.vector_memory.search_similar_outline(query, top_k=3)
            if not candidates: return None

            similar = candidates[0]
            similar["title"] = task_plan.get("title", similar.get("title", "未命名项目"))
            similar["genre"] = task_plan.get("genre", similar.get("genre", "通用"))
            similar["total_chapters"] = task_plan.get("total_chapters", similar.get("total_chapters", 1))
            similar["words_per_chapter"] = task_plan.get("words_per_chapter", similar.get("words_per_chapter", 2500))

            if self._validate_outline(similar): return similar
            return None
        except Exception: return None

    def _reasoning_fallback(self, task_plan: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            engine_res = self.controller.reasoning_engine.generate_outline(task_plan)
            if engine_res.get("success"):
                outline = engine_res["outline"]
                if self._validate_outline(outline): return outline
        except Exception: pass
        return None

    def _learning_cycle(self, task_plan: Dict[str, Any], user_prompt: str) -> Optional[Dict[str, Any]]:
        if not hasattr(self.controller, "learning_engine"): return None

        summary_text = "多温度 LLM 未返回合法 JSON。\n向量记忆检索未命中相似大纲。\nReasoningEngine 生成失败。"
        meta_prompt = f"""你是 Prompt 改写专家。以下是本次大纲生成的全部失败原因：
{summary_text}
请基于用户原始需求（{user_prompt!r}）给出一个全新的 Prompt，该 Prompt 必须仍使用以下两个占位符：
{{project_meta}} – 包含任务的完整元信息
{{constraints}} – 包含用户自定义的约束
在 Prompt 最后一定要加入 “只返回合法 JSON，禁止出现任何解释”。只返回 Prompt 本身。"""

        resp = call_ollama_safe([{"role": "system", "content": meta_prompt}], timeout=SAFE_TIMEOUT, retry=2)
        if not resp.get("success"): return None

        improved_prompt = resp.get("content", "").strip()
        if not improved_prompt: return None

        try:
            rendered = self._safe_replace_placeholders(improved_prompt, task_plan)
            resp2 = call_ollama_safe([{"role": "system", "content": rendered}], timeout=SAFE_TIMEOUT, retry=2, options={"temperature": 1.0})
            if not resp2.get("success"): return None

            candidate = self._extract_json(resp2.get("content", ""))
            if candidate and self._validate_outline(candidate):
                if hasattr(self.controller.learning_engine, "record_successful_prompt"):
                    self.controller.learning_engine.record_successful_prompt(original_prompt=user_prompt, improved_prompt=improved_prompt, result_outline=candidate)
                return candidate
            return None
        except Exception: return None

    def _validate_outline(self, outline: Any) -> bool:
        """🌟 放宽校验：兼容 LLM 自由发挥的合理描述字段，释放灵活性。"""
        if not isinstance(outline, dict): return False
        if not outline.get("title") or not isinstance(outline["title"], str): return False
        
        chapters = outline.get("chapters") or outline.get("chapter_outlines") or outline.get("volume_breakdown")
        if not isinstance(chapters, list) or len(chapters) == 0: return False
        
        for chap in chapters:
            if not isinstance(chap, dict): return False
            if not chap.get("title") and not chap.get("name"): return False
            has_desc = any(chap.get(k) for k in ["summary", "overview", "description", "plot", "content", "keywords", "core_goal"])
            if not has_desc: return False
        return True

    def _finalize_success(self, task_plan: Dict[str, Any], outline: Dict[str, Any], task_id: str) -> Dict[str, Any]:
        if "total_chapters" in outline: task_plan["total_chapters"] = outline["total_chapters"]
        else: task_plan["total_chapters"] = len(outline.get("chapters") or outline.get("chapter_outlines"))
        if "words_per_chapter" in outline: task_plan["words_per_chapter"] = outline["words_per_chapter"]

        try:
            outline_path = self.controller.outline_manager.get_outline_path(task_id)
            ensure_path(outline_path)
            
            # 🌟 工业级原子写入：防止进程中断导致文件半写损坏
            tmp_path = outline_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(outline, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, outline_path)
        except Exception as e:
            return {"action": "chat", "explanation": f"❌ 大纲写入失败：{e}", "task_id": task_id}

        extra_fields = {"total_chapters": task_plan.get("total_chapters"), "words_per_chapter": task_plan.get("words_per_chapter")}
        self.controller.task_planner.update_plan_status(
            task_id, "outline_generated",
            {"outline_created": True, "outline_id": outline.get("title", ""), "outline_generated_at": datetime.now().isoformat(), "outline_file": outline_path},
            extra_fields=extra_fields,
        )

        explanation = f"📋 大纲已成功生成！\n📖 书名：{outline.get('title', '')}\n🗂️ 章节数：{task_plan['total_chapters']}\n🏷️ 题材：{outline.get('genre', '')}\n\n请前往项目目录检查大纲文件，确认无误后回复 “确认大纲”。"
        return {"action": "outline_generated", "explanation": explanation, "outline": outline, "task_id": task_id}
