import os
import json
import re
from typing import Dict, Any, List

class EventTracker:
    """跨章事件追踪器：解决长事件断层、物理动作不连贯、空间跳跃问题。"""
    
    def __init__(self, project_dir: str, log_callback=None):
        self.log_callback = log_callback
        self.tracker_dir = os.path.join(project_dir, "03_设定", "事件追踪")
        os.makedirs(self.tracker_dir, exist_ok=True)
        self.active_events_path = os.path.join(self.tracker_dir, "active_events.json")

    def _log(self, msg: str, level: str = "INFO") -> None:
        """统一日志输出，兼容单参数和双参数回调。"""
        if self.log_callback:
            try: self.log_callback(msg, level=level)
            except TypeError: self.log_callback(msg)
        else:
            print(f"[{level}] {msg}")

    def load_active_events(self) -> List[Dict[str, Any]]:
        """读取活跃事件，文件损坏时安全回退为空列表。"""
        if not os.path.exists(self.active_events_path):
            return []
        try:
            with open(self.active_events_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception as exc:
            self._log(f"⚠️ 加载活跃事件文件失败，已回退为空列表：{exc}", level="WARN")
            return []

    def save_active_events(self, events: List[Dict[str, Any]]) -> bool:
        """原子写入活跃事件，防止进程意外终止导致文件残缺。"""
        tmp_path = f"{self.active_events_path}.tmp"
        try:
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(events, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.active_events_path)
            return True
        except Exception as exc:
            if os.path.isfile(tmp_path):
                try: os.remove(tmp_path)
                except Exception: pass
            self._log(f"❌ 保存活跃事件失败：{exc}", level="ERROR")
            return False

    def get_breakpoint_context(self, chapter_outline: Dict[str, Any]) -> str:
        """根据活跃事件生成前置上下文，提供过渡策略池供 LLM 自主决策。"""
        events = self.load_active_events()
        if not events:
            return ""

        context = "【前序状态同步与过渡策略池 (自主决策)】\n"
        for evt in events:
            context += (
                f"▶ 活跃事件：{evt.get('name', '未知')} (进度: {evt.get('progress', '初期')})\n"
                f"  - 上一章物理断点：{evt.get('last_physical_breakpoint', '无')}\n"
                f"  - 上一章空间位置：{evt.get('current_location', '未知')}\n"
                f"  - 遗留微观悬念：{evt.get('micro_hooks', '无')}\n\n"
            )

        context += (
            "⚠️ 过渡策略自主选择（请根据本章大纲的内在逻辑与情感张力，自主决定使用哪种过渡方式，切勿生搬硬套）：\n"
            "1️⃣ 微观无缝衔接 → 直接延续上章的动作、对话或感官细节（适合高潮或紧凑剧情）。\n"
            "2️⃣ 时间跳跃/留白 → 使用蒙太奇手法或环境描写暗示时间流逝（适合平缓过渡或修炼/赶路）。\n"
            "3️⃣ 空间切换/平行剪辑 → 通过“与此同时”或视角转移切换场景（适合多线叙事）。\n"
            "4️⃣ 视角切换 → 切换到配角或宏观背景视角（适合补充世界观或侧面烘托）。\n"
        )
        return context

    def update_event_status(self, chapter_num: int, chapter_content: str, chapter_outline: Dict[str, Any]):
        """
        在章节完成后调用 LLM，自主分析并更新活跃事件的物理断点和进度。
        🌟 核心重构：释放 LLM 主动性，废弃死板模板，使用首尾括号定位法 robust 提取 JSON。
        """
        from .config import call_ollama_safe

        events = self.load_active_events()

        if not events:
            summary = chapter_outline.get("summary", "")
            if "【跨章事件" in summary or "伏笔" in summary or "悬念" in summary:
                events.append({
                    "name": chapter_outline.get("title", "新事件"),
                    "progress": "初期",
                    "last_physical_breakpoint": "事件刚爆发",
                    "current_location": "未知",
                    "micro_hooks": "无",
                })
            else:
                return

        prompt = f"""你是专业的小说连贯性审查员。第 {chapter_num} 章刚刚写完。
本章大纲：{chapter_outline.get('summary', '无')}
本章结尾内容（最后 800 字）：
{chapter_content[-800:]}

请深度分析本章结尾的剧情张力、人物动作和空间位置，更新当前活跃事件的状态。
输出纯 JSON 数组，每项结构如下：
[
  {{
    "name": "事件名",
    "progress": "初期/中期/高潮/已解决",
    "last_physical_breakpoint": "本章最后定格的具体物理动作或画面（≤50字，需极具画面感）",
    "current_location": "当前精确地点",
    "micro_hooks": "遗留的微观悬念或未尽之意（如无则填'无'）"
  }}
]
只返回纯 JSON 数组，绝不包含任何解释或 Markdown 标记！"""

        try:
            resp = call_ollama_safe(
                [{"role": "user", "content": prompt}],
                options={"num_predict": 400, "temperature": 0.3},
            )
            raw = resp.get("content", "") if isinstance(resp, dict) else str(resp)
            
            # 🌟 健壮的 JSON 提取：废弃非贪婪正则，改用首尾括号定位 + Markdown 剥离
            text = raw.strip()
            if text.startswith("```"):
                text = re.sub(r"^```(?:json)?\s*", "", text)
                text = re.sub(r"\s*```$", "", text)
                
            start_idx = text.find('[')
            end_idx = text.rfind(']')
            
            if start_idx == -1 or end_idx == -1 or end_idx <= start_idx:
                self._log("⚠️ LLM 未返回有效的 JSON 数组结构，跳过事件更新", level="WARN")
                return

            json_str = text[start_idx:end_idx + 1]
            try:
                updated = json.loads(json_str)
            except json.JSONDecodeError:
                self._log("⚠️ LLM 返回的 JSON 解析失败，跳过事件更新", level="WARN")
                return

            if not isinstance(updated, list) or not all(isinstance(i, dict) for i in updated):
                self._log("⚠️ LLM 返回的结构不符合预期（非 dict 列表），跳过事件更新", level="WARN")
                return

            for upd in updated:
                for evt in events:
                    if evt.get("name") == upd.get("name"):
                        evt.update(upd)

            events = [e for e in events if e.get("progress") != "已解决"]
            self.save_active_events(events)

        except Exception as exc:
            self._log(f"❌ 更新活跃事件时异常：{exc}", level="ERROR")
