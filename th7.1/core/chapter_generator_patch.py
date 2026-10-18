# core/chapter_generator_patch.py
# ------------------------------------------------------------
# 为 ChapterGenerator 注入 **可靠的原子写入** 实现 (防弹完美契约版)
# ------------------------------------------------------------
import os
import json
import time
import shutil
import glob
import importlib
from datetime import datetime
from typing import Dict, Any

# 安全导入配置和路径管理器
try:
    from .config import PROFESSIONAL_LOG_ENABLED, TASK_TRACKING_DIR
except ImportError:
    PROFESSIONAL_LOG_ENABLED = False
    TASK_TRACKING_DIR = "task_tracking"

try:
    from .project_path_manager import ProjectPathManager
except ImportError:
    ProjectPathManager = None

# 🌟 追根溯源修复：增加 try-except，防止 chapter_generator 导入失败时导致 patch 级联崩溃
try:
    _chapter_mod = importlib.import_module('.chapter_generator', __package__)
    _ChapterGenerator = getattr(_chapter_mod, 'ChapterGenerator')
except Exception as e:
    print(f"[Patch] ⚠️ chapter_generator 导入失败，跳过 chapter_generator_patch: {e}")
    _ChapterGenerator = None
def _patched_save_chapter_content_atomic(
    self, 
    task_id: str, 
    chapter_num: int, 
    content: str, 
    quality_result: Dict[str, Any] = None,  
    task_plan: dict = None,                 
    **kwargs                                
) -> Dict[str, Any]:                        
    """原子保存章节内容（火力覆盖式多路径搬运）- 完美契约版。"""
    import os, json, time, shutil, glob, re
    from datetime import datetime
    
    quality_result = quality_result or {}
    task_plan = task_plan or getattr(self, "task_plan", {}) or {}
    
    timestamp = int(time.time())
    filename = f"{task_id}_第{chapter_num}章_{timestamp}.txt"
    
    generated_chapters_dir = getattr(self, 'generated_chapters_dir', 'generated_chapters')
    try:
        from .config import PROFESSIONAL_LOG_ENABLED, TASK_TRACKING_DIR
        if PROFESSIONAL_LOG_ENABLED and getattr(self, 'project_id', None):
            generated_chapters_dir = os.path.join(TASK_TRACKING_DIR, "projects", str(self.project_id), "chapters")
    except Exception:
        pass
    
    os.makedirs(generated_chapters_dir, exist_ok=True)
    filepath = os.path.join(generated_chapters_dir, filename)

    atomic_enabled = getattr(self, 'atomic_operation_enabled', True)
    if not atomic_enabled:
        try:
            with open(filepath, "w", encoding="utf-8") as f: f.write(content)
            return {"filepath": filepath, "success": True, "method": "normal"}
        except Exception as e:
            return {"filepath": "", "success": False, "error": str(e), "method": "normal"}

    tmp_filepath = filepath + ".tmp"
    try:
        with open(tmp_filepath, "w", encoding="utf-8") as f: 
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
            
        if not os.path.exists(tmp_filepath) or os.path.getsize(tmp_filepath) == 0:
            raise Exception("临时文件创建失败或为空")
        os.replace(tmp_filepath, filepath)

        meta_filepath = filepath + ".meta.json"
        meta_data = {
            "task_id": task_id, "chapter": chapter_num, 
            "quality_result": quality_result, 
            "content_length": len(content), 
            "created_at": datetime.now().isoformat()
        }
        tmp_meta = meta_filepath + ".tmp"
        with open(tmp_meta, "w", encoding="utf-8") as f: 
            json.dump(meta_data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_meta, meta_filepath)

        result = {"filepath": filepath, "meta_filepath": meta_filepath, "success": True, "method": "atomic"}

        try:
            pure_project_id = task_id
            try:
                from .project_path_manager import ProjectPathManager
                if ProjectPathManager and hasattr(ProjectPathManager, 'extract_pure_id'):
                    extracted = ProjectPathManager.extract_pure_id(task_id)
                    if extracted and "task_" not in extracted and re.match(r'^[0-9a-fA-F]{8,32}$', extracted):
                        pure_project_id = extracted
                    else:
                        pure_project_id = None
            except Exception:
                pure_project_id = None

            target_dirs = set()
            explicit_dir = task_plan.get("project_dir") if isinstance(task_plan, dict) else ""
            if explicit_dir and os.path.isdir(explicit_dir): 
                target_dirs.add(os.path.abspath(explicit_dir))
            else:
                inst_plan = getattr(self, "task_plan", {})
                if isinstance(inst_plan, dict):
                    fallback_dir = inst_plan.get("project_dir", "")
                    if fallback_dir and os.path.isdir(fallback_dir):
                        target_dirs.add(os.path.abspath(fallback_dir))

            if pure_project_id:
                project_dir_path = "invalid_project"
                try:
                    from .project_path_manager import ProjectPathManager
                    if ProjectPathManager and hasattr(ProjectPathManager, 'resolve_project_dir'):
                        project_dir_path = ProjectPathManager.resolve_project_dir(pure_project_id)
                except Exception:
                    pass
                if project_dir_path != "invalid_project": 
                    target_dirs.add(os.path.abspath(project_dir_path))
                
                base_dir = "novel_projects"
                target_dirs.add(os.path.abspath(os.path.join(base_dir, str(pure_project_id))))
                for m in glob.glob(os.path.join(base_dir, f"*_{pure_project_id}")):
                    if os.path.isdir(m): target_dirs.add(os.path.abspath(m))

            if not target_dirs:
                if hasattr(self, '_log'):
                    self._log("⚠️ 未找到合法的 project_dir，拒绝在根目录创建垃圾文件夹，跳过火力覆盖搬运。", level="WARN")
            else:
                for _proj_dir in target_dirs:
                    try:
                        target_chapter_dir = os.path.join(_proj_dir, "02_章节", f"第{chapter_num:03d}章")
                        os.makedirs(target_chapter_dir, exist_ok=True)
                        target_file = os.path.join(target_chapter_dir, f"第{chapter_num:03d}章.txt")
                        
                        # 🌟 致命修复：防止源文件和目标文件是同一个物理文件导致报错
                        if os.path.abspath(filepath) != os.path.abspath(target_file):
                            shutil.copy2(filepath, target_file)
                        else:
                            if hasattr(self, '_log'):
                                self._log(f"ℹ️ 源文件与目标文件相同，跳过复制: {target_file}")

                        if hasattr(self, '_log'):
                            self._log(f"📂 已同步章节文件至: {target_file}")
                        result["filepath"] = target_file 
                    except Exception as copy_err:
                        if hasattr(self, '_log'):
                            self._log(f"⚠️ 复制章节到 {_proj_dir} 失败: {copy_err}", level="WARN")
        except Exception as move_err:
            if hasattr(self, '_log'):
                self._log(f"⚠️ 同步章节文件至项目目录失败: {move_err}", level="WARN")

        return result
    except Exception as e:
        if hasattr(self, '_log'):
            self._log(f"❌ 原子保存失败：{e}", level="ERROR")
        if os.path.exists(tmp_filepath):
            try: os.remove(tmp_filepath)
            except Exception: pass
        return {"filepath": "", "success": False, "error": str(e), "method": "atomic"}

# 挂载逻辑
try:
    import importlib
    _chapter_mod = importlib.import_module('.chapter_generator', __package__)
    _ChapterGenerator = getattr(_chapter_mod, 'ChapterGenerator')
    _ChapterGenerator._save_chapter_content_atomic = _patched_save_chapter_content_atomic
    print("[Patch] ✅ ChapterGenerator._save_chapter_content_atomic 已成功挂载 (完美契约版)")
except Exception as e:
    print(f"[Patch] ⚠️ 挂载失败: {e}")
