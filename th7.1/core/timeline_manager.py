import os
import json
import re
from datetime import datetime
from typing import Dict, Any, Optional

from .config import TASK_TRACKING_DIR, call_ollama_safe, PROFESSIONAL_LOG_ENABLED

class TimelineManager:
    """时间线管理器：生成、保存、读取时间线 JSON。"""

    def __init__(self, log_callback=None):
        self.log_callback = log_callback
        self.timeline_dir = os.path.join(TASK_TRACKING_DIR, "timelines")
        os.makedirs(self.timeline_dir, exist_ok=True)
        self.prof_log = PROFESSIONAL_LOG_ENABLED

    def _log(self, msg: str, level: str = "INFO"):
        if self.log_callback:
            ts = datetime.now().isoformat()
            if self.prof_log:
                entry = {"timestamp": ts, "level": level, "module": "TimelineManager", "message": msg}
                msg = json.dumps(entry, ensure_ascii=False)
            else:
                msg = f"[{ts}] [{level}] {msg}"
            try: self.log_callback(msg, level=level)
            except TypeError: self.log_callback(msg)

    def _robust_extract_json(self, text: str) -> Optional[dict]:
        """🌟 工业级 JSON 提取器。"""
        if not text: return None
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
        text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE).strip()
        start_idx = text.find('{')
        end_idx = text.rfind('}')
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            json_str = text[start_idx:end_idx + 1]
            json_str = re.sub(r",\s*([}\]])", r"\1", json_str)
            try: return json.loads(json_str)
            except json.JSONDecodeError: pass
        try: return json.loads(text)
        except json.JSONDecodeError: return None

    def _atomic_write_json(self, filepath: str, data: Any) -> bool:
        """🌟 工业级原子写入 JSON。"""
        tmp_path = filepath + ".tmp"
        try:
            dir_name = os.path.dirname(filepath)
            if dir_name: os.makedirs(dir_name, exist_ok=True)
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, filepath)
            return True
        except Exception as e:
            if os.path.exists(tmp_path):
                try: os.remove(tmp_path)
                except Exception: pass
            self._log(f"❌ 原子写入失败 ({filepath}): {e}", level="ERROR")
            return False

    def _filepath(self, task_id: str) -> str:
        return os.path.join(self.timeline_dir, f"{task_id}_timeline.json")

    def generate_timeline(self, task_id: str, user_prompt: str) -> bool:
        """根据用户的整体创意生成时间线 JSON。"""
        self._log(f"🕰️ 开始为任务 {task_id} 生成时间线…")
        prompt = f"""请为下面的小说创作需求生成一份**时间线**（JSON）。
需求：{user_prompt}
时间线要满足：
1. 用数组 `timeline` 表示，每个元素为 {{"year":int,"event":"描述"}}；
2. 必须覆盖整个小说的时间跨度；
3. 与后面的大纲章节数保持对应关系。

请只返回 JSON（不要有任何解释文字或 Markdown 标记）:
{{"timeline": [{{"year": 0, "event": "世界观建立"}}, {{"year": 10, "event": "主角出生"}}]}}"""

        resp = call_ollama_safe(
            [{"role": "system", "content": "你是一个底层 JSON API，只输出纯 JSON。"}, {"role": "user", "content": prompt}], 
            timeout=180, options={"temperature": 0.3, "format": "json"}
        )
        if not resp.get("success"):
            self._log(f"⚠️ 时间线生成失败：{resp.get('error')}", level="WARN")
            return False

        timeline_json = self._robust_extract_json(resp.get("content", ""))
        if not timeline_json or "timeline" not in timeline_json:
            self._log("⚠️ 解析时间线 JSON 失败或结构缺失", level="WARN")
            return False

        path = self._filepath(task_id)
        if self._atomic_write_json(path, timeline_json):
            self._log(f"✅ 时间线已写入 {path}")
            return True
        return False

    def load_timeline(self, task_id: str) -> Optional[Dict[str, Any]]:
        """读取已保存的时间线。"""
        path = self._filepath(task_id)
        if not os.path.exists(path): return None
        try:
            with open(path, "r", encoding="utf-8") as f: return json.load(f)
        except Exception as e:
            self._log(f"⚠️ 读取时间线文件出错：{e}", level="WARN")
            return None
