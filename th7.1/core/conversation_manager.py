# core/conversation_manager.py
# ==================== 对话管理器 ====================
# 功能：管理对话历史、话题切换、磁盘缓存集成

import os
import json
import time
import threading
from datetime import datetime
from typing import Dict, Any, List, Optional

from .config import KNOWLEDGE_BASE_DIR, MAX_OUTPUT_TOKENS, MAX_CONTEXT_TOKENS, TEMPERATURE, REQUEST_TIMEOUT, SAFE_TIMEOUT
from .disk_cache_manager import DiskCacheManager, save_conversation_to_disk, load_conversation_from_disk
import requests


class ConversationManager:
    """
    对话管理器
    负责：
    1. 对话历史管理
    2. 话题切换与持久化
    3. 磁盘缓存集成
    4. AI 对话调用
    """
    
    def __init__(self, log_callback=None, model_client=None):
        self.log_callback = log_callback
        self.model_client = model_client
        
        self.conversation_history: List[Dict[str, str]] = []
        self.last_raw_response = ""
        self.max_history_length = 500
        self.current_topic = "default"
        
        # 线程锁
        self._conversation_lock = threading.Lock()
        
        # 磁盘缓存
        self.disk_cache: Optional[DiskCacheManager] = None
        
        # 机器人配置
        self.robot_name = "筱溪"
        self.robot_icon = "❤️"
    
    def _log(self, msg: str) -> None:
        if self.log_callback:
            self.log_callback(msg)
    
    def set_disk_cache(self, disk_cache: DiskCacheManager) -> None:
        """设置磁盘缓存管理器"""
        self.disk_cache = disk_cache
    
    def load_conversation_history(self) -> None:
        """加载对话历史"""
        if self.disk_cache:
            self.conversation_history = load_conversation_from_disk(
                self.current_topic,
                self.disk_cache,
                default=[]
            )
            self._log(f"📚 从磁盘加载 {len(self.conversation_history)} 条历史对话")
        else:
            history_file = os.path.join(KNOWLEDGE_BASE_DIR, f"conversation_{self.current_topic}.json")
            try:
                with open(history_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self.conversation_history = data.get('messages', [])[-self.max_history_length:]
                self._log(f"📚 从文件加载 {len(self.conversation_history)} 条历史对话")
            except Exception as e:
                self._log(f"⚠️ 加载对话历史失败：{e}")
                self.conversation_history = []
    
    def save_conversation_history(self) -> None:
        """保存对话历史"""
        with self._conversation_lock:
            if self.disk_cache:
                save_conversation_to_disk(
                    self.conversation_history,
                    self.current_topic,
                    self.disk_cache
                )
            else:
                history_file = os.path.join(KNOWLEDGE_BASE_DIR, f"conversation_{self.current_topic}.json")
                try:
                    os.makedirs(KNOWLEDGE_BASE_DIR, exist_ok=True)
                    with open(history_file, 'w', encoding='utf-8') as f:
                        json.dump({
                            "topic": self.current_topic,
                            "messages": self.conversation_history[-self.max_history_length:],
                            "last_updated": time.time()
                        }, f, indent=2, ensure_ascii=False)
                except Exception as e:
                    self._log(f"❌ 保存对话历史失败：{e}")
    
    def start_new_topic(self, topic_name: str = "new_topic") -> Dict[str, Any]:
        """开启新话题"""
        self.save_conversation_history()
        self.current_topic = topic_name
        self.conversation_history = []
        self._log(f"🆕 开启新话题：{topic_name}")
        return {"success": True, "message": f"已开启新话题：{topic_name}"}
    
    def list_topics(self) -> List[str]:
        """列出所有话题"""
        topics = []
        if os.path.exists(KNOWLEDGE_BASE_DIR):
            for f in os.listdir(KNOWLEDGE_BASE_DIR):
                if f.startswith('conversation_') and f.endswith('.json'):
                    topics.append(f.replace('conversation_', '').replace('.json', ''))
        return topics
    
    def get_conversation_history(self) -> List[Dict[str, str]]:
        """获取对话历史"""
        return self.conversation_history
    
    # 改进后代码
    def send_message(self, user_prompt: str, system_context: str = "", is_learning_mode: bool = False) -> Dict[str, Any]:
        """
        发送消息并获取 AI 回复

        统一处理：
        * 网络异常、非 200 响应、JSON 解析错误均捕获；
        * 超出最大重试次数后返回统一的 “思考超时” 提示；
        * 在成功返回前把对话写入磁盘缓存（或文件）；
        """
        # 组装请求消息
        messages = [{"role": "system", "content": system_context}]
        messages.extend(self.conversation_history[-500:])
        messages.append({"role": "user", "content": user_prompt})

        # 参数根据是否为学习模式动态调整
        num_predict = MAX_OUTPUT_TOKENS if is_learning_mode else 4096
        timeout = REQUEST_TIMEOUT if is_learning_mode else SAFE_TIMEOUT

        max_retries = 3
        last_error = None

        for attempt in range(max_retries):
            try:
                resp = requests.post(
                    "http://localhost:11434/api/chat",
                    json={
                        "model": "gemma4:latest",
                        "messages": messages,
                        "stream": False,
                        "options": {
                            "temperature": TEMPERATURE,
                            "num_predict": num_predict,
                            "num_ctx": MAX_CONTEXT_TOKENS,
                        },
                    },
                    timeout=timeout,
                )
                # 确认 HTTP 状态码
                if resp.status_code != 200:
                    raise RuntimeError(f"HTTP {resp.status_code}: {resp.text}")

                resp_json = resp.json()
                ai_text = resp_json.get('message', {}).get('content', '').strip()
                self.last_raw_response = ai_text

                # 记录对话并截断至 max_history_length
                with self._conversation_lock:
                    self.conversation_history.append({"role": "user", "content": user_prompt})
                    self.conversation_history.append({"role": "assistant", "content": ai_text})
                    if len(self.conversation_history) > self.max_history_length:
                        self.conversation_history = self.conversation_history[-self.max_history_length :]

                # 持久化对话
                self.save_conversation_history()

                return {"action": "chat", "explanation": ai_text, "need_confirm": False}
            except Exception as e:
                last_error = e
                self._log(f"⚠️ 第 {attempt + 1}/{max_retries} 次发送消息失败: {e}")
                time.sleep(2)

        # 所有重试都失败 → 返回统一的超时信息
        return {"action": "chat", "explanation": f"思考超时：{last_error}", "need_confirm": False}

    
    def load_robot_config(self) -> None:
        """加载机器人配置"""
        prompt_file = os.path.join(KNOWLEDGE_BASE_DIR, "system_prompt.json")
        try:
            with open(prompt_file, 'r', encoding='utf-8') as f:
                prompt_data = json.load(f)
            self.robot_name = prompt_data.get('robot_name', '筱溪')
            self.robot_icon = prompt_data.get('robot_icon', '❤️')
        except Exception:
            self.robot_name = "筱溪"
            self.robot_icon = "❤️"
