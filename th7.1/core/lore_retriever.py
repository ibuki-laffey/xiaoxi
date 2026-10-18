import os
import re
import json
from typing import List, Dict, Any, Optional

def _robust_extract_json(text: str) -> Optional[Any]:
    """🌟 工业级 JSON 提取器：首尾括号定位，彻底解决非贪婪正则截断问题。"""
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
        if os.path.exists(tmp): os.remove(tmp)
        raise

def _atomic_append_text(filepath: str, text: str):
    """🌟 工业级原子追加文本（带 fsync）。"""
    dir_name = os.path.dirname(filepath)
    if dir_name: os.makedirs(dir_name, exist_ok=True)
    with open(filepath, "a", encoding="utf-8") as f:
        f.write(text); f.flush(); os.fsync(f.fileno())

class LoreRetriever:
    """基于 LLM 语义路由的设定检索器。"""
    
    def __init__(self, project_dir: str):
        self.lore_dir = os.path.join(project_dir, "03_设定")
        self._lore_index = None

    def _build_lore_index(self) -> Dict[str, str]:
        if self._lore_index is not None: return self._lore_index
        index: Dict[str, str] = {}
        if not os.path.isdir(self.lore_dir): return index
        for root, _, files in os.walk(self.lore_dir):
            for file in files:
                if not (file.endswith(".md") or file.endswith(".txt")): continue
                if "角色卡" in root or "角色卡" in file: continue
                file_path = os.path.join(root, file)
                try:
                    with open(file_path, "r", encoding="utf-8") as f: head = f.read(300).replace("\n", " ").strip()
                    rel_path = os.path.relpath(file_path, self.lore_dir)
                    index[rel_path] = head
                except Exception: pass
        self._lore_index = index
        return index

    def _ai_select_files(self, chapter_outline: Dict[str, Any], lore_index: Dict[str, str]) -> List[str]:
        if not lore_index: return []
        from .config import call_ollama_safe
        summary = chapter_outline.get("summary", "无大纲")
        title = chapter_outline.get("title", "未知章节")
        index_text = "\n".join([f"- [{path}]: {content[:80]}..." for path, content in lore_index.items()])
        prompt = f"""你是专业的设定管理员。请根据章节大纲挑选出**必须查阅**的设定文件路径。返回一个 **JSON 数组**。
【章节大纲】\n标题：{title}\n内容：{summary}\n\n【设定目录清单】\n{index_text}"""
        try:
            resp = call_ollama_safe(
                [{"role": "system", "content": "只返回纯 JSON 数组"}, {"role": "user", "content": prompt}],
                options={"num_predict": 150, "temperature": 0.2, "format": "json"},
            )
            raw = resp.get("content", "") if isinstance(resp, dict) else str(resp)
            selected = _robust_extract_json(raw)
            if isinstance(selected, list) and all(isinstance(i, str) for i in selected):
                return [str(i) for i in selected]
        except Exception: pass
        return []

    def retrieve_relevant_lore(self, chapter_outline: Dict[str, Any], max_files: int = 3) -> str:
        if not isinstance(chapter_outline, dict): return ""
        lore_index = self._build_lore_index()
        if not lore_index: return ""
        selected_paths = self._ai_select_files(chapter_outline, lore_index)[:max_files]
        if not selected_paths: return ""
        retrieved = "【当前章节相关核心设定（由 AI 自主挑选）】\n"
        for rel_path in selected_paths:
            file_path = os.path.join(self.lore_dir, rel_path)
            if not os.path.isfile(file_path): continue
            try:
                with open(file_path, "r", encoding="utf-8") as f: txt = f.read()[:1000].replace("\n\n\n", "\n").strip()
                name = os.path.splitext(os.path.basename(rel_path))[0]
                retrieved += f"\n### 📖 {name} (路径: {rel_path})\n{txt}...\n"
            except Exception: pass
        return retrieved

