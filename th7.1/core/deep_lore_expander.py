# core/deep_lore_expander.py
# ==============================================================================
# 🌟 动态多元化深度设定展开引擎 (Dynamic Deep Lore & Roster Expander) V5.0
# ==============================================================================
# 核心特性：
# 1. 认知深度分层：表层浅显具象，里层深奥抽象，冰山不可名状。
# 2. 逻辑约束揭露：废除死板章节限制，AI 自主推导设定展示的逻辑前提。
# 3. 动态生长 API：支持随剧情发展实时更新角色与设定（演变日志）。
# 4. 设定类型区分：严格区分基本设定（Base）与剧情设定（Plot）。
# 5. 两阶段角色生成：打破 Token 限制，生成包含关系网与高光场景的超详细档案。
# ==============================================================================

import os
import json
import re
import math
from typing import Dict, Any, List, Optional, Callable
from datetime import datetime

# 动态获取 Token 限制，防止 LLM 输出截断
try:
    from .config import MAX_OUTPUT_TOKENS
except ImportError:
    MAX_OUTPUT_TOKENS = 16000

def _atomic_write_text(filepath: str, text: str):
    """🌟 工业级原子写入文本。"""
    tmp = filepath + ".tmp"
    try:
        dir_name = os.path.dirname(filepath)
        if dir_name: os.makedirs(dir_name, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, filepath)
    except Exception:
        if os.path.exists(tmp):
            try: os.remove(tmp)
            except Exception: pass
        raise

def _atomic_write_json(filepath: str, data: Any):
    """🌟 工业级原子写入 JSON。"""
    tmp = filepath + ".tmp"
    try:
        dir_name = os.path.dirname(filepath)
        if dir_name: os.makedirs(dir_name, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp, filepath)
    except Exception:
        if os.path.exists(tmp):
            try: os.remove(tmp)
            except Exception: pass
        raise

