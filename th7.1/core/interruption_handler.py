import os
import json
import threading
import time
from datetime import datetime
from typing import Dict, Any, List, Optional, Callable

from .config import (
    TASK_TRACKING_DIR, INTERRUPTION_ENABLED, INTERRUPTION_CHECK_INTERVAL,
    ATOMIC_OPERATION_ENABLED, MODIFICATION_CONFIRMATION_REQUIRED,
    PROFESSIONAL_LOG_ENABLED, PROJECT_LOG_ISOLATION
)

class InterruptionHandler:
    """中断处理器：智能识别用户中断需求，支持暂停、修改、恢复。"""
    
    def __init__(self, log_callback=None, project_id: str = None):
        self.log_callback = log_callback
        self.project_id = project_id
        
        self.enabled = INTERRUPTION_ENABLED
        self.check_interval = INTERRUPTION_CHECK_INTERVAL
        self.atomic_operation_enabled = ATOMIC_OPERATION_ENABLED
        self.modification_confirmation_required = MODIFICATION_CONFIRMATION_REQUIRED
        self.professional_log_enabled = PROFESSIONAL_LOG_ENABLED
        self.project_log_isolation = PROJECT_LOG_ISOLATION
        
        self.is_interrupted = False
        self.interruption_type = None
        self.pending_modification = None
        self.user_confirmed = False
        
        if self.project_log_isolation and project_id:
            self.log_dir = os.path.join(TASK_TRACKING_DIR, "projects", project_id, "logs")
        else:
            self.log_dir = os.path.join(TASK_TRACKING_DIR, "interruption_logs")
        os.makedirs(self.log_dir, exist_ok=True)
        
        self.state_file = os.path.join(self.log_dir, "interruption_state.json")
        self.listener_thread = None
        self.stop_listener = False
        
        self._log("✅ 中断处理器已初始化")

    def _atomic_write_json(self, filepath: str, data: Any) -> None:
        """🌟 工业级原子写入 JSON。"""
        tmp_path = filepath + ".tmp"
        try:
            dir_name = os.path.dirname(filepath)
            if dir_name: os.makedirs(dir_name, exist_ok=True)
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, filepath)
        except Exception as e:
            if os.path.exists(tmp_path):
                try: os.remove(tmp_path)
                except Exception: pass
            self._log(f"❌ 原子写入失败 ({filepath}): {e}", level="ERROR")
    
    def _log(self, msg: str, level: str = "INFO") -> None:
        timestamp = datetime.now().isoformat()
        if self.professional_log_enabled:
            log_entry = {"timestamp": timestamp, "level": level, "module": "InterruptionHandler", "project_id": self.project_id, "message": msg}
            log_line = json.dumps(log_entry, ensure_ascii=False)
        else:
            log_line = f"[{timestamp}] [{level}] {msg}"
        
        if self.log_callback: self.log_callback(log_line)
        
        log_file = os.path.join(self.log_dir, f"interruption_{datetime.now().strftime('%Y%m%d')}.log")
        # 🌟 核心修复：增加 flush 和 fsync 防止日志丢失
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(log_line + '\n')
            f.flush()
            os.fsync(f.fileno())
    
    def start_listening(self) -> None:
        if not self.enabled: return
        self.stop_listener = False
        self.listener_thread = threading.Thread(target=self._listen_loop, daemon=True)
        self.listener_thread.start()
        self._log("👂 中断监听已启动")
    
    def stop_listening(self) -> None:
        self.stop_listener = True
        if self.listener_thread: self.listener_thread.join(timeout=5)
        self._log("🛑 中断监听已停止")
    
    def _listen_loop(self) -> None:
        while not self.stop_listener:
            signal_file = os.path.join(self.log_dir, "interrupt_signal.json")
            if os.path.exists(signal_file):
                try:
                    with open(signal_file, 'r', encoding='utf-8') as f: signal_data = json.load(f)
                    self._handle_signal(signal_data)
                    os.remove(signal_file)
                except Exception as e:
                    self._log(f"⚠️ 处理中断信号失败：{e}", level="WARN")
            time.sleep(self.check_interval)
    
    def _handle_signal(self, signal_data: Dict[str, Any]) -> None:
        signal_type = signal_data.get("type")
        if signal_type == "pause":
            self._log("⏸️ 收到暂停信号")
            self.is_interrupted = True
            self.interruption_type = "pause"
        elif signal_type == "stop":
            self._log("⏹️ 收到停止信号")
            self.is_interrupted = True
            self.interruption_type = "stop"
        elif signal_type == "modify":
            self._log("✏️ 收到修改信号")
            self.is_interrupted = True
            self.interruption_type = "modify"
            self.pending_modification = signal_data.get("modification")
            if self.modification_confirmation_required: self._request_confirmation(signal_data)
        self._save_state()
    
    def is_active(self) -> bool:
        return self.is_interrupted

    # 🌟 核心修复：物理删除了误复制的 init_directories 方法，防止 ImportError 导致模块加载失败

    def _request_confirmation(self, signal_data: Dict[str, Any]) -> None:
        self._log("❓ 等待用户确认修改...")
        confirmation_file = os.path.join(self.log_dir, "confirmation_pending.json")
        confirmation_data = {"modification": signal_data.get("modification"), "impact_analysis": signal_data.get("impact_analysis"), "requested_at": datetime.now().isoformat(), "status": "pending"}
        self._atomic_write_json(confirmation_file, confirmation_data)
    
    def confirm_modification(self, confirmed: bool, user_feedback: str = None) -> None:
        self.user_confirmed = confirmed
        confirmation_file = os.path.join(self.log_dir, "confirmation_pending.json")
        if os.path.exists(confirmation_file):
            try:
                with open(confirmation_file, 'r', encoding='utf-8') as f: data = json.load(f)
                data["status"] = "confirmed" if confirmed else "cancelled"
                data["confirmed_at"] = datetime.now().isoformat()
                data["user_feedback"] = user_feedback
                self._atomic_write_json(confirmation_file, data)
                if confirmed: self._log("✅ 用户确认修改，继续执行")
                else:
                    self._log("❌ 用户取消修改")
                    self._ask_modify_again()
            except Exception as e:
                self._log(f"⚠️ 保存确认状态失败：{e}", level="WARN")
    
    def _ask_modify_again(self) -> None:
        self._log("❓ 询问：是否还有其他需要修改的地方？")
        reask_file = os.path.join(self.log_dir, "modify_again_question.json")
        question_data = {"question": "是否还有其他需要修改的地方？", "options": ["继续修改", "恢复生成"], "asked_at": datetime.now().isoformat()}
        self._atomic_write_json(reask_file, question_data)
    
    def check_atomic_point(self) -> bool:
        if not self.atomic_operation_enabled: return True
        if not self.is_interrupted: return True
        self._log("📍 到达原子操作点，可以安全暂停")
        return True
    
    def wait_for_safe_pause(self, atomic_callback: Callable) -> None:
        if not self.is_interrupted: return
        self._log("⏳ 等待原子操作完成...")
        while self.is_interrupted:
            time.sleep(0.5)
            if atomic_callback(): break
        self._log("✅ 安全暂停完成")
    
    def resume_generation(self) -> None:
        self.is_interrupted = False
        self.interruption_type = None
        self.user_confirmed = False
        self._log("▶️ 生成已恢复")
        self._save_state()
    
    def get_interruption_state(self) -> Dict[str, Any]:
        return {"is_interrupted": self.is_interrupted, "interruption_type": self.interruption_type, "pending_modification": self.pending_modification, "user_confirmed": self.user_confirmed, "last_updated": datetime.now().isoformat()}
    
    def _save_state(self) -> None:
        self._atomic_write_json(self.state_file, self.get_interruption_state())
    
    def load_state(self) -> None:
        if not os.path.exists(self.state_file): return
        try:
            with open(self.state_file, 'r', encoding='utf-8') as f: state = json.load(f)
            self.is_interrupted = state.get("is_interrupted", False)
            self.interruption_type = state.get("interruption_type")
            self.pending_modification = state.get("pending_modification")
            self.user_confirmed = state.get("user_confirmed", False)
            self._log("🔄 已恢复中断状态")
        except Exception as e:
            self._log(f"⚠️ 加载中断状态失败：{e}", level="WARN")
    
    def send_interrupt_signal(self, signal_type: str, modification: Dict[str, Any] = None) -> None:
        if not self.enabled: return
        signal_file = os.path.join(self.log_dir, "interrupt_signal.json")
        signal_data = {"type": signal_type, "modification": modification, "sent_at": datetime.now().isoformat()}
        self._atomic_write_json(signal_file, signal_data)
        self._log(f"📡 已发送中断信号：{signal_type}")
    
    def get_pending_modifications(self) -> List[Dict[str, Any]]:
        if not self.pending_modification: return []
        return [self.pending_modification]
    
    def clear_interruption_state(self) -> None:
        self.is_interrupted = False
        self.interruption_type = None
        self.pending_modification = None
        self.user_confirmed = False
        if os.path.exists(self.state_file):
            try: os.remove(self.state_file)
            except Exception: pass
        self._log("🧹 已清除中断状态（强制恢复）")
    
    def export_interruption_log(self, output_path: str) -> Dict[str, Any]:
        log_entries = []
        for filename in os.listdir(self.log_dir):
            if filename.startswith('interruption_') and filename.endswith('.log'):
                filepath = os.path.join(self.log_dir, filename)
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        for line in f:
                            if line.strip(): log_entries.append(line.strip())
                except Exception: continue  # 🌟 核心修复：改为 except Exception
        
        try:
            with open(output_path, 'w', encoding='utf-8') as f: f.write('\n'.join(log_entries))
            return {"success": True, "count": len(log_entries), "filepath": output_path}
        except Exception as e:
            return {"success": False, "error": str(e)}
