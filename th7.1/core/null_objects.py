# core/null_objects.py
"""
空对象模式 (Null Object Pattern) 基础设施。
🌟 核心升级：加入 __getattr__ 魔法方法，实现终极防弹，免疫任何 AttributeError。
"""
from typing import Dict, Any, List, Optional, Callable

class DummyBase:
    """所有 Dummy 类的基类"""
    def __init__(self, name: str = "Dummy", log_callback: Optional[Callable] = None):
        self._name = name
        self._log = log_callback if callable(log_callback) else (lambda msg, level="INFO": None)

    def _warn(self, method_name: str):
        self._log(f"⚠️ [{self._name}] 调用了未初始化的组件方法: {method_name}，已返回安全默认值。", level="WARN")

    # 🌟 终极防弹：当调用不存在的方法时，自动返回一个安全的 Dummy 函数
    def __getattr__(self, name):
        def dummy_method(*args, **kwargs):
            self._warn(name)
            # 根据方法名智能返回默认值
            if name.startswith('get_') or name.startswith('extract_') or name.startswith('inspect_') or name.startswith('load_') or name.startswith('search_'):
                return {} if 'list' not in name else []
            if name.startswith('is_') or name.startswith('has_'):
                return False
            return {"success": False, "error": f"{self._name} not initialized"}
        return dummy_method

class DummyTaskPlanner(DummyBase):
    def __init__(self, log_callback=None): super().__init__("TaskPlanner", log_callback)
    def get_plan(self, task_id: str) -> Dict[str, Any]: self._warn("get_plan"); return {}
    def parse_user_request(self, prompt: str) -> Dict: self._warn("parse_user_request"); return {"task_id": "dummy", "status": "error"}

class DummyOutlineManager(DummyBase):
    def __init__(self, log_callback=None): super().__init__("OutlineManager", log_callback)
    def get_outline(self, task_id: str) -> Optional[Dict]: self._warn("get_outline"); return None
    def get_chapter_outline(self, task_id: str, chapter_num: int) -> Dict: self._warn("get_chapter_outline"); return {}

class DummyProgressTracker(DummyBase):
    def __init__(self, log_callback=None): super().__init__("ProgressTracker", log_callback)
    def get_completed_chapters(self, task_id: str) -> List[int]: self._warn("get_completed_chapters"); return []

class DummyProjectGenerator(DummyBase):
    def __init__(self, log_callback=None): super().__init__("ProjectGenerator", log_callback)
    def create_novel_project(self, prompt: str, spec: Dict) -> Dict: 
        self._warn("create_novel_project")
        return {"success": False, "error": "ProjectGenerator not initialized"}

class DummyChapterGenerator(DummyBase):
    def __init__(self, log_callback=None): super().__init__("ChapterGenerator", log_callback)
    def generate_chapter(self, *args, **kwargs) -> Dict: 
        self._warn("generate_chapter")
        return {"success": False, "chapter": 0, "error": "ChapterGenerator not initialized"}

class DummyQualityInspector(DummyBase):
    def __init__(self, log_callback=None): super().__init__("QualityInspector", log_callback)
    def inspect_paragraphs(self, content: str, task_plan: Dict, rules: Dict = None) -> List[Dict]: self._warn("inspect_paragraphs"); return []
    def inspect_chapter(self, content: str, chapter_num: int, quality_rules: Dict, task_plan: Dict) -> Dict: 
        self._warn("inspect_chapter")
        return {"score": 60, "overall_score": 60, "passed": True, "issues": [], "suggestions": []}

class DummyDialogAnalyzer(DummyBase):
    def __init__(self, log_callback=None): super().__init__("DialogAnalyzer", log_callback)
    def extract_spec(self, conversation: List, user_prompt: str) -> Dict: self._warn("extract_spec"); return {}

class DummyProjectPlanBuilder(DummyBase):
    def __init__(self, log_callback=None): super().__init__("ProjectPlanBuilder", log_callback)
    def elaborate_plan(self, spec: Dict) -> Dict: self._warn("elaborate_plan"); return {}

class DummyLoreRevelationManager(DummyBase):
    def __init__(self, log_callback=None): super().__init__("LoreRevelationManager", log_callback)
    def get_chapter_directives(self, task_id: str, chapter_num: int, chapter_outline: Dict) -> str: self._warn("get_chapter_directives"); return ""

class DummyDeepLoreExpander(DummyBase):
    def __init__(self, log_callback=None): super().__init__("DeepLoreExpander", log_callback)

class DummyMemoryGraph(DummyBase):
    def __init__(self, log_callback=None): super().__init__("MemoryGraph", log_callback)
    def get_context_subgraph(self, seed_entities: List[str], max_hops: int = 2, max_nodes: int = 15) -> str: self._warn("get_context_subgraph"); return ""

class DummyGraphExtractor(DummyBase):
    def __init__(self, graph=None, log_callback=None): super().__init__("GraphExtractor", log_callback)

class DummyEventTracker(DummyBase):
    def __init__(self, log_callback=None): super().__init__("EventTracker", log_callback)
    def get_breakpoint_context(self, chapter_outline: Dict) -> str: self._warn("get_breakpoint_context"); return ""

class DummyChapterCardManager(DummyBase):
    def __init__(self, log_callback=None): super().__init__("ChapterCardManager", log_callback)
    def create_chapter_card(self, *args, **kwargs) -> Dict: self._warn("create_chapter_card"); return {"success": False}

class DummyLoreRetriever(DummyBase):
    def __init__(self, log_callback=None): super().__init__("LoreRetriever", log_callback)
    def retrieve_relevant_lore(self, chapter_outline: Dict) -> str: self._warn("retrieve_relevant_lore"); return ""

class DummyLoreGapAnalyzer(DummyBase):
    def __init__(self, log_callback=None): super().__init__("LoreGapAnalyzer", log_callback)
    def analyze_and_fill(self, chapter_outline: Dict, relevant_lore: str) -> str: self._warn("analyze_and_fill"); return ""
