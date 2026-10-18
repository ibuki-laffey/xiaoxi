import os
import json
import re
from datetime import datetime
from typing import Dict, Any, Optional, List

from .config import TASK_TRACKING_DIR, call_ollama_safe, PROFESSIONAL_LOG_ENABLED

class SceneGraphManager:
    """
    场景图 + 因果支路管理。
    生成的文件结构（以 task_id 为前缀）：
    <task_id>_scene_graph.json – 场景列表
    <task_id>_cause_edges.json – 每个场景的三条因果支路
    """

    def __init__(self, log_callback=None):
        self.log_callback = log_callback
        self.scene_dir = os.path.join(TASK_TRACKING_DIR, "scene_graphs")
        os.makedirs(self.scene_dir, exist_ok=True)
        self.prof_log = PROFESSIONAL_LOG_ENABLED

    def _log(self, msg: str, level: str = "INFO"):
        if self.log_callback:
            ts = datetime.now().isoformat()
            if self.prof_log:
                entry = {"timestamp": ts, "level": level, "module": "SceneGraphManager", "message": msg}
                msg = json.dumps(entry, ensure_ascii=False)
            else:
                msg = f"[{ts}] [{level}] {msg}"
            try:
                self.log_callback(msg, level=level)
            except TypeError:
                self.log_callback(msg)

    def _robust_extract_json(self, text: str) -> Optional[dict]:
        """🌟 工业级 JSON 提取器：剥离 Markdown、首尾括号定位、清理尾部逗号。"""
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
        """🌟 工业级原子写入 JSON，防止断电数据丢失。"""
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

    def _scene_path(self, task_id: str) -> str:
        return os.path.join(self.scene_dir, f"{task_id}_scene_graph.json")

    def _edge_path(self, task_id: str) -> str:
        return os.path.join(self.scene_dir, f"{task_id}_cause_edges.json")

    def generate_scene_graph(self, task_id: str, user_prompt: str) -> bool:
        """根据用户的整体创意生成场景列表（JSON）并写入磁盘。"""
        self._log(f"🏞️ 为任务 {task_id} 生成场景图…")

        prompt = f"""请为下面的小说创作需求生成 **场景列表**（JSON），每个场景必须包含以下字段：
id：唯一标识（可以用 4 位十六进制）
name：地点名称
description：该地点的概括（150-200 字）
tags：与题材/氛围相关的关键词列表

需求： {user_prompt}

请只返回下面的 JSON（不要有任何说明文字或 Markdown 标记）:
{{"scenes": [{{"id": "a1b2", "name": "首都城墙", "description": "...", "tags": ["城堡","政治"]}}]}}"""

        try:
            # 🌟 核心修复：强制开启 format: json，从根源约束 LLM
            resp = call_ollama_safe(
                [{"role": "user", "content": prompt}], 
                timeout=180,
                options={"temperature": 0.3, "format": "json"}
            )
            if not resp.get("success"):
                self._log(f"⚠️ 场景图生成失败：{resp.get('error')}", level="WARN")
                return False

            # 🌟 核心修复：废弃脆弱的 find/rfind，使用防弹提取器
            scene_json = self._robust_extract_json(resp.get("content", ""))
            if not scene_json or "scenes" not in scene_json:
                self._log("⚠️ 解析场景图 JSON 失败或结构不完整", level="WARN")
                return False

            path = self._scene_path(task_id)
            if self._atomic_write_json(path, scene_json):
                self._log(f"✅ 场景图已写入 {path}")
                return True
            return False
        except Exception as e:
            self._log(f"❌ 生成场景图异常：{e}", level="ERROR")
            return False

    def generate_cause_edges(self, task_id: str) -> bool:
        """根据已生成的场景图，为每个场景自动生成三条因果支路。"""
        self._log(f"⚙️ 为任务 {task_id} 生成因果支路…")

        scene_path = self._scene_path(task_id)
        if not os.path.exists(scene_path):
            self._log("⚠️ 场景文件不存在，无法生成因果支路", level="WARN")
            return False

        try:
            with open(scene_path, "r", encoding="utf-8") as f:
                scene_data = json.load(f)
        except Exception as e:
            self._log(f"⚠️ 读取场景文件失败：{e}", level="WARN")
            return False

        scenes = scene_data.get("scenes", [])
        if not scenes:
            self._log("⚠️ 场景列表为空，跳过因果支路生成", level="WARN")
            return False

        prompt = f"""请为下面的每个场景分别生成 **三条因果支路**（不经历、经历、离开），返回 JSON。
场景列表如下：
{json.dumps({"scenes": scenes}, ensure_ascii=False, indent=2)}

JSON 结构示例（不要有任何说明文字或 Markdown 标记）：
{{"edges": [{{"scene_id":"<id>","type":"not_experience|experience|leave","description":"..."}}]}}"""

        try:
            # 🌟 核心修复：强制开启 format: json
            resp = call_ollama_safe(
                [{"role": "user", "content": prompt}], 
                timeout=240,
                options={"temperature": 0.3, "format": "json"}
            )
            if not resp.get("success"):
                self._log(f"⚠️ 因果支路生成失败：{resp.get('error')}", level="WARN")
                return False

            # 🌟 核心修复：使用防弹提取器
            edge_json = self._robust_extract_json(resp.get("content", ""))
            if not edge_json or "edges" not in edge_json:
                self._log("⚠️ 解析因果支路 JSON 失败或结构不完整", level="WARN")
                return False

            path = self._edge_path(task_id)
            if self._atomic_write_json(path, edge_json):
                self._log(f"✅ 因果支路已写入 {path}")
                return True
            return False
        except Exception as e:
            self._log(f"❌ 生成因果支路异常：{e}", level="ERROR")
            return False

    def load_scene_graph(self, task_id: str) -> Optional[Dict[str, Any]]:
        """读取场景图。"""
        path = self._scene_path(task_id)
        if not os.path.exists(path): return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            self._log(f"⚠️ 读取场景图出错：{e}", level="WARN")
            return None

    def load_cause_edges(self, task_id: str) -> Optional[Dict[str, Any]]:
        """读取因果支路。"""
        path = self._edge_path(task_id)
        if not os.path.exists(path): return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            self._log(f"⚠️ 读取因果支路出错：{e}", level="WARN")
            return None
