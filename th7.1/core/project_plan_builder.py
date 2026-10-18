# core/project_plan_builder.py
"""
Project Plan Builder – 将已经完整的项目规格（dict）转化为 **详细工程计划**（JSON + Markdown）。
"""

import json
import os
import re
from datetime import datetime
from typing import Dict, Any, List

class ProjectPlanBuilder:
    def __init__(self, log_callback=None):
        self._log_callback = log_callback or (lambda m: print(f"[ProjectPlanBuilder] {m}"))

        def _log(msg: str, level: str = "INFO"):
            if level == "ERROR":
                self._log_callback(f"[ProjectPlanBuilder][ERROR] {msg}")
            elif level == "WARN":
                self._log_callback(f"[ProjectPlanBuilder][WARN] {msg}")
            else:
                self._log_callback(f"[ProjectPlanBuilder] {msg}")

        self._log = _log

    def elaborate_plan(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        """生成机器可读的项目计划（JSON）。🌟 核心升级：在工程阶段自主推演写作约束与死锁规则。"""
        import re
        import json
        
        try:
            # 1. 渲染 Prompt (带 Hardcode 兜底)
            try:
                from .prompt_manager import PromptManager
                prompt = PromptManager.render("project_plan", project_meta=spec)
            except Exception as tpl_e:
                self._log(f"⚠️ project_plan 模板加载失败: {tpl_e}，启用 Hardcode 兜底", level="WARN")
                prompt = f"""你是严谨的小说工程规划师。请根据以下项目元信息生成工程计划。
项目元信息：{json.dumps(spec, ensure_ascii=False)}

【核心任务：推演写作约束与死锁规则】：
除了常规的工程计划，你必须根据题材和主角，自主推演以下“写作约束（writing_constraints）”，用于后续质检扣分：
1. 姓名死锁：主角姓名，以及绝对禁止替换的套路名（如林凡、叶凡、萧炎）。
2. 代词死锁：主角的性别及对应的指代词（他/她/它），防止代词混乱。
3. 题材纯净度禁用词：根据题材推演 5-10 个绝对禁止在正文中出现的“串味词/套路词”（如古装禁“维度/量子”，科幻禁“修仙/天道”）。

返回纯 JSON：
{{
  "selling_points": ["卖点1", "卖点2"],
  "power_system": {{"early": "前期体系", "mid": "中期体系", "late": "后期体系"}},
  "core_conflict": "核心冲突描述",
  "climax_nodes": ["高潮节点1", "高潮节点2"],
  "writing_constraints": {{
    "protagonist_name": "主角名",
    "forbidden_names": ["套路名1", "套路名2"],
    "protagonist_pronoun": "他/她",
    "forbidden_words": ["禁用词1", "禁用词2", "禁用词3", "禁用词4", "禁用词5"]
  }}
}}
只输出纯 JSON，绝不输出 Markdown 标记或废话！"""
            
            # 2. 调用 LLM
            from .llm_utils import llm_call
            resp = llm_call(
                user_prompt=prompt,
                system_prompt="你是一个底层的 JSON 数据生成 API。只输出合法的纯 JSON，绝不输出 Markdown 标记或废话。",
                temperature=0.5,
                enforce_json=True,
                max_tokens=8192
            )
            
            if not resp.get("success"):
                raise ValueError(f"LLM 调用失败: {resp.get('error')}")
                
            raw_content = resp.get("content")
            plan = None
            
            # 3. 防弹 JSON 解析
            if isinstance(raw_content, dict):
                plan = raw_content
            elif isinstance(raw_content, str):
                text = re.sub(r"^```(?:json)?\s*", "", raw_content, flags=re.MULTILINE)
                text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE).strip()
                try:
                    plan = json.loads(text)
                except json.JSONDecodeError:
                    match = re.search(r'\{.*\}', text, re.DOTALL)
                    if match:
                        json_str = match.group()
                        json_str = re.sub(r",\s*([}\]])", r"\1", json_str)
                        try:
                            plan = json.loads(json_str)
                        except Exception:
                            pass
                            
            if isinstance(plan, dict):
                self._log("✅ LLM 工程计划生成并解析成功（含写作约束推演）")
                
                # 🌟 核心兜底：如果 LLM 没生成 writing_constraints，强制补全默认值
                if "writing_constraints" not in plan or not isinstance(plan["writing_constraints"], dict):
                    plan["writing_constraints"] = {
                        "protagonist_name": spec.get("main_character", "主角"),
                        "forbidden_names": ["林凡", "叶凡", "萧炎", "楚风"],
                        "protagonist_pronoun": "他",
                        "forbidden_words": ["维度", "量子", "时间线", "底层逻辑", "禁忌"]
                    }
                return plan
                
            raise ValueError(f"LLM 返回的内容无法解析为字典，原始内容前 200 字: {str(raw_content)[:200]}")
                
        except Exception as e:
            self._log(f"⚠️ ProjectPlanBuilder 生成失败，回退占位方案：{e}", level="WARN")
            return {
                "title": spec.get("title", "未命名"), 
                "genre": spec.get("genre", "通用"),
                "selling_points": ["核心卖点1（待人工补全）", "核心卖点2（待人工补全）"],
                "power_system": {"early": "前期体系（待补全）", "mid": "中期体系（待补全）", "late": "后期体系（待补全）"},
                "core_conflict": "核心冲突（待人工补全）", 
                "climax_nodes": ["高潮节点1（待补全）", "高潮节点2（待补全）"],
                "writing_constraints": {
                    "protagonist_name": spec.get("main_character", "主角"),
                    "forbidden_names": ["林凡", "叶凡", "萧炎"],
                    "protagonist_pronoun": "他",
                    "forbidden_words": ["维度", "量子", "时间线", "底层逻辑", "禁忌"]
                },
                "notes": "占位项目计划（LLM 解析失败，请手动修改此文件）"
            }

    def save_plan(self, plan: Dict[str, Any], project_dir: str) -> None:
        """将方案写入两份文件。"""
        os.makedirs(project_dir, exist_ok=True)
        json_path = os.path.join(project_dir, "project_plan.json")
        md_path = os.path.join(project_dir, "project_plan.md")
 
        # 强制注入约束
        if "constraints" not in plan:
            plan["constraints"] = {}
        plan["constraints"]["locked_rules"] = [
            "绝对禁止自行脑补、替换主角名字，必须严格使用 task_plan 中的 main_character。",
            "严格遵循设定的“揭露逻辑约束”，禁止 Info-dumping。",
            f"必须严格模仿写作风格画像：{plan.get('writing_style_profile', '默认平铺直叙')}"
        ]
        
        # JSON 原子写入
        json_tmp = json_path + ".tmp"
        try:
            with open(json_tmp, "w", encoding="utf-8") as jf:
                json.dump(plan, jf, ensure_ascii=False, indent=2)
                jf.flush(); os.fsync(jf.fileno())
            os.replace(json_tmp, json_path)
            self._log(f"JSON 计划已写入 {json_path}")
        except Exception as e:
            self._log(f"写入 JSON 失败: {e}", level="ERROR")
            if os.path.exists(json_tmp): os.remove(json_tmp)

        # Markdown 原子写入 (🌟 核心修复：增加严格的类型校验，防止 LLM 返回字符串导致 .items() 崩溃)
        md_tmp = md_path + ".tmp"
        try:
            # 提取并校验数据
            selling_points = plan.get("selling_points", [])
            if not isinstance(selling_points, list): selling_points = [str(selling_points)]
            
            power_system = plan.get("power_system", {})
            if not isinstance(power_system, dict): power_system = {"default": str(power_system)}
            
            climax_nodes = plan.get("climax_nodes", [])
            if not isinstance(climax_nodes, list): climax_nodes = [str(climax_nodes)]

            with open(md_tmp, "w", encoding="utf-8") as mf:
                mf.write(f"# {plan.get('title', '未命名')} 项目工程计划\n\n")
                mf.write(f"_生成时间: {datetime.now().isoformat()}_\n\n")
                
                mf.write("## 核心卖点\n" + "\n".join([f"- {p}" for p in selling_points]) + "\n\n")
                
                mf.write("## 力量体系演进\n")
                for k, v in power_system.items(): 
                    mf.write(f"- **{k}**: {v}\n")
                mf.write("\n")
                
                mf.write(f"## 核心冲突\n{plan.get('core_conflict', '待补全')}\n\n")
                
                mf.write("## 预期高潮节点\n" + "\n".join([f"- {n}" for n in climax_nodes]) + "\n\n")
                
                mf.flush(); os.fsync(mf.fileno())
            os.replace(md_tmp, md_path)
            self._log(f"Markdown 计划已写入 {md_path}")
        except Exception as e:
            self._log(f"写入 Markdown 失败: {e}", level="ERROR")
            if os.path.exists(md_tmp): os.remove(md_tmp)

    # =================================================================
    # 内部统一调用 LLM
    # =================================================================
    def _call_llm(self, messages: List[Dict[str, str]], timeout: int = 30) -> Dict[str, Any]:
        """
        对 ``call_ollama_safe`` 的一次封装，统一异常处理并返回
        ``{'success': bool, 'content': str, 'error': str}`` 结构。
        """
        try:
            return call_ollama_safe(messages, timeout=timeout)
        except Exception as e:  # pragma: no cover
            self._log(f"[ProjectPlanBuilder] LLM 调用异常: {e}")
            return {"success": False, "error": str(e)}
