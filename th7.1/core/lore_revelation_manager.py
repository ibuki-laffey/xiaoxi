# core/lore_revelation_manager.py
import os
import json
import re
from typing import Dict, Any, List, Optional
from .log_mixin import LogMixin
from .llm_utils import llm_call

class LoreRevelationManager(LogMixin):
    def __init__(self, project_dir: str, log_callback=None):
        super().__init__()
        # 🌟 兼容各种签名的日志回调
        self._raw_log = log_callback
        def _safe_log(msg, level="INFO"):
            if self._raw_log:
                try: self._raw_log(msg, level=level)
                except TypeError: self._raw_log(msg)
            else:
                print(f"[{level}] {msg}")
        self._log = _safe_log
        
        self.project_dir = project_dir
        self.plan_dir = os.path.join(project_dir, "system")
        os.makedirs(self.plan_dir, exist_ok=True)
        self.plan_path = os.path.join(self.plan_dir, "lore_revelation_plan.json")
        self.settings_dir = os.path.join(project_dir, "03_设定")

    def _atomic_write_json(self, filepath: str, data: Any) -> None:
        """🌟 工业级原子写入 JSON，防止断电数据丢失。"""
        tmp_path = filepath + ".tmp"
        try:
            dir_name = os.path.dirname(filepath)
            if dir_name: os.makedirs(dir_name, exist_ok=True)
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush(); os.fsync(f.fileno())
            os.replace(tmp_path, filepath)
        except Exception as e:
            if os.path.exists(tmp_path):
                try: os.remove(tmp_path)
                except Exception: pass
            self._log(f"❌ 原子写入失败 ({filepath}): {e}", level="ERROR")

    def _robust_extract_json(self, text: str) -> Optional[Any]:
        """防弹 JSON 提取。"""
        if not text: return None
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
        text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE).strip()
        try: return json.loads(text)
        except json.JSONDecodeError: pass
        for start_char, end_char in [('{', '}'), ('[', ']')]:
            start = text.find(start_char)
            end = text.rfind(end_char)
            if start != -1 and end != -1 and end > start:
                json_str = text[start:end + 1]
                json_str = re.sub(r",\s*([}\]])", r"\1", json_str)
                try: return json.loads(json_str)
                except json.JSONDecodeError: pass
        return None

    def build_initial_plan_from_layered_settings(self, task_plan: Dict, outline: Dict):
        """读取带有 layers 的设定卡片，基于“逻辑约束”自动组装全局揭露计划。"""
        self._log("🧠 [LoreRevelation] 正在解析分层设定卡片，构建初始揭露计划...")
        
        layered_settings = []
        if os.path.exists(self.settings_dir):
            for root, _, files in os.walk(self.settings_dir):
                for f in files:
                    if f.endswith(".json") and f != "分层世界观设定.json":
                        try:
                            with open(os.path.join(root, f), "r", encoding="utf-8") as fp:
                                data = json.load(fp)
                                if isinstance(data, dict) and "layers" in data:
                                    layered_settings.append(data)
                        except Exception: pass

        if not layered_settings:
            # 🌟 核心升级：关闭设定卡片时，降级为“基于大纲的轻量级推演”
            self._log("⚠️ 未找到分层设定卡片，启动【轻量级大纲推演模式】构建基础揭露计划...")
            self._build_lightweight_plan_from_outline(task_plan, outline)
            return

        # 🌟 核心升级：提取精简摘要时，必须包含 reveal_constraints（揭露逻辑约束）
        brief_settings = []
        for setting in layered_settings[:40]:  # 最多取 40 个防超限
            layers_info = []
            for l in setting.get("layers", []):
                if l.get("layer_id") != "iceberg": # 过滤冰山
                    layers_info.append({
                        "layer_name": l.get("layer_name", ""),
                        "reveal_trigger": l.get("reveal_trigger", ""),
                        "reveal_constraints": l.get("reveal_constraints", "无特殊约束") # 🌟 V5.0 核心
                    })
            if layers_info:
                brief_settings.append({
                    "concept": setting.get("concept", "未命名"),
                    "category": setting.get("category", "未分类"),
                    "layers": layers_info
                })

        if not brief_settings: return

        prompt = f"""你是顶级网文主编。以下是小说《{task_plan.get('title', '')}》的【分层设定清单】。
请根据设定的“揭露逻辑约束（reveal_constraints）”，结合【大纲概要】，制定严格的“信息控制时间线”。

【分层设定清单】：
{json.dumps(brief_settings, ensure_ascii=False)}

【大纲概要】：
{json.dumps(outline, ensure_ascii=False)[:3000]}

【任务】：
请规划这些设定在哪些剧情节点（或章节范围）揭露。必须严格遵守 `reveal_constraints`。

输出 JSON 格式的揭露计划：
{{
  "timeline": [
    {{
      "chapter_range": "预估章节范围或剧情节点",
      "targets": [
        {{"concept": "设定名", "layer": "表层/里层", "show_method": "具体的展示手法（Show, Don't Tell）"}}
      ]
    }}
  ],
  "forbidden_icebergs": ["绝对不能写入正文的冰山底牌"]
}}
只输出纯 JSON！"""

        resp = llm_call(user_prompt=prompt, temperature=0.5, enforce_json=True)
        raw_content = resp.get("content", "")
        plan = self._robust_extract_json(raw_content) if isinstance(raw_content, str) else raw_content
        
        if isinstance(plan, dict) and "timeline" in plan:
            self._atomic_write_json(self.plan_path, plan)
            self._log("✅ [LoreRevelation] 初始揭露计划已基于逻辑约束构建完成。")
        else:
            self._log("⚠️ [LoreRevelation] 揭露计划生成失败或格式错误。", level="WARN")

    def _build_lightweight_plan_from_outline(self, task_plan: Dict, outline: Dict):
        """🌟 新增：无设定卡片时的轻量级揭露计划推演（防剧透兜底）"""
        prompt = f"""你是顶级网文主编。当前小说《{task_plan.get('title', '')}》没有详细的分层设定卡片。
请仅根据【大纲概要】，自主推演一个基础的“信息控制与悬念释放时间线”，防止 AI 在正文中过早剧透（Info-dumping）。

【大纲概要】：
{json.dumps(outline, ensure_ascii=False)[:3000]}

【任务】：
规划 3-5 个核心悬念/设定的揭露节点。
输出 JSON：
{{
  "timeline": [
    {{"chapter_range": "预估章节", "targets": [{{"concept": "悬念/设定名", "layer": "表层/里层", "show_method": "展示手法"}}]}}
  ],
  "forbidden_icebergs": ["绝对不能在前半部分剧透的核心底牌"]
}}
只输出纯 JSON！"""
        
        resp = llm_call(user_prompt=prompt, temperature=0.5, enforce_json=True)
        raw_content = resp.get("content", "")
        plan = self._robust_extract_json(raw_content) if isinstance(raw_content, str) else raw_content
        
        if isinstance(plan, dict) and "timeline" in plan:
            self._atomic_write_json(self.plan_path, plan)
            self._log("✅ [LoreRevelation] 轻量级揭露计划已基于大纲构建完成。")
        else:
            self._log("⚠️ [LoreRevelation] 轻量级揭露计划生成失败。", level="WARN")


    def get_chapter_directives(self, task_id: str, chapter_num: int, chapter_outline: Dict) -> str:
        """根据当前章节的大纲，让 AI 严格校验逻辑约束，判断是否允许揭露设定。"""
        if not os.path.exists(self.plan_path): return ""
        try:
            with open(self.plan_path, "r", encoding="utf-8") as f:
                plan = json.load(f)
        except Exception: return ""

        all_layers = []
        for phase in plan.get("timeline", []):
            for target in phase.get("targets", []):
                all_layers.append(target)
        forbidden_icebergs = plan.get("forbidden_icebergs", [])

        if not all_layers and not forbidden_icebergs: return ""

        # 🌟 核心升级：Prompt 强制 AI 校验 reveal_constraints
        prompt = f"""你是严苛的网文主编。当前正在创作第 {chapter_num} 章。
【本章大纲】：{json.dumps(chapter_outline, ensure_ascii=False)}
【当前可用的设定分层及逻辑约束】：{json.dumps(all_layers, ensure_ascii=False)}

【最高指令：逻辑约束校验】：
请严格判断：本章大纲的剧情发展，是否**完全满足**了上述某个设定的 `reveal_constraints`（揭露逻辑约束）？
- 如果满足：请给出极其具体的“展示手法（Show, Don't Tell）”指导。
- 如果不满足：`reveal_target` 必须填 null，并严厉警告禁止在本章揭露该设定！

输出 JSON 格式：
{{
  "reveal_target": "需要揭露的设定名（如不满足约束则填 null）",
  "forbidden_actions": ["禁止使用的生硬解说方式（Info-dumping）"],
  "show_techniques": ["推荐的展示手法（通过动作/对话/环境展示）"]
}}
只输出纯 JSON！"""

        resp = llm_call(user_prompt=prompt, temperature=0.3, enforce_json=True) # 降低温度保证逻辑严谨
        raw_content = resp.get("content", {})
        data = self._robust_extract_json(raw_content) if isinstance(raw_content, str) else raw_content
        
        if not isinstance(data, dict) or not data.get("reveal_target"): 
            # 如果没有满足条件的设定，只返回全局禁忌
            if forbidden_icebergs:
                icebergs_text = "\n".join([f"- ❌ 绝对禁止提及：{ice}" for ice in forbidden_icebergs[:5]])
                return f"⚠️ 全局禁忌（以下属于“冰山底牌”，当前阶段绝对禁止剧透）：\n{icebergs_text}"
            return ""

        forbidden = "\n".join([f"- ❌ {f}" for f in data.get("forbidden_actions", [])])
        techniques = "\n".join([f"- ✅ {t}" for t in data.get("show_techniques", [])])
        
        directive = f"""🌍 【世界观揭露控制 - 第{chapter_num}章专属】
本章剧情已满足逻辑约束，允许展示设定：【{data.get('reveal_target')}】
⚠️ 绝对禁止（Info-dumping 行为）：
{forbidden}
💡 必须采用（Show, Don't Tell 手法）：
{techniques}
【主编警告】：把设定揉碎在角色的动作、对话潜台词和环境反馈中！如果读者感觉你在“上课”，本章直接作废！"""
        
        if forbidden_icebergs:
            icebergs_text = "\n".join([f"- ❌ 绝对禁止提及：{ice}" for ice in forbidden_icebergs[:5]])
            directive += f"\n\n⚠️ 全局禁忌（以下属于“冰山底牌”，当前阶段绝对禁止剧透）：\n{icebergs_text}"
            
        return directive

    def dynamic_patch_plan(self, chapter_num: int, issue_type: str, issue_detail: str):
        """当 QualityInspector 发现 Info-dumping 或越级揭露时调用，动态重构后续计划。"""
        self._log(f"🔧 [LoreRevelation] 触发动态修正 (第{chapter_num}章): {issue_type} - {issue_detail}")
        
        if not os.path.exists(self.plan_path): return
        try:
            with open(self.plan_path, "r", encoding="utf-8") as f:
                current_plan = json.load(f)
        except Exception: return

        prompt = f"""你是网文主编。在第 {chapter_num} 章的写作中，AI 犯了以下设定揭露错误：
【错误类型】：{issue_type} (如：Info-dumping 旁白解说 / 越级揭露深层设定)
【具体表现】：{issue_detail}
【当前揭露计划】：{json.dumps(current_plan, ensure_ascii=False)}

请根据这个失误，**动态重构**后续的揭露计划。
要求：收紧后续设定的 `show_method`，强制增加“通过冲突/代价展示”的约束，杜绝再次出现生硬解说。
输出更新后的完整 JSON 计划。只输出纯 JSON！"""

        resp = llm_call(user_prompt=prompt, temperature=0.4, enforce_json=True)
        raw_content = resp.get("content", "")
        new_plan = self._robust_extract_json(raw_content) if isinstance(raw_content, str) else raw_content
        
        if isinstance(new_plan, dict) and "timeline" in new_plan:
            self._atomic_write_json(self.plan_path, new_plan)
            self._log(f"✅ [LoreRevelation] 计划已动态重构，后续章节将自动纠正节奏。")
