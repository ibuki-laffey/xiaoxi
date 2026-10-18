# ==================== 宏录制模块 ====================
import os
import json
import time
import threading
from pynput import mouse, keyboard as pynput_keyboard
import pyautogui
from .config import MACROS_DIR
from .logger import action_logger

class MacroRecorder:
    def __init__(self, log_callback=None):
        self.log_callback = log_callback
        self.is_recording = False
        self.is_playing = False
        self.recording_data = []
        self.recording_start_time = None
    
    def _log(self, msg):
        if self.log_callback:
            self.log_callback(msg)
    
    def start_recording(self):
        if self.is_recording or self.is_playing:
            return False
        self.recording_data = []
        self.recording_start_time = time.perf_counter()
        self.is_recording = True
        self._log("🔴 开始录制...")
        mouse.Listener(on_move=self._on_move, on_click=self._on_click).start()
        pynput_keyboard.Listener(on_press=self._on_press, on_release=self._on_release).start()
        return True
    
    def stop_recording(self):
        if not self.is_recording:
            return False
        self.is_recording = False
        self._log(f"💾 录制完成，共 {len(self.recording_data)} 个动作")
        return True
    
    def save_macro(self, filename):
        path = os.path.join(MACROS_DIR, filename if filename.endswith('.json') else filename + '.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(self.recording_data, f, indent=2, ensure_ascii=False)
        action_logger.log("MACRO_SAVE", path, f"{len(self.recording_data)} 个动作")
        return path
    
    def load_macro(self, filename):
        path = os.path.join(MACROS_DIR, filename if filename.endswith('.json') else filename + '.json')
        if not os.path.exists(path):
            return None
        with open(path, 'r', encoding='utf-8') as f:
            self.recording_data = json.load(f)
        return self.recording_data
    
    def list_macros(self):
        if not os.path.exists(MACROS_DIR):
            return []
        return [f.replace('.json', '') for f in os.listdir(MACROS_DIR) if f.endswith('.json')]
    
    def play_macro(self, loops=1, interval=0.5, callback=None):
        if self.is_recording or self.is_playing or not self.recording_data:
            return False
        self.is_playing = True
        threading.Thread(target=self._playback_loop, args=(loops, interval, callback), daemon=True).start()
        return True
    
    def stop_playback(self):
        self.is_playing = False
    
    def _playback_loop(self, loops, interval, callback):
        for loop in range(loops):
            if not self.is_playing:
                break
            self._playback_single()
            if loop < loops - 1 and self.is_playing and interval > 0:
                time.sleep(interval)
        self.is_playing = False
        if callback:
            callback()
    
    def _playback_single(self):
        start_time = time.time()
        for action in self.recording_data:
            if not self.is_playing:
                break
            elapsed = time.time() - start_time
            wait = action.get('time', 0) - elapsed
            if wait > 0:
                time.sleep(wait)
            try:
                action_type = action.get('type')
                if action_type == 'key':
                    key = action.get('key', '').replace('Key.', '')
                    if action.get('action') == 'down':
                        pyautogui.keyDown(key)
                    else:
                        pyautogui.keyUp(key)
                elif action_type in ['mouse', 'move']:
                    pyautogui.moveTo(action.get('x'), action.get('y'), duration=0.01)
            except:
                continue
    
    def _on_move(self, x, y):
        if self.is_recording:
            self.recording_data.append({"type": "move", "x": x, "y": y, "time": time.perf_counter() - self.recording_start_time})
    
    def _on_click(self, x, y, button, pressed):
        if self.is_recording:
            self.recording_data.append({"type": "mouse", "action": "down" if pressed else "up", "button": str(button).split('.')[-1], "x": x, "y": y, "time": time.perf_counter() - self.recording_start_time})
    
    def _on_press(self, key):
        if self.is_recording:
            self.recording_data.append({"type": "key", "action": "down", "key": key.char if hasattr(key, 'char') else str(key), "time": time.perf_counter() - self.recording_start_time})
    
    def _on_release(self, key):
        if self.is_recording:
            self.recording_data.append({"type": "key", "action": "up", "key": key.char if hasattr(key, 'char') else str(key), "time": time.perf_counter() - self.recording_start_time})
