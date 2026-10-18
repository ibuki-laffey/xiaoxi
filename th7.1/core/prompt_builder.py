import json
from typing import Any, Dict
from .prompt_manager import PromptManager
from .log_mixin import LogMixin
from .config import PROMPT_TEMPLATES_DIR

try:
    from .config import STRICT_JSON
except Exception:
    STRICT_JSON = False

class PromptBuilder(LogMixin):
    """统一的 Prompt 生成器（模板 + 硬编码回退）。"""

    _FALLBACK_TEMPLATES: Dict[str, str] = {
        "outline_generation": (
            "请根据以下项目元信息（JSON）与约束信息（JSON）生成完整的大纲 JSON，"
            "字段必须包含 title、genre、total_chapters、chapter_outlines（每章 title 与 summary）。"
            "以下是项目元信息：{project_meta}\n以下是约束信息：{constraints}"
        ),
        "intent_understanding": (
            "分析用户输入的意图，返回 JSON：{{\"primary_intent\": \"...\", "
            "\"confidence\": 0.0, \"reasoning\": \"...\", \"generation_mode\": \"all\"}}。"
            "输入：{user_input}"
        ),
    }

    def __init__(self, controller: Any = None):
        self.controller = controller

    def render(self, template_name: str, **kwargs: Any) -> str:
        """统一渲染入口：模板渲染 -> 硬编码回退 -> Strict JSON 处理。"""
        try:
            rendered = PromptManager.render(template_name, **kwargs)
        except Exception as e:
            self._log(f"[PromptBuilder] ⚠️ 渲染模板 {template_name} 失败，回退硬编码 Prompt：{e}", level="WARN")
            fallback_tpl = self._FALLBACK_TEMPLATES.get(template_name)
            if fallback_tpl:
                try:
                    rendered = fallback_tpl.format(**kwargs)
                except Exception as e2:
                    self._log(f"[PromptBuilder] ❌ 硬编码 Prompt 渲染异常（{template_name}）：{e2}", level="ERROR")
                    rendered = f"请根据以下信息生成内容：{json.dumps(kwargs, ensure_ascii=False)}"
            else:
                rendered = f"请根据以下信息生成内容：{json.dumps(kwargs, ensure_ascii=False)}"

        strict_flag = kwargs.pop("strict_json", False)
        if strict_flag or STRICT_JSON:
            strict_instruction = (
                "\n\n请 **只返回合法 JSON**，"
                "不允许出现任何解释、代码块（```）、前后文字或多余标点。"
                "如果需要举例，请把例子直接写在对应字段的值里。"
            )
            rendered = rendered.rstrip() + strict_instruction

        self._log(f"[PromptBuilder] ✅ 使用模板渲染成功：{template_name}", level="DEBUG")
        return rendered

    def _log(self, msg: str, level: str = "INFO"):
        """统一日志：优先使用 controller._log，否则使用 LogMixin 或 print。"""
        try:
            if self.controller and hasattr(self.controller, "_log"):
                self.controller._log(msg, level=level)
            else:
                super()._log(msg, level=level)
        except Exception:
            print(f"[PromptBuilder][{level}] {msg}")

_PROMPT_BUILDER = None

def get_prompt_builder(controller: Any = None) -> PromptBuilder:
    """返回全局唯一的 PromptBuilder 实例（懒加载）。"""
    global _PROMPT_BUILDER
    if _PROMPT_BUILDER is None:
        _PROMPT_BUILDER = PromptBuilder(controller=controller)
    elif controller is not None:
        _PROMPT_BUILDER.controller = controller
    return _PROMPT_BUILDER
