# core/__init__.py
"""
XIAOXI 智能助手核心模块初始化。
包含关键组件的防崩溃兜底机制（Dummy Fallback），确保在部分子模块损坏或缺失时，
主系统（如 AIController）仍能正常启动并降级运行。
"""
import sys

def _fallback_reasoning_engine():
    """生成 ReasoningEngine 的占位类（功能受限，但保证绝对不崩溃）。"""
    class DummyReasoningEngine:
        def __init__(self, *args, **kwargs):
            sys.stderr.write("[WARN] 使用了占位 ReasoningEngine（reasoning_engine.py 导入失败，功能受限）\n")

        def generate_outline(self, *args, **kwargs):
            return {"success": False, "error": "reasoning_engine 未实现或导入失败"}

        def validate_project_blueprint(self, *args, **kwargs):
            return {"status": "failed", "message": "占位引擎跳过验证"}

        def analyze_request(self, *args, **kwargs):
            return {"recommended_approach": "chat", "explanation": "占位引擎", "confidence": 0.5}

        def generate_health_check_fix_plan(self, *args, **kwargs):
            return {"steps": [], "estimated_time_seconds": 0, "auto_execute_possible": False}

        def reconstruct_outline_from_texts(self, *args, **kwargs):
            return {"success": False, "error": "占位引擎无法逆向大纲"}

        # 🌟 核心新增：终极防弹机制（兼容未来可能新增的任意方法调用）
        def __getattr__(self, name):
            """当调用占位类中不存在的方法时，自动返回一个安全的 Dummy 函数，防止 AttributeError。"""
            def dummy_method(*args, **kwargs):
                sys.stderr.write(f"[WARN] 调用了占位 ReasoningEngine 的未实现方法: {name}\n")
                return {"success": False, "error": f"占位引擎未实现方法: {name}"}
            return dummy_method

    return DummyReasoningEngine

try:
    # 尝试正常导入真实的推理引擎
    from .reasoning_engine import ReasoningEngine
except Exception as e:
    # 任何错误（ImportError, SyntaxError, NameError 等）都回退到占位类
    sys.stderr.write(f"[ERROR] 导入 reasoning_engine 失败: {e}。启用 Dummy 兜底。\n")
    ReasoningEngine = _fallback_reasoning_engine()

# 暴露核心类（方便外部直接 from core import ReasoningEngine）
__all__ = ["ReasoningEngine"]
