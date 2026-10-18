# core/highlight_director.py
"""
高光时刻导演 (Highlight Director) - 语义级动态触发版
废除死板的关键词匹配，通过 AI 对“场景节拍”的语义级深度评估，
精准识别需要“潜台词博弈、极致情绪、爽点拉扯”的段落，进行微操指导。
"""
from __future__ import annotations
import json
import re
from typing import Dict, Any, List, Optional
from .config import call_ollama_safe

class HighlightDirector:
    def __init__(self, log_callback=None):
        self._log = log_callback if callable(log_callback) else print

    def _robust_extract_json(self, text: str) -> Optional[Dict]:
        if not text: return None
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
        text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE).strip()
        try: return json.loads(text)
        except json.JSONDecodeError: pass
        start, end = text.find('{'), text.rfind('}')
        if start != -1 and end > start:
            try: return json.loads(re.sub(r",\s*([}\]])", r"\1", text[start:end+1]))
            except: pass
        return None

    def analyze_and_direct(self, chapter_num: int, scene_beats: List[Dict], previous_context: str) -> str:
        """
        分析场景节拍，仅对 AI 判定为“高深度需求”的节拍生成微操指令。
        如果没有高深度节拍，直接返回空字符串，不浪费任何 Token。
        """
        if not scene_beats:
            return ""

        # 🌟 核心逻辑：筛选出 AI 判定为“高深度/极高”的节拍
        high_depth_beats = []
        for beat in scene_beats:
            depth = str(beat.get("depth_requirement", "")).lower()
            # 兼容中英文及不同程度的表达
            if depth in ["high", "高", "极高", "深度", "deep", "extreme"]:
                high_depth_beats.append(beat)

        if not high_depth_beats:
            # AI 认为本章全是平铺直叙或普通过渡，无需微操，直接放行
            return ""

        self._log(f"🎬 [HighlightDirector] 识别到 {len(high_depth_beats)} 个高深度节拍，启动微操推演...")

        # 构建针对高深度节拍的推演 Prompt
        beats_desc = "\n".join([
            f"节拍{b.get('beat_id', '?')}: {b.get('action', '')} | 焦点: {b.get('focus_type', '未知')}" 
            for b in high_depth_beats
        ])

        prompt = f"""你是顶级的网文导演。请为第{chapter_num}章中以下“高深度需求”的节拍，设计“微操骨架”。
【需要微操的节拍】：
{beats_desc}

【前情提要】：{previous_context[:1000]}

【导演任务】：
1. 针对上述节拍，设计具体的“情绪张力曲线”（如：压抑->嘲讽->隐忍->爆发）。
2. 设计核心博弈的“潜台词”或“打脸爽点”（角色表面说什么，心里想什么，如何通过动作/环境暗示）。
3. 绝对不要写正文！只输出导演指导指令。

返回纯 JSON：
{{
  "tension_arc": "情绪张力曲线描述(限100字)",
  "subtext_and_pacing": "核心潜台词、博弈逻辑与爽点爆发节奏指导(限200字)",
  "micro_expressions": "关键微表情/动作/环境烘托指导(限100字)"
}}"""

        try:
            resp = call_ollama_safe(
                [{"role": "user", "content": prompt}], 
                options={"temperature": 0.3, "num_predict": 1024, "format": "json"}
            )
            raw = resp.get("content", "") if isinstance(resp, dict) else str(resp)
            data = self._robust_extract_json(raw)
            
            if data:
                directive = "\n【🎬 导演微操最高指令（必须严格执行于高深度节拍）】\n"
                directive += f"1. 情绪张力曲线：{data.get('tension_arc', '')}\n"
                directive += f"2. 潜台词与爽点节奏：{data.get('subtext_and_pacing', '')}\n"
                directive += f"3. 微表情与环境烘托：{data.get('micro_expressions', '')}\n"
                directive += "⚠️ 警告：请将上述骨架完美融入对应节拍的正文描写，通过动作和对话展现，禁止直接旁白解说！\n"
                self._log(f"✅ [HighlightDirector] 微操骨架推演完成！")
                return directive
        except Exception as e:
            self._log(f"⚠️ 微操推演异常: {e}", level="WARN")
            
        return ""
