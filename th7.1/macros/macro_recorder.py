import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from pynput import mouse, keyboard as pynput_keyboard
import pyautogui
import json
import time
import threading
import os

# 配置
MACROS_DIR = "macros"
FILE_NAME = os.path.join(MACROS_DIR, "default.json")

# 全局状态
is_recording = False
is_playing = False
recording_data = []
recording_start_time = None

# GUI 引用
root = None
button_record = None
button_play = None
button_stop = None
status_label = None

def ensure_macros_dir():
    if not os.path.exists(MACROS_DIR):
        os.makedirs(MACROS_DIR)

def start_recording():
    global is_recording, recording_data, recording_start_time
    if is_recording or is_playing:
        return
    
    recording_data = []
    recording_start_time = time.perf_counter()
    is_recording = True
    
    status_label.config(text="🔴 正在录制... (按 ESC 停止)", foreground="red")
    button_record.config(state=tk.DISABLED)
    button_play.config(state=tk.DISABLED)
    
    # 启动监听
    mouse.Listener(on_move=on_move, on_click=on_click).start()
    pynput_keyboard.Listener(on_press=on_press, on_release=on_release).start()

def stop_recording():
    global is_recording
    if not is_recording:
        return
    
    is_recording = False
    status_label.config(text=f"💾 已录制 {len(recording_data)} 个动作", foreground="orange")
    button_record.config(state=tk.NORMAL)
    button_play.config(state=tk.NORMAL)
    
    # 保存
    try:
        with open(FILE_NAME, 'w', encoding='utf-8') as f:
            json.dump(recording_data, f, indent=2)
        status_label.config(text=f"✅ 已保存", foreground="green")
    except Exception as e:
        status_label.config(text=f"❌ 保存失败：{e}", foreground="red")

def on_move(x, y):
    if is_recording:
        recording_data.append({"type": "move", "x": x, "y": y, "time": time.perf_counter() - recording_start_time})

def on_click(x, y, button, pressed):
    if is_recording:
        recording_data.append({
            "type": "mouse", "action": "down" if pressed else "up",
            "button": str(button).split('.')[-1], "x": x, "y": y,
            "time": time.perf_counter() - recording_start_time
        })

def on_press(key):
    if is_recording:
        recording_data.append({
            "type": "key", "action": "down",
            "key": key.char if hasattr(key, 'char') else str(key),
            "time": time.perf_counter() - recording_start_time
        })

def on_release(key):
    if is_recording:
        recording_data.append({
            "type": "key", "action": "up",
            "key": key.char if hasattr(key, 'char') else str(key),
            "time": time.perf_counter() - recording_start_time
        })

def play_macro():
    global is_playing
    if is_recording or not os.path.exists(FILE_NAME):
        return
    
    is_playing = True
    status_label.config(text="▶ 正在播放...", foreground="blue")
    button_record.config(state=tk.DISABLED)
    button_play.config(state=tk.DISABLED)
    button_stop.config(state=tk.NORMAL)
    
    threading.Thread(target=_playback, daemon=True).start()

def _playback():
    global is_playing
    try:
        with open(FILE_NAME, 'r', encoding='utf-8') as f:
            actions = json.load(f)
        
        start_time = time.time()
        for action in actions:
            if not is_playing:
                break
            
            elapsed = time.time() - start_time
            wait = action.get('time', 0) - elapsed
            if wait > 0:
                time.sleep(wait)
            
            try:
                if action.get('type') == 'key':
                    key = action.get('key', '').replace('Key.', '')
                    if action.get('action') == 'down':
                        pyautogui.keyDown(key)
                    else:
                        pyautogui.keyUp(key)
                elif action.get('type') == 'mouse':
                    pyautogui.moveTo(action.get('x'), action.get('y'), duration=0.01)
                    if action.get('action') == 'down':
                        pyautogui.mouseDown()
                    else:
                        pyautogui.mouseUp()
                elif action.get('type') == 'move':
                    pyautogui.moveTo(action.get('x'), action.get('y'), duration=0.01)
            except:
                continue
        
        status_label.config(text="✅ 播放完成", foreground="green")
    except Exception as e:
        status_label.config(text=f"❌ 播放失败：{e}", foreground="red")
    finally:
        is_playing = False
        root.after(0, lambda: button_stop.config(state=tk.DISABLED))
        root.after(0, lambda: button_record.config(state=tk.NORMAL))
        root.after(0, lambda: button_play.config(state=tk.NORMAL))

def stop_playback():
    global is_playing
    is_playing = False
    status_label.config(text="⏹ 已停止", foreground="orange")

# GUI
def create_gui():
    global root, button_record, button_play, button_stop, status_label
    
    root = tk.Tk()
    root.title("📼 宏录制器")
    root.geometry("400x300")
    
    ttk.Label(root, text="📼 宏录制器", font=('Helvetica', 16, 'bold')).pack(pady=10)
    
    status_label = ttk.Label(root, text="⚫ 就绪", font=('Helvetica', 12))
    status_label.pack(pady=10)
    
    btn_frame = ttk.Frame(root)
    btn_frame.pack(pady=20)
    
    button_record = ttk.Button(btn_frame, text="🔴 录制", command=start_recording, width=12)
    button_record.pack(side=tk.LEFT, padx=5)
    
    button_play = ttk.Button(btn_frame, text="▶ 播放", command=play_macro, width=12)
    button_play.pack(side=tk.LEFT, padx=5)
    
    button_stop = ttk.Button(btn_frame, text="⏹ 停止", command=stop_playback, width=12, state=tk.DISABLED)
    button_stop.pack(side=tk.LEFT, padx=5)
    
    ttk.Button(root, text="📂 打开宏文件夹", command=lambda: os.startfile(os.path.abspath(MACROS_DIR))).pack(pady=5)
    ttk.Button(root, text="🚪 退出", command=root.destroy).pack(pady=5)
    
    ensure_macros_dir()
    root.mainloop()

if __name__ == "__main__":
    create_gui()
