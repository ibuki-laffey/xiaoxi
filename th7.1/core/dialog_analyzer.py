# core/dialog_analyzer.py
# --------------------------------------------------------------
"""
Dialog Analyzer – 提取机器可读的项目规格（JSON）  
实现了 **五层递归方案 + 强制清洗降维层 + 反误导/反脑补铁律**。
"""

import json
import re
import jsonschema
from typing import List, Dict, Any, Optional

from .config import call_ollama_safe, REQUEST_TIMEOUT, SAFE_TIMEOUT
from .constants import TaskMetaSchema


class DialogAnalyzer:
    """
    根据对话历史生成完整的项目规格（spec）。  
    最终返回 **保证所有 REQUIRED_FIELDS 均已填充且类型严格正确** 的 dict。  
    """

    REQUIRED_FIELDS = {
        "title": "未命名项目",
        "genre": "通用",
        "world_type": "默认世界",
        "main_character": "主角",
        "total_word_count": 50000,
        "words_per_chapter": 2500,
        "total_chapters": None,
    }

    NUMERIC_CONFIG_FIELDS = ["total_word_count", "words_per_chapter"]
    MAX_EXTRACT_RETRIES = 3

    def __init__(self, log_callback=None, vector_memory=None):
        self._log = log_callback if callable(log_callback) else print
        self.vector_memory = vector_memory

    # -----------------------------------------------------------------
    # 🌟 核心：工业级防弹 JSON 提取器
    # -----------------------------------------------------------------
    def _robust_extract_json(self, text: str) -> Optional[Dict[str, Any]]:
        if not text or not isinstance(text, str): return None
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
        text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE).strip()
        try: return json.loads(text)
        except json.JSONDecodeError: pass
        start, end = text.find('{'), text.rfind('}')
        if start != -1 and end > start:
            json_str = re.sub(r",\s*([}\]])", r"\1", text[start:end+1])
            try: return json.loads(json_str)
            except: pass
        return None

    # -----------------------------------------------------------------
    # 🌟 核心加强：LLM 结果强制清洗与降维层 (免疫类型漂移、脑补与泛指词)
    # -----------------------------------------------------------------
    def _sanitize_llm_spec(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        """
        对 LLM 返回的结果进行物理降维与清洗：
        1. 拍平字典、截断括号解释、提取纯数字、剔除冗余字段。
        2. 🌟 新增：过滤 main_character 的泛指代词（如"员工"、"主角"）。
        3. 🌟 新增：过滤 title 的长句误抓（包含逗号或超长）。
        """
        if not spec or not isinstance(spec, dict): return {}
        sanitized = {}
        
        # 🌟 泛指代词黑名单
        GENERIC_PRONOUNS = {"主角", "男主", "女主", "员工", "他", "她", "我", "普通人", "保安", "学生", "少年", "少女", "路人"}
        
        for key in self.REQUIRED_FIELDS:
            val = spec.get(key)
            if val is None: continue
            
            if key in ["title", "main_character", "genre", "world_type"]:
                if isinstance(val, dict):
                    val = val.get("name") or val.get("title") or next(iter(val.values()), "")
                val = str(val).strip()
                # 截断括号及后面的解释
                val = re.split(r'[\(（]', val)[0].strip()
                
                # 🌟 针对性清洗：title 不能是长句（防正则误抓“陈默，是个保安”）
                if key == "title":
                    if any(p in val for p in [",", "，", "。", "！", "？"]) or len(val) > 25:
                        val = "" 
                        
                # 🌟 针对性清洗：main_character 不能是泛指代词
                if key == "main_character":
                    if val in GENERIC_PRONOUNS or len(val) > 15:
                        val = ""
                        
                if val: sanitized[key] = val
                
            elif key in ["total_word_count", "words_per_chapter"]:
                if isinstance(val, str):
                    nums = re.findall(r'\d+', val)
                    if nums:
                        num = int(nums[0])
                        if '万' in val or 'w' in val.lower() or 'W' in val: num *= 10000
                        val = num
                try: sanitized[key] = int(val)
                except (ValueError, TypeError): pass
                    
            elif key == "total_chapters":
                try: sanitized[key] = int(val)
                except (ValueError, TypeError): pass

        return sanitized

    # -----------------------------------------------------------------
    # 公共入口
    # -----------------------------------------------------------------
    def extract_task_meta(self, conversation: Any, user_prompt: Optional[str] = None) -> Dict[str, Any]:
        if isinstance(conversation, str):
            user_prompt = conversation
            conversation = [{"role": "user", "content": conversation}]
        elif not isinstance(conversation, list):
            conversation = []

        source_tracking = {}

        # 1️⃣ 正则首轮抓取
        prior_meta = self._extract_project_meta(user_prompt or "")
        self._log("[DialogAnalyzer] 正则首轮抓取参考：" + json.dumps(prior_meta, ensure_ascii=False))
        
        for k, v in prior_meta.items():
            if v and v != 0: source_tracking[k] = "user_extracted"

        # 🌟 核心修改：彻底废除 NUMERIC_CONFIG_FIELDS 的提前默认值填充！
        # 不再强行塞入 50000 字，把字数推断权完全交给 LLM。

        # 2️⃣ LLM 完整提取 (AI 自由推断)
        llm_spec = self._run_full_extraction(conversation, user_prompt, prior_meta)
        
        # 3️⃣ 强制清洗与降维
        if llm_spec:
            llm_spec = self._sanitize_llm_spec(llm_spec)
            for k, v in llm_spec.items():
                if v and v != 0:
                    if k not in source_tracking or source_tracking[k] != "user_extracted":
                        source_tracking[k] = "llm_inferred"
            meta = {**prior_meta, **llm_spec}
            self._log("[DialogAnalyzer] LLM 提取并清洗后：" + json.dumps(meta, ensure_ascii=False))
        else:
            meta = prior_meta
            self._log("[DialogAnalyzer] LLM 未返回有效结果，使用正则参考值")

        meta = self._derive_dependent_fields(meta)

        # 4️⃣ 递归补全缺失字段 (继续让 AI 推断)
        retry = 0
        while not self._is_meta_complete(meta) and retry < self.MAX_EXTRACT_RETRIES:
            missing = [k for k in self.REQUIRED_FIELDS if not meta.get(k)]
            self._log(f"[DialogAnalyzer] 第 {retry+1} 次尝试让 AI 补全缺失字段：{missing}")
            new_part = self._fill_missing_fields(meta, conversation, missing, user_prompt)
            if new_part:
                new_part = self._sanitize_llm_spec(new_part)
                for k, v in new_part.items():
                    if v and v != 0: source_tracking[k] = "llm_inferred"
                meta = {**meta, **new_part}
            meta = self._derive_dependent_fields(meta)
            retry += 1

        # 5️⃣ 向量记忆相似大纲推断
        if not self._is_meta_complete(meta) and self.vector_memory:
            try:
                query = {
                    "genre": meta.get("genre"), "total_word_count": meta.get("total_word_count"),
                    "words_per_chapter": meta.get("words_per_chapter"),
                    "title_keywords": re.findall(r'[\u4e00-\u9fa5]{2,4}', meta.get("title", "")),
                }
                similar_outlines = self.vector_memory.search_similar_outline(query, top_k=3)
                if similar_outlines:
                    inferred = self._infer_from_outlines(similar_outlines)
                    for k, v in inferred.items():
                        if v and v != 0: source_tracking[k] = "vector_memory"
                    meta = {**meta, **inferred}
            except Exception as e:
                self._log(f"[DialogAnalyzer] 向量记忆推断异常: {e}")

        # 6️⃣ 终极兜底：正则回退与系统底线默认值
        if not self._is_meta_complete(meta):
            regex_fallback = self._regex_fallback(conversation)
            for k, v in regex_fallback.items():
                if k not in source_tracking:
                    if v == self.REQUIRED_FIELDS.get(k):
                        source_tracking[k] = "system_default"
                    else:
                        source_tracking[k] = "regex_fallback"
            meta = {**regex_fallback, **meta}

        meta = self._derive_dependent_fields(meta)
        source_tracking["total_chapters"] = "system_derived"

        # 7️⃣ JSON‑Schema 校验
        try:
            jsonschema.validate(instance=meta, schema=TaskMetaSchema)
        except jsonschema.ValidationError as ve:
            self._log(f"[DialogAnalyzer] JSON‑Schema 校验失败: {ve.message}")
            raise ve

        meta["_source_tracking"] = source_tracking
        self._log(f"[DialogAnalyzer] 来源追踪：{json.dumps(source_tracking, ensure_ascii=False)}")

        return meta


    # -----------------------------------------------------------------
    # 2️⃣ 完整提取（LLM） - 🌟 强化反脑补与反误导铁律
    # -----------------------------------------------------------------
    def _run_full_extraction(self, conversation: List[Dict[str, str]], user_prompt: Optional[str], prior_meta: Dict) -> Dict[str, Any]:
        system_prompt = (
            "你是一名极其严谨且深谙网文市场的项目需求分析专家。请从对话中提炼小说创作需求，返回严格 JSON。\n"
            "【最高铁律 - 违反将导致系统崩溃】：\n"
            "1. 绝对不要被用户的闲聊（如写代码、算数学、问天气、玩游戏、画图）误导！\n"
            "2. 忽略用户的反问、建议或修改意见。只提取用户**陈述性**确定的事实设定！\n"
            "3. 🌟 【AI 自由推断与网文常识】：如果用户没有提供书名、题材、主角、字数等设定，请你**务必根据网文常识和已知设定进行合理推断**！\n"
            "   - 字数推断：例如‘赛博修仙/玄幻’通常是长篇（total_word_count: 2000000, words_per_chapter: 3000）；‘规则怪谈/悬疑’通常是中篇（total_word_count: 500000, words_per_chapter: 2500）。\n"
            "   - 书名推断：根据题材起一个具有网文吸引力的书名。\n"
            "   - 主角推断：起一个符合题材背景的主角名字。\n"
            "4. title 和 main_character 必须是纯粹的名称！绝对禁止包含任何括号、描述、后缀！\n"
            "5. total_word_count 和 words_per_chapter 必须是纯整数，绝对禁止包含'字'、'万'等单位！\n"
            "6. 只返回 JSON 对象，键名必须严格为：title, genre, world_type, main_character, total_word_count, words_per_chapter。禁止添加任何其他键！"
        )
        
        prior_text = f"\n【正则预提取参考（可能包含噪音，请仔细甄别）】：\n{json.dumps(prior_meta, ensure_ascii=False)}" if prior_meta else ""
        user_content = self._format_conversation(conversation, user_prompt) + prior_text

        messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_content}]
        response = self._call_llm(messages, timeout=REQUEST_TIMEOUT)
        if not response.get("success"): return {}
        
        spec = self._robust_extract_json(response.get("content", ""))
        return spec if isinstance(spec, dict) else {}

    # -----------------------------------------------------------------
    # 3️⃣ 缺失字段填补（递归）
    # -----------------------------------------------------------------
    def _fill_missing_fields(self, partial_spec: Dict, conversation: List[Dict], missing_fields: List[str], user_prompt: Optional[str]) -> Dict[str, Any]:
        missing_str = ", ".join(missing_fields)
        system_prompt = (
            f"请在已有项目规格中补全以下缺失字段：{missing_str}。\n"
            "【铁律】：\n"
            "1. 请根据已有题材和世界观，运用网文常识进行**合理推断**（如推断合理的字数、起个好书名/主角名）。\n"
            "2. title/main_character 必须是纯名称，禁止加括号解释！字数必须是纯整数！\n"
            "3. 禁止添加多余键！只返回 JSON。"
        )
        user_content = (
            f"已有规格：\n{json.dumps(partial_spec, ensure_ascii=False, indent=2)}\n\n"
            f"对话内容：\n{self._format_conversation(conversation, user_prompt)}"
        )
        messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_content}]
        response = self._call_llm(messages, timeout=REQUEST_TIMEOUT)
        if not response.get("success"): return {}
        
        new_part = self._robust_extract_json(response.get("content", ""))
        return new_part if isinstance(new_part, dict) else {}


    # -----------------------------------------------------------------
    # 4️⃣ 正则回退 & 8️⃣ 正则首轮抽取 (🌟 优化 title 正则边界)
    # -----------------------------------------------------------------
    def _get_regex_patterns(self):
        NEG = r"(?!(?:SQL|Python|正则|代码|爬虫|翻译|总结|数学|计算|火锅|天气|游戏|基金|股票|猫|狗|midjourney|提示词|画)[^\n]*$)"
        CAP = r"([^\n，,\.！]{1,40})"
        
        return {
            # 🌟 核心修复：移除单独的 "叫"，防止匹配到 "主角叫"、"他叫"，只保留 "书名叫"
            "title": r"(?:书名|书名叫|定为|就叫|名字是)\s*[《]?([^》\n]{1,25})[》]?",
            "genre": rf"(?:题材|类型|风格|写个|构思一个|写一本)\s*{NEG}{CAP}",
            "world_type": rf"(?:背景是|世界类型|世界观|设定是|发生在)\s*{NEG}{CAP}",
            "main_character": rf"(?:主角叫|主角是|主角名|男主叫|女主叫|主人公叫)\s*{NEG}{CAP}",
            "total_word_count": r"(?:总字数|大概|控制在)\s*([\d,]+)\s*万字?",
            "words_per_chapter": r"(?:每章字数|每章)\s*([\d,]+)\s*字?",
        }

    def _extract_project_meta(self, user_prompt: str) -> Dict[str, Any]:
        meta = {k: (0 if "word" in k else "") for k in self.REQUIRED_FIELDS if k != "total_chapters"}
        if not isinstance(user_prompt, str): return meta
        
        for key, pat in self._get_regex_patterns().items():
            m = re.search(pat, user_prompt)
            if m:
                if "word" in key:
                    try: meta[key] = int(m.group(1).replace(",", ""))
                    except: pass
                else:
                    val = re.split(r'[\(（]', m.group(1).strip())[0].strip()
                    # 🌟 核心修复：去除开头可能误抓的冒号（如 "：巫蛊" -> "巫蛊"）
                    val = val.lstrip(":：").strip()
                    if val: meta[key] = val
        return meta

    def _regex_fallback(self, conversation: List[Dict[str, str]]) -> Dict[str, Any]:
        user_text = ""
        if isinstance(conversation, list):
            user_text = " ".join(msg.get("content", "") for msg in conversation if isinstance(msg, dict) and msg.get("role") == "user")
        
        meta = {k: (0 if "word" in k else "") for k in self.REQUIRED_FIELDS if k != "total_chapters"}
        for key, pat in self._get_regex_patterns().items():
            m = re.search(pat, user_text)
            if m:
                if "word" in key:
                    try: meta[key] = int(m.group(1).replace(",", ""))
                    except: pass
                else:
                    val = re.split(r'[\(（]', m.group(1).strip())[0].strip()
                    # 🌟 核心修复：去除开头可能误抓的冒号（如 "：巫蛊" -> "巫蛊"）
                    val = val.lstrip(":：").strip()
                    if val: meta[key] = val
                    
        for k, default in self.REQUIRED_FIELDS.items():
            if not meta.get(k): meta[k] = default
        return meta

    # -----------------------------------------------------------------
    # 辅助方法 (保持原样)
    # -----------------------------------------------------------------
    def _derive_dependent_fields(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        total_wc = spec.get("total_word_count") or self.REQUIRED_FIELDS["total_word_count"]
        wp_ch = spec.get("words_per_chapter") or self.REQUIRED_FIELDS["words_per_chapter"]
        try: spec["total_chapters"] = max(1, int(total_wc // wp_ch))
        except: spec["total_chapters"] = 1
        return spec

    def _is_meta_complete(self, meta: Dict[str, Any]) -> bool:
        for k in self.REQUIRED_FIELDS:
            val = meta.get(k)
            if k == "total_chapters":
                if not isinstance(val, int) or val <= 0: return False
            else:
                if val in (None, "", 0): return False
        return True

    def _infer_from_outlines(self, outlines: List[Dict[str, Any]]) -> Dict[str, Any]:
        inferred = {}
        str_counters = {}
        for outline in outlines:
            for key in ("title", "genre", "world_type"):
                val = outline.get(key)
                if val:
                    str_counters.setdefault(key, {})
                    str_counters[key][val] = str_counters[key].get(val, 0) + 1
        for key, counter in str_counters.items():
            if counter: inferred[key] = max(counter.items(), key=lambda x: x[1])[0]
        for key in ("total_word_count", "words_per_chapter"):
            vals = [o.get(key) for o in outlines if isinstance(o.get(key), (int, float))]
            if vals: inferred[key] = int(sum(vals) / len(vals))
        return inferred

    def _format_conversation(self, conversation: List[Dict[str, str]], extra_prompt: Optional[str]) -> str:
        lines = []
        if isinstance(conversation, list):
            for msg in conversation:
                if isinstance(msg, dict):
                    role = "User" if msg.get("role") == "user" else "Assistant"
                    lines.append(f"{role}: {msg.get('content', '')}")
        if extra_prompt: lines.append(f"用户原始请求: {extra_prompt}")
        return "\n".join(lines)

    def _call_llm(self, messages: List[Dict[str, str]], timeout: int = 30) -> Dict[str, Any]:
        try: return call_ollama_safe(messages, timeout=timeout)
        except Exception as e: return {"success": False, "error": str(e)}

    def extract_spec(self, conversation: Any, user_prompt: Optional[str] = None) -> Dict[str, Any]:
        self._log("[DialogAnalyzer] 开始提取项目规格（extract_spec）")
        spec = self.extract_task_meta(conversation, user_prompt)
        self._log("[DialogAnalyzer] 提取完成，返回 spec：" + json.dumps(spec, ensure_ascii=False))
        return spec
