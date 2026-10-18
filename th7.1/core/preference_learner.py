import os
import json
import hashlib
import time
from datetime import datetime
from typing import Dict, Any, List, Optional
from .config import KNOWLEDGE_BASE_DIR, TASK_TRACKING_DIR

class PreferenceLearner:
    """偏好学习器：识别、分类、保存和应用用户偏好。"""
    
    def __init__(self, log_callback=None, model_client=None):
        self.log_callback = log_callback
        self.model_client = model_client
        self.temporary_prefs_dir = os.path.join(TASK_TRACKING_DIR, "temporary_preferences")
        self.permanent_prefs_file = os.path.join(KNOWLEDGE_BASE_DIR, "permanent_preferences.json")
        os.makedirs(self.temporary_prefs_dir, exist_ok=True)
    
    def _log(self, msg: str, level: str = "INFO"):
        if self.log_callback:
            try: self.log_callback(msg, level=level)
            except TypeError: self.log_callback(msg)

    def _atomic_write_json(self, filepath: str, data: Any) -> None:
        """🌟 工业级原子写入 JSON，防止断电数据丢失。"""
        tmp_path = filepath + ".tmp"
        try:
            dir_name = os.path.dirname(filepath)
            if dir_name: os.makedirs(dir_name, exist_ok=True)
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, filepath)
        except Exception as e:
            if os.path.exists(tmp_path):
                try: os.remove(tmp_path)
                except Exception: pass
            self._log(f"❌ 原子写入失败 ({filepath}): {e}", level="ERROR")
    
    def analyze_user_feedback(self, user_input: str, context: Dict[str, Any] = None) -> Dict[str, Any]:
        """分析用户反馈，识别是否是偏好指令。"""
        self._log("🔍 分析用户反馈...")
        txt = (user_input or "").lower()
        preference_keywords = ['记住', '偏好', '喜欢', '不喜欢', '以后都', '每次都', '默认']
        is_preference = any(k in txt for k in preference_keywords)
        permanent_keywords = ['记住这个偏好', '永久记住', '以后都这样', '保存偏好', '设为默认']
        is_permanent = any(k in txt for k in permanent_keywords)
        
        if not is_preference:
            return {'is_preference': False, 'is_permanent': False, 'preference_type': None, 'preference_content': None}
        
        extract_prompt = f"""分析用户的偏好指令：{user_input}
返回 JSON：{{"preference_type": "writing_style/file_management/quality_check/other", "preference_content": "具体内容", "scope": "current_task/all_tasks", "priority": 5}}"""
        try:
            preference_data = {}
            # 🌟 核心修复：增加 hasattr 检查，防止 AttributeError
            if self.model_client and hasattr(self.model_client, '_parse_user_request'):
                preference_data = self.model_client._parse_user_request(extract_prompt)
            else:
                preference_data = self._simple_extract(user_input)
            
            preference_data['is_preference'] = True
            preference_data['is_permanent'] = is_permanent or preference_data.get('scope') == 'all_tasks'
            preference_data['created_at'] = datetime.now().isoformat()
            preference_data['source'] = user_input
            
            self._log(f"✅ 偏好已识别：{preference_data.get('preference_type', 'unknown')}")
            return preference_data
        except Exception as e:
            self._log(f"⚠️ 偏好分析失败：{e}", level="WARN")
            return {'is_preference': False, 'is_permanent': False}
    
    def _simple_extract(self, user_input: str) -> Dict[str, Any]:
        txt = user_input.lower()
        if any(w in txt for w in ['风格', '文风', '写法']): pref_type = 'writing_style'
        elif any(w in txt for w in ['文件', '命名', '保存']): pref_type = 'file_management'
        elif any(w in txt for w in ['检查', '质量', '字数']): pref_type = 'quality_check'
        else: pref_type = 'other'
        return {'preference_type': pref_type, 'preference_content': user_input, 'scope': 'all_tasks' if '以后' in txt else 'current_task', 'priority': 5}
    
    def save_preference(self, preference_data: Dict[str, Any], task_id: str = None) -> str:
        if preference_data.get('is_permanent', False): return self._save_permanent_preference(preference_data)
        else: return self._save_temporary_preference(preference_data, task_id)
    
    def _save_permanent_preference(self, preference_data: Dict[str, Any]) -> str:
        existing_prefs = self._load_permanent_preferences()
        pref_id = hashlib.md5(f"{preference_data.get('preference_type', '')}{preference_data.get('preference_content', '')}".encode()).hexdigest()[:12]
        preference_data['id'] = pref_id
        preference_data['saved_at'] = datetime.now().isoformat()
        
        conflicts = self._check_preference_conflicts(preference_data, existing_prefs)
        if conflicts:
            self._log(f"⚠️ 检测到 {len(conflicts)} 条冲突偏好", level="WARN")
            preference_data['conflicts'] = conflicts
        existing_prefs.append(preference_data)
        
        self._atomic_write_json(self.permanent_prefs_file, existing_prefs)
        self._log(f"💾 永久偏好已保存：{pref_id}")
        return pref_id
    
    def _save_temporary_preference(self, preference_data: Dict[str, Any], task_id: str) -> str:
        if not task_id: task_id = 'global_temp'
        pref_id = hashlib.md5(f"{task_id}{preference_data.get('preference_content', '')}{time.time()}".encode()).hexdigest()[:12]
        preference_data['id'] = pref_id
        preference_data['task_id'] = task_id
        preference_data['saved_at'] = datetime.now().isoformat()
        
        temp_file = os.path.join(self.temporary_prefs_dir, f"pref_{task_id}_{pref_id}.json")
        self._atomic_write_json(temp_file, preference_data)
        self._log(f"💾 临时偏好已保存：{pref_id}（任务：{task_id}）")
        return pref_id
    
    def get_preferences_for_task(self, task_id: str) -> List[Dict[str, Any]]:
        preferences = self._load_permanent_preferences()
        for filename in os.listdir(self.temporary_prefs_dir):
            if filename.startswith(f"pref_{task_id}_") and filename.endswith('.json'):
                try:
                    with open(os.path.join(self.temporary_prefs_dir, filename), 'r', encoding='utf-8') as f:
                        preferences.append(json.load(f))
                except Exception: continue
        preferences.sort(key=lambda x: x.get('priority', 5), reverse=True)
        return preferences
    
    def _check_preference_conflicts(self, new_preference: Dict[str, Any], existing_prefs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        conflicts = []
        new_type = new_preference.get('preference_type', '')
        new_content = new_preference.get('preference_content', '')
        for existing in existing_prefs:
            if existing.get('preference_type') == new_type and existing.get('preference_content') != new_content:
                conflicts.append({'conflict_id': existing.get('id', ''), 'conflict_content': existing.get('preference_content', ''), 'reason': f'同类型偏好冲突：{new_type}'})
        return conflicts
    
    def _load_permanent_preferences(self) -> List[Dict[str, Any]]:
        if not os.path.exists(self.permanent_prefs_file): return []
        try:
            with open(self.permanent_prefs_file, 'r', encoding='utf-8') as f: return json.load(f)
        except json.JSONDecodeError as e:
            # 🌟 核心修复：记录 WARN 日志，防止偏好静默丢失
            self._log(f"⚠️ 永久偏好文件 JSON 损坏，已重置: {e}", level="WARN")
            return []
        except Exception: return []
    
    def delete_preference(self, pref_id: str, is_permanent: bool) -> bool:
        if is_permanent:
            existing = self._load_permanent_preferences()
            filtered = [p for p in existing if p.get('id') != pref_id]
            if len(filtered) < len(existing):
                self._atomic_write_json(self.permanent_prefs_file, filtered)
                self._log(f"🗑️ 永久偏好已删除：{pref_id}")
                return True
        else:
            # 🌟 核心修复：增加异常捕获，防止文件被占用时崩溃
            for filename in os.listdir(self.temporary_prefs_dir):
                if f"_{pref_id}." in filename:
                    try: 
                        os.remove(os.path.join(self.temporary_prefs_dir, filename))
                        self._log(f"🗑️ 临时偏好已删除：{pref_id}")
                        return True
                    except Exception as e:
                        self._log(f"⚠️ 删除临时偏好失败: {e}", level="WARN")
        return False
    
    def list_all_preferences(self) -> Dict[str, List[Dict[str, Any]]]:
        return {'permanent': self._load_permanent_preferences(), 'temporary': []}
    
    def clear_temporary_preferences(self, task_id: str) -> int:
        cleared = 0
        for filename in os.listdir(self.temporary_prefs_dir):
            if filename.startswith(f"pref_{task_id}_"):
                try: 
                    os.remove(os.path.join(self.temporary_prefs_dir, filename))
                    cleared += 1
                except Exception: pass
        self._log(f"🧹 清理了 {cleared} 条临时偏好")
        return cleared
