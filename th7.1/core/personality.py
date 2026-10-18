# core/personality.py
# ==================== 人格管理系统 ====================
import os
import json
import re
from datetime import datetime
from .config import (KNOWLEDGE_BASE_DIR,
    call_ollama_safe, MODEL_NAME,
     SAFE_TIMEOUT
     )

class PersonalityManager:
    DEFAULT_PERSONALITY = {
        "version": "1.0",
        "created_at": "",
        "updated_at": "",
        "base": {
            "name": "筱溪",
            "icon": "❤️",
            "title": "智能电脑助手",
            "style": "friendly",
            "tone": "warm",
            "emoji_usage": "moderate",
            "response_length": "medium",
            "humor_level": "light"
        },
        "user_preferences": {},
        "learning": {
            "total_conversations": 0,
            "total_tasks": 0,
            "success_rate": 0.0,
            "common_mistakes": [],
            "improvements": [],
            "success_count": 0
        },
        "memory": {
            "user_name": "",
            "user_habits": [],
            "important_dates": [],
            "favorite_commands": [],
            "past_corrections": []
        },
        "backdoor": {
            "reset_key": "XIAOXI_RESET_2024",
            "backup_enabled": True,
            "last_backup": ""
        }
    }
    
    def __init__(self, log_callback=None):
        self.log_callback = log_callback
        self.personality_file = os.path.join(KNOWLEDGE_BASE_DIR, "personality.json")
        self.backup_file = os.path.join(KNOWLEDGE_BASE_DIR, "personality_backup.json")
        self.personality = None
        self.data = {}
        self._ensure_file_exists()
        self.load_personality()
    
    def _log(self, msg):
        if self.log_callback:
            self.log_callback(msg)
    
    def _ensure_file_exists(self):
        os.makedirs(KNOWLEDGE_BASE_DIR, exist_ok=True)
        if not os.path.exists(self.personality_file):
            self._create_default_personality()
    
    def _create_default_personality(self):
        self.DEFAULT_PERSONALITY["created_at"] = datetime.now().isoformat()
        self.DEFAULT_PERSONALITY["updated_at"] = datetime.now().isoformat()
        with open(self.personality_file, 'w', encoding='utf-8') as f:
            json.dump(self.DEFAULT_PERSONALITY, f, indent=2, ensure_ascii=False)
        self._log("✅ 默认人格文档已创建")
    
    def load_personality(self):
        try:
            with open(self.personality_file, 'r', encoding='utf-8') as f:
                self.personality = json.load(f)
            self.data = self.personality
            self._log(f"🧠 人格文档已加载：{self.personality['base']['name']}")
        except Exception as e:
            self._log(f"❌ 加载人格失败：{e}，尝试从备份恢复...")
            self._restore_from_backup()
    
    def save_personality(self):
        try:
            self.personality["updated_at"] = datetime.now().isoformat()
            if self.personality.get("backdoor", {}).get("backup_enabled", True):
                self._create_backup()
            with open(self.personality_file, 'w', encoding='utf-8') as f:
                json.dump(self.personality, f, indent=2, ensure_ascii=False)
            self._log("💾 人格文档已保存")
        except Exception as e:
            self._log(f"❌ 保存人格失败：{e}")
    
    def _create_backup(self):
        try:
            with open(self.backup_file, 'w', encoding='utf-8') as f:
                json.dump(self.personality, f, indent=2, ensure_ascii=False)
            self.personality["backdoor"]["last_backup"] = datetime.now().isoformat()
            self._log("📦 人格备份已创建")
        except Exception as e:
            self._log(f"⚠️ 备份失败：{e}")
    
    def _restore_from_backup(self):
        try:
            if os.path.exists(self.backup_file):
                with open(self.backup_file, 'r', encoding='utf-8') as f:
                    self.personality = json.load(f)
                self.save_personality()
                self._log("✅ 已从备份恢复人格")
            else:
                self._create_default_personality()
                self.load_personality()
        except Exception as e:
            self._log(f"❌ 恢复失败：{e}")
            self._create_default_personality()
            self.load_personality()
    
    def update_preference(self, key, value):
        self.personality.setdefault("user_preferences", {})
        self.personality["user_preferences"][key] = value
        self.personality["updated_at"] = datetime.now().isoformat()
        self.save_personality()
        self._log(f"🧠 偏好已更新：{key} = {value}")
    
    def add_memory(self, memory_type, content):
        self.personality.setdefault("memory", {})
        self.personality["memory"].setdefault(memory_type, [])
        self.personality["memory"][memory_type].append({
            "content": content,
            "timestamp": datetime.now().isoformat()
        })
        # 只保留最近 50 条
        if len(self.personality["memory"][memory_type]) > 50:
            self.personality["memory"][memory_type] = self.personality["memory"][memory_type][-50:]
        self.save_personality()
        self._log(f"🧠 记忆已添加：{memory_type}")
    
    def record_correction(self, original, corrected):
        correction = {"original": original, "corrected": corrected, "timestamp": datetime.now().isoformat()}
        self.add_memory("past_corrections", correction)
        self._log("📝 纠正已记录")
    
    def update_emotion(self, emotion):
        self.personality.setdefault("emotion_state", "neutral")
        self.personality["emotion_state"] = emotion
        self.save_personality()
    
    def increment_conversation(self):
        self.personality.setdefault("learning", {})
        self.personality["learning"]["total_conversations"] = \
            self.personality["learning"].get("total_conversations", 0) + 1
        self.save_personality()
    
    def increment_task(self, success=True):
        self.personality.setdefault("learning", {})
        self.personality["learning"]["total_tasks"] = \
            self.personality["learning"].get("total_tasks", 0) + 1
        total = self.personality["learning"]["total_tasks"]
        if success:
            self.personality["learning"]["success_count"] = \
                self.personality["learning"].get("success_count", 0) + 1
        self.personality["learning"]["success_rate"] = \
            self.personality["learning"].get("success_count", 0) / total if total > 0 else 0
        self.save_personality()
    
    def reset_personality(self, reset_key=""):
        if reset_key != self.personality.get("backdoor", {}).get("reset_key", ""):
            return {"success": False, "message": "❌ 重置密钥错误"}
        old = self.personality.copy()
        self._create_default_personality()
        self.load_personality()
        self.personality["memory"]["user_name"] = old.get("memory", {}).get("user_name", "")
        self.save_personality()
        self._log("🔄 人格已重置")
        return {"success": True, "message": "✅ 人格已重置，保留了核心记忆"}
    
    # ---------- 导入/导出 ----------
    def export_personality(self, filepath):
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(self.personality, f, indent=2, ensure_ascii=False)
            return {"success": True, "message": f"✅ 人格已导出：{filepath}"}
        except Exception as e:
            return {"success": False, "message": f"❌ 导出失败：{e}"}
    
    def import_personality(self, filepath):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                imported = json.load(f)
            if "base" not in imported or "version" not in imported:
                return {"success": False, "message": "❌ 文件格式错误"}
            self.personality = imported
            self.save_personality()
            return {"success": True, "message": "✅ 人格已导入"}
        except Exception as e:
            return {"success": False, "message": f"❌ 导入失败：{e}"}
    
    # ---------- 生成系统上下文 ----------
    def get_context(self):
        """
        生成供 LLM 使用的系统 Prompt。
        除了原有的人格设定、用户偏好、记忆、学习进度外，
        现在还会把最近一次 LLM 反思的结构化信息（last_reflection）追加进去，
        让 AI 在后续对话中能够感知自己的改进点与用户偏好。
        """
        if not self.personality:
            return ""

        base = self.personality.get("base", {})
        prefs = self.personality.get("user_preferences", {})
        mem = self.personality.get("memory", {})
        learning = self.personality.get("learning", {})

        # ==== 原始上下文 ====
        context = f"""
【人格设定】
- 名字：{base.get('name', '筱溪')}
- 风格：{base.get('style', 'friendly')}
- 语气：{base.get('tone', 'warm')}

【用户偏好】
- 喜欢的主题：{', '.join(prefs.get('favorite_topics', [])[:5]) or '无'}
- 不喜欢的主题：{', '.join(prefs.get('disliked_topics', [])[:5]) or '无'}
- 交流风格：{prefs.get('communication_style', '自然友好')}

【重要记忆】
- 用户名：{mem.get('user_name', '未知')}
- 习惯：{', '.join(mem.get('user_habits', [])[:5]) or '无'}

【学习进度】
- 对话次数：{learning.get('total_conversations', 0)}
- 任务次数：{learning.get('total_tasks', 0)}
- 成功率：{learning.get('success_rate', 0) * 100:.1f}%

"""

        # ==== 新增：LLM 反思信息 ====
        last_reflection = learning.get("last_reflection")
        if isinstance(last_reflection, dict) and last_reflection:
            # 把结构化信息转成易读文本（保留 JSON 结构，便于 LLM 直接使用）
            reflection_json = json.dumps(last_reflection, ensure_ascii=False, indent=2)
            context += f"""
【最近一次深度反思（由 LLM 自动生成）】
{reflection_json}
"""
        return context


    def get_stats(self):
        return {
            "name": self.personality.get("base", {}).get("name", "筱溪"),
            "conversations": self.personality.get("learning", {}).get("total_conversations", 0),
            "tasks": self.personality.get("learning", {}).get("total_tasks", 0),
            "success_rate": self.personality.get("learning", {}).get("success_rate", 0) * 100,
            "memories": sum(len(v) for v in self.personality.get("memory", {}).values()),
            "created": self.personality.get("created_at", "未知"),
            "updated": self.personality.get("updated_at", "未知")
        }
    
    def train_from_topics(self):
        """
        基于最近的对话历史让本地 LLM 进行「深度反思」。
        - 读取最近 N 条（默认 10 条）用户对话 + AI 回复；
        - 构造专用 Prompt，要求 LLM 输出 JSON，字段包括:
            * preferences  – 用户偏好（如喜欢的主题、称呼等）；
            * mistakes      – 交互中出现的常见误解或错误；
            * suggestions   – 对未来对话、人格设置的改进建议；
        - 将 LLM 返回的结构化信息写入人格 JSON
          (`self.personality['learning']['last_reflection']`)；
        - 同步更新 `user_preferences`、`memory`（如果有直接抽取到的内容）；
        - 返回统一的结果字典，供 GUI 显示。
        """
        self._log("🧠 开始基于 LLM 的深度训练（从对话历史中提取洞见）")
        # ------------------------------------------------------------------
        # ① 读取最近的对话记录（倒序取最近的 10 条，会话文件位于 KNOWLEDGE_BASE_DIR）
        # ------------------------------------------------------------------
        recent_messages = []
        try:
            # 按文件修改时间倒序，取最近的几个对话文件
            files = sorted(
                (f for f in os.listdir(KNOWLEDGE_BASE_DIR) if f.startswith("conversation_") and f.endswith(".json")),
                key=lambda x: os.path.getmtime(os.path.join(KNOWLEDGE_BASE_DIR, x)),
                reverse=True,
            )
            for fname in files[:10]:                     # 只取最近 10 条记录
                with open(os.path.join(KNOWLEDGE_BASE_DIR, fname), "r", encoding="utf-8") as f:
                    data = json.load(f)
                    # 只保留 user/assistant 两种角色，保持顺序
                    for msg in data.get("messages", []):
                        if msg.get("role") in ("user", "assistant"):
                            recent_messages.append(f"{msg['role'].capitalize()}: {msg.get('content', '').strip()}")
        except Exception as e:
            self._log(f"⚠️ 读取对话历史出错，回退到规则抽取方式：{e}")
            recent_messages = []   # 若读取失败仍继续使用空历史，让 LLM 给出通用建议

        # ------------------------------------------------------------------
        # ② 组装 Prompt（如果历史为空则交给 LLM 给出默认建议）
        # ------------------------------------------------------------------
        history_text = "\n".join(recent_messages) if recent_messages else "（暂无近期对话）"
        prompt = f"""请根据以下最近的对话历史（用户与 AI 的来回），帮助 AI 完成深度自省。

对话历史：
{history_text}

请返回一个 **合法的 JSON**（不含任何额外文字），结构如下：

{{
  "preferences": {{   // 用户偏好，可直接写入 personality['user_preferences']
    "user_name": "<如果用户在对话中提到想让你怎么称呼>",
    "favorite_topics": ["topic1", "topic2", ...],
    "disliked_topics": ["topicX", ...]
  }},
  "mistakes": [   // 在对话中 AI 出现的常见错误或误解
    "错误描述 1",
    "错误描述 2"
  ],
  "suggestions": [ // 对未来对话、人格设定、交互方式的改进建议
    "建议 1",
    "建议 2"
  ]
}}

请确保 JSON 格式完整且可被 `json.loads` 正确解析。谢谢！"""

        # ------------------------------------------------------------------
        # ③ 调用本地 LLM（Ollama）进行推理
        # ------------------------------------------------------------------
        try:
            llm_resp = call_ollama_safe(
                messages=[{"role": "user", "content": prompt}],
                timeout=SAFE_TIMEOUT,
                model=MODEL_NAME,
            )
            if not llm_resp.get("success"):
                raise RuntimeError(f"Ollama 调用失败：{llm_resp.get('error')}")
            raw_content = llm_resp.get("content", "")
        except Exception as e:
            self._log(f"❌ LLM 深度训练调用异常：{e}")
            # 兜底：返回空结果，使 UI 仍能正常显示
            raw_content = ""

        # ------------------------------------------------------------------
        # ④ 解析 LLM 输出（JSON）并写入人格结构
        # ------------------------------------------------------------------
        reflection = {}
        learned = 0
        try:
            # 某些模型会在 JSON 前后多余文字，尝试提取首个 "{" 到最后的 "}"
            json_start = raw_content.find("{")
            json_end = raw_content.rfind("}") + 1
            json_str = raw_content[json_start:json_end] if json_start != -1 else raw_content
            reflection = json.loads(json_str)
            learned = 1
        except Exception as e:
            self._log(f"⚠️ LLM 返回的内容无法解析为 JSON，回退解析：{e}")

        # ------------------------------------------------------------------
        # ⑤ 将解析结果写回人格（若有对应字段则覆盖）
        # ------------------------------------------------------------------
        # 把 LLM 给出的偏好写入 user_preferences（若存在）
        if isinstance(reflection.get("preferences"), dict):
            pref = reflection["preferences"]
            # 只覆盖已有的键，防止意外覆盖其它字段
            for k, v in pref.items():
                if v:
                    self.personality.setdefault("user_preferences", {})[k] = v
            learned += 1

        # 把记忆（如叫法）写入 memory
        if "user_name" in reflection.get("preferences", {}):
            self.personality.setdefault("memory", {})["user_name"] = reflection["preferences"]["user_name"]
            learned += 1

        # 记录本次 LLM 反思结果（完整 JSON，供后续 Prompt 使用）
        self.personality.setdefault("learning", {})["last_reflection"] = reflection

        # 保存人格文件
        self.save_personality()

        self._log(f"✅ 深度训练完成，提取了 {learned} 条新信息")
        return {"success": True, "learned_count": learned, "reflection": reflection}

