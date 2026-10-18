# ==================== 深度训练系统 ====================
import os
import json
from datetime import datetime
from .config import KNOWLEDGE_BASE_DIR

class TrainingManager:
    def __init__(self, log_callback=None):
        self.log_callback = log_callback
        self.data_file = os.path.join(KNOWLEDGE_BASE_DIR, "training_logs.json")
        self.data = {"sessions": [], "reflections": []}
        self._load()

    def _log(self, msg):
        if self.log_callback:
            self.log_callback(msg)

    def _load(self):
        if os.path.exists(self.data_file):
            try:
                with open(self.data_file, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                    self.data = json.loads(content) if content else {"sessions": [], "reflections": []}
            except:
                self.data = {"sessions": [], "reflections": []}
        else:
            self.data = {"sessions": [], "reflections": []}

    def _save(self):
        with open(self.data_file, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)

    def record_session(self, user_input, ai_response, emotion="neutral"):
        self.data["sessions"].append({
            "time": datetime.now().isoformat(),
            "user": user_input,
            "ai": ai_response,
            "emotion": emotion
        })
        if len(self.data["sessions"]) > 1000:
            self.data["sessions"] = self.data["sessions"][-1000:]
        self._save()

    def reflect_and_learn(self, personality_manager):
        self._log("🧠 开始深度反思训练...")
        sessions = self.data["sessions"][-100:]
        improvements = 0
        
        for s in sessions:
            user = s.get("user", "")
            if "叫我" in user or "称呼我" in user:
                personality_manager.update_memory("user_name", user)
                improvements += 1
            if "喜欢" in user or "讨厌" in user:
                personality_manager.update_memory("preferences", user[:100])
                improvements += 1
        
        self.data["reflections"].append({
            "time": datetime.now().isoformat(),
            "improvements": improvements,
            "sessions_analyzed": len(sessions)
        })
        self._save()
        self._log(f"✅ 反思完成，更新 {improvements} 条记忆")
        return {"success": True, "improvements": improvements}

    def get_learning_context(self):
        sessions = self.data["sessions"][-50:]
        if not sessions:
            return "\n【学习回顾】无历史数据"
        success_count = sum(1 for s in sessions if s.get("emotion") != "angry")
        success_rate = success_count / len(sessions) * 100 if sessions else 0
        return f"\n【学习回顾】\n近期对话：{len(sessions)} 次\n情绪健康度：{success_rate:.1f}%"

    def clear_training_data(self):
        self.data = {"sessions": [], "reflections": []}
        self._save()
        return {"success": True, "message": "✅ 训练数据已清空"}
