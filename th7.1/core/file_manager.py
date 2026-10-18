import os
import json
import re
import shutil
import time
from datetime import datetime
from typing import Dict, Any, List
from .paths import DEFAULT_SAVE_DIR, ensure_path
from .config import (
    KNOWLEDGE_BASE_DIR, TASK_TRACKING_DIR, NOVEL_PROJECTS_DIR,
    OLLAMA_URL, MODEL_NAME, SAFE_TIMEOUT, ATOMIC_OPERATION_ENABLED,
    USER_EDIT_PRIORITY, AUTO_ADAPT_USER_EDITS
)

class FileManager:
    """文件管理器（增强版）：原子操作、UTF-8 强制编码、用户编辑优先。"""
    
    def __init__(self, log_callback=None, model_client=None):
        self.log_callback = log_callback
        self.model_client = model_client
        self.novel_storage_dir = os.path.join(TASK_TRACKING_DIR, "novel_storage")
        os.makedirs(self.novel_storage_dir, exist_ok=True)
        
        self.atomic_operation_enabled = ATOMIC_OPERATION_ENABLED
        self.user_edit_priority = USER_EDIT_PRIORITY
        self.auto_adapt_user_edits = AUTO_ADAPT_USER_EDITS
        
        self.file_types = {
            'machine': ['.machine.json', '.pickle', '.db', '.sqlite', '.bin'],
            'human': ['.txt', '.md', '.html'],
            'config': ['.json', '.yaml', '.ini'],
            'backup': ['.bak', '.backup', '.archive']
        }
        self._log("✅ 文件管理器已初始化（增强版 - 原子操作）")
    
    def _log(self, msg: str, level: str = "INFO") -> None:
        if self.log_callback:
            try: self.log_callback(msg, level=level)
            except TypeError: self.log_callback(msg)

    def atomic_write(self, filepath: str, content: str, encoding: str = 'utf-8') -> Dict[str, Any]:
        """原子写入文件，使用 tmp + rename 模式，防止中断损坏。"""
        if not self.atomic_operation_enabled:
            return self._normal_write(filepath, content, encoding)
        
        tmp_filepath = filepath + '.tmp'
        try:
            with open(tmp_filepath, 'w', encoding=encoding) as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            
            if not os.path.exists(tmp_filepath) or os.path.getsize(tmp_filepath) == 0:
                raise Exception("临时文件创建失败或为空")
            
            # 🌟 修复 Windows 权限锁死：增加重试与强制删除兜底
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    os.replace(tmp_filepath, filepath)
                    break
                except PermissionError:
                    if attempt < max_retries - 1:
                        time.sleep(0.5)
                        if os.path.exists(filepath):
                            try: os.remove(filepath)
                            except Exception: pass
                    else:
                        raise
            
            if not os.path.exists(filepath):
                raise Exception("最终文件不存在")
            
            return {"success": True, "filepath": filepath, "size": os.path.getsize(filepath), "method": "atomic"}
        except Exception as e:
            if os.path.exists(tmp_filepath):
                try: os.remove(tmp_filepath)
                except Exception: pass
            return {"success": False, "error": str(e), "method": "atomic"}
    
    def _normal_write(self, filepath: str, content: str, encoding: str = 'utf-8') -> Dict[str, Any]:
        """普通写入（降级方案）。"""
        try:
            with open(filepath, 'w', encoding=encoding) as f:
                f.write(content)
            return {"success": True, "filepath": filepath, "size": os.path.getsize(filepath), "method": "normal"}
        except Exception as e:
            return {"success": False, "error": str(e), "method": "normal"}
    
    def atomic_read(self, filepath: str, encoding: str = 'utf-8', default: str = "") -> str:
        """安全读取文件，支持损坏检测和编码自动回退。"""
        if not os.path.exists(filepath): return default
        try:
            if os.path.getsize(filepath) == 0: return default
            with open(filepath, 'r', encoding=encoding) as f:
                return f.read()
        except UnicodeDecodeError:
            for enc in ['gbk', 'latin-1']:
                try:
                    with open(filepath, 'r', encoding=enc) as f:
                        return f.read()
                except Exception: continue
            return default
        except Exception:
            return default
    
    def atomic_read_json(self, filepath: str, default: Dict[str, Any] = None) -> Dict[str, Any]:
        """安全读取 JSON 文件，支持损坏检测和自动重建。"""
        if default is None: default = {}
        if not os.path.exists(filepath): return default
        try:
            if os.path.getsize(filepath) == 0:
                self.atomic_write(filepath, json.dumps(default, ensure_ascii=False, indent=2))
                return default
            content = self.atomic_read(filepath)
            if not content: return default
            return json.loads(content)
        except json.JSONDecodeError:
            backup_path = filepath + '.bak'
            try: shutil.copy2(filepath, backup_path)
            except Exception: pass
            self.atomic_write(filepath, json.dumps(default, ensure_ascii=False, indent=2))
            return default
        except Exception:
            return default
    
    def atomic_write_json(self, filepath: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """原子写入 JSON 文件。"""
        content = json.dumps(data, ensure_ascii=False, indent=2)
        return self.atomic_write(filepath, content)
    
    def check_user_edit_conflict(self, filepath: str, ai_generated_content: str, last_modified_time: float) -> Dict[str, Any]:
        """检查用户编辑与 AI 生成的冲突。"""
        if not os.path.exists(filepath):
            return {"has_conflict": False, "user_edited": False}
        
        file_mtime = os.path.getmtime(filepath)
        # 🌟 修复时间戳精度问题：增加 1.0 秒容差，防止文件系统精度丢失导致误判
        user_edited = file_mtime > (last_modified_time + 1.0)
        
        if not user_edited:
            return {"has_conflict": False, "user_edited": False}
        
        user_content = self.atomic_read(filepath)
        if user_content == ai_generated_content:
            return {"has_conflict": False, "user_edited": False}
        
        return {
            "has_conflict": True, "user_edited": True, "user_content": user_content,
            "ai_content": ai_generated_content, "user_mtime": file_mtime, "ai_mtime": last_modified_time
        }
    
    def resolve_edit_conflict(self, conflict_result: Dict[str, Any], resolution_strategy: str = "user_priority") -> str:
        """解决编辑冲突。"""
        if not conflict_result.get("has_conflict"):
            return conflict_result.get("ai_content", "")
        
        if self.user_edit_priority == "highest" or resolution_strategy == "user_priority":
            return conflict_result.get("user_content", "")
        elif resolution_strategy == "ai_priority":
            return conflict_result.get("ai_content", "")
        elif resolution_strategy == "merge":
            return self._merge_contents(conflict_result.get("user_content", ""), conflict_result.get("ai_content", ""))
        return conflict_result.get("user_content", "")
    
    def _merge_contents(self, user_content: str, ai_content: str) -> str:
        """合并用户编辑和 AI 生成内容。"""
        prompt = f"""请智能合并以下两个版本的小说内容，优先保留用户编辑的部分。
用户编辑版本：\n{user_content[:10000]}...\n
AI 生成版本：\n{ai_content[:10000]}...\n
合并要求：优先保留用户编辑的内容和风格，保持情节连贯性。请输出合并后的完整内容："""
        try:
            merged_content = self._call_ai_model(prompt)
            return merged_content if isinstance(merged_content, str) and merged_content else user_content
        except Exception:
            return user_content
    
    def parse_file_naming_rule(self, user_instruction: str, task_plan: Dict[str, Any]) -> Dict[str, Any]:
        """解析用户文件命名指令。"""
        parse_prompt = f"""用户希望按以下方式组织小说文件：\n用户指令：{user_instruction}\n
请解析文件命名规则（JSON 格式）：
{{"organization_type": "single_file/multi_file/volume", "naming_pattern": "命名模式", "sort_method": "排序方式", "include_title": true, "include_timestamp": false, "file_extension": ".txt", "special_char_handling": "replace", "volume_size": 10, "machine_format": true, "human_format": true}}"""
        try:
            rule_data = self._call_ai_model(parse_prompt)
            if not rule_data or not isinstance(rule_data, dict):
                rule_data = self._simple_parse(user_instruction)
            
            rule_data.setdefault('organization_type', 'multi_file')
            rule_data.setdefault('naming_pattern', '书名_章节号_标题.txt')
            rule_data.setdefault('sort_method', 'chapter_number')
            rule_data.setdefault('file_extension', '.txt')
            rule_data.setdefault('human_format', True)
            rule_data['created_at'] = datetime.now().isoformat()
            return rule_data
        except Exception:
            return self._get_default_naming_rule()
    
    def _simple_parse(self, instruction: str) -> Dict[str, Any]:
        instruction_lower = instruction.lower()
        if '单文件' in instruction_lower or '合并' in instruction_lower: organization_type = 'single_file'
        elif '分卷' in instruction_lower: organization_type = 'volume'
        else: organization_type = 'multi_file'
        return {'organization_type': organization_type, 'naming_pattern': '书名_章节号_标题.txt', 'sort_method': 'chapter_number', 'include_title': True, 'include_timestamp': False, 'file_extension': '.txt', 'special_char_handling': 'replace', 'volume_size': 10, 'machine_format': True, 'human_format': True}
    
    def _get_default_naming_rule(self) -> Dict[str, Any]:
        return {'organization_type': 'multi_file', 'naming_pattern': '书名_章节号_标题.txt', 'sort_method': 'chapter_number', 'include_title': True, 'include_timestamp': True, 'file_extension': '.txt', 'special_char_handling': 'replace', 'volume_size': 10, 'machine_format': True, 'human_format': True}
    
    def generate_filename(self, task_id: str, chapter_num: int, chapter_title: str = None, naming_rule: Dict[str, Any] = None, book_title: str = None, file_type: str = 'human') -> str:
        if not naming_rule: naming_rule = self._get_default_naming_rule()
        if not book_title: book_title = f"小说_{task_id}"
        
        if naming_rule.get('special_char_handling') == 'replace':
            book_title = self._sanitize_filename(book_title)
            if chapter_title: chapter_title = self._sanitize_filename(chapter_title)
        
        filename = naming_rule.get('naming_pattern', '书名_章节号_标题.txt')
        filename = filename.replace('书名', book_title).replace('章节号', f"第{chapter_num}章").replace('标题', chapter_title if chapter_title else f"第{chapter_num}章")
        
        if naming_rule.get('include_timestamp', False):
            filename = filename.replace('.txt', f"_{int(time.time())}.txt")
        
        ext = naming_rule.get('file_extension', '.txt')
        if file_type == 'machine': ext = '.machine.json'
        if not filename.endswith(ext): filename = filename + ext
        return filename
    
    def _sanitize_filename(self, filename: str) -> str:
        sanitized = re.sub(r'[/\\:*?"<>|]', '_', filename)
        return sanitized[:100] if len(sanitized) > 100 else sanitized
    
    def save_chapter_content(self, content: str, filepath: str, backup_enabled: bool = True, save_machine_format: bool = True, save_human_format: bool = True) -> Dict[str, Any]:
        results = {'success': True, 'files': []}
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        
        if save_human_format:
            human_result = self.atomic_write(filepath, content)
            results['files'].append({'type': 'human', **human_result})
            if not human_result['success']: results['success'] = False
        
        if save_machine_format:
            # 🌟 修复后缀与内容不符问题：机器格式统一使用 .machine.json
            machine_filepath = filepath.rsplit('.', 1)[0] + '.machine.json'
            try:
                machine_data = {'content': content, 'saved_at': datetime.now().isoformat(), 'format': 'machine'}
                machine_result = self.atomic_write_json(machine_filepath, machine_data)
                results['files'].append({'type': 'machine', **machine_result})
            except Exception as e:
                results['files'].append({'type': 'machine', 'success': False, 'error': str(e)})
                results['success'] = False
        return results
    
    def get_project_directory(self, project_id: str, project_name: str) -> str:
        project_dir = os.path.join(NOVEL_PROJECTS_DIR, f"{project_id}_{project_name}")
        os.makedirs(project_dir, exist_ok=True)
        for subdir in ["00_工程配置", "01_大纲", "02_角色设定", "03_世界观设定", "04_章节内容", "05_笔记", "99_备份", "system", "logs"]:
            os.makedirs(os.path.join(project_dir, subdir), exist_ok=True)
        return project_dir
    
    def get_chapter_directory(self, project_dir: str, chapter_num: int) -> str:
        chapter_dir = os.path.join(project_dir, "04_章节内容", f"chapter_{chapter_num:03d}")
        os.makedirs(chapter_dir, exist_ok=True)
        return chapter_dir
    
    def get_config_directory(self, project_dir: str) -> str:
        config_dir = os.path.join(project_dir, "00_工程配置")
        os.makedirs(config_dir, exist_ok=True)
        return config_dir
    
    def get_log_directory(self, project_dir: str) -> str:
        log_dir = os.path.join(project_dir, "logs")
        os.makedirs(log_dir, exist_ok=True)
        return log_dir
    
    def create_index_file(self, task_id: str, chapters: List[Dict[str, Any]], naming_rule: Dict[str, Any]) -> str:
        index_content = [f"小说文件索引\n任务 ID：{task_id}\n生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n" + "=" * 50]
        for ch in sorted(chapters, key=lambda x: x.get('chapter', 0)):
            index_content.append(f"第{ch.get('chapter', 0)}章：{ch.get('filepath', '')}")
            if ch.get('summary'): index_content.append(f"  摘要：{ch['summary'][:50]}...")
        index_content.append("=" * 50 + f"\n总计：{len(chapters)}章")
        
        index_filepath = os.path.join(self.novel_storage_dir, f"index_{task_id}.txt")
        self.atomic_write(index_filepath, '\n'.join(index_content))
        return index_filepath
    
    def merge_chapters_to_single_file(self, task_id: str, chapter_files: List[str], output_filepath: str) -> Dict[str, Any]:
        """合并多个章节文件为单文件。"""
        try:
            # 🌟 修复 OOM：改为流式写入，避免大文件合并时内存溢出
            tmp_filepath = output_filepath + '.tmp'
            with open(tmp_filepath, 'w', encoding='utf-8') as out_f:
                for filepath in chapter_files:
                    if os.path.exists(filepath):
                        with open(filepath, 'r', encoding='utf-8') as in_f:
                            shutil.copyfileobj(in_f, out_f)
                        out_f.write("\n\n" + "="*50 + "\n\n")
                out_f.flush()
                os.fsync(out_f.fileno())
            
            os.replace(tmp_filepath, output_filepath)
            return {'success': True, 'filepath': output_filepath, 'file_size': os.path.getsize(output_filepath)}
        except Exception as e:
            if os.path.exists(tmp_filepath):
                try: os.remove(tmp_filepath)
                except Exception: pass
            return {'success': False, 'error': str(e)}
    
    def list_task_files(self, task_id: str) -> List[str]:
        files = []
        task_dir = os.path.join(self.novel_storage_dir, task_id)
        if os.path.exists(task_dir):
            for filename in os.listdir(task_dir):
                if filename.endswith('.txt') and not filename.endswith('.tmp'):
                    files.append(os.path.join(task_dir, filename))
        return sorted(files)
    
    def get_storage_path(self, task_id: str) -> str:
        return os.path.join(self.novel_storage_dir, task_id)
    
    def get_file_type(self, filepath: str) -> str:
        ext = os.path.splitext(filepath)[1].lower()
        for file_type, extensions in self.file_types.items():
            if ext in extensions: return file_type
        return 'unknown'
    
    def cleanup_temp_files(self, directory: str) -> int:
        cleaned = 0
        for filename in os.listdir(directory):
            if filename.endswith('.tmp') or filename.endswith('.bak'):
                try: os.remove(os.path.join(directory, filename)); cleaned += 1
                except Exception: pass
        return cleaned
    
    def get_directory_size(self, directory: str) -> Dict[str, Any]:
        total_size, file_count, by_type = 0, 0, {}
        for root, dirs, files in os.walk(directory):
            for filename in files:
                filepath = os.path.join(root, filename)
                try:
                    size = os.path.getsize(filepath)
                    total_size += size; file_count += 1
                    file_type = self.get_file_type(filepath)
                    by_type[file_type] = by_type.get(file_type, 0) + size
                except Exception: pass
        return {"total_size": total_size, "total_size_mb": total_size / (1024 * 1024), "file_count": file_count, "by_type": by_type}
    
    def _call_ai_model(self, prompt: str) -> Any:
        """内部统一的 AI 调用封装，返回 JSON 结构或字符串。"""
        import requests
        try:
            resp = requests.post(
                OLLAMA_URL,
                json={"model": MODEL_NAME, "messages": [{"role": "user", "content": prompt}], "stream": False, "options": {"temperature": 0.3, "num_predict": 2048}},
                timeout=SAFE_TIMEOUT,
            )
            if resp.status_code != 200: return {}
            content = resp.json().get("message", {}).get("content", "").strip()
            if not content: return {}
            
            # 🌟 健壮的 JSON 提取：剥离 Markdown，首尾括号定位，清理尾部逗号
            text = re.sub(r"^```(?:json)?\s*", "", content, flags=re.MULTILINE)
            text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE).strip()
            
            start_idx = text.find('{')
            end_idx = text.rfind('}')
            if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                json_str = text[start_idx:end_idx + 1]
                json_str = re.sub(r",\s*([}\]])", r"\1", json_str)
                return json.loads(json_str)
            return json.loads(text)
        except Exception:
            return {}
