import os
import json
import time
from datetime import datetime
from typing import Dict, Any, List, Optional
from .config import TASK_TRACKING_DIR

class ErrorHandler:
    """错误处理器：记录、重试、恢复、文件保护。"""
    
    def __init__(self, log_callback=None):
        self.log_callback = log_callback
        self.error_log_dir = os.path.join(TASK_TRACKING_DIR, "error_logs")
        self.recovery_dir = os.path.join(TASK_TRACKING_DIR, "recovery")
        os.makedirs(self.error_log_dir, exist_ok=True)
        os.makedirs(self.recovery_dir, exist_ok=True)
    
    def _log(self, msg):
        if self.log_callback: self.log_callback(msg)
    
    def record_error(self, task_id: str, chapter_num: int, error_type: str, error_message: str, retry_count: int = 0, context: Dict[str, Any] = None) -> str:
        error_id = f"error_{task_id}_{chapter_num}_{int(time.time())}"
        error_record = {
            'error_id': error_id, 'task_id': task_id, 'chapter_num': chapter_num, 'error_type': error_type,
            'error_message': error_message, 'retry_count': retry_count, 'max_retries': 3, 'context': context or {},
            'created_at': datetime.now().isoformat(), 'resolved': False, 'resolution_method': None, 'resolved_at': None
        }
        error_file = os.path.join(self.error_log_dir, f"{error_id}.json")
        with open(error_file, 'w', encoding='utf-8') as f: json.dump(error_record, f, indent=2, ensure_ascii=False)
        self._log(f"❌ 错误已记录：{error_id} | 类型：{error_type} | 章节：{chapter_num}")
        return error_id
    
    def should_retry(self, error_id: str) -> bool:
        record = self._load_error_record(error_id)
        return record.get('retry_count', 0) < record.get('max_retries', 3) if record else False
    
    def increment_retry(self, error_id: str) -> int:
        record = self._load_error_record(error_id)
        if not record: return 0
        record['retry_count'] = record.get('retry_count', 0) + 1
        record['last_retry_at'] = datetime.now().isoformat()
        self._save_error_record(record)
        return record['retry_count']
    
    def mark_resolved(self, error_id: str, resolution_method: str) -> bool:
        record = self._load_error_record(error_id)
        if not record: return False
        record.update({'resolved': True, 'resolution_method': resolution_method, 'resolved_at': datetime.now().isoformat()})
        self._save_error_record(record)
        return True
    
    def mark_unresolvable(self, error_id: str, reason: str) -> bool:
        record = self._load_error_record(error_id)
        if not record: return False
        record.update({'resolved': True, 'resolution_method': 'skipped', 'skip_reason': reason, 'resolved_at': datetime.now().isoformat()})
        self._save_error_record(record)
        return True
    
    def get_unresolved_errors(self, task_id: str) -> List[Dict[str, Any]]:
        errors = []
        for filename in os.listdir(self.error_log_dir):
            if filename.startswith(f"error_{task_id}_") and filename.endswith('.json'):
                try:
                    with open(os.path.join(self.error_log_dir, filename), 'r', encoding='utf-8') as f:
                        record = json.load(f)
                    if not record.get('resolved', False): errors.append(record)
                except Exception: continue
        return sorted(errors, key=lambda x: x.get('chapter_num', 0))
    
    def generate_error_report(self, task_id: str) -> Dict[str, Any]:
        all_errors = []
        for filename in os.listdir(self.error_log_dir):
            if filename.startswith(f"error_{task_id}_") and filename.endswith('.json'):
                try:
                    with open(os.path.join(self.error_log_dir, filename), 'r', encoding='utf-8') as f: all_errors.append(json.load(f))
                except Exception: continue
        
        if not all_errors: return {'task_id': task_id, 'total_errors': 0, 'message': '无错误记录'}
        
        resolved = sum(1 for e in all_errors if e.get('resolved'))
        skipped = sum(1 for e in all_errors if e.get('resolution_method') == 'skipped')
        error_types = {}
        for e in all_errors:
            etype = e.get('error_type', 'unknown')
            error_types[etype] = error_types.get(etype, 0) + 1
            
        report = {
            'task_id': task_id, 'generated_at': datetime.now().isoformat(), 'total_errors': len(all_errors),
            'resolved_errors': resolved, 'skipped_errors': skipped, 'unresolved_errors': len(all_errors) - resolved,
            'error_types': error_types, 'failed_chapters': sorted(list(set(e.get('chapter_num', 0) for e in all_errors))),
            'error_details': all_errors, 'suggestions': self._generate_recovery_suggestions(all_errors)
        }
        with open(os.path.join(self.error_log_dir, f"error_report_{task_id}.json"), 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        return report
    
    def _generate_recovery_suggestions(self, errors: List[Dict[str, Any]]) -> List[str]:
        suggestions = []
        error_types = [e.get('error_type', '') for e in errors]
        error_msgs = " ".join([e.get('error_message', '') for e in errors]).lower()
        
        if error_types.count('generation_failed') > 3: suggestions.append("多章生成失败，建议检查 Ollama 模型服务状态或显存是否溢出。")
        if error_types.count('validation_failed') > 3: suggestions.append("多章验证失败，建议放宽质量检查阈值或优化大纲逻辑。")
        if error_types.count('file_write_failed') > 0: suggestions.append("文件写入失败，请检查磁盘空间、权限或杀毒软件拦截。")
        if 'timeout' in error_msgs or 'timed out' in error_msgs: suggestions.append("检测到超时错误，建议在 config 中增加 SAFE_TIMEOUT 时长。")
        
        chapters = sorted([e.get('chapter_num', 0) for e in errors])
        if len(chapters) >= 3:
            consecutive = max_consecutive = 1
            for i in range(1, len(chapters)):
                if chapters[i] == chapters[i-1] + 1: consecutive += 1; max_consecutive = max(max_consecutive, consecutive)
                else: consecutive = 1
            if max_consecutive >= 3: suggestions.append(f"发现连续 {max_consecutive} 章失败，建议检查该段落的大纲设定是否存在逻辑死结。")
        return suggestions
    
    def backup_failed_chapter(self, task_id: str, chapter_num: int, content: str) -> Optional[str]:
        if not content: return None
        backup_dir = os.path.join(self.recovery_dir, task_id)
        os.makedirs(backup_dir, exist_ok=True)
        backup_filepath = os.path.join(backup_dir, f"第{chapter_num}章_失败备份_{int(time.time())}.txt")
        try:
            with open(backup_filepath, 'w', encoding='utf-8') as f:
                f.write(content)
                f.write(f"\n\n【错误信息】本章节生成失败，内容已备份，待人工检查")
            return backup_filepath
        except Exception: return None
    
    def retry_failed_chapters(self, task_id: str, chapter_generator, task_plan: Dict[str, Any], continuation_guide: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        results = []
        for error in self.get_unresolved_errors(task_id):
            chapter_num = error.get('chapter_num', 0)
            error_id = error.get('error_id', '')
            if not self.should_retry(error_id):
                self.mark_unresolvable(error_id, "已达最大重试次数")
                results.append({'chapter': chapter_num, 'success': False, 'reason': '已达最大重试次数'})
                continue
            
            retry_count = self.increment_retry(error_id)
            result = chapter_generator.generate_chapter(task_id=task_id, chapter_num=chapter_num, task_plan=task_plan, continuation_guide=continuation_guide)
            if result.get('success'):
                self.mark_resolved(error_id, f"重试{retry_count}次后成功")
                results.append({'chapter': chapter_num, 'success': True, 'retry_count': retry_count})
            else:
                results.append({'chapter': chapter_num, 'success': False, 'retry_count': retry_count, 'error': result.get('error', '')})
        return results
    
    def _load_error_record(self, error_id: str) -> Optional[Dict[str, Any]]:
        error_file = os.path.join(self.error_log_dir, f"{error_id}.json")
        if not os.path.exists(error_file): return None
        try:
            with open(error_file, 'r', encoding='utf-8') as f: return json.load(f)
        except Exception: return None
    
    def _save_error_record(self, error_record: Dict[str, Any]):
        error_file = os.path.join(self.error_log_dir, f"{error_record['error_id']}.json")
        with open(error_file, 'w', encoding='utf-8') as f: json.dump(error_record, f, indent=2, ensure_ascii=False)
    
    def cleanup_old_errors(self, days: int = 30) -> int:
        cutoff_date = time.time() - (days * 24 * 3600)
        cleaned = 0
        for filename in os.listdir(self.error_log_dir):
            if filename.startswith('error_') and filename.endswith('.json'):
                filepath = os.path.join(self.error_log_dir, filename)
                try:
                    if os.path.getmtime(filepath) < cutoff_date: os.remove(filepath); cleaned += 1
                except Exception: continue
        return cleaned