class LoreGapAnalyzer:
    """设定缺口自动分析与补全引擎。"""
    def __init__(self, project_dir: str, task_plan: Dict[str, Any], log_callback=None):
        self.project_dir = project_dir
        self._log = log_callback if log_callback else (lambda msg, **kwargs: print(msg))
        if not isinstance(task_plan, dict):
            self._log(f"⚠️ [LoreGapAnalyzer] task_plan 非字典类型，已使用空字典代替", level="WARN")
            self.task_plan = {}
        else:
            self.task_plan = task_plan
        self.lore_dir = os.path.join(project_dir, "03_设定")
        self.char_dir = os.path.join(self.lore_dir, "角色卡")
        self.dynamic_lore_file = os.path.join(self.lore_dir, "动态新增设定.md")
        os.makedirs(self.lore_dir, exist_ok=True)
        os.makedirs(self.char_dir, exist_ok=True)

    def analyze_and_fill(self, chapter_outline: Dict[str, Any], retrieved_lore: str) -> str:
        if not chapter_outline: return ""
        from .config import call_ollama_safe
        summary = chapter_outline.get("summary", "")
        title = chapter_outline.get("title", "")
        prompt = f"""你是极其严谨的小说设定校对员。请对比【本章大纲】与【已有设定摘要】，找出缺失的核心实体。
【本章大纲】\n标题：{title}\n内容：{summary}
【已有设定摘要】\n{retrieved_lore if retrieved_lore else "（暂无相关设定）"}
【任务】\n请输出 JSON 数组，每个对象包含：type(character/location/item), name, detail(不少于100字)。若全覆盖返回[]。"""
        try:
            resp = call_ollama_safe(
                [{"role": "system", "content": "纯 JSON API"}, {"role": "user", "content": prompt}],
                options={"num_predict": 1500, "temperature": 0.5, "format": "json"},
            )
            raw = resp.get("content", "") if isinstance(resp, dict) else str(resp)
            gaps = _robust_extract_json(raw)
            if not isinstance(gaps, list) or not all(isinstance(i, dict) for i in gaps):
                self._log("⚠️ LLM 返回的设定缺口结构不符合预期，已放弃补全", level="WARN")
                return ""
            if not gaps: return ""
            self._log(f"🔍 发现 {len(gaps)} 条设定缺口，启动自动补全...", level="INFO")
            return self._process_and_save_gaps(gaps)
        except Exception as exc:
            self._log(f"⚠️ 设定缺口分析异常：{exc}", level="WARN")
            return ""

    def _process_and_save_gaps(self, gaps: List[Dict]) -> str:
        generated_text = "【🌟 本章自动补全的新增设定（必须严格使用，禁止二次编造）】\n"
        for gap in gaps:
            entity_type = gap.get("type", "item").lower()
            name = gap.get("name", "").strip()
            detail = gap.get("detail", "").strip()
            if not name or not detail: continue
            safe_name = re.sub(r'[\\/*?:"<>|]', "", name)

            if entity_type == "character":
                file_path = os.path.join(self.char_dir, f"角色卡_{safe_name}.md")
                if not os.path.isfile(file_path):
                    md = f"# 角色卡：{name} (动态补全)\n\n**身份/定位**: 剧情配角/反派/路人\n\n## 详细设定\n{detail}\n\n> ⚠️ 此角色由系统在第 {self.task_plan.get('current_chapter', '?')} 章自动补全\n"
                    _atomic_write_text(file_path, md)
                    self._log(f"👤 自动补全角色卡：{safe_name}")
            else:
                need_prefix_newline = os.path.isfile(self.dynamic_lore_file) and os.path.getsize(self.dynamic_lore_file) > 0
                text_to_write = "\n" if need_prefix_newline else ""
                text_to_write += f"\n## {name} ({entity_type})\n{detail}\n\n---\n"
                _atomic_append_text(self.dynamic_lore_file, text_to_write)
                self._log(f"📦 自动补全 {entity_type} 设定：{safe_name}")
            generated_text += f"\n### {name} ({entity_type})\n{detail}\n"
        return generated_text
