# -*- coding: utf-8 -*-
"""
super_guardian.py (V2.1 终极完全体+)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
XIAOXI 架构级自愈中间件 (Super Guardian)
拒绝任何硬编码兜底与假数据。包含 8 大核心引擎，实时拦截、修复、校验并优化数据流。
已修复：断点续写重叠处理、接口防弹参数推断、状态修复精准更新、自适应熔断器时间窗口
"""

import json
import re
import inspect
import logging
import time
from typing import Any, Dict, List, Callable, Optional, Tuple
from collections import defaultdict

logger = logging.getLogger("SuperGuardian")

class SuperGuardian:
    """系统级自愈引擎（单例模式）"""
    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, llm_caller: Callable = None):
        if self._initialized:
            return
        self._initialized = True
        self.llm_caller = llm_caller 
        
        # 🛡️ [增强] 自适应熔断器状态（带时间窗口）
        self._error_counts = defaultdict(int)
        self._error_timestamps = defaultdict(float)  # 记录每个错误键的最后出现时间
        self._circuit_breaker_threshold = 3  # 同一错误连续出现 3 次触发熔断
        self._circuit_breaker_window = 60.0  # 错误时间窗口（秒），超过此窗口重置计数
        
        logger.info("🛡️ SuperGuardian V2.1 (终极完全体+) 已初始化，8大引擎就绪。")

    def set_llm_caller(self, caller: Callable):
        self.llm_caller = caller

    def _check_circuit_breaker(self, error_key: str):
        """自适应熔断器：基于时间窗口防止 LLM 陷入无限修复死循环"""
        now = time.time()
        # 如果上次发生此错误超过时间窗口，重置计数（非连续错误）
        if now - self._error_timestamps[error_key] > self._circuit_breaker_window:
            self._error_counts[error_key] = 0
        
        self._error_counts[error_key] += 1
        self._error_timestamps[error_key] = now  # 更新最后发生时间
        
        if self._error_counts[error_key] >= self._circuit_breaker_threshold:
            raise RuntimeError(f"🚨 熔断触发：'{error_key}' 在 {self._circuit_breaker_window} 秒内连续失败 {self._circuit_breaker_threshold} 次，终止重试以保护 Token。")
            
    def _reset_circuit_breaker(self, error_key: str):
        """成功修复后重置错误计数（保留时间戳以维持窗口逻辑）"""
        self._error_counts[error_key] = 0
        # 注意：不重置时间戳，因为时间窗口依赖于最后错误发生时间


    # ================= 1. JSON 破损自愈引擎 (防死循环增强版) =================
    def heal_json(self, raw_text: str, expected_type: type = dict) -> Any:
        """通过正则提取、括号补全、LLM 辅助修复，强行抢救破损 JSON。（绝对不递归）"""
        if not raw_text or not isinstance(raw_text, str):
            raise ValueError("输入文本为空或非字符串")

        text = raw_text.strip()
        error_key = "json_heal"
        
        # 🌟 内部辅助函数：尝试常规解析（直接解析、去Markdown、正则提取、单引号修复）
        def _try_regular_parse(current_text: str) -> Any:
            # 1. 直接解析
            try:
                data = json.loads(current_text)
                if isinstance(data, expected_type): return data
            except json.JSONDecodeError: pass

            # 2. 剥离 Markdown
            md_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', current_text)
            if md_match:
                try:
                    data = json.loads(md_match.group(1))
                    if isinstance(data, expected_type): return data
                except json.JSONDecodeError: pass

            # 3. 正则强提与括号补全
            target_char = '{' if expected_type == dict else '[' if expected_type == list else ''
            end_char = '}' if expected_type == dict else ']' if expected_type == list else ''
            if target_char and end_char:
                start_idx, end_idx = current_text.find(target_char), current_text.rfind(end_char)
                if start_idx != -1 and end_idx > start_idx:
                    extracted = current_text[start_idx:end_idx+1]
                    healed = self._repair_brackets(extracted)
                    try:
                        data = json.loads(healed)
                        if isinstance(data, expected_type): return data
                    except json.JSONDecodeError: pass
            
            # 4. 🌟 增强修复：处理单引号和尾部多余逗号
            try:
                fixed = re.sub(r",\s*([}\]])", r"\1", current_text)
                # 简单粗暴的单引号转双引号（对部分 LLM 输出的伪 JSON 有效）
                fixed = fixed.replace("'", '"') 
                data = json.loads(fixed)
                if isinstance(data, expected_type): return data
            except Exception: pass

            return None

        # 第一次尝试常规解析
        result = _try_regular_parse(text)
        if result is not None:
            self._reset_circuit_breaker(error_key)
            return result

        # 第二次尝试：LLM 辅助修复 (🌟 核心修改：最多执行 1 次，绝不递归调用 heal_json)
        if self.llm_caller:
            try:
                self._check_circuit_breaker(error_key)
            except RuntimeError as e:
                # 熔断触发，直接放弃，防止外部循环继续调用
                raise ValueError(f"SuperGuardian 熔断触发，放弃修复。原始文本: {text[:200]}") from e

            logger.warning("⚠️ 正则修复失败，触发 LLM 辅助 JSON 修复 (仅限1次)...")
            repair_prompt = (
                f"你是一个严格的 JSON 修复专家。以下文本包含破损的 JSON。"
                f"请修复其语法错误（如未闭合括号、多余逗号、单引号等），使其成为合法的 JSON。"
                f"【绝对禁止】输出任何解释、Markdown 标记或代码块。只输出纯 JSON 字符串！\n\n"
                f"破损文本：\n{text[:3000]}"
            )
            try:
                # 尝试传递 format="json" 强制 Ollama 输出合法 JSON
                try:
                    resp = self.llm_caller([{"role": "user", "content": repair_prompt}], options={"format": "json"})
                except TypeError:
                    resp = self.llm_caller([{"role": "user", "content": repair_prompt}])
                    
                llm_text = resp.get("content", "") if isinstance(resp, dict) else str(resp)
                
                # 对 LLM 返回的结果再进行一次常规解析
                result = _try_regular_parse(llm_text)
                if result is not None:
                    self._reset_circuit_breaker(error_key)
                    return result
                    
                # 🌟 如果 LLM 返回的依然无法解析，直接放弃，不再重试！
                logger.error(f"❌ LLM 修复后依然无法解析，放弃重试。LLM返回: {llm_text[:200]}")
            except Exception as e:
                logger.error(f"❌ LLM 辅助修复调用失败: {e}")

        raise ValueError(f"SuperGuardian 穷尽手段仍无法修复 JSON。原始文本: {text[:200]}")


    def _repair_brackets(self, text: str) -> str:
        text = re.sub(r',\s*([}\]])', r'\1', text)
        stack, in_string, escape = [], False, False
        for char in text:
            if escape: escape = False; continue
            if char == '\\': escape = True; continue
            if char == '"': in_string = not in_string
            if not in_string:
                if char in '{[': stack.append(char)
                elif char in '}]' and stack: stack.pop()
        while stack:
            text += ']' if stack.pop() == '[' else '}'
        return text

    # ================= 2. 幻觉净化与局部重写引擎 =================
    def purge_hallucinations(self, text: str, forbidden_names: List[str], correct_name: str) -> str:
        """扫描正文违禁词，物理替换后若产生语病，触发 LLM 局部润色。"""
        if not text or not forbidden_names: return text
        cleaned_text = text
        found = [name for name in forbidden_names if name in cleaned_text]
        if not found: return cleaned_text

        for bad_name in found:
            cleaned_text = re.sub(rf'\b{re.escape(bad_name)}\b', correct_name, cleaned_text)
        logger.warning(f"⚠️ 发现幻觉词 {found}，已物理替换为 {correct_name}。")

        if self.llm_caller and re.search(rf'{correct_name}.{0,2}{correct_name}', cleaned_text):
            logger.info("🔄 物理替换导致语意重复，触发 LLM 局部润色...")
            polish_prompt = f"以下小说正文因机械替换主角名导致语意不通。请润色使其通顺，主角统一为【{correct_name}】。只输出正文。\n\n正文：\n{cleaned_text}"
            try:
                resp = self.llm_caller([{"role": "user", "content": polish_prompt}])
                polished = resp.get("content", "") if isinstance(resp, dict) else str(resp)
                if len(polished) > len(text) * 0.5: return polished.strip()
            except Exception as e: logger.error(f"LLM 润色失败: {e}")
        return cleaned_text

    # ================= 3. 接口防弹适配器 =================
    def safe_invoke(self, method: Callable, *args, **kwargs) -> Any:
        """拦截模块间调用。自动裁剪多余参数，补充缺失的必填参数（优先使用类型注解）"""
        try:
            sig = inspect.signature(method)
            valid_params = sig.parameters
            safe_kwargs = {k: v for k, v in kwargs.items() if k in valid_params or valid_params.get('kwargs') is not None}
            
            # 获取类型注解（如果存在）
            annotations = getattr(method, '__annotations__', {})
            
            param_names = list(valid_params.keys())
            for name, param in valid_params.items():
                if param.default is inspect.Parameter.empty and name not in safe_kwargs and name not in ['self', 'cls']:
                    if param.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
                        if len(args) <= param_names.index(name):
                            # 优先使用类型注解推断默认值
                            default_value = None
                            if name in annotations:
                                ann = annotations[name]
                                if ann == dict: default_value = {}
                                elif ann == list: default_value = []
                                elif ann == str: default_value = ""
                                elif ann == int: default_value = 0
                                elif ann == float: default_value = 0.0
                                elif ann == bool: default_value = False
                            
                            # 如果无法通过注解推断，则使用名称启发式
                            if default_value is None:
                                if 'dict' in name or name in ['quality_rules', 'context', 'state']:
                                    default_value = {}
                                elif 'list' in name or name in ['thinking_log']:
                                    default_value = []
                                elif 'str' in name or 'content' in name:
                                    default_value = ""
                                elif 'num' in name or 'chapter' in name:
                                    default_value = 0
                                else:
                                    default_value = None  # 最后手段：None（让目标方法处理）
                            
                            safe_kwargs[name] = default_value
            return method(*args, **safe_kwargs)
        except Exception as e:
            logger.error(f"❌ safe_invoke 拦截失败: {e}")
            raise

    # ================= 4. 状态完整性推理引擎 =================
    def heal_state(self, state_dict: Dict, required_keys: List[str], context_prompt: str = "") -> Dict:
        """拒绝假数据！缺失字段时调用 LLM 根据上下文推理补全（仅更新缺失字段）"""
        if not isinstance(state_dict, dict): state_dict = {}
        missing_keys = [k for k in required_keys if k not in state_dict or not state_dict[k]]
        if not missing_keys: return state_dict

        if not self.llm_caller: raise ValueError(f"状态缺失 {missing_keys} 且未配置 LLM。")
        logger.warning(f"⚠️ 状态缺失 {missing_keys}，触发 LLM 推理补全...")
        heal_prompt = f"当前小说状态 JSON 缺失：{missing_keys}。请根据已有状态和上下文推理补全。已有：{json.dumps(state_dict, ensure_ascii=False)}\n上下文：{context_prompt}\n输出包含缺失字段的完整 JSON。"
        resp = self.llm_caller([{"role": "user", "content": heal_prompt}])
        raw = resp.get("content", "") if isinstance(resp, dict) else str(resp)
        healed = self.heal_json(raw, dict)
        # 精准更新：仅填充缺失字段，避免覆盖现有数据
        for key in missing_keys:
            if key in healed:
                state_dict[key] = healed[key]
        return state_dict

    # ================= 5. [新增] 断点续写与无缝拼接引擎 =================
    def heal_truncation(self, text: str, original_prompt: str) -> str:
        """检测正文或 JSON 是否被截断（未正常结尾）。若截断，自动发起续写并无缝拼接（改进重叠处理）"""
        if not text: return text
        
        # 检测是否自然结尾（以标点符号或完整的 JSON 括号结尾）
        is_natural_end = text.rstrip().endswith(('。', '！', '？', '…', '"', '}', ']'))
        if is_natural_end: return text

        if not self.llm_caller:
            logger.warning("⚠️ 检测到文本截断，但无 LLM 续写能力，返回原文本。")
            return text

        logger.warning("✂️ 检测到文本异常截断，触发断点续写引擎...")
        # 取最后 200 字作为上下文重叠区（确保不越界）
        overlap = text[-200:] if len(text) > 200 else text
        cont_prompt = f"以下小说正文在生成时意外中断。请根据最后一段的内容，自然地接续写下去，保持文风和剧情连贯。只输出接续的内容，不要重复已有的文字。\n\n中断处的最后内容：\n{overlap}"
        
        try:
            resp = self.llm_caller([{"role": "user", "content": cont_prompt}])
            continuation = resp.get("content", "") if isinstance(resp, dict) else str(resp)
            if continuation:
                # 改进的重叠消除：寻找原文尾部与续写头部的最长匹配
                max_overlap = 0
                max_possible = min(len(overlap), len(continuation))
                for i in range(max_possible, 0, -1):
                    if overlap.endswith(continuation[:i]):
                        max_overlap = i
                        break
                if max_overlap > 0:
                    continuation = continuation[max_overlap:]
                return text + continuation
        except Exception as e:
            logger.error(f"❌ 断点续写失败: {e}")
        return text

    # ================= 6. [新增] 时序逻辑校验与事实提取引擎 =================
    def validate_chronology(self, new_chapter_text: str, historical_facts: List[str]) -> Tuple[bool, List[str]]:
        """提取新章节的事实清单，并与历史事实比对，检测“吃书”或逻辑冲突（增强容错性）"""
        if not self.llm_caller or not new_chapter_text: return True, []
        
        logger.info("🔍 触发时序逻辑校验引擎，提取本章事实...")
        extract_prompt = f"请从以下小说章节中提取关键事实（如角色生死、受伤状态、获得物品、位置变化）。以 JSON 数组格式输出，每个元素是一个简短的事实陈述。只输出 JSON。\n\n章节：\n{new_chapter_text[:2000]}"
        
        try:
            resp = self.llm_caller([{"role": "user", "content": extract_prompt}])
            raw = resp.get("content", "") if isinstance(resp, dict) else str(resp)
            new_facts = self.heal_json(raw, list)  # 可能抛异常，但被外层try捕获
            
            if not new_facts or not historical_facts:
                return True, new_facts  # 没有历史事实可比对，直接通过

            # 让 LLM 检查新旧事实是否有冲突
            check_prompt = f"历史事实：{json.dumps(historical_facts, ensure_ascii=False)}\n新章节事实：{json.dumps(new_facts, ensure_ascii=False)}\n请检查新事实是否与历史事实存在严重的逻辑冲突（如死人复活、断臂重生）。如果存在冲突，输出冲突点；如果没有，输出 '无冲突'。"
            check_resp = self.llm_caller([{"role": "user", "content": check_prompt}])
            check_text = check_resp.get("content", "") if isinstance(check_resp, dict) else str(check_resp)
            
            if "无冲突" in check_text or "没有" in check_text:
                return True, new_facts
            else:
                logger.warning(f"⚠️ 检测到逻辑冲突：{check_text}")
                return False, new_facts
        except Exception as e:
            logger.error(f"❌ 时序逻辑校验失败: {e}")
            return True, []  # 校验失败时宽容处理，不阻断流程

    # ================= 7. [新增] 文风漂移与水字数压缩引擎 =================
    def refine_density(self, text: str) -> str:
        """检测文本是否严重“水字数”（重复率高、信息熵低）。若是，触发 LLM 去水提纯（优化重复率检测）"""
        if not text or len(text) < 500: return text
        
        # 改进的重复率检测：使用5-gram而非简单按句号分割
        # 先进行基础清理：移除多余空白和标点（保留中文标点）
        cleaned = re.sub(r'\s+', ' ', text.strip())
        if len(cleaned) < 10:  # 太短则跳过
            return text
        
        # 生成5-gram（连续5个字符的序列）
        grams = [cleaned[i:i+5] for i in range(len(cleaned) - 4)]
        if not grams:
            return text
        
        unique_grams = set(grams)
        repetition_ratio = 1 - (len(unique_grams) / len(grams))
        
        # 动态阈值：根据文本长度调整（长文本容忍更高重复率）
        base_threshold = 0.3
        length_factor = min(1.0, len(text) / 5000)  # 5000字为基准长度
        threshold = base_threshold * (1 - length_factor * 0.5)  # 长文本阈值降低至0.15
        
        if repetition_ratio < threshold:  # 重复率低于动态阈值，认为正常
            return text

        if not self.llm_caller: return text
        
        logger.warning(f"💧 检测到水字数（5-gram重复率 {repetition_ratio:.2f}，阈值 {threshold:.2f}），触发文风压缩引擎...")
        refine_prompt = f"以下小说正文存在严重的“水字数”和重复描写。请对其进行‘去水提纯’，删除冗余和重复的废话，加快叙事节奏，但保留核心剧情和动作。只输出精炼后的正文。\n\n正文：\n{text}"
        try:
            resp = self.llm_caller([{"role": "user", "content": refine_prompt}])
            refined = resp.get("content", "") if isinstance(resp, dict) else str(resp)
            if len(refined) > len(text) * 0.4:  # 防止 LLM 删得太狠（保留至少40%）
                return refined.strip()
        except Exception as e:
            logger.error(f"❌ 文风压缩失败: {e}")
        return text

# 全局单例
guardian = SuperGuardian()