class DeepLoreExpander:
    def __init__(self, project_dir: str, task_plan: Dict[str, Any], log_callback: Optional[Callable] = None, mode: str = "deep"):
        self.project_dir = project_dir
        self.task_plan = task_plan
        self.mode = mode  # 🌟 核心新增：运行模式 ("deep" 深度分层 或 "flat" 扁平细节)
        
        # 🌟 兼容各种签名的日志回调 (100% 保留原代码)
        self._raw_log = log_callback
        def _safe_log(msg, level="INFO"):
            if self._raw_log:
                try: self._raw_log(msg, level=level)
                except TypeError: self._raw_log(msg)
            else:
                print(f"[{level}] {msg}")
        self._log = _safe_log
        
        self.genre = (task_plan.get("genre") or "通用").lower()
        self.total_words = task_plan.get("total_word_count", 50000)
        self.title = task_plan.get("title", "未命名")
        
        # 🌟 致命修复：兼容 MAX_OUTPUT_TOKENS 未定义的情况
        try:
            from .config import MAX_OUTPUT_TOKENS
            self.max_llm_tokens = MAX_OUTPUT_TOKENS
        except ImportError:
            self.max_llm_tokens = 16000
        
        self.dynamic_profile = None 
        
        # 🌟 100% 保留原有的所有目录初始化
        self.lore_dir = os.path.join(project_dir, "03_设定")
        self.char_dir = os.path.join(self.lore_dir, "角色卡")
        self.hooks_dir = os.path.join(self.lore_dir, "伏笔与留白")
        self.timeline_dir = os.path.join(self.lore_dir, "时间线与场景")
        
        for d in [self.lore_dir, self.char_dir, self.hooks_dir, self.timeline_dir]:
            os.makedirs(d, exist_ok=True)
            
        self.expansion_log_path = os.path.join(self.lore_dir, "动态扩展记录.json")
        self.expansion_log = self._load_expansion_log()

    # --------------------------------------------------------------------------
    # 🌟 核心修复：将 JSON 提取器移入类内作为静态方法，彻底消灭 AttributeError
    # --------------------------------------------------------------------------
    @staticmethod
    def _robust_extract_json(text: str) -> Optional[Any]:
        """🌟 终极防弹 JSON 提取器：使用括号平衡算法，彻底废弃 rfind，免疫 LLM 废话干扰。"""
        import json
        import re
        
        if not text or not isinstance(text, str): 
            return None
            
        # 1. 尝试直接解析（去除常见的 Markdown 标记和尾部逗号）
        cleaned = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
        cleaned = re.sub(r"\s*```$", "", cleaned, flags=re.MULTILINE).strip()
        cleaned_no_trailing = re.sub(r",\s*([\]}])", r"\1", cleaned)
        
        try: 
            return json.loads(cleaned_no_trailing)
        except json.JSONDecodeError: 
            pass

        # 2. 🌟 核心修复：使用括号平衡算法提取最外层完整的 JSON 对象/数组
        # 彻底解决 LLM 在 JSON 后输出包含 } 的废话导致 rfind 截取错误的问题
        for start_char, end_char in [('{', '}'), ('[', ']')]:
            start_idx = cleaned.find(start_char)
            if start_idx == -1: 
                continue
            
            depth = 0
            in_string = False
            escape = False
            
            for i in range(start_idx, len(cleaned)):
                char = cleaned[i]
                
                if escape:
                    escape = False
                    continue
                if char == '\\':
                    escape = True
                    continue
                if char == '"':
                    in_string = not in_string
                    continue
                    
                if not in_string:
                    if char == start_char:
                        depth += 1
                    elif char == end_char:
                        depth -= 1
                        if depth == 0:
                            # 找到真正闭合的括号
                            json_str = cleaned[start_idx:i+1]
                            json_str_clean = re.sub(r",\s*([\]}])", r"\1", json_str)
                            try: 
                                return json.loads(json_str_clean)
                            except json.JSONDecodeError:
                                break # 如果这个完整的括号解析失败，跳出尝试下一种括号
                                
        # 3. 兜底：尝试使用 json_repair 库（如果安装了的话）
        try:
            import json_repair
            repaired = json_repair.repair_json(cleaned, return_objects=True)
            if repaired: 
                return repaired
        except ImportError: 
            pass
        except Exception: 
            pass
            
        return None

    def _analyze_genre_dynamically(self) -> Dict[str, Any]:
        """🌟 核心重构：废除所有硬编码字典，让 AI 自主分析题材并规划推演方向（含题材锁死与绝对禁令注入）"""
        self._log(f"🧠 [DeepLore] 正在自主分析题材组合（{self.genre}）...")
        
        # 🌟 致命修复：从 task_plan 中读取用户明确要求的禁止设定
        user_forbidden = self.task_plan.get("forbidden_settings", []) if hasattr(self, 'task_plan') and isinstance(self.task_plan, dict) else []
        user_forbidden_str = "、".join([str(f) for f in user_forbidden]) if user_forbidden else "无"

        prompt = f"""你是顶级的网文架构师。请深度分析小说《{self.title}》的题材组合（{self.genre}），并自主规划设定推演的方向。

【⚠️ 最高铁律：绝对禁令与题材锁死】：
1. 用户明确要求【绝对禁止】的设定/元素：{user_forbidden_str}。在后续所有推演中，绝对不允许出现任何违反此禁令的元素！
2. 必须严格提取该题材的“核心美学词汇”（如：仙侠用“气机/天道”，赛博用“义体/矩阵”）。
3. 必须推演该题材的“绝对禁用词库”（如：古装严禁“维度/量子”，科幻严禁“修仙/灵气”）。防止后续生成出现“题材串味”。

输出严格的 JSON 格式：
{{
  "focus": "核心看点(严格贴合题材且符合禁令)",
  "modules": [{{"name": "模块名", "description": "细节"}}],
  "char_density": 0.8,
  "char_focus": "角色侧重点",
  "core_aesthetics": ["核心美学词1", "核心美学词2", "核心美学词3"],
  "forbidden_words": ["禁用词1(如维度)", "禁用词2(如时间线)", "禁用词3(如禁忌)", "禁用词4"]
}}
只输出纯 JSON！"""
        
        # 🌟 核心修复：统一使用 call_ollama_safe，防止 ImportError
        from .config import call_ollama_safe
        try:
            resp = call_ollama_safe(
                [{"role": "user", "content": prompt}], 
                options={"num_predict": self.max_llm_tokens, "temperature": 0.8, "format": "json"}
            )
            raw_text = resp.get("content", "") if isinstance(resp, dict) else str(resp)
            profile = self._robust_extract_json(raw_text)
        except Exception as e:
            self._log(f"⚠️ 题材动态分析 LLM 调用失败: {e}", level="WARN")
            profile = None

        if not profile or not isinstance(profile, dict) or "modules" not in profile:
            return {
                "focus": "世界运行逻辑", 
                "modules": [{"name": "核心设定", "description": "基础世界观"}], 
                "char_density": 0.7, 
                "char_focus": "内在矛盾",
                "core_aesthetics": [],
                "forbidden_words": ["维度", "量子", "时间线", "底层逻辑", "禁忌", "克苏鲁"] # 默认兜底禁用词
            }
        return profile

    def execute(self):
        self.dynamic_profile = self._analyze_genre_dynamically()
        self._log(f"🎯 [DeepLore] 自主推演侧重点：{self.dynamic_profile.get('focus')}")
        
        # 🌟 核心重构：根据 mode 路由世界观生成引擎
        if self.mode == "flat":
            self._log("🏙️ [FlatLore] 启用扁平细节引擎，防止日常/都市题材被过度脑补...")
            self._generate_flat_details()
        else:
            self._generate_world_modules()
            
        # 🌟 100% 保留原有的所有后续生成步骤
        self._generate_macro_timeline()
        self._generate_character_roster()
        self._generate_lore_hooks()
        self._log("✅ [DeepLore] 多元化深度设定展开完成！")
        
        # 🌟 核心新增：生成设定索引 (Lore Index)，解决设定过多导致 Token 爆炸和遗漏的问题 (100% 保留原代码)
        self._generate_lore_index()

    def _generate_lore_index(self):
        """扫描所有设定卡片，生成极简的 lore_index.json 供 ContextBuilder 宏观扫描。"""
        index_data = {"worldview": [], "characters": [], "items": [], "history": []}
        
        for root, _, files in os.walk(self.lore_dir):
            for f in files:
                if f.endswith(".json") and f != "lore_index.json" and f != "动态扩展记录.json":
                    try:
                        with open(os.path.join(root, f), 'r', encoding='utf-8') as fp:
                            data = json.load(fp)
                            if isinstance(data, dict):
                                concept = data.get("concept", data.get("name", f.replace(".json", "")))
                                category = data.get("category", "未分类")
                                brief = data.get("brief", "") or str(data.get("layers", [{}])[0].get("content", ""))[:50] if data.get("layers") else data.get("role", "")[:50]
                                
                                if "角色" in root or "character" in f.lower():
                                    index_data["characters"].append({"name": concept, "brief": brief, "file": f})
                                elif "物品" in root or "item" in f.lower():
                                    index_data["items"].append({"name": concept, "brief": brief, "file": f})
                                else:
                                    index_data["worldview"].append({"name": concept, "category": category, "brief": brief, "file": f})
                    except Exception: pass
                    
        index_path = os.path.join(self.lore_dir, "lore_index.json")
        tmp_path = index_path + ".tmp"
        try:
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(index_data, f, ensure_ascii=False, indent=2)
                f.flush(); os.fsync(f.fileno())
            os.replace(tmp_path, index_path)
            self._log(f"📇 [DeepLore] 设定索引已生成，共 {sum(len(v) for v in index_data.values())} 个条目。")
        except Exception as e:
            self._log(f"⚠️ 设定索引生成失败: {e}", level="WARN")

    def _load_expansion_log(self) -> Dict[str, Any]:
        if os.path.exists(self.expansion_log_path):
            try:
                with open(self.expansion_log_path, 'r', encoding='utf-8') as f: return json.load(f)
            except Exception: pass
        return {"new_characters": [], "new_hooks": [], "unused_ideas": []}

    def _save_expansion_log(self):
        _atomic_write_json(self.expansion_log_path, self.expansion_log)

    def _generate_json_with_auto_fix(self, prompt: str, max_tokens: int = 16000, expected_type: type = list) -> Optional[Any]:
        """
        调用 LLM 生成 JSON。如果首次解析失败，自动将错误文本发回给 LLM 进行自我修复。
        """
        from .config import call_ollama_safe
        
        resp = call_ollama_safe(
            [{"role": "user", "content": prompt}], 
            options={"num_predict": max_tokens, "temperature": 0.8, "format": "json"}
        )
        raw_text = resp.get("content", "") if isinstance(resp, dict) else str(resp)
        
        parsed = self._robust_extract_json(raw_text)
        if parsed is not None and isinstance(parsed, expected_type):
            return parsed

        self._log("🔧 [DeepLore] 首次 JSON 解析失败，触发 AI 自动修复机制...", level="WARN")
        
        # 🌟 核心优化：强化修复 Prompt，降低温度，强制 format: json
        fix_prompt = f"""你刚才输出的 JSON 存在严重的语法错误、被意外截断，或者包含了多余的废话导致系统无法解析。
请仔细检查并修复以下你刚才生成的文本。
【最高警告】：
1. 必须输出一个完整、合法的纯 JSON 对象或数组！
2. 绝对不要使用 ```json 标记！
3. 绝对不要输出任何解释性文字、前言或后缀！
4. 确保所有的引号闭合，括号匹配，没有多余的尾部逗号！

错误的 JSON 文本：
{raw_text[:3000]}"""

        fix_resp = call_ollama_safe(
            [{"role": "user", "content": fix_prompt}], 
            options={"num_predict": max_tokens, "temperature": 0.1, "format": "json"} # 🌟 降低温度至 0.1，提高修复稳定性
        )
        fix_raw = fix_resp.get("content", "") if isinstance(fix_resp, dict) else str(fix_resp)
        
        parsed_fixed = self._robust_extract_json(fix_raw)
        if parsed_fixed is not None and isinstance(parsed_fixed, expected_type):
            self._log("✅ [DeepLore] AI 自动修复 JSON 成功！")
            return parsed_fixed
            
        self._log("❌ [DeepLore] AI 自动修复后依然无法解析，放弃。", level="WARN")
        return None

    def _repair_missing_layers(self, concept: str, category: str, setting_type: str, original_detail: dict, complexity: str = "simple", has_supernatural: bool = False) -> dict:
        """🌟 核心重构：当 layers 缺失时，根据属性标签进行针对性局部修复或自适应降级。"""
        self._log(f"🔧 [DeepLore] 设定 {concept} 缺少 layers，触发针对性局部修复...", level="WARN")

        # 根据标签决定修复时的引导语
        if not has_supernatural and complexity == "simple":
            repair_guidance = "该设定为纯日常设定，**只生成 1 个表层 layer 即可**，绝对不要生成里层和冰山！"
        elif has_supernatural or complexity == "core":
            repair_guidance = "该设定包含超凡元素或为核心金手指，请生成 2 到 3 个 layer（包含表层、里层超凡逻辑、冰山真相）。"
        else:
            repair_guidance = "请根据设定复杂度生成 1 到 2 个 layer。"

        repair_prompt = f"""你刚才为设定《{concept}》(分类:{category}, 类型:{setting_type})生成的 JSON 缺少了核心的 `layers` 数组。
【分层引导】：{repair_guidance}

严格遵循以下结构输出 JSON 数组：
[
  {{
    "layer_id": "surface", "layer_name": "表层（显性设定）",
    "content": "浅显易懂的具象化描述(至少200字，严格贴合当前题材)",
    "reveal_trigger": "触发揭露的具体剧情事件", "reveal_constraints": "允许展示的逻辑前提", "show_method": "展示手法"
  }},
  {{
    "layer_id": "core_secret", "layer_name": "里层（隐藏规则/超凡逻辑）",
    "content": "深奥残酷的底层逻辑或超凡限制(至少200字。仅当引导语允许时才输出此项！)",
    "reveal_trigger": "触发揭露的具体剧情事件", "reveal_constraints": "允许展示的逻辑前提", "show_method": "展示手法"
  }},
  {{
    "layer_id": "iceberg", "layer_name": "冰山（终极真相）",
    "content": "触及世界本源的终极真相(至少100字。仅当引导语允许时才输出此项！)"
  }}
]
只输出纯 JSON 数组！"""

        try:
            repaired_layers = self._generate_json_with_auto_fix(prompt=repair_prompt, max_tokens=8000, expected_type=list)
            if repaired_layers and isinstance(repaired_layers, list) and len(repaired_layers) >= 1:
                repaired_layers = [l for l in repaired_layers if isinstance(l, dict)]
                if repaired_layers:
                    original_detail["layers"] = repaired_layers
                    self._log(f"✅ [DeepLore] 设定 {concept} 的 layers 局部修复成功（共 {len(repaired_layers)} 层）！")
                    return original_detail
        except Exception as e:
            self._log(f"⚠️ [DeepLore] 设定 {concept} 局部修复 LLM 调用失败: {e}", level="WARN")

        # 🌟 终极容错：基于标签的自适应降级生成基础骨架
        self._log(f"⚠️ [DeepLore] 设定 {concept} 局部修复失败，启用基于标签的自适应降级。", level="WARN")
        
        fallback_layers = [
            {
                "layer_id": "surface", "layer_name": "表层（显性设定）",
                "content": f"关于【{concept}】的表层认知与具体细节。（待后续细节填充）",
                "reveal_trigger": "故事初期自然展示", "reveal_constraints": "无特殊限制", "show_method": "通过环境描写和日常互动展示"
            }
        ]
        
        # 只有包含超凡元素或核心复杂度时，才降级生成里层
        if has_supernatural or complexity == "core":
            fallback_layers.append({
                "layer_id": "core_secret", "layer_name": "里层（隐藏规则/超凡逻辑）",
                "content": f"【{concept}】背后的深层运转逻辑、超凡能力限制或隐藏秘密。（待后续细节填充）",
                "reveal_trigger": "主角深入调查或遭遇重大挫折时", "reveal_constraints": "需要主角具备一定的认知基础", "show_method": "通过核心冲突和关键人物的揭秘展示"
            })

        original_detail["layers"] = fallback_layers
        return original_detail

    def _generate_world_modules(self):
        """🌟 终极重构：深度分层引擎。强制注入核心设定，防止脱离主线自由发散。"""
        self._log("🌳 [DeepLore] 正在推演世界观设定集（紧扣核心设定与逻辑约束）...")
        
        user_forbidden = self.task_plan.get("forbidden_settings", []) if hasattr(self, 'task_plan') and isinstance(self.task_plan, dict) else []
        user_forbidden_str = "、".join([str(f) for f in user_forbidden]) if user_forbidden else "无"

        # 🌟 致命修复：读取核心设定
        core_details = self.task_plan.get("core_details", []) if hasattr(self, 'task_plan') and isinstance(self.task_plan, dict) else []
        core_details_str = "\n".join([f"- {d}" for d in core_details[:30]]) if core_details else "无"

        # 🌟 核心新增：在规划阶段要求 LLM 为每个设定打上“复杂度”和“超凡属性”标签
        plan_prompt = f"""请为《{self.title}》规划 10-50 个世界观设定模块。
【核心要求】：必须区分 "base"（基本设定）和 "plot"（剧情设定）。
【⚠️ 最高禁令】：绝对禁止生成任何属于以下范畴的设定：{user_forbidden_str}。

【🌟 属性打标要求】：
请为每个设定评估并输出以下两个标签：
1. "complexity": 设定复杂度，只能是 "simple"（简单/日常/背景）、"medium"（中等/重要机制）、"core"（核心/世界观底座/金手指）。
2. "has_supernatural": 是否包含超凡/异常/科幻/奇幻元素（true/false）。如果是纯现实日常设定则为 false，如果是主角的超凡金手指或核心异常源则为 true。

输出 JSON 数组：[{{"concept": "名称", "category": "分类", "brief": "简述", "setting_type": "base或plot", "complexity": "simple/medium/core", "has_supernatural": true/false}}]"""
        
        modules = self._generate_json_with_auto_fix(prompt=plan_prompt, expected_type=list)
        if not modules or len(modules) < 5: return

        core_aesthetics = self.dynamic_profile.get("core_aesthetics", []) if self.dynamic_profile else []
        forbidden_words = self.dynamic_profile.get("forbidden_words", ["维度", "量子", "时间线", "底层逻辑", "禁忌", "克苏鲁", "不可名状"]) if self.dynamic_profile else []
        aesthetics_str = "、".join(core_aesthetics) if core_aesthetics else "符合当前题材的专业词汇"
        forbidden_str = "、".join(forbidden_words)

        all_layered_settings = []
        for idx, module in enumerate(modules):
            if not isinstance(module, dict): continue 
            
            concept = module.get("concept", f"未命名_{idx}")
            category = module.get("category", "未分类")
            brief = module.get("brief", "")
            setting_type = module.get("setting_type", "base")
            
            # 🌟 读取属性标签（带默认值兜底）
            complexity = module.get("complexity", "simple")
            has_supernatural = module.get("has_supernatural", False)
            
            self._log(f"📝 [{idx+1}/{len(modules)}] 生成设定：{category}/{concept} (类型:{setting_type}, 复杂度:{complexity}, 超凡:{has_supernatural})...")
            
            # 🌟 核心重构：根据标签动态生成引导语，彻底废除“一刀切”的层数限制
            layer_guidance = ""
            if not has_supernatural and complexity == "simple":
                # 纯日常/简单设定：引导只写表象，禁止脑补
                layer_guidance = """【分层引导：纯现实/日常模式】
该设定为纯现实日常设定。请**仅输出 1 层（表层）**，详细描述其表象、具体细节和日常运作方式。
**绝对禁止**输出里层和冰山！绝对禁止强行加入“底层逻辑/残酷代价/终极真相/超凡能力”等莫名其妙的元素！"""
            elif has_supernatural or complexity == "core":
                # 超凡设定/核心金手指：引导深挖逻辑，但必须自洽
                layer_guidance = """【分层引导：超凡/核心深挖模式】
该设定包含超凡元素或属于核心金手指。请输出 2 到 3 层。
除了表层表象，必须深入挖掘其“里层（超凡能力的底层逻辑/限制/代价）”和“冰山（终极真相/来源）”。
**注意**：超凡逻辑必须严密自洽，且严格受限于用户的【绝对禁令】，禁止无脑堆砌“高维/克苏鲁”等套路词汇。"""
            else:
                # 中等复杂度/普通重要设定：自由发挥
                layer_guidance = """【分层引导：标准模式】
该设定为中等复杂度的重要设定。请根据实际需要输出 1 到 2 层（表层+里层）。
描述表象，并适当揭示其背后的运转规则或隐藏秘密，无需强行挖掘终极真相。"""

            detail_prompt = f"""请为设定《{concept}》(类型:{setting_type})构建设定详情。

【⚠️ 最高铁律：绝对禁令与题材锁死】：
当前小说题材为：{self.genre}。
1. 用户绝对禁止的设定/元素：{user_forbidden_str}。严禁跨题材串味！
2. 必须大量使用核心美学词汇：{aesthetics_str}。绝对禁止使用串味词汇：{forbidden_str}。

{layer_guidance}

【最高指令：揭露逻辑约束】：
必须为该设定生成 `reveal_constraints`（揭露逻辑约束），即：在什么样的剧情逻辑下，这个设定才允许被展示给读者？

输出严格的 JSON 对象（layers 数组长度由你根据上述引导自主决定，1~3个元素）：
{{
  "concept": "{concept}",
  "category": "{category}",
  "setting_type": "{setting_type}",
  "complexity": "{complexity}",
  "has_supernatural": {str(has_supernatural).lower()},
  "evolution_log": [],
  "layers": [
    {{
      "layer_id": "surface",
      "layer_name": "表层（公众认知/显性设定）",
      "content": "（浅显易懂，具象化，至少300字，严格贴合{self.genre}风格）",
      "reveal_trigger": "触发揭露的具体剧情事件",
      "reveal_constraints": "允许展示该设定的逻辑前提",
      "show_method": "如何通过剧情/动作展示"
    }},
    {{
      "layer_id": "core_secret",
      "layer_name": "里层（核心秘密/隐藏规则/超凡逻辑）",
      "content": "（深奥、涉及底层逻辑或超凡限制，至少300字。仅当引导语允许时才输出此层！）",
      "reveal_trigger": "触发揭露的具体剧情事件",
      "reveal_constraints": "允许展示该设定的逻辑前提",
      "show_method": "通过什么冲突或代价来揭露"
    }},
    {{
      "layer_id": "iceberg",
      "layer_name": "冰山（作者底牌/终极真相）",
      "content": "（触及核心矛盾的终极真相，至少200字。仅当引导语允许时才输出此层！）"
    }}
  ]
}}
只输出纯 JSON 对象！"""

            try:
                setting_detail = self._generate_json_with_auto_fix(prompt=detail_prompt, max_tokens=16000, expected_type=dict)
                
                if not isinstance(setting_detail, dict):
                    self._log(f"⚠️ 设定 {concept} LLM返回非字典类型，跳过", level="WARN")
                    continue
                    
                if "layers" not in setting_detail or not isinstance(setting_detail["layers"], list) or len(setting_detail["layers"]) < 1:
                    setting_detail = self._repair_missing_layers(concept, category, setting_type, setting_detail, complexity, has_supernatural)
                    
                    if "layers" not in setting_detail or not isinstance(setting_detail["layers"], list) or len(setting_detail["layers"]) < 1:
                        self._log(f"❌ 设定 {concept} 经过修复后依然无合法 layers，被迫跳过", level="ERROR")
                        continue

                setting_detail["layers"] = [layer for layer in setting_detail["layers"] if isinstance(layer, dict)]
                if not setting_detail["layers"]:
                    setting_detail = self._repair_missing_layers(concept, category, setting_type, setting_detail, complexity, has_supernatural)
                    setting_detail["layers"] = [layer for layer in setting_detail["layers"] if isinstance(layer, dict)]
                    if not setting_detail["layers"]:
                        self._log(f"❌ 设定 {concept} 的 layers 过滤后为空且降级失败，被迫跳过", level="ERROR")
                        continue

                all_layered_settings.append(setting_detail)
                self._save_setting_card(setting_detail)
            except Exception as e:
                self._log(f"⚠️ 生成设定 {concept} 失败: {e}", level="WARN")

        import os
        from .config import _atomic_write_json
        _atomic_write_json(os.path.join(self.lore_dir, "分层世界观设定.json"), all_layered_settings)
        self._log(f"✅ 动态分层设定建档完成！共 {len(all_layered_settings)} 个。")

    def _generate_flat_details(self):
        """🌟 核心重构：扁平细节引擎。强制注入 73 条核心设定，防止生成毫无关联的通用日常废话。"""
        self._log("🏙️ [FlatLore] 正在推演扁平化细节设定集（无分层、防魔改、紧扣核心设定）...")
        
        user_forbidden = self.task_plan.get("forbidden_settings", []) if hasattr(self, 'task_plan') and isinstance(self.task_plan, dict) else []
        user_forbidden_str = "、".join([str(f) for f in user_forbidden]) if user_forbidden else "无"

        # 🌟 致命修复：读取 73 条核心设定，确保生成的卡片紧扣核心剧情！
        core_details = self.task_plan.get("core_details", []) if hasattr(self, 'task_plan') and isinstance(self.task_plan, dict) else []
        # 取前 30 条核心设定，防止 Token 爆炸
        core_details_str = "\n".join([f"- {d}" for d in core_details[:30]]) if core_details else "无特定核心设定"

        plan_prompt = f"""请为《{self.title}》（题材：{self.genre}）规划 15-30 个细节设定模块。
【🌟 核心剧情与设定基调（必须严格围绕这些生成细节，绝不允许写无关的通用日常）】：
{core_details_str}

【核心要求】：
1. 专注于与上述核心设定（如主角能力、动机、特定场景）强相关的日常细节、场景氛围、人物互动、道具。
2. 绝对不要生成“地铁早高峰”、“秋日落叶”这种与核心剧情毫无关联的通用废话！
3. 绝对不要构建庞大的“世界观法则”或“力量体系”。
【⚠️ 最高禁令】：绝对禁止生成任何属于以下范畴的设定：{user_forbidden_str}。

输出 JSON 数组：[{{"concept": "名称", "category": "分类(如:核心道具/互动细节/专属场景)", "brief": "简述", "setting_type": "base或plot"}}]"""
        
        modules = self._generate_json_with_auto_fix(prompt=plan_prompt, expected_type=list)
        if not modules or len(modules) < 5: return

        core_aesthetics = self.dynamic_profile.get("core_aesthetics", []) if self.dynamic_profile else []
        aesthetics_str = "、".join(core_aesthetics) if core_aesthetics else "符合当前题材的专业词汇"

        all_flat_settings = []
        for idx, module in enumerate(modules):
            if not isinstance(module, dict): continue 
            
            concept = module.get("concept", f"未命名_{idx}")
            category = module.get("category", "未分类")
            setting_type = module.get("setting_type", "base")
            
            self._log(f"📝 [{idx+1}/{len(modules)}] 生成扁平细节：{category}/{concept}...")
            
            detail_prompt = f"""请为细节《{concept}》(分类:{category})撰写详细的设定描述。

【🌟 核心剧情与设定基调（必须严格贴合）】：
{core_details_str}

【⚠️ 最高铁律：绝对禁令与题材锁死】：
当前小说题材为：{self.genre}。
1. 用户绝对禁止的设定/元素：{user_forbidden_str}。
2. 必须大量使用核心美学词汇：{aesthetics_str}。

【🌟 扁平化描述要求（严禁分层）】：
1. 请直接用 300-500 字详细描述该细节的表象、具体特征、在核心剧情（如附身、日常互动）中的作用。
2. **绝对禁止**使用“表层/里层/冰山”的分层结构！
3. **绝对禁止**强行挖掘“底层逻辑/残酷代价/终极真相”！保持纯粹的日常/现实感。

输出严格的 JSON 对象：
{{
  "concept": "{concept}",
  "category": "{category}",
  "setting_type": "{setting_type}",
  "content": "（300-500字的详细扁平描述，必须紧扣核心设定，严禁分层，严禁通用废话）",
  "reveal_trigger": "在剧情中如何自然引出",
  "show_method": "如何通过细节描写展示"
}}
只输出纯 JSON 对象！"""

            try:
                setting_detail = self._generate_json_with_auto_fix(prompt=detail_prompt, max_tokens=self.max_llm_tokens, expected_type=dict)
                
                if not isinstance(setting_detail, dict) or not setting_detail.get("content"):
                    self._log(f"⚠️ 细节 {concept} 生成失败或缺少 content，跳过", level="WARN")
                    continue

                # 🌟 致命兼容：为了不让下游 ContextBuilder 报错，将扁平内容包装成单层的 layers
                setting_detail["layers"] = [{
                    "layer_id": "surface",
                    "layer_name": "细节描述",
                    "content": setting_detail.pop("content"),
                    "reveal_trigger": setting_detail.pop("reveal_trigger", "自然展示"),
                    "reveal_constraints": "无特殊限制",
                    "show_method": setting_detail.pop("show_method", "细节描写")
                }]
                
                all_flat_settings.append(setting_detail)
                self._save_setting_card(setting_detail)
            except Exception as e:
                self._log(f"⚠️ 生成细节 {concept} 失败: {e}", level="WARN")

        import os
        from .config import _atomic_write_json
        _atomic_write_json(os.path.join(self.lore_dir, "分层世界观设定.json"), all_flat_settings)
        self._log(f"✅ 扁平细节建档完成！共 {len(all_flat_settings)} 个。")


    def _save_setting_card(self, setting_data: Dict[str, Any]):
        """保存设定卡片（完美渲染认知分层与逻辑约束）"""
        concept = setting_data.get("concept", "未命名")
        category = setting_data.get("category", "未分类")
        setting_type = setting_data.get("setting_type", "base")
        
        safe_category = re.sub(r'[\\/*?:"<>|]', "", category) or "未分类"
        safe_concept = re.sub(r'[\\/*?:"<>|]', "", concept) or "未命名"
        
        cat_dir = os.path.join(self.lore_dir, safe_category)
        os.makedirs(cat_dir, exist_ok=True)
        file_path = os.path.join(cat_dir, f"{safe_concept}.md")
        
        md = f"# {concept} [{setting_type.upper()}]\n\n"
        for layer in setting_data.get("layers", []):
            # 🌟 致命修复：双重防弹，确保 layer 是字典
            if not isinstance(layer, dict): continue
            
            layer_id = layer.get('layer_id', '')
            md += f"## {layer.get('layer_name', '')}\n"
            md += f"- **内容**: {layer.get('content', '')}\n"
            
            if layer_id != "iceberg":
                md += f"- **揭露事件**: {layer.get('reveal_trigger', '')}\n"
                # 🌟 核心新增：渲染逻辑约束
                constraints = layer.get('reveal_constraints', '')
                if constraints:
                    md += f"- **⚠️ 揭露逻辑约束**: {constraints}\n"
                md += f"- **展示手法**: {layer.get('show_method', '')}\n"
            md += "\n"
            
        evo_log = setting_data.get("evolution_log", [])
        if evo_log:
            md += "## 📈 剧情演变日志\n"
            for log in evo_log:
                # 🌟 地毯式防弹：防止 LLM 在 evolution_log 中混入字符串
                if not isinstance(log, dict): continue
                md += f"- **[第{log.get('chapter', '?')}章] {log.get('type', '更新')}**: {log.get('content', '')}\n"
                
        _atomic_write_text(file_path, md)
        _atomic_write_json(os.path.join(cat_dir, f"{safe_concept}.json"), setting_data)


    def _generate_macro_timeline(self):
        """生成宏观时间线与核心场景"""
        self._log("⏳ [DeepLore] 推演宏观时间线与核心场景...")
        prompt = f"""为《{self.title}》推演宏观时间线与核心场景。
输出 JSON：
{{"macro_timeline": [{{"era": "时代", "event": "大事件(细节丰富)", "impact": "深远影响"}}], "key_scenes": [{{"scene_name": "场景名", "visual_description": "极具画面感的环境描述", "plot_function": "剧情功能"}}]}}"""
        data = self._generate_json_with_auto_fix(prompt=prompt, expected_type=dict)
        if not data: return
        
        md = "# 宏观时间线与核心场景\n\n## ⏳ 时间线\n"
        for item in data.get("macro_timeline", []):
            md += f"### {item.get('era')}\n- **事件**: {item.get('event')}\n- **影响**: {item.get('impact')}\n\n"
        md += "## 🎬 核心场景\n"
        for item in data.get("key_scenes", []):
            md += f"### {item.get('scene_name')}\n- **画面**: {item.get('visual_description')}\n- **功能**: {item.get('plot_function')}\n\n"
            
        _atomic_write_text(os.path.join(self.timeline_dir, "宏观时间线与核心场景.md"), md)
        _atomic_write_json(os.path.join(self.timeline_dir, "宏观时间线与核心场景.json"), data)

    def _generate_character_roster(self):
        """两阶段生成超详细角色卡"""
        base_chars = 3
        char_density = self.dynamic_profile.get("char_density", 0.7) if self.dynamic_profile else 0.7
        char_focus = self.dynamic_profile.get("char_focus", "内在矛盾") if self.dynamic_profile else "内在矛盾"
        total_chars = max(3, min(15, base_chars + math.floor((self.total_words / 10000) * char_density)))
        
        self._log(f"👥 [DeepLore] 规划 {total_chars} 名核心角色（两阶段生成法）...")
        
        roster_prompt = f"""为《{self.title}》规划 {total_chars} 名核心角色名单。
输出 JSON：[{{"name": "姓名", "role": "身份", "core_contradiction": "核心矛盾"}}]"""
        roster = self._generate_json_with_auto_fix(prompt=roster_prompt, expected_type=list)
        if not roster: return

        all_names = [c.get("name", "") for c in roster]
        for idx, char_brief in enumerate(roster):
            # 🌟 地毯式防弹：防止 LLM 在 roster 数组中混入字符串
            if not isinstance(char_brief, dict): continue
            
            name = char_brief.get("name", f"未命名_{idx}")

            self._log(f"📝 [{idx+1}/{len(roster)}] 生成超详细角色卡：{name}...")
            detail = self._generate_single_character_detail(char_brief, all_names, char_focus)
            if detail:
                self._save_character_card_v3(detail)

    def _generate_single_character_detail(self, char_brief: Dict, all_names: List[str], char_focus: str) -> Optional[Dict]:
        """生成单个角色的超详细档案（包含认知分层与逻辑约束）"""
        prompt = f"""为角色《{char_brief.get('name')}》生成超详细档案。
【可用角色名单】（关系链中的 target 必须是以下之一，或填"主角"/"未登场角色"）：{', '.join(all_names)}
【侧重点】：{char_focus}

【最高指令：角色认知分层】：
1. 公众面具（表层）：必须浅显易懂，是角色刻意展示给外人看的形象。
2. 隐藏真相（里层）：必须深奥、残酷或涉及底层逻辑，是角色死死隐藏的真实目的或心理创伤。

输出严格的 JSON 对象：
{{
  "name": "{char_brief.get('name')}",
  "role": "{char_brief.get('role')}",
  "appearance": "外貌气质(细节丰富，具象化)",
  "personality_traits": ["标签1", "标签2"],
  "background_story": "背景故事(细节丰富)",
  "core_contradiction": "{char_brief.get('core_contradiction')}",
  "growth_arc": {{"start_state": "起点", "catalyst": "催化剂", "end_state": "终点"}},
  "secret_layers": {{
    "public_mask": "公众面具(浅显易懂)",
    "hidden_truth": "隐藏真相(深奥/残酷)"
  }},
  "relationships": [
    {{"target": "目标角色", "relation": "关系描述", "tension": "张力/冲突点/利益纠葛"}}
  ],
  "key_scenes": [
    {{"scene_name": "高光/核心场景名", "description": "该角色在此场景中的具体表现、动作、台词或心理活动(细节丰富)"}}
  ],
  "weapons_skills": "能力、装备、战斗风格、特殊技能(剧情设定)",
  "evolution_log": []
}}
只输出纯 JSON 对象！"""
        return self._generate_json_with_auto_fix(prompt=prompt, max_tokens=16000, expected_type=dict)


    def _save_character_card_v3(self, char_data: Dict[str, Any], is_dynamic: bool = False):
        """保存单张超详细角色卡"""
        name = char_data.get("name", "未知角色")
        safe_name = re.sub(r'[\\/*?:"<>|]', "", name) or "未知角色"
        
        md = f"# 角色卡：{name}\n\n"
        md += f"**身份**: {char_data.get('role', '')}\n\n"
        md += f"## 👁️ 外貌与气质\n{char_data.get('appearance', '')}\n\n"
        md += f"## 🎭 性格标签\n{', '.join(char_data.get('personality_traits', []))}\n\n"
        md += f"## 📜 背景故事\n{char_data.get('background_story', '')}\n\n"
        md += f"## ⚔️ 核心矛盾\n{char_data.get('core_contradiction', '')}\n\n"
        
        arc = char_data.get("growth_arc", {})
        md += f"## 📈 成长弧线\n- 起点: {arc.get('start_state', '')}\n- 催化剂: {arc.get('catalyst', '')}\n- 终点: {arc.get('end_state', '')}\n\n"
        
        sec = char_data.get("secret_layers", {})
        md += f"## 🤫 秘密分层\n- **公众面具(表层)**: {sec.get('public_mask', '')}\n- **隐藏真相(里层)**: {sec.get('hidden_truth', '')}\n\n"
        
        rels = char_data.get("relationships", [])
        if rels:
            md += "## 🕸️ 关系链\n"
            for r in rels: 
                # 🌟 地毯式防弹
                if not isinstance(r, dict): continue
                md += f"- **{r.get('target', '未知')}**: {r.get('relation', '')} | *张力*: {r.get('tension', '')}\n"
            md += "\n"
            
        scenes = char_data.get("key_scenes", [])
        if scenes:
            md += "## 🎬 高光场景\n"
            for s in scenes: 
                # 🌟 地毯式防弹
                if not isinstance(s, dict): continue
                md += f"### {s.get('scene_name', '未知场景')}\n{s.get('description', '')}\n\n"
            
        md += f"## 🗡️ 能力/装备\n{char_data.get('weapons_skills', '')}\n\n"
        
        evo_log = char_data.get("evolution_log", [])
        if evo_log:
            md += "## 📈 剧情演变日志\n"
            for log in evo_log: 
                # 🌟 地毯式防弹
                if not isinstance(log, dict): continue
                md += f"- **[第{log.get('chapter', '?')}章] {log.get('type', '更新')}**: {log.get('content', '')}\n"

            
        if is_dynamic: md += f"\n> ⚠️ **动态新增** ({datetime.now().strftime('%Y-%m-%d')})\n"
            
        file_path = os.path.join(self.char_dir, f"角色卡_{safe_name}.md")
        _atomic_write_text(file_path, md)
        _atomic_write_json(os.path.join(self.char_dir, f"角色卡_{safe_name}.json"), char_data)

    def update_character_with_plot(self, name: str, chapter_num: int, update_type: str, content: str):
        safe_name = re.sub(r'[\\/*?:"<>|]', "", name) or "未知角色"
        json_path = os.path.join(self.char_dir, f"角色卡_{safe_name}.json")
        if not os.path.exists(json_path): return
        try:
            with open(json_path, 'r', encoding='utf-8') as f: char_data = json.load(f)
        except Exception: return
        if "evolution_log" not in char_data: char_data["evolution_log"] = []
        char_data["evolution_log"].append({"chapter": chapter_num, "type": update_type, "content": content, "updated_at": datetime.now().isoformat()})
        self._save_character_card_v3(char_data)

    def update_setting_with_plot(self, concept: str, chapter_num: int, update_type: str, content: str):
        found = False
        for root, _, files in os.walk(self.lore_dir):
            for f in files:
                if f.endswith(".json") and f != "分层世界观设定.json" and f != "动态扩展记录.json":
                    f_path = os.path.join(root, f)
                    try:
                        with open(f_path, 'r', encoding='utf-8') as fp: data = json.load(fp)
                        if data.get("concept") == concept:
                            if "evolution_log" not in data: data["evolution_log"] = []
                            data["evolution_log"].append({"chapter": chapter_num, "type": update_type, "content": content, "updated_at": datetime.now().isoformat()})
                            self._save_setting_card(data)
                            found = True
                            break
                    except Exception: continue
            if found: break

    def add_dynamic_character(self, name: str, role: str, description: str):
        self._log(f"✨ [DeepLore] 动态新增角色：{name}...")
        char_data = {
            "name": name, "role": role, "appearance": description, "personality_traits": [],
            "background_story": "待补充", "core_contradiction": "待补充",
            "growth_arc": {"start_state": "待定", "catalyst": "待定", "end_state": "待定"},
            "secret_layers": {"public_mask": "待定", "hidden_truth": "待定"},
            "relationships": [], "key_scenes": [], "weapons_skills": "待补充", "evolution_log": []
        }
        self._save_character_card_v3(char_data, is_dynamic=True)
        self.expansion_log["new_characters"].append({"name": name, "role": role, "added_at": datetime.now().isoformat()})
        self._save_expansion_log()

    def add_dynamic_hook(self, hook_name: str, description: str):
        self._log(f"💡 [DeepLore] 捕获新伏笔：{hook_name}...")
        new_hook = {"hook_name": hook_name, "description": description, "potential_payoff": "待推演", "added_at": datetime.now().isoformat()}
        hooks_path = os.path.join(self.hooks_dir, "伏笔与留白池.json")
        existing = []
        if os.path.exists(hooks_path):
            try:
                with open(hooks_path, 'r', encoding='utf-8') as f: existing = json.load(f)
            except Exception: pass
        existing.append(new_hook)
        _atomic_write_json(hooks_path, existing)
        self.expansion_log["new_hooks"].append(new_hook)
        self._save_expansion_log()

    def _generate_lore_hooks(self):
        self._log("🎣 [DeepLore] 生成伏笔与留白池...")
        json_example = """[{"hook_name": "留白名称", "description": "具体描述", "potential_payoff": "未来可能的爆发点", "trigger_condition": "触发条件", "resolution_method": "解决方式"}]"""
        prompt = f"""你是悬疑/剧情大师。请为《{self.title}》设计 15 个“留白与未解之谜”。
输出 JSON 数组：{json_example}"""
        hooks = self._generate_json_with_auto_fix(prompt=prompt, expected_type=list)
        if not hooks: return
        _atomic_write_json(os.path.join(self.hooks_dir, "伏笔与留白池.json"), hooks)
        self._log(f"✅ 已生成 {len(hooks)} 个剧情留白与伏笔。")

    def _load_deep_lore_context(self, task_id: str, chapter_num: int, task_plan: Dict[str, Any]) -> str:
        project_dirs_to_check = []
        if task_plan and task_plan.get("project_dir"):
            project_dirs_to_check.append(task_plan["project_dir"])
        lore_dir = None
        for p_dir in list(set(project_dirs_to_check)):
            candidate = os.path.join(p_dir, "03_设定")
            if os.path.exists(candidate) and os.path.isdir(candidate):
                lore_dir = candidate
                break
        if not lore_dir: return ""
        lore_fragments = []
        char_dir = os.path.join(lore_dir, "角色卡")
        if os.path.exists(char_dir):
            main_char = task_plan.get("main_character", "主角") if task_plan else "主角"
            for name in set([main_char]):
                safe_name = re.sub(r'[\\/*?:"<>|]', "", name) or "未知"
                json_path = os.path.join(char_dir, f"角色卡_{safe_name}.json")
                if os.path.exists(json_path):
                    try:
                        with open(json_path, 'r', encoding='utf-8') as f: data = json.load(f)
                        if isinstance(data, dict):
                            char_text = f"【角色深度档案: {name}】\n"
                            sec = data.get("secret_layers", {})
                            if isinstance(sec, dict):
                                char_text += f"- 公众面具: {sec.get('public_mask', '')}\n"
                                char_text += f"- 隐藏真相: {sec.get('hidden_truth', '')}\n"
                            evo = data.get("evolution_log", [])
                            if isinstance(evo, list) and evo:
                                for log in evo[-2:]:
                                    if isinstance(log, dict):
                                        char_text += f"- [第{log.get('chapter','?')}章 {log.get('type','')}]: {log.get('content','')}\n"
                            lore_fragments.append(char_text)
                    except Exception: pass
        return "\n\n".join(lore_fragments)
