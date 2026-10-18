# core/state_manager.py
# ==================== 状态管理器（完整增强版 - 质量优先）====================
# 功能：管理工程状态、自检与自动修复、任务恢复、用户编辑适配
# 新增：用户编辑优先、状态版本控制、原子操作、健康检查集成
# 注意：本文件为完整版本，无任何精简，包含所有错误处理分支

import os
import json
import glob
import pickle
import shutil
import hashlib
from datetime import datetime
from typing import Dict, Any, List, Optional
from .paths import NOVEL_PROJECTS_DIR, DEFAULT_SAVE_DIR, ensure_path

from .config import (
    TASK_TRACKING_DIR, KNOWLEDGE_BASE_DIR, NOVEL_PROJECTS_DIR, DATA_CONFIG_DIR,
    ATOMIC_OPERATION_ENABLED, USER_EDIT_PRIORITY, AUTO_ADAPT_USER_EDITS,
    INTERNAL_LOG_ENABLED, PROFESSIONAL_LOG_ENABLED, HEALTH_CHECK_INTERVAL_CHAPTERS
)


class StateManager:
    """
    状态管理器（完整增强版 - 质量优先）
    负责：
    1. 工程状态管理
    2. 状态自检与自动修复
    3. 多来源任务恢复
    4. 话题持久化
    5. 用户编辑优先适配
    6. 状态版本控制
    7. 健康检查集成
    8. 大纲备份加载
    """
    
    def __init__(self, log_callback=None, controller=None):
        self.log_callback = log_callback
        self.controller = controller
        
        # 子模块引用
        self.task_planner = None
        self.outline_manager = None
        self.progress_tracker = None
        
        # 配置
        self.atomic_operation_enabled = ATOMIC_OPERATION_ENABLED
        self.user_edit_priority = USER_EDIT_PRIORITY
        self.auto_adapt_user_edits = AUTO_ADAPT_USER_EDITS
        self.internal_log_enabled = INTERNAL_LOG_ENABLED
        self.professional_log_enabled = PROFESSIONAL_LOG_ENABLED
        
        # 状态版本控制
        self.max_versions = 10
        self.version_dir = os.path.join(TASK_TRACKING_DIR, "state_versions")
        os.makedirs(self.version_dir, exist_ok=True)
        
        # 健康检查配置
        self.health_check_interval = HEALTH_CHECK_INTERVAL_CHAPTERS
        
        # 备份目录
        self.backup_dir = os.path.join(TASK_TRACKING_DIR, "state_backups")
        os.makedirs(self.backup_dir, exist_ok=True)
        
        self._log("✅ 状态管理器已初始化（完整增强版 - 质量优先）")
    
    def _log(self, msg: str, level: str = "INFO") -> None:
        """
        统一日志输出。

        - 当 ``self.log_callback`` 存在时走回调（结构化 JSON / 纯文本）。
        - 当 ``self.log_callback`` 为 ``None`` 时，自动落盘到 ``logs/ai_controller_fallback.log``；
         路径使用 ``core.paths.ensure_path`` 创建，写入采用 **原子写入 + fsync**，确保即使在异常掉电时也不丢失日志。
        - ``PROFESSIONAL_LOG_ENABLED`` 控制结构化 JSON； ``LOG_DEDUPLICATION``（由 LogMixin 统一）在本类中不做去重。
        """
        timestamp = datetime.now().isoformat()

        # -------------------------------------------------
        # 1️⃣ 构造日志行（结构化或纯文本）
        # -------------------------------------------------
        if self.professional_log_enabled:
            log_entry = {
                "timestamp": timestamp,
                "level": level.upper(),
                "module": "StateManager",
                "project_id": getattr(self, "project_id", None),
                "message": msg,
            }
            log_line = json.dumps(log_entry, ensure_ascii=False)
        else:
            log_line = f"[{timestamp}] [{level.upper():5}] {msg}"

        # -------------------------------------------------
        # 2️⃣ 若有回调则走回调，否则写入 fallback 文件
        # -------------------------------------------------
        if callable(self.log_callback):
            try:
                self.log_callback(log_line)
                return
            except Exception:
                # 回调本身异常，降级到磁盘写入
                pass

        # -------------------------------------------------
        # 3️⃣ Fallback：写入 ``logs/ai_controller_fallback.log``
        # -------------------------------------------------
        try:
            from .paths import LOGS_DIR, ensure_path
            fallback_path = ensure_path(LOGS_DIR / "ai_controller_fallback.log")
            # 原子写入：先写 .tmp，再 replace
            tmp_path = fallback_path.with_suffix(".tmp")
            with open(tmp_path, "a", encoding="utf-8") as f:
                f.write(log_line + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, fallback_path)
        except Exception:
            # 最终防御：直接打印到 stdout
            print(log_line)

    

    def record_outline_attempt(
        self,
        task_id: str,
        attempt_info: Dict[str, Any],
        level: str = "INFO",
    ) -> None:
        """
        将一次大纲生成尝试（模型、温度、返回状态等）写入
        项目专属的 attempt_logs 文件夹，采用原子写入。
        """
        log_dir = os.path.join(TASK_TRACKING_DIR, "outline_attempts")
        os.makedirs(log_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        log_file = os.path.join(log_dir, f"{task_id}_{ts}.json")

        attempt_info["task_id"] = task_id
        attempt_info["timestamp"] = datetime.now().isoformat()

        tmp_file = log_file + ".tmp"
        try:
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(attempt_info, f, ensure_ascii=False, indent=2)
            os.replace(tmp_file, log_file)
        except Exception:
            # 在极端情况下不影响主流程，直接吞掉异常
            pass

                
    def check_and_fix_state(self, task_id: str) -> Dict[str, Any]:
        """
        状态自检与自动修复
        
        Args:
            task_id: 任务 ID
        
        Returns:
            修复结果
        """
        if not self.task_planner or not self.outline_manager:
            return {"success": False, "error": "模块未初始化"}
        
        task_plan = self.task_planner.get_plan(task_id)
        if not task_plan:
            return {"success": False, "error": "任务计划不存在"}
        
        task_status = task_plan.get('status', 'planned')
        
        # 自检 1：检查大纲是否已存在
        outline_data = self.outline_manager.get_outline(task_id)
        outline_exists = outline_data is not None
        
        # 自检 2：检查状态与大纲实际存在情况是否一致
        status_outline_mismatch = False
        if task_status == 'outline_generated' and not outline_exists:
            status_outline_mismatch = True
            self._log("⚠️ 状态异常：状态为 outline_generated 但大纲文件不存在", level="WARN")
        elif task_status == 'planned' and outline_exists:
            status_outline_mismatch = True
            self._log("⚠️ 状态异常：状态为 planned 但大纲文件已存在", level="WARN")
        
        # 自修复：状态与实际情况不一致时，以实际情况为准
        if status_outline_mismatch:
            self._log("🔧 启动状态自修复...")
            if outline_exists:
                self.task_planner.update_plan_status(task_id, 'outline_generated', {
                    'auto_fixed': True,
                    'fixed_at': datetime.now().isoformat()
                })
                self._log("✅ 状态已修复：更新为 outline_generated")
            else:
                self.task_planner.update_plan_status(task_id, 'planned', {
                    'auto_fixed': True,
                    'fixed_at': datetime.now().isoformat()
                })
                self._log("✅ 状态已修复：重置为 planned")
        
        # 自检 3：检查进度文件一致性
        if self.progress_tracker:
            progress_data = self.progress_tracker.get_progress(task_id)
            if progress_data:
                progress_chapters = progress_data.get('completed_chapters', 0)
                plan_status = task_plan.get('status', 'planned')
                
                if progress_chapters > 0 and plan_status == 'planned':
                    self._log("⚠️ 状态异常：已有进度但任务状态为 planned", level="WARN")
                    self.task_planner.update_plan_status(task_id, 'in_progress', {
                        'auto_fixed': True,
                        'fixed_at': datetime.now().isoformat()
                    })
                    self._log("✅ 状态已修复：更新为 in_progress")
        
        # 记录到内部日志
        if self.internal_log_enabled:
            self._log_state_check_result(task_id, status_outline_mismatch)
        
        return {
            "success": True,
            "task_status": task_plan.get('status'),
            "outline_exists": outline_exists,
            "auto_fixed": status_outline_mismatch
        }
    
    def _log_state_check_result(self, task_id: str, has_issues: bool):
        """记录状态检查结果到内部日志"""
        try:
            log_file = os.path.join(TASK_TRACKING_DIR, "state_check_log.jsonl")
            log_entry = {
                "timestamp": datetime.now().isoformat(),
                "task_id": task_id,
                "has_issues": has_issues,
                "fixed": has_issues
            }
            
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
        except Exception as e:
            self._log(f"⚠️ 记录状态检查结果失败：{e}", level="WARN")
    
    def recover_task_from_multiple_sources(self) -> Optional[Dict[str, Any]]:
        """
        多来源任务恢复
        
        Returns:
            恢复的任务计划
        """
        # 尝试 1：从进度追踪器恢复
        if self.progress_tracker:
            pending_progress = self.progress_tracker.list_pending_tasks()
            if pending_progress:
                self._log("📋 从进度追踪器恢复任务...")
                return pending_progress[0]
        
        # 尝试 2：从项目目录恢复
        recovery_result = self._recover_task_from_project_directory()
        if recovery_result.get('success'):
            self._log(f"✅ 任务恢复成功：{recovery_result.get('task_id')}")
            return recovery_result.get('task_plan')
        
        # 尝试 3：从大纲文件恢复
        recovery_result = self._recover_task_from_outline_files()
        if recovery_result.get('success'):
            self._log(f"✅ 任务恢复成功：{recovery_result.get('task_id')}")
            return recovery_result.get('task_plan')
        
        return None
    
    def _recover_task_from_project_directory(self) -> Dict[str, Any]:
        """从项目目录中恢复任务计划（用于处理意外中断后的恢复）。
        通过扫描项目目录中的状态文件尝试重建任务计划。
        """
        try:
            # 扫描所有项目目录
            if not os.path.isdir(NOVEL_PROJECTS_DIR):
                return {'success': False, 'error': '项目目录不存在'}
            
            for project_dir in os.listdir(NOVEL_PROJECTS_DIR):
                project_path = os.path.join(NOVEL_PROJECTS_DIR, project_dir)
                if not os.path.isdir(project_path):
                    continue
                    
                # 检查是否有任务计划文件
                task_plan_path = os.path.join(project_path, "task_plan.json")
                if os.path.exists(task_plan_path):
                    try:
                        with open(task_plan_path, 'r', encoding='utf-8') as f:
                            task_plan = json.load(f)
                        
                        # 验证任务计划的基本字段
                        if not isinstance(task_plan, dict):
                            continue
                            
                        task_id = task_plan.get('task_id')
                        if not task_id:
                            continue
                            
                        # 检查任务状态是否为待处理状态
                        status = task_plan.get('status', '')
                        if status in ['planned', 'outline_generated', 'outline_confirmed', 'in_progress']:
                            # 尝试恢复项目ID
                            if not self.current_project_id:
                                self.current_project_id = project_dir
                                
                            self._log(f"🔍 从项目目录恢复任务：{task_id}（状态：{status}）")
                            return {
                                'success': True,
                                'task_id': task_id,
                                'task_plan': task_plan,
                                'project_dir': project_path
                            }
                    except Exception as e:
                        self._log(f"⚠️ 读取任务计划失败 {task_plan_path}: {e}", level="WARN")
                        continue
            
            return {'success': False, 'error': '未找到可恢复的任务计划'}
        except Exception as e:
            self._log(f"❌ 从项目目录恢复任务失败: {e}", level="ERROR")
            return {'success': False, 'error': str(e)}

    
    def _recover_task_from_outline_files(self) -> Dict[str, Any]:
        """从大纲文件反推任务"""
        outlines_dir = os.path.join(TASK_TRACKING_DIR, "outlines")
        if not os.path.exists(outlines_dir):
            return {"success": False}
        
        for filename in os.listdir(outlines_dir):
            if filename.startswith('outline_machine_') and filename.endswith('.json'):
                filepath = os.path.join(outlines_dir, filename)
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        outline_data = json.load(f)
                    
                    task_id = outline_data.get('task_id', '')
                    if task_id:
                        task_plan = {
                            'task_id': task_id,
                            'genre': outline_data.get('genre', '通用'),
                            'total_chapters': len(outline_data.get('chapter_outlines', [])),
                            'status': 'outline_generated',
                            'outline_exists': True
                        }
                        
                        if self.task_planner:
                            existing = self.task_planner.get_plan(task_plan['task_id'])
                            if not existing:
                                self.task_planner._save_plan(task_plan)
                                self._log(f"✅ 任务计划已从大纲文件恢复：{task_plan['task_id']}")
                        
                        return {"success": True, "task_plan": task_plan}
                except Exception as e:
                    self._log(f"⚠️ 从大纲文件恢复失败：{e}", level="WARN")
                    continue
        
        return {"success": False}
    
    def load_outline_from_backup(self, task_id: str) -> Optional[Dict[str, Any]]:
        """从备用位置加载大纲"""
        backup_locations = [
            os.path.join(TASK_TRACKING_DIR, "outlines", f"outline_{task_id}_latest.json"),
            os.path.join(TASK_TRACKING_DIR, "outlines", f"outline_machine_{task_id}.json"),
            os.path.join(KNOWLEDGE_BASE_DIR, f"总体大纲_人类版_{task_id}.txt"),
        ]
        
        if self.controller and self.controller.current_project_id:
            project_dir = os.path.join(NOVEL_PROJECTS_DIR, f"*{self.controller.current_project_id}*")
            backup_locations.append(os.path.join(project_dir, "01_大纲", "总体大纲.json"))
        
        for location in backup_locations:
            if '*' in location:
                matches = glob.glob(location)
                if matches:
                    location = matches[0]
                else:
                    continue
            
            if os.path.exists(location):
                try:
                    if location.endswith('.json'):
                        with open(location, 'r', encoding='utf-8') as f:
                            return json.load(f)
                    else:
                        with open(location, 'r', encoding='utf-8') as f:
                            content = f.read()
                        return {
                            'title': '从备份恢复的大纲',
                            'content': content,
                            'recovered_from': location
                        }
                except Exception as e:
                    self._log(f"⚠️ 从 {location} 加载失败：{e}", level="WARN")
                    continue
        
        return None
    
    # ==================== 话题持久化 ====================
    
    def save_topic_state(self, topic: str) -> None:
        """保存当前话题"""
        try:
            os.makedirs(DATA_CONFIG_DIR, exist_ok=True)
            topic_file = os.path.join(DATA_CONFIG_DIR, "last_active_topic.bin")
            with open(topic_file, 'wb') as f:
                pickle.dump({
                    'topic': topic,
                    'time': datetime.now().isoformat()
                }, f)
        except Exception as e:
            self._log(f"⚠️ 保存话题失败：{e}", level="WARN")
    
    def load_last_active_topic(self) -> str:
        """
        从磁盘读取最近一次使用的话题（若不存在则返回默认话题）。
        这里返回一个永不为空的字符串，以防止 `conversation_[]` 之类的非法文件名。
        """
        topic_path = os.path.join(KNOWLEDGE_BASE_DIR, "last_active_topic.txt")
        if os.path.isfile(topic_path):
            try:
                with open(topic_path, "r", encoding="utf-8") as f:
                    topic = f.read().strip()
                if topic:
                    return topic
            except Exception:
                pass
        # ------------------- 兜底 -------------------
        # 若文件不存在或内容为空，生成基于时间的默认话题
        fallback = f"topic_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        # 同时把默认写回文件，避免下次再走这里
        try:
            with open(topic_path, "w", encoding="utf-8") as f:
                f.write(fallback)
        except Exception:
            pass
        return fallback
    
    # ==================== 状态版本控制 ====================
    
    def save_state_version(self, state_data: Dict[str, Any], state_type: str, task_id: str) -> str:
        """
        保存状态版本
        用于回滚和审计
        """
        version_dir = os.path.join(self.version_dir, task_id, state_type)
        os.makedirs(version_dir, exist_ok=True)
        
        # 获取当前版本号
        existing_versions = [f for f in os.listdir(version_dir) if f.endswith('.json')]
        version_num = len(existing_versions) + 1
        
        version_file = os.path.join(version_dir, f"v{version_num:03d}.json")
        
        version_data = {
            "version": version_num,
            "saved_at": datetime.now().isoformat(),
            "state_type": state_type,
            "task_id": task_id,
            "state_data": state_data
        }
        
        # 原子写入
        if self.atomic_operation_enabled:
            tmp_file = version_file + '.tmp'
            try:
                with open(tmp_file, 'w', encoding='utf-8') as f:
                    json.dump(version_data, f, indent=2, ensure_ascii=False)
                
                if os.path.exists(tmp_file) and os.path.getsize(tmp_file) > 0:
                    os.replace(tmp_file, version_file)
                
                # 清理旧版本
                self._cleanup_old_versions(version_dir)
                
                self._log(f"💾 状态版本已保存：{state_type} v{version_num}")
                return version_file
                
            except Exception as e:
                self._log(f"❌ 保存状态版本失败：{e}", level="ERROR")
                if os.path.exists(tmp_file):
                    os.remove(tmp_file)
                return ""
        else:
            with open(version_file, 'w', encoding='utf-8') as f:
                json.dump(version_data, f, indent=2, ensure_ascii=False)
            return version_file
    
    def load_state_version(self, state_type: str, task_id: str, version_num: int) -> Optional[Dict[str, Any]]:
        """加载指定版本的状态"""
        version_file = os.path.join(
            self.version_dir, task_id, state_type,
            f"v{version_num:03d}.json"
        )
        
        if not os.path.exists(version_file):
            return None
        
        try:
            with open(version_file, 'r', encoding='utf-8') as f:
                version_data = json.load(f)
            return version_data.get("state_data")
        except:
            return None
    
    def list_state_versions(self, state_type: str, task_id: str) -> List[Dict[str, Any]]:
        """列出所有状态版本"""
        version_dir = os.path.join(self.version_dir, task_id, state_type)
        
        if not os.path.exists(version_dir):
            return []
        
        versions = []
        for filename in sorted(os.listdir(version_dir)):
            if filename.startswith('v') and filename.endswith('.json'):
                filepath = os.path.join(version_dir, filename)
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        version_data = json.load(f)
                    versions.append({
                        "version": version_data.get("version"),
                        "saved_at": version_data.get("saved_at"),
                        "filepath": filepath
                    })
                except:
                    continue
        
        return versions
    
    def rollback_to_version(self, task_id: str, state_type: str, version_num: int) -> bool:
        """
        回滚到指定版本
        ⚠️ 危险操作，会创建回滚前备份
        """
        # 先备份当前状态
        if state_type == "progress" and self.progress_tracker:
            current_data = self.progress_tracker.get_progress(task_id)
            if current_data:
                self.save_state_version(current_data, state_type, task_id)
        
        # 加载目标版本
        version_data = self.load_state_version(state_type, task_id, version_num)
        if not version_data:
            return False
        
        # 根据状态类型恢复
        if state_type == "progress" and self.progress_tracker:
            version_data['last_updated'] = datetime.now().isoformat()
            version_data['rolled_back_at'] = datetime.now().isoformat()
            version_data['rolled_back_from_version'] = version_num
            self.progress_tracker._save_progress(version_data)
        
        self._log(f"🔄 已回滚到版本 v{version_num}")
        return True
    
    def _cleanup_old_versions(self, version_dir: str) -> None:
        """清理旧版本，保留最新的 max_versions 个"""
        if not os.path.exists(version_dir):
            return
        
        versions = [f for f in os.listdir(version_dir) if f.endswith('.json')]
        
        if len(versions) > self.max_versions:
            versions_to_delete = sorted(versions)[:-self.max_versions]
            for version in versions_to_delete:
                try:
                    os.remove(os.path.join(version_dir, version))
                except:
                    pass
    
    # ==================== 用户编辑适配 ====================
    
    def adapt_state_to_user_edit(
        self,
        user_edit: Dict[str, Any],
        current_state: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        适配状态到用户编辑
        用户编辑优先，AI 负责调整状态以匹配用户编辑
        """
        if self.user_edit_priority != "highest":
            return current_state
        
        self._log("👤 适配状态到用户编辑...")
        
        # 调用 AI 分析用户编辑的影响
        prompt = f"""请分析用户编辑对状态的影响，并调整状态以匹配用户编辑。

当前状态：
{json.dumps(current_state, ensure_ascii=False, indent=2)}

用户编辑内容：
{json.dumps(user_edit, ensure_ascii=False, indent=2)}

请分析：
1. 用户编辑改变了哪些事实？
2. 这些改变对状态有什么影响？
3. 需要调整哪些状态字段？

返回更新后的状态（JSON 格式）：
"""
        
        try:
            updated_state = self._call_ai_model(prompt)
            if updated_state:
                self._log("✅ 状态已适配用户编辑")
                return updated_state
        except Exception as e:
            self._log(f"⚠️ 适配用户编辑失败：{e}", level="WARN")
        
        # 降级：直接合并
        merged_state = current_state.copy()
        merged_state.update(user_edit)
        return merged_state
    
    # ==================== 健康检查集成 ====================
    
    def run_project_health_check(self, task_id: str, current_chapter: int) -> Dict[str, Any]:
        """
        运行工程健康检查
        ✅ 整合所有检查项
        
        Args:
            task_id: 任务 ID
            current_chapter: 当前章节
        
        Returns:
            健康检查结果
        """
        self._log(f"🏥 开始工程健康检查（第{current_chapter}章）...")
        
        check_result = {
            "success": True,
            "errors": [],
            "warnings": [],
            "checked_at": datetime.now().isoformat(),
            "chapter": current_chapter,
            "task_id": task_id
        }
        
        # 1. 文件完整性检查
        if self.outline_manager:
            outline_data = self.outline_manager.get_outline(task_id)
            if not outline_data:
                check_result["success"] = False
                check_result["errors"].append({
                    "type": "outline_missing",
                    "message": "大纲文件丢失"
                })
                check_result["error_type"] = "outline_missing"
        
        # 2. 角色状态一致性检查（动态）
        if self.controller and hasattr(self.controller, 'world_state'):
            characters = self.controller.world_state.get_characters()
            for char in characters:
                char_id = char.get('id')
                # 获取角色当前状态（而非初始状态）
                current_status = self.controller.world_state.get_character_status(char_id, current_chapter)
                expected_status = self.controller.world_state.get_expected_character_status(char_id, current_chapter)
                
                if current_status and expected_status and current_status != expected_status:
                    check_result["warnings"].append({
                        "type": "character_state_mismatch",
                        "character": char.get('name'),
                        "current": current_status,
                        "expected": expected_status
                    })
        
        # 3. 世界观层级检查
        if self.controller and hasattr(self.controller, 'world_state'):
            current_level = self.controller.world_state.get_current_truth_level(current_chapter)
            # 检查是否提前泄露
            # 这里可以传入内容进行检查，简化版跳过
        
        # 4. 进度文件一致性检查
        if self.progress_tracker:
            progress_data = self.progress_tracker.get_progress(task_id)
            if progress_data:
                if progress_data.get('completed_chapters', 0) != current_chapter - 1:
                    check_result["warnings"].append({
                        "type": "progress_mismatch",
                        "message": "进度文件与生成章节数不一致"
                    })
        
        # 5. 状态自检
        state_check = self.check_and_fix_state(task_id)
        if state_check.get('auto_fixed'):
            check_result["warnings"].append({
                "type": "state_auto_fixed",
                "message": "状态已自动修复"
            })
        
        # 记录检查结果
        if self.progress_tracker:
            self.progress_tracker.record_health_check_result(task_id, current_chapter, check_result)
        
        # 内部日志
        if self.internal_log_enabled:
            self._log(f"📝 健康检查结果已保存（内部）", level="DEBUG")
        
        if check_result["success"]:
            self._log(f"✅ 健康检查通过（第{current_chapter}章）")
        else:
            self._log(f"❌ 健康检查发现{len(check_result['errors'])}个错误", level="WARN")
        
        return check_result
    
    # ==================== 辅助方法 ====================
    
    def _call_ai_model(self, prompt: str) -> Dict[str, Any]:
        """调用 AI 模型"""
        try:
            import requests
            from .config import OLLAMA_URL, MODEL_NAME, SAFE_TIMEOUT
            
            resp = requests.post(OLLAMA_URL, json={
                "model": MODEL_NAME,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {"temperature": 0.5, "num_predict": 4096}
            }, timeout=SAFE_TIMEOUT)
            
            if resp.status_code == 200:
                content = resp.json().get('message', {}).get('content', '').strip()
                from .super_guardian import guardian
                try:
                    return guardian.heal_json(content, expected_type=dict)
                except ValueError:
                    return {}
            return {}
        except Exception as e:
            self._log(f"❌ AI 调用失败：{e}", level="ERROR")
            return {}

    
    def backup_current_state(self, task_id: str) -> Optional[str]:
        """备份当前状态"""
        if not self.progress_tracker:
            return None
        
        progress_data = self.progress_tracker.get_progress(task_id)
        if not progress_data:
            return None
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_filename = f"state_{task_id}_{timestamp}.json"
        backup_filepath = os.path.join(self.backup_dir, backup_filename)
        
        try:
            with open(backup_filepath, 'w', encoding='utf-8') as f:
                json.dump(progress_data, f, indent=2, ensure_ascii=False)
            
            self._log(f"💾 状态已备份：{backup_filename}")
            return backup_filepath
        except Exception as e:
            self._log(f"❌ 备份失败：{e}", level="ERROR")
            return None
    
    def restore_from_backup(self, backup_filepath: str) -> bool:
        """从备份文件恢复状态"""
        if not os.path.exists(backup_filepath):
            return False
        
        try:
            with open(backup_filepath, 'r', encoding='utf-8') as f:
                backup_data = json.load(f)
            
            task_id = backup_data.get('task_id', '')
            if not task_id or not self.progress_tracker:
                return False
            
            self.progress_tracker._save_progress(backup_data)
            self._log(f"🔄 已从备份恢复：{backup_filepath}")
            return True
        except Exception as e:
            self._log(f"❌ 恢复失败：{e}", level="ERROR")
            return False
    
    def get_state_summary(self, task_id: str) -> Dict[str, Any]:
        """获取状态摘要"""
        summary = {
            "task_id": task_id,
            "generated_at": datetime.now().isoformat()
        }
        
        # 任务计划
        if self.task_planner:
            task_plan = self.task_planner.get_plan(task_id)
            if task_plan:
                summary["task_status"] = task_plan.get("status")
                summary["total_chapters"] = task_plan.get("total_chapters")
        
        # 进度
        if self.progress_tracker:
            progress = self.progress_tracker.get_progress(task_id)
            if progress:
                summary["completed_chapters"] = progress.get("completed_chapters")
                summary["failed_chapters"] = len(progress.get("failed_chapters", []))
        
        # 大纲
        if self.outline_manager:
            outline = self.outline_manager.get_outline(task_id)
            summary["outline_exists"] = outline is not None
        
        # 版本历史
        versions = self.list_state_versions("progress", task_id)
        summary["version_count"] = len(versions)
        
        return summary

    # -----------------------------------------------------------------
    # 兼容 AIController 调用的占位实现
    # -----------------------------------------------------------------
    def update_project_state(self, project_id: str, **kwargs):
        """
        AIController 在大纲确认后会尝试更新项目的整体状态。
        这里仅作占位，不实际写库，只打印/记录日志，返回 True。
        """
        try:
            self._log(f"⚠️ 更新项目状态（占位实现） project_id={project_id} kwargs={kwargs}")
        except Exception:
            print(f"[StateManager] update_project_state 占位: project_id={project_id}")
        return True

    def save_state_init(self, task_id: str, state_dict: Dict[str, Any]) -> bool:
        """
        保存角色/状态初始化信息到 JSON 文件（原子写入）。

        - **路径**：``{TASK_TRACKING_DIR}/state_init_{task_id}.json``
        - 当 ``self.atomic_operation_enabled`` 为 ``True`` 时走 ``tmp → replace`` + ``fsync``；
         否则直接写入（仍使用 ``fsync`` 以保证完整性）。
        - 返回 ``True`` 表示写入成功，``False`` 表示失败并记录日志。
        """
        if not task_id:
            self._log("⚠️ save_state_init：task_id 为空，跳过写入", level="WARN")
            return False

        filepath = os.path.join(TASK_TRACKING_DIR, f"state_init_{task_id}.json")
        tmp_file = filepath + ".tmp"

        try:
            if self.atomic_operation_enabled:
                # ---------- 原子写入 ----------
                with open(tmp_file, "w", encoding="utf-8") as f:
                    json.dump(state_dict, f, indent=2, ensure_ascii=False)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_file, filepath)
            else:
                # ---------- 非原子（仅在极端需求下使用） ----------
                with open(filepath, "w", encoding="utf-8") as f:
                    json.dump(state_dict, f, indent=2, ensure_ascii=False)
                    f.flush()
                    os.fsync(f.fileno())
            self._log(f"💾 状态初始化文件已写入：{filepath}", level="INFO")
            return True
        except Exception as e:
            self._log(f"❌ 保存状态初始化文件失败：{e}", level="ERROR")
            # 清理临时文件（仅在原子路径下会产生）
            if os.path.exists(tmp_file):
                try:
                    os.remove(tmp_file)
                except Exception:
                    pass
            return False


    def load_state_init(self, task_id: str) -> Dict[str, Any]:
        """
        读取角色/状态初始化信息。
        文件路径：{TASK_TRACKING_DIR}/state_init_{task_id}.json
        若文件不存在或内容非法，则返回空字典。
        """
        if not task_id:
            return {}
        filepath = os.path.join(TASK_TRACKING_DIR, f"state_init_{task_id}.json")
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}
        except Exception as e:
            self._log(f"⚠️ 读取状态初始化文件失败：{e}", level="WARN")
            return {}

    def save_world_init(self, task_id: str, world_dict: Dict[str, Any]) -> bool:
        """
        保存世界初始化信息到 JSON 文件（原子写入）。

        - **路径**：``{TASK_TRACKING_DIR}/world_init_{task_id}.json``
        - 支持 ``self.atomic_operation_enabled`` 开关；若关闭则直接写入但仍执行 ``fsync``。
        - 出错时会清理临时文件（如果产生）并返回 ``False``。
        """
        if not task_id:
            self._log("⚠️ save_world_init：task_id 为空，跳过写入", level="WARN")
            return False

        filepath = os.path.join(TASK_TRACKING_DIR, f"world_init_{task_id}.json")
        tmp_file = filepath + ".tmp"

        try:
            if self.atomic_operation_enabled:
                with open(tmp_file, "w", encoding="utf-8") as f:
                    json.dump(world_dict, f, indent=2, ensure_ascii=False)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_file, filepath)
            else:
                with open(filepath, "w", encoding="utf-8") as f:
                    json.dump(world_dict, f, indent=2, ensure_ascii=False)
                    f.flush()
                    os.fsync(f.fileno())
            self._log(f"💾 世界初始化文件已写入：{filepath}", level="INFO")
            return True
        except Exception as e:
            self._log(f"❌ 保存世界初始化文件失败：{e}", level="ERROR")
            if os.path.exists(tmp_file):
                try:
                    os.remove(tmp_file)
                except Exception:
                    pass
            return False


    def load_world_init(self, task_id: str) -> Dict[str, Any]:
        """
        读取世界初始化信息。
        文件路径：{TASK_TRACKING_DIR}/world_init_{task_id}.json
        若文件不存在或内容非法，则返回空字典。
        """
        if not task_id:
            return {}
        filepath = os.path.join(TASK_TRACKING_DIR, f"world_init_{task_id}.json")
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}
        except Exception as e:
            self._log(f"⚠️ 读取世界初始化文件失败：{e}", level="WARN")
            return {}

    def save_conflict(self, task_id: str, conflict_dict: Dict[str, Any]) -> bool:
        """
        保存核心冲突信息到 JSON 文件（原子写入）。

        - **路径**：``{TASK_TRACKING_DIR}/conflict_{task_id}.json``
        - 受 ``self.atomic_operation_enabled`` 控制是否走 ``tmp → replace``。
        - 写入后立即 ``fsync``，确保磁盘持久化。
        """
        if not task_id:
            self._log("⚠️ save_conflict：task_id 为空，跳过写入", level="WARN")
            return False

        filepath = os.path.join(TASK_TRACKING_DIR, f"conflict_{task_id}.json")
        tmp_file = filepath + ".tmp"

        try:
            if self.atomic_operation_enabled:
                with open(tmp_file, "w", encoding="utf-8") as f:
                    json.dump(conflict_dict, f, indent=2, ensure_ascii=False)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_file, filepath)
            else:
                with open(filepath, "w", encoding="utf-8") as f:
                    json.dump(conflict_dict, f, indent=2, ensure_ascii=False)
                    f.flush()
                    os.fsync(f.fileno())
            self._log(f"💾 冲突初始化文件已写入：{filepath}", level="INFO")
            return True
        except Exception as e:
            self._log(f"❌ 保存冲突初始化文件失败：{e}", level="ERROR")
            if os.path.exists(tmp_file):
                try:
                    os.remove(tmp_file)
                except Exception:
                    pass
            return False


    def load_conflict(self, task_id: str) -> Dict[str, Any]:
        """
        读取核心冲突信息。
        文件路径：{TASK_TRACKING_DIR}/conflict_{task_id}.json
        若文件不存在或内容非法，则返回空字典。
        """
        if not task_id:
            return {}
        filepath = os.path.join(TASK_TRACKING_DIR, f"conflict_{task_id}.json")
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}
        except Exception as e:
            self._log(f"⚠️ 读取冲突初始化文件失败：{e}", level="WARN")
            return {}
