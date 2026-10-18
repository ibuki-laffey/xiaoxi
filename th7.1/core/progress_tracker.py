# core/progress_tracker.py
# ==================== 进度追踪器（完整增强版 - 质量优先）====================
import os
import json
import time
import hashlib
import shutil
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

from .config import (
    TASK_TRACKING_DIR, KNOWLEDGE_BASE_DIR, HEALTH_CHECK_INTERVAL_CHAPTERS,
    SILENT_HEALTH_CHECK, INTERNAL_LOG_ENABLED, PROFESSIONAL_LOG_ENABLED,
    ATOMIC_OPERATION_ENABLED, DATA_ARCHIVE_DIR
)

class ProgressTracker:
    """
    进度追踪器（完整增强版 - 质量优先）
    """
    
    def __init__(self, log_callback=None):
        self.log_callback = log_callback
        self.progress_dir = os.path.join(TASK_TRACKING_DIR, "progress")
        self.backup_dir = os.path.join(TASK_TRACKING_DIR, "progress_backups")
        os.makedirs(self.progress_dir, exist_ok=True)
        os.makedirs(self.backup_dir, exist_ok=True)
        
        self.disk_cache = None
        self.enable_integrity_check = True
        self.backup_enabled = True
        self.enable_hash_verification = True
        self.atomic_operation_enabled = ATOMIC_OPERATION_ENABLED
        self.health_check_interval = HEALTH_CHECK_INTERVAL_CHAPTERS
        self.silent_health_check = SILENT_HEALTH_CHECK
        self.professional_log_enabled = PROFESSIONAL_LOG_ENABLED
        self.internal_log_enabled = INTERNAL_LOG_ENABLED
        self.max_versions = 10
        self.version_dir = os.path.join(TASK_TRACKING_DIR, "state_versions")
        os.makedirs(self.version_dir, exist_ok=True)
        self.experience_db_dir = os.path.join(KNOWLEDGE_BASE_DIR, "experience_db")
        os.makedirs(self.experience_db_dir, exist_ok=True)
        
        self._log("✅ 进度追踪器已初始化（完整增强版 - 质量优先）")
    
    def _log(self, msg, level: str = "INFO"):
        if self.log_callback:
            timestamp = datetime.now().isoformat()
            if self.professional_log_enabled:
                log_entry = {
                    "timestamp": timestamp, "level": level,
                    "module": "ProgressTracker", "message": msg
                }
                msg = json.dumps(log_entry, ensure_ascii=False)
            else:
                msg = f"[{timestamp}] [{level}] {msg}"
            self.log_callback(msg)
    
    def create_progress_record(self, task_plan: Dict[str, Any]) -> Dict[str, Any]:
        progress_data = {
            'task_id': task_plan.get('task_id', ''),
            'task_type': task_plan.get('task_type', 'novel'),
            'status': 'planned',
            'total_chapters': task_plan.get('total_chapters', 1),
            'completed_chapters': 0,
            'current_chapter': 0,
            'failed_chapters': [],
            'started_at': None, 'completed_at': None,
            'last_updated': datetime.now().isoformat(),
            'chapter_summaries': [],
            'file_naming_rule': task_plan.get('file_naming_preference', ''),
            'continuation_guide': None, 'last_error': None,
            'split_chapters': {},
            'integrity_check': {
                'last_check': None, 'last_result': None,
                'verified_files': [], 'hash_mismatches': []
            },
            'health_check': {
                'enabled': True, 'interval': self.health_check_interval,
                'silent': self.silent_health_check, 'last_check_chapter': 0,
                'last_check_result': None, 'check_history': [],
                'auto_reconstruct_enabled': True
            },
            'learning_log': [], 'version_history': [],
            'unexpected_demands': [], 'user_edits': []
        }
        self._save_progress(progress_data)
        self._log(f"📊 进度记录已创建：{progress_data['task_id']}")
        return progress_data
    
    def update_progress(
        self,
        task_id: str,
        chapter_num: int,
        success: bool,
        chapter_summary: str = None,
        filepath: str = None,
        error: str = None,
        split_info: Dict[str, Any] = None
    ) -> bool:
        """
        更新进度
        ✅ 新增：健康检查触发点记录、经验学习日志
        🌟 致命修复：防止重试导致 completed_chapters 重复 +1
        """
        progress_data = self.get_progress(task_id)
        if not progress_data:
            return False
        
        # 备份当前状态（原子操作）
        if self.backup_enabled:
            self._backup_progress(progress_data)
        
        # 保存状态版本
        self._save_state_version(progress_data, "before_update")
        
        # 🌟 致命修复：检查该章节是否已经存在于 summaries 中
        summaries = progress_data.get('chapter_summaries', [])
        existing_summary_idx = next((i for i, s in enumerate(summaries) if s.get('chapter') == chapter_num), -1)
        
        if success:
            if chapter_summary:
                # 计算内容哈希
                content_hash = hashlib.md5(chapter_summary.encode()).hexdigest() if chapter_summary else None
                
                summary_record = {
                    'chapter': chapter_num,
                    'summary': chapter_summary,
                    'filepath': filepath,
                    'content_hash': content_hash,
                    'completed_at': datetime.now().isoformat()
                }
                
                if existing_summary_idx != -1:
                    # 重试成功：更新已有记录，不增加计数
                    summaries[existing_summary_idx] = summary_record
                else:
                    # 首次成功：追加记录并增加计数
                    summaries.append(summary_record)
                    progress_data['completed_chapters'] = progress_data.get('completed_chapters', 0) + 1
            
            progress_data['current_chapter'] = max(progress_data.get('current_chapter', 0), chapter_num)
            
            # 从失败列表中移除
            if chapter_num in progress_data.get('failed_chapters', []):
                progress_data['failed_chapters'].remove(chapter_num)
            
            if split_info:
                progress_data['split_chapters'][str(chapter_num)] = split_info
            
            if self.enable_integrity_check and filepath:
                if os.path.exists(filepath):
                    file_hash = self._calculate_file_hash(filepath)
                    verified_files = progress_data['integrity_check']['verified_files']
                    existing_file_idx = next((i for i, f in enumerate(verified_files) if f.get('chapter') == chapter_num), -1)
                    file_record = {
                        'chapter': chapter_num,
                        'filepath': filepath,
                        'file_hash': file_hash,
                        'verified_at': datetime.now().isoformat(),
                        'size': os.path.getsize(filepath)
                    }
                    if existing_file_idx != -1:
                        verified_files[existing_file_idx] = file_record
                    else:
                        verified_files.append(file_record)
            
            # ✅ 新增：记录健康检查触发点（每 10 章）
            if chapter_num % self.health_check_interval == 0 and chapter_num > 0:
                progress_data['health_check']['last_check_chapter'] = chapter_num
                progress_data['learning_log'].append({
                    'type': 'health_check_triggered',
                    'chapter': chapter_num,
                    'timestamp': datetime.now().isoformat(),
                    'next_check_chapter': chapter_num + self.health_check_interval
                })
                self._log(f"🏥 第{chapter_num}章完成，下次健康检查将在第{chapter_num + self.health_check_interval}章")
            
            # ✅ 新增：记录成功经验到学习日志
            progress_data['learning_log'].append({
                'type': 'chapter_success',
                'chapter': chapter_num,
                'timestamp': datetime.now().isoformat(),
                'summary_length': len(chapter_summary) if chapter_summary else 0
            })
            
            self._log(f"✅ 进度更新：第{chapter_num}章已完成")
        else:
            if chapter_num not in progress_data.get('failed_chapters', []):
                progress_data['failed_chapters'].append(chapter_num)
            progress_data['last_error'] = error
            
            # ✅ 新增：记录失败经验到学习日志
            progress_data['learning_log'].append({
                'type': 'chapter_failure',
                'chapter': chapter_num,
                'timestamp': datetime.now().isoformat(),
                'error': error
            })
            
            self._log(f"⚠️ 进度更新：第{chapter_num}章失败")
        
        # 更新状态
        if progress_data['completed_chapters'] >= progress_data['total_chapters']:
            progress_data['status'] = 'completed'
            progress_data['completed_at'] = datetime.now().isoformat()
            
            # ✅ 新增：记录完成经验
            progress_data['learning_log'].append({
                'type': 'project_completed',
                'timestamp': datetime.now().isoformat(),
                'total_chapters': progress_data['completed_chapters'],
                'failed_chapters': len(progress_data['failed_chapters'])
            })
            
            # ✅ 保存到经验库
            self._save_project_experience(progress_data)
            
        elif progress_data['status'] == 'planned':
            progress_data['status'] = 'in_progress'
            progress_data['started_at'] = datetime.now().isoformat()
        
        progress_data['last_updated'] = datetime.now().isoformat()
        
        # 保存状态版本
        self._save_state_version(progress_data, "after_update")
        
        self._save_progress(progress_data)
        return True


    def get_progress(self, task_id: str) -> Optional[Dict[str, Any]]:
        progress_file = os.path.join(self.progress_dir, f"progress_{task_id}.json")
        if not os.path.exists(progress_file):
            return None
        try:
            with open(progress_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            self._log(f"❌ 进度文件损坏：{e}", level="ERROR")
            return self._restore_from_backup(task_id)
        except Exception as e:
            self._log(f"❌ 读取进度失败：{e}", level="ERROR")
            return self._restore_from_backup(task_id)
    
    def list_pending_tasks(self) -> List[Dict[str, Any]]:
        pending = []
        for filename in os.listdir(self.progress_dir):
            if filename.startswith('progress_') and filename.endswith('.json'):
                try:
                    with open(os.path.join(self.progress_dir, filename), 'r', encoding='utf-8') as f:
                        progress_data = json.load(f)
                    if progress_data.get('status') in ['in_progress', 'paused', 'outline_confirmed', 'planned', 'outline_generated']:
                        pending.append(progress_data)
                except: continue
        return pending
    
    def pause_task(self, task_id: str) -> bool:
        progress_data = self.get_progress(task_id)
        if not progress_data: return False
        progress_data['status'] = 'paused'
        progress_data['paused_at'] = datetime.now().isoformat()
        progress_data['learning_log'].append({'type': 'task_paused', 'timestamp': datetime.now().isoformat()})
        self._save_progress(progress_data)
        return True
    
    def resume_task(self, task_id: str) -> bool:
        progress_data = self.get_progress(task_id)
        if not progress_data: return False
        progress_data['status'] = 'in_progress'
        progress_data['resumed_at'] = datetime.now().isoformat()
        progress_data['learning_log'].append({'type': 'task_resumed', 'timestamp': datetime.now().isoformat()})
        self._save_progress(progress_data)
        return True
    
    def get_next_chapter(self, task_id: str) -> int:
        progress_data = self.get_progress(task_id)
        if not progress_data: return 1
        failed = progress_data.get('failed_chapters', [])
        if failed: return min(failed)
        return progress_data.get('current_chapter', 0) + 1
    
    def get_chapter_summaries(self, task_id: str, up_to_chapter: int = None) -> List[Dict[str, Any]]:
        progress_data = self.get_progress(task_id)
        if not progress_data: return []
        summaries = progress_data.get('chapter_summaries', [])
        if up_to_chapter:
            summaries = [s for s in summaries if s.get('chapter', 0) < up_to_chapter]
        return sorted(summaries, key=lambda x: x.get('chapter', 0))
    
    def get_chapter_summary(self, task_id: str, chapter_num: int) -> Optional[str]:
        summaries = self.get_chapter_summaries(task_id)
        for summary in summaries:
            if summary.get('chapter') == chapter_num:
                return summary.get('summary', '')
        return None
    
    def should_trigger_health_check(self, task_id: str, current_chapter: int) -> bool:
        if current_chapter <= 0: return False
        if current_chapter % self.health_check_interval == 0:
            progress_data = self.get_progress(task_id)
            if progress_data:
                last_check = progress_data.get('health_check', {}).get('last_check_chapter', 0)
                if last_check < current_chapter: return True
        return False
    
    def record_health_check_result(self, task_id: str, chapter: int, result: Dict[str, Any]) -> bool:
        progress_data = self.get_progress(task_id)
        if not progress_data: return False
        if 'health_check' not in progress_data:
            progress_data['health_check'] = {'enabled': True, 'interval': self.health_check_interval, 'silent': self.silent_health_check, 'last_check_chapter': 0, 'last_check_result': None, 'check_history': [], 'auto_reconstruct_enabled': True}
        
        check_record = {'chapter': chapter, 'timestamp': datetime.now().isoformat(), 'success': result.get('success', False), 'errors': result.get('errors', []), 'warnings': result.get('warnings', []), 'fix_plan': result.get('fix_plan', None)}
        progress_data['health_check']['last_check_chapter'] = chapter
        progress_data['health_check']['last_check_result'] = result
        progress_data['health_check']['check_history'].append(check_record)
        if len(progress_data['health_check']['check_history']) > 20:
            progress_data['health_check']['check_history'] = progress_data['health_check']['check_history'][-20:]
        
        if not result.get('success'):
            progress_data['learning_log'].append({'type': 'health_check_failed', 'chapter': chapter, 'errors': result.get('errors', []), 'timestamp': datetime.now().isoformat()})
        else:
            progress_data['learning_log'].append({'type': 'health_check_passed', 'chapter': chapter, 'timestamp': datetime.now().isoformat()})
        
        self._save_progress(progress_data)
        return True
    
    def get_health_check_history(self, task_id: str) -> List[Dict[str, Any]]:
        progress_data = self.get_progress(task_id)
        return progress_data.get('health_check', {}).get('check_history', []) if progress_data else []
    
    def get_last_health_check_result(self, task_id: str) -> Optional[Dict[str, Any]]:
        progress_data = self.get_progress(task_id)
        return progress_data.get('health_check', {}).get('last_check_result', None) if progress_data else None
    
    def _save_state_version(self, state_data: Dict[str, Any], version_tag: str) -> str:
        """
        保存状态版本
        用于回滚和审计
        🌟 致命修复：加入 fsync 防止断电丢数据
        """
        task_id = state_data.get('task_id', 'unknown')
        version_subdir = os.path.join(self.version_dir, task_id)
        os.makedirs(version_subdir, exist_ok=True)
        
        # 获取当前版本号
        existing_versions = [f for f in os.listdir(version_subdir) if f.endswith('.json')]
        version_num = len(existing_versions) + 1
        
        version_file = os.path.join(version_subdir, f"v{version_num:03d}_{version_tag}.json")
        
        version_data = {
            "version": version_num,
            "version_tag": version_tag,
            "saved_at": datetime.now().isoformat(),
            "task_id": task_id,
            "state_data": state_data
        }
        
        # 原子写入
        if self.atomic_operation_enabled:
            tmp_file = version_file + '.tmp'
            try:
                with open(tmp_file, 'w', encoding='utf-8') as f:
                    json.dump(version_data, f, indent=2, ensure_ascii=False)
                    f.flush(); os.fsync(f.fileno()) # 🌟 强制刷盘
                
                if os.path.exists(tmp_file) and os.path.getsize(tmp_file) > 0:
                    os.replace(tmp_file, version_file)
                
                # 清理旧版本
                self._cleanup_old_versions(version_subdir)
                
                self._log(f"💾 状态版本已保存：{version_tag} v{version_num}")
                return version_file
                
            except Exception as e:
                self._log(f"❌ 保存状态版本失败：{e}", level="ERROR")
                if os.path.exists(tmp_file):
                    os.remove(tmp_file)
                return ""
        else:
            with open(version_file, 'w', encoding='utf-8') as f:
                json.dump(version_data, f, indent=2, ensure_ascii=False)
                f.flush(); os.fsync(f.fileno()) # 🌟 强制刷盘
            return version_file

    
    def load_state_version(self, task_id: str, version_num: int) -> Optional[Dict[str, Any]]:
        version_subdir = os.path.join(self.version_dir, task_id)
        if not os.path.exists(version_subdir): return None
        for filename in os.listdir(version_subdir):
            if filename.startswith(f"v{version_num:03d}") and filename.endswith('.json'):
                try:
                    with open(os.path.join(version_subdir, filename), 'r', encoding='utf-8') as f:
                        return json.load(f).get("state_data")
                except: return None
        return None
    
    def list_state_versions(self, task_id: str) -> List[Dict[str, Any]]:
        version_subdir = os.path.join(self.version_dir, task_id)
        if not os.path.exists(version_subdir): return []
        versions = []
        for filename in sorted(os.listdir(version_subdir)):
            if filename.startswith('v') and filename.endswith('.json'):
                try:
                    with open(os.path.join(version_subdir, filename), 'r', encoding='utf-8') as f:
                        v_data = json.load(f)
                    versions.append({"version": v_data.get("version"), "version_tag": v_data.get("version_tag"), "saved_at": v_data.get("saved_at")})
                except: continue
        return versions
    
    def rollback_to_version(self, task_id: str, version_num: int) -> bool:
        current_data = self.get_progress(task_id)
        if current_data: self._save_state_version(current_data, "before_rollback")
        version_data = self.load_state_version(task_id, version_num)
        if not version_data: return False
        version_data['last_updated'] = datetime.now().isoformat()
        version_data['rolled_back_at'] = datetime.now().isoformat()
        self._save_progress(version_data)
        return True
    
    def _cleanup_old_versions(self, version_subdir: str) -> None:
        versions = [f for f in os.listdir(version_subdir) if f.endswith('.json')]
        if len(versions) > self.max_versions * 2:
            for version in sorted(versions)[:-self.max_versions * 2]:
                try: os.remove(os.path.join(version_subdir, version))
                except: pass
    
    def _save_project_experience(self, progress_data: Dict[str, Any]) -> str:
        """
        保存项目完成经验到经验库
        ✅ 用于后续项目参考和学习
        🌟 致命修复：加入 fsync 防止断电丢数据
        """
        try:
            experience_file = os.path.join(self.experience_db_dir, "project_completion_experience.json")
            
            # 加载现有经验
            if os.path.exists(experience_file):
                with open(experience_file, 'r', encoding='utf-8') as f:
                    experiences = json.load(f)
            else:
                experiences = {"experiences": []}
            
            # 分析项目数据
            learning_summary = self._analyze_project_learning(progress_data)
            
            # 添加新经验
            new_experience = {
                "project_id": progress_data.get('task_id', ''),
                "genre": progress_data.get('genre', 'unknown'),
                "total_chapters": progress_data.get('total_chapters', 0),
                "completed_chapters": progress_data.get('completed_chapters', 0),
                "failed_chapters": len(progress_data.get('failed_chapters', [])),
                "success_rate": progress_data.get('completed_chapters', 0) / max(progress_data.get('total_chapters', 1), 1),
                "learning_summary": learning_summary,
                "health_check_history": progress_data.get('health_check', {}).get('check_history', [])[-5:],  # 最近 5 次
                "completed_at": datetime.now().isoformat(),
                "lessons_learned": learning_summary.get('suggestions', [])
            }
            
            experiences["experiences"].append(new_experience)
            experiences["experiences"] = experiences["experiences"][-500:]  # 保留最近 500 条
            
            with open(experience_file, 'w', encoding='utf-8') as f:
                json.dump(experiences, f, indent=2, ensure_ascii=False)
                f.flush(); os.fsync(f.fileno()) # 🌟 强制刷盘
            
            self._log(f"💾 项目完成经验已保存：{progress_data.get('task_id', '')}")
            return new_experience.get('project_id', '')
            
        except Exception as e:
            self._log(f"⚠️ 保存项目经验失败：{e}", level="WARN")
            return ""

    
    def _analyze_project_learning(self, progress_data: Dict[str, Any]) -> Dict[str, Any]:
        learning_log = progress_data.get('learning_log', [])
        success_count = sum(1 for log in learning_log if log.get('type') == 'chapter_success')
        failure_count = sum(1 for log in learning_log if log.get('type') == 'chapter_failure')
        failure_reasons = {}
        for log in learning_log:
            if log.get('type') == 'chapter_failure':
                err = log.get('error', 'unknown')
                failure_reasons[err] = failure_reasons.get(err, 0) + 1
        return {"success_count": success_count, "failure_count": failure_count, "failure_reasons": failure_reasons}
    
    def get_project_experience(self, task_id: str) -> Optional[Dict[str, Any]]:
        experience_file = os.path.join(self.experience_db_dir, "project_completion_experience.json")
        if not os.path.exists(experience_file): return None
        try:
            with open(experience_file, 'r', encoding='utf-8') as f:
                for exp in json.load(f).get("experiences", []):
                    if exp.get("project_id") == task_id: return exp
        except: pass
        return None
    
    def _backup_progress(self, progress_data: Dict[str, Any]) -> Optional[str]:
        """
        备份进度文件
        ✅ 原子操作，防止损坏
        🌟 致命修复：加入 fsync 防止断电丢数据
        """
        if not self.backup_enabled:
            return None
        
        task_id = progress_data.get('task_id', 'unknown')
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_filename = f"progress_{task_id}_{timestamp}.json"
        backup_filepath = os.path.join(self.backup_dir, backup_filename)
        
        try:
            # 原子写入
            tmp_filepath = backup_filepath + '.tmp'
            with open(tmp_filepath, 'w', encoding='utf-8') as f:
                json.dump(progress_data, f, indent=2, ensure_ascii=False)
                f.flush(); os.fsync(f.fileno()) # 🌟 强制刷盘
            
            if os.path.exists(tmp_filepath) and os.path.getsize(tmp_filepath) > 0:
                os.replace(tmp_filepath, backup_filepath)
            
            # 清理旧备份（保留最近 10 个）
            self._cleanup_old_backups(task_id)
            
            self._log(f"💾 进度已备份：{backup_filename}")
            return backup_filepath
            
        except Exception as e:
            self._log(f"❌ 备份失败：{e}", level="ERROR")
            if os.path.exists(tmp_filepath):
                os.remove(tmp_filepath)
            return None


    def _restore_from_backup(self, task_id: str) -> Optional[Dict[str, Any]]:
        backup_files = [os.path.join(self.backup_dir, f) for f in os.listdir(self.backup_dir) if f.startswith(f"progress_{task_id}_") and f.endswith('.json')]
        if not backup_files: return None
        backup_files.sort(key=os.path.getmtime, reverse=True)
        try:
            with open(backup_files[0], 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            self._log(f"❌ 恢复备份失败：{e}", level="ERROR")
            return None
    
    def _cleanup_old_backups(self, task_id: str, keep_count: int = 10) -> None:
        backup_files = [(os.path.join(self.backup_dir, f), os.path.getmtime(os.path.join(self.backup_dir, f))) for f in os.listdir(self.backup_dir) if f.startswith(f"progress_{task_id}_") and f.endswith('.json')]
        if len(backup_files) > keep_count:
            for filepath, _ in sorted(backup_files, key=lambda x: x[1], reverse=True)[keep_count:]:
                try: os.remove(filepath)
                except: pass
    
    def list_backups(self, task_id: str = None) -> List[Dict[str, Any]]:
        backups = []
        for filename in os.listdir(self.backup_dir):
            if filename.startswith('progress_') and filename.endswith('.json'):
                if task_id is None or task_id in filename:
                    filepath = os.path.join(self.backup_dir, filename)
                    backups.append({"filename": filename, "filepath": filepath, "size": os.path.getsize(filepath), "created_at": datetime.fromtimestamp(os.path.getmtime(filepath)).isoformat()})
        return sorted(backups, key=lambda x: x["created_at"], reverse=True)
    
    def restore_from_backup_file(self, backup_filepath: str, target_task_id: str = None) -> bool:
        if not os.path.exists(backup_filepath): return False
        try:
            with open(backup_filepath, 'r', encoding='utf-8') as f:
                backup_data = json.load(f)
            if target_task_id:
                current_data = self.get_progress(target_task_id)
                if current_data: self._backup_progress(current_data)
            self._save_progress(backup_data)
            return True
        except Exception as e:
            self._log(f"❌ 恢复备份文件失败：{e}", level="ERROR")
            return False
    
    def _calculate_file_hash(self, filepath: str) -> str:
        hasher = hashlib.md5()
        with open(filepath, 'rb') as f:
            hasher.update(f.read())
        return hasher.hexdigest()
    
    def _save_progress(self, progress_data: Dict[str, Any]) -> bool:
        """
        保存进度文件
        ✅ 原子操作，防止损坏
        🌟 致命修复：加入 fsync 防止断电丢数据
        """
        task_id = progress_data.get('task_id', '')
        progress_file = os.path.join(self.progress_dir, f"progress_{task_id}.json")
        
        if self.atomic_operation_enabled:
            # 原子写入
            tmp_filepath = progress_file + '.tmp'
            try:
                with open(tmp_filepath, 'w', encoding='utf-8') as f:
                    json.dump(progress_data, f, indent=2, ensure_ascii=False)
                    f.flush(); os.fsync(f.fileno()) # 🌟 强制刷盘
                
                if os.path.exists(tmp_filepath) and os.path.getsize(tmp_filepath) > 0:
                    os.replace(tmp_filepath, progress_file)
                    
                    # 验证保存
                    if os.path.exists(progress_file) and os.path.getsize(progress_file) > 0:
                        self._log(f"💾 进度已保存：{task_id}")
                        return True
                    else:
                        raise Exception("保存后文件验证失败")
                else:
                    raise Exception("临时文件写入失败")
                    
            except Exception as e:
                self._log(f"❌ 保存进度失败：{e}", level="ERROR")
                if os.path.exists(tmp_filepath):
                    os.remove(tmp_filepath)
                return False
        else:
            # 普通写入
            try:
                with open(progress_file, 'w', encoding='utf-8') as f:
                    json.dump(progress_data, f, indent=2, ensure_ascii=False)
                    f.flush(); os.fsync(f.fileno()) # 🌟 强制刷盘
                return True
            except Exception as e:
                self._log(f"❌ 保存进度失败：{e}", level="ERROR")
                return False

    
    def delete_progress(self, task_id: str) -> bool:
        progress_file = os.path.join(self.progress_dir, f"progress_{task_id}.json")
        if os.path.exists(progress_file):
            try:
                progress_data = self.get_progress(task_id)
                if progress_data: self._backup_progress(progress_data)
                os.remove(progress_file)
                return True
            except Exception as e:
                self._log(f"❌ 删除进度失败：{e}", level="ERROR")
                return False
        return False
    
    def get_progress_statistics(self, task_id: str) -> Dict[str, Any]:
        progress_data = self.get_progress(task_id)
        if not progress_data: return {"error": "进度记录不存在"}
        total = progress_data.get('total_chapters', 0)
        completed = progress_data.get('completed_chapters', 0)
        return {
            "task_id": task_id, "status": progress_data.get('status', 'unknown'),
            "total_chapters": total, "completed_chapters": completed,
            "failed_chapters": len(progress_data.get('failed_chapters', [])),
            "success_rate": completed / total if total > 0 else 0,
            "progress_percent": (completed / total * 100) if total > 0 else 0
        }

    def get_completed_chapters(self, task_id: str = None) -> int:
        """
        获取已完成的章节数量（返回整数，而非列表，更符合外层计算逻辑）。
        🌟 致命修复：使用 set 去重计算真实完成数量，防止跳章导致计算错误
        """
        try:
            progress_data = self.get_progress(task_id) if task_id else {}
            if not progress_data:
                return 0
            
            # 优先使用明确的计数器
            completed_count = progress_data.get('completed_chapters', 0)
            
            # 如果有 summaries 列表，计算去重后的真实章节数
            summaries = progress_data.get('chapter_summaries', [])
            if summaries:
                # 🌟 致命修复：使用 set 提取所有唯一的章节号，而不是取 max
                unique_chapters = set(item.get('chapter', 0) for item in summaries if item.get('chapter'))
                return max(completed_count, len(unique_chapters))
                
            return completed_count
        except Exception as e:
            self._log(f"⚠️ 获取已完成章节数失败: {e}", level="WARN")
            return 0


    def export_progress(self, task_id: str, output_path: str) -> Dict[str, Any]:
        progress_data = self.get_progress(task_id)
        if not progress_data: return {"success": False, "error": "进度记录不存在"}
        try:
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(progress_data, f, indent=2, ensure_ascii=False)
            return {"success": True, "filepath": output_path}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def import_progress(self, input_path: str) -> Dict[str, Any]:
        if not os.path.exists(input_path): return {"success": False, "error": "文件不存在"}
        try:
            with open(input_path, 'r', encoding='utf-8') as f:
                progress_data = json.load(f)
            task_id = progress_data.get('task_id', '')
            if not task_id: return {"success": False, "error": "进度记录缺少 task_id"}
            self._save_progress(progress_data)
            return {"success": True, "task_id": task_id}
        except Exception as e:
            return {"success": False, "error": str(e)}
