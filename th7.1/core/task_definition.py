import dataclasses
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Type
from datetime import datetime

@dataclass
class Task:
    """统一的任务描述对象。所有「生成」类需求都会被转化为一个 Task。"""
    action: str
    user_prompt: str
    filename: Optional[str] = None
    desired_length: Optional[int] = None
    total_chapters: int = 1
    chapter_index: int = 1
    options: Dict[str, Any] = field(default_factory=dict)
    task_id: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    status: str = "planned"
    project_id: Optional[str] = None

@dataclass
class NovelWritingTask(Task):
    """小说创作任务 - 继承自 Task，增加小说特有字段。"""
    genre: str = "奇幻"
    writing_style: str = "自然流畅"
    target_audience: str = "通用"
    require_outline: bool = False
    chapter_titles: List[str] = field(default_factory=list)
    worldview_summary: str = ""
    characters: List[Dict[str, Any]] = field(default_factory=list)
    settings: Dict[str, Any] = field(default_factory=dict)
    notes: str = ""
    growth_trajectory: Dict[str, Any] = field(default_factory=dict)
    truth_level_plan: Dict[str, Any] = field(default_factory=dict)

@dataclass
class ContentModificationTask(Task):
    """内容修改任务 - 继承自 Task，增加修改特有字段。"""
    modification_type: str = "general"
    original_content: str = ""
    modification_scope: str = "全部"
    preserve_elements: List[str] = field(default_factory=list)

@dataclass
class ResearchTask(Task):
    """研究任务 - 继承自 Task，增加研究特有字段。"""
    research_topic: str = ""
    depth: str = "基础"
    output_format: str = "总结"
    sources: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)

@dataclass
class AnalysisTask(Task):
    """分析任务 - 继承自 Task，增加分析特有字段。"""
    analysis_type: str = "综合分析"
    target_content: str = ""
    analysis_dimensions: List[str] = field(default_factory=list)
    criteria: Dict[str, Any] = field(default_factory=dict)

@dataclass
class LearningTask(Task):
    """学习任务 - 继承自 Task，增加学习特有字段。"""
    learning_objective: str = ""
    desired_level: str = "熟练掌握"
    learning_method: str = "综合学习"
    resources: List[str] = field(default_factory=list)
    deadline: Optional[str] = None

@dataclass
class CreativeTask(Task):
    """创意任务 - 继承自 Task，增加创意特有字段。"""
    creativity_type: str = "原创"
    innovation_level: str = "中等"
    constraints: List[str] = field(default_factory=list)
    inspiration_sources: List[str] = field(default_factory=list)

# 任务类型映射（用于动态创建任务）
TASK_TYPES: Dict[str, Type[Task]] = {
    "novel": NovelWritingTask,
    "modification": ContentModificationTask,
    "research": ResearchTask,
    "analysis": AnalysisTask,
    "learning": LearningTask,
    "creative": CreativeTask,
    "general": Task
}

# ==================== 🌟 防弹实例化核心 ====================

def _filter_and_create(cls: Type[Task], **kwargs) -> Task:
    """
    🌟 根源级修复：防弹任务创建器。
    自动过滤 LLM 幻觉产生的非法字段，并将多余字段安全地塞入 options 字典，
    彻底杜绝 TypeError: __init__() got an unexpected keyword argument。
    """
    valid_fields = {f.name for f in dataclasses.fields(cls)}
    filtered = {}
    extra = {}
    
    for k, v in kwargs.items():
        if k in valid_fields:
            filtered[k] = v
        else:
            extra[k] = v
            
    # 将非法字段合并到 options 字典中（如果该类支持 options）
    if 'options' in valid_fields:
        opts = filtered.get('options') or {}
        if not isinstance(opts, dict): opts = {}
        opts.update(extra)
        filtered['options'] = opts
        
    return cls(**filtered)

def create_task(task_type: str, **kwargs) -> Task:
    """工厂函数：根据任务类型创建对应的 Task 对象。"""
    task_class = TASK_TYPES.get(task_type, Task)
    return _filter_and_create(task_class, **kwargs)

# ==================== 便捷创建函数 ====================

def create_novel_task(
    action: str, user_prompt: str, genre: str = "奇幻", filename: Optional[str] = None,
    desired_length: Optional[int] = None, total_chapters: int = 1, chapter_index: int = 1,
    writing_style: str = "自然流畅", target_audience: str = "通用", project_id: Optional[str] = None, **options
) -> NovelWritingTask:
    """创建小说创作任务。"""
    kwargs = {
        'action': action, 'user_prompt': user_prompt, 'genre': genre, 'filename': filename,
        'desired_length': desired_length, 'total_chapters': total_chapters, 'chapter_index': chapter_index,
        'writing_style': writing_style, 'target_audience': target_audience, 'project_id': project_id,
        'options': options
    }
    return _filter_and_create(NovelWritingTask, **kwargs)

def create_modification_task(
    action: str, user_prompt: str, original_content: str, modification_type: str = "general",
    filename: Optional[str] = None, project_id: Optional[str] = None, **options
) -> ContentModificationTask:
    """创建内容修改任务。"""
    kwargs = {
        'action': action, 'user_prompt': user_prompt, 'original_content': original_content,
        'modification_type': modification_type, 'filename': filename, 'project_id': project_id, 'options': options
    }
    return _filter_and_create(ContentModificationTask, **kwargs)

def create_research_task(
    action: str, user_prompt: str, research_topic: str, depth: str = "基础",
    filename: Optional[str] = None, project_id: Optional[str] = None, **options
) -> ResearchTask:
    """创建研究任务。"""
    kwargs = {
        'action': action, 'user_prompt': user_prompt, 'research_topic': research_topic,
        'depth': depth, 'filename': filename, 'project_id': project_id, 'options': options
    }
    return _filter_and_create(ResearchTask, **kwargs)

def create_analysis_task(
    action: str, user_prompt: str, target_content: str, analysis_type: str = "综合分析",
    filename: Optional[str] = None, project_id: Optional[str] = None, **options
) -> AnalysisTask:
    """创建分析任务。"""
    kwargs = {
        'action': action, 'user_prompt': user_prompt, 'target_content': target_content,
        'analysis_type': analysis_type, 'filename': filename, 'project_id': project_id, 'options': options
    }
    return _filter_and_create(AnalysisTask, **kwargs)

def create_learning_task(
    action: str, user_prompt: str, learning_objective: str, desired_level: str = "熟练掌握",
    filename: Optional[str] = None, project_id: Optional[str] = None, **options
) -> LearningTask:
    """创建学习任务。"""
    kwargs = {
        'action': action, 'user_prompt': user_prompt, 'learning_objective': learning_objective,
        'desired_level': desired_level, 'filename': filename, 'project_id': project_id, 'options': options
    }
    return _filter_and_create(LearningTask, **kwargs)

def create_creative_task(
    action: str, user_prompt: str, creativity_type: str = "原创",
    filename: Optional[str] = None, project_id: Optional[str] = None, **options
) -> CreativeTask:
    """创建创意任务。"""
    kwargs = {
        'action': action, 'user_prompt': user_prompt, 'creativity_type': creativity_type,
        'filename': filename, 'project_id': project_id, 'options': options
    }
    return _filter_and_create(CreativeTask, **kwargs)

# ==================== 状态与优先级常量 ====================

class TaskStatus:
    PLANNED = "planned"
    OUTLINE_CONFIRMED = "outline_confirmed"
    IN_PROGRESS = "in_progress"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"

class TaskPriority:
    LOW = 1
    NORMAL = 5
    HIGH = 8
    URGENT = 10
