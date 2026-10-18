# core/dependency_manager.py
# ==================== 依赖管理模块 ====================
import subprocess
import sys
import os

REQUIREMENTS_FILE = "requirements.txt"

def check_and_install_dependencies():
    """检查并尝试自动安装所有必需的 Python 依赖"""
    print("\n" + "="*50)
    print("⚙️ [环境检测] 开始检查项目依赖...")
    
    if not os.path.exists(REQUIREMENTS_FILE):
        print("="*50)
        print(f"⚠️ 警告：未找到 {REQUIREMENTS_FILE} 文件")
        print("="*50)
        return False

    print(f"🔎 找到依赖清单：{REQUIREMENTS_FILE}，尝试自动安装...")

    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", REQUIREMENTS_FILE],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        print("\n✨ 所有必需的 Python 依赖已成功安装！")
        return True

    except subprocess.CalledProcessError as e:
        print("\n" + "="*60)
        print("🚨 依赖包安装失败！")
        print("详细错误信息：")
        print(e.stderr.decode())
        print("="*60)
        print("\n>>> 【🛠️ 备用方案】")
        print(f"请手动运行：pip install -r {REQUIREMENTS_FILE}")
        return False
    
    except FileNotFoundError:
        print("\n" + "="*60)
        print("🚨 找不到 pip 或 Python 解释器！")
        print(">>> 【🛠️ 备用方案】")
        print("请执行：python -m pip install --upgrade pip")
        print("="*60)
        return False
    
    except Exception as e:
        print("\n" + "="*60)
        print(f"🚨 依赖检查发生未知错误：{e}")
        print(">>> 【🛠️ 备用方案】")
        print("请手动运行：pip install -r requirements.txt")
        print("="*60)
        return False
