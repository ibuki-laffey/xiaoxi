# core/learning_manager.py
# ==================== 学习管理器 ====================
# 功能：管理学习历史、失败记忆、成功经验

import os
import json
import time
import hashlib
from datetime import datetime
from typing import Dict, Any, List, Optional

from .config import TASK_TRACKING_DIR


class LearningManager:
    """
    学习管理器
    负责：
    1. 学习历史管理
    2. 失败记忆记录
    3. 成功经验记录
    4. 已学习解决方案查询
    """
    
    def __init__(self, log_callback=None):
        self.log_callback = log_callback
        self.failure_memory: List[Dict[str, Any]] = []
        self.max_failure_memory = 1000
        self.learning_history_file = os.path.join(TASK_TRACKING_DIR, "learning_history.json")
    
    def _log(self, msg: str) -> None:
        if self.log_callback:
            self.log_callback(msg)
    
    def load_learning_history(self) -> None:
        """加载学习历史"""
        try:
            with open(self.learning_history_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self.failure_memory = data.get("failures", [])[-self.max_failure_memory:]
            self._log(f"📚 已加载 {len(self.failure_memory)} 条学习记忆")
        except Exception:
            self.failure_memory = []
    
    def save_learning_history(self) -> None:
        """保存学习历史"""
        try:
            os.makedirs(TASK_TRACKING_DIR, exist_ok=True)
            with open(self.learning_history_file, 'w', encoding='utf-8') as f:
                json.dump({
                    "failures": self.failure_memory[-self.max_failure_memory:],
                    "last_updated": time.time()
                }, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self._log(f"❌ 保存学习历史失败：{e}")
    
    def record_failure(self, user_request: str, error_reason: str, solution_attempted: str) -> None:
        """记录失败经验"""
        failure = {
            "id": hashlib.md5(f"{user_request}{time.time()}".encode()).hexdigest()[:16],
            "timestamp": datetime.now().isoformat(),
            "user_request": user_request,
            "error_reason": error_reason,
            "solution_attempted": solution_attempted,
            "learned_solution": None,
            "success_count": 0,
            "fail_count": 1
        }
        
        for existing in self.failure_memory:
            if existing["user_request"] == user_request:
                existing["fail_count"] += 1
                existing["last_attempt"] = datetime.now().isoformat()
                self._log(f"📝 更新失败记忆：{user_request[:50]}...")
                self.save_learning_history()
                return
        
        self.failure_memory.append(failure)
        self._log(f"📝 记录失败记忆：{user_request[:50]}...")
        self.save_learning_history()
    
    def record_success(self, user_request: str, solution: str) -> bool:
        """记录成功经验"""
        for existing in self.failure_memory:
            if existing["user_request"] == user_request:
                existing["learned_solution"] = solution
                existing["success_count"] += 1
                existing["last_success"] = datetime.now().isoformat()
                self._log(f"✅ 记录成功解决方案：{user_request[:50]}...")
                self.save_learning_history()
                return True
        
        success = {
            "id": hashlib.md5(f"{user_request}{time.time()}".encode()).hexdigest()[:16],
            "timestamp": datetime.now().isoformat(),
            "user_request": user_request,
            "learned_solution": solution,
            "success_count": 1,
            "fail_count": 0
        }
        self.failure_memory.append(success)
        self._log(f"✅ 记录新成功方案：{user_request[:50]}...")
        self.save_learning_history()
        return True
    
    def get_learned_solution(self, user_request: str) -> Optional[str]:
        """获取已学习的解决方案"""
        for existing in self.failure_memory:
            if existing["user_request"] == user_request:
                if existing.get("learned_solution"):
                    return existing["learned_solution"]
        return None
