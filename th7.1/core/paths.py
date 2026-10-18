import os
from pathlib import Path
from typing import Union

BASE_DIR: Path = Path(__file__).resolve().parents[1]
NOVEL_PROJECTS_DIR: Path = Path(os.getenv("XIAOXI_NOVEL_PROJECTS_DIR", BASE_DIR / "novel_projects"))
KNOWLEDGE_BASE_DIR: Path = BASE_DIR / "knowledge_base"
DEFAULT_SAVE_DIR: Path = Path(os.getenv("XIAOXI_DEFAULT_SAVE_DIR", Path.home() / "Xiaoxi_Archive"))

DATA_DIR: Path = BASE_DIR / "data"
DATA_ACTIVE_DIR: Path = DATA_DIR / "active"
DATA_ARCHIVE_DIR: Path = DATA_DIR / "archive"
DATA_TEMP_DIR: Path = DATA_DIR / "temp"
DATA_CONFIG_DIR: Path = DATA_DIR / "config"
DB_DIR: Path = DATA_DIR / "db"

NOVEL_WORKS_DIR: Path = DEFAULT_SAVE_DIR / "Novels"
OUTLINES_DIR: Path = DEFAULT_SAVE_DIR / "Outlines"
RESEARCH_DIR: Path = DEFAULT_SAVE_DIR / "Research"
EXPERIENCE_DB_DIR: Path = KNOWLEDGE_BASE_DIR / "experience_db"
TOPIC_STORAGE_DIR: Path = DATA_CONFIG_DIR / "topics"
CURRENT_TOPIC_META: Path = DATA_CONFIG_DIR / "current_topic.txt"

LOGS_DIR: Path = BASE_DIR / "logs"
PLUGINS_DIR: Path = BASE_DIR / "plugins"
TASK_TRACKING_DIR: Path = BASE_DIR / "task_tracking"
DISK_CACHE_DIR: Path = TASK_TRACKING_DIR / "disk_cache"
VECTOR_DB_DIR: Path = KNOWLEDGE_BASE_DIR / "vector_db"
REASONING_LOGS_DIR: Path = BASE_DIR / "reasoning_logs"
WORLD_STATES_DIR: Path = BASE_DIR / "world_states"
CHAPTER_CARDS_DIR: Path = TASK_TRACKING_DIR / "chapter_cards"

def ensure_path(p: Union[str, Path]) -> Path:
    path = Path(p)
    parent = path.parent if path.suffix else path
    if not parent.exists(): parent.mkdir(parents=True, exist_ok=True)
    return path

__all__ = [
    "BASE_DIR", "NOVEL_PROJECTS_DIR", "KNOWLEDGE_BASE_DIR", "DEFAULT_SAVE_DIR",
    "DATA_DIR", "DATA_ACTIVE_DIR", "DATA_ARCHIVE_DIR", "DATA_TEMP_DIR", "DATA_CONFIG_DIR", "DB_DIR",
    "NOVEL_WORKS_DIR", "OUTLINES_DIR", "RESEARCH_DIR", "EXPERIENCE_DB_DIR",
    "TOPIC_STORAGE_DIR", "CURRENT_TOPIC_META", "ensure_path",
    "LOGS_DIR", "PLUGINS_DIR", "TASK_TRACKING_DIR", "DISK_CACHE_DIR",
    "VECTOR_DB_DIR", "REASONING_LOGS_DIR", "WORLD_STATES_DIR", "CHAPTER_CARDS_DIR",
]
