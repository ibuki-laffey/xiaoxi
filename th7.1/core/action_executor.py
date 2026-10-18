import os
import re
import json
import time
import shutil
from datetime import datetime
from typing import Dict, Any, List, Optional
from .config import CODE_SANDBOX_DIR, KNOWLEDGE_BASE_DIR
from .logger import action_logger
from .task_definition import Task, NovelWritingTask, ContentModificationTask, ResearchTask

class ActionExecutor:
    """增强版动作执行器 - 支持智能创作任务与真实 LLM 调用"""
    
    def __init__(self, plugin_manager=None, time_service=None, log_callback=None):
        self.plugin_manager = plugin_manager
        self.time_service = time_service
        self.log_callback = log_callback
        from .config import DEFAULT_SAVE_DIR
        self.save_dir = DEFAULT_SAVE_DIR
        os.makedirs(self.save_dir, exist_ok=True)
        
        self.novels_dir = os.path.join(self.save_dir, "小说作品")
        self.outlines_dir = os.path.join(self.save_dir, "创作大纲")
        self.research_dir = os.path.join(self.save_dir, "研究资料")
        os.makedirs(self.novels_dir, exist_ok=True)
        os.makedirs(self.outlines_dir, exist_ok=True)
        os.makedirs(self.research_dir, exist_ok=True)
    
    def _log(self, msg, level="INFO"):
        if self.log_callback:
            self.log_callback(msg)

    def _call_llm_for_content(self, prompt: str, task_desc: str) -> str:
        """🌟 核心防阉割：真实调用 LLM 生成内容，彻底消灭硬编码假模板"""
        try:
            from .config import call_ollama_safe
            self._log(f"🧠 正在调用 LLM 执行 {task_desc}...")
            resp = call_ollama_safe([{"role": "user", "content": prompt}], options={"temperature": 0.7})
            if resp.get("success"):
                content = resp.get("content", "").strip()
                if content.startswith("```"):
                    content = re.sub(r'^```[a-zA-Z]*\n?', '', content)
                    content = re.sub(r'\n?```$', '', content)
                return content
        except Exception as e:
            self._log(f"⚠️ LLM 调用失败 ({task_desc}): {e}")
        return f"[系统提示：LLM 调用失败，无法自动生成{task_desc}。请检查 Ollama 服务状态。]\n\n原始 Prompt:\n{prompt}"

    def _fix_ai_code(self, code):
        """修复 AI 生成代码中常见的缩进丢失、分号代替换行等问题"""
        code = code.replace(';', '\n').replace('\\n', '\n').replace('\\t', '    ')
        lines = code.split('\n')
        fixed_lines = []
        indent_level = 0
        in_multiline_string = False
        
        for i, line in enumerate(lines):
            stripped = line.strip()
            triple_quote_count = stripped.count('"""') + stripped.count("'''")
            if triple_quote_count % 2 == 1:
                in_multiline_string = not in_multiline_string
            if in_multiline_string or not stripped or stripped.startswith('#'):
                fixed_lines.append(line)
                continue
            if stripped.startswith(('else:', 'elif', 'except:', 'finally:')):
                indent_level = max(0, indent_level - 1)
            fixed_lines.append('    ' * indent_level + stripped)
            if stripped.endswith(':'):
                indent_level += 1
            if i + 1 < len(lines):
                next_stripped = lines[i + 1].strip()
                if next_stripped.startswith(('else:', 'elif', 'except:', 'finally:')):
                    indent_level = max(0, indent_level - 1)
        
        code = '\n'.join(fixed_lines)
        for keyword in ['with', 'if', 'for', 'while', 'try', 'except', 'else', 'elif', 'def', 'class']:
            pattern = rf'({keyword}\s*.+?:)\s*(\S+)'
            code = re.sub(pattern, r'\1\n    \2', code)
        return '\n'.join(line.rstrip() for line in code.split('\n'))

    def execute(self, code, description="执行代码"):
        """执行 Python 代码"""
        self._log(f"⚙️ 执行代码：{code[:300]}...")
        try:
            code = self._fix_ai_code(code)
            debug_file = os.path.join(CODE_SANDBOX_DIR, f"debug_{int(time.time())}.py")
            with open(debug_file, 'w', encoding='utf-8') as f:
                f.write(code)
            exec_env = {
                'os': os, 'json': json, 'time': time, 'random': __import__('random'),
                'string': __import__('string'), 'datetime': __import__('datetime').datetime,
                'subprocess': __import__('subprocess'), 'shutil': __import__('shutil'),
                'pathlib': __import__('pathlib').Path, 'pyautogui': __import__('pyautogui'),
                'requests': __import__('requests'), 'open': open, 'print': print,
                '__builtins__': __builtins__
            }
            exec(code, exec_env, {})
            action_logger.log("CODE_EXECUTE", code[:500], "成功")
            return {"success": True, "message": f"✅ {description}"}
        except SyntaxError as e:
            return {"success": False, "message": f"语法错误：{e} (行 {e.lineno})"}
        except Exception as e:
            return {"success": False, "message": f"执行失败：{e}"}

    def execute_action(self, action: str, params: Dict[str, Any]) -> Dict[str, Any]:
        return self._execute_basic_action(action, params)

    def _execute_task(self, task: Task) -> Dict[str, Any]:
        try:
            if isinstance(task, NovelWritingTask): return self._execute_novel_task(task)
            elif isinstance(task, ContentModificationTask): return self._execute_modification_task(task)
            else: return self._execute_general_task(task)
        except Exception as e:
            self._log(f"❌ 任务执行失败: {e}")
            return {"success": False, "message": f"任务执行失败: {e}"}

    def _execute_novel_task(self, task: NovelWritingTask) -> Dict[str, Any]:
        if task.action == 'create_outline': return self._create_novel_outline(task)
        elif task.action == 'create_chapter': return self._create_novel_chapter(task)
        elif task.action == 'create_summary': return self._create_novel_summary(task)
        else: return self._create_novel_content(task)

    def _execute_modification_task(self, task: ContentModificationTask) -> Dict[str, Any]:
        return self._modify_content(task)

    def _execute_general_task(self, task: Task) -> Dict[str, Any]:
        if task.action == 'create_content': return self._create_general_content(task)
        return {"success": False, "message": f"未知任务动作: {task.action}"}

    def _execute_basic_action(self, action: str, params: Dict[str, Any]) -> Dict[str, Any]:
        try:
            if action == 'get_time': return self._get_time()
            elif action == 'open_url': return self._open_url(params.get('url', 'https://www.bilibili.com'))
            elif action == 'read_file': return self._read_file(params.get('path', ''))
            elif action == 'create_file': return self._create_file(params.get('content', ''), params.get('filename', 'temp.txt'))
            elif action == 'get_weather': return self._get_weather()
            elif action == 'get_system_stats': return self._get_system_stats()
            else: return self._execute_plugin(action, params)
        except Exception as e:
            return {"success": False, "message": f"执行失败：{e}"}

    def _get_time(self):
        if self.time_service:
            time_str = self.time_service.get_beijing_time()
            return {"success": True, "message": f"当前北京时间：{time_str}", "time": time_str}
        return {"success": False, "message": "时间服务未初始化"}

    def _open_url(self, url):
        try:
            import webbrowser
            webbrowser.open(url)
            return {"success": True, "message": f"已在浏览器中打开：{url}"}
        except Exception as e:
            return {"success": False, "message": f"打开网页失败：{e}"}

    def _read_file(self, path):
        if not path or not os.path.exists(path):
            return {"success": False, "message": f"文件不存在：{path}"}
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            return {"success": True, "message": f"文件内容：{content[:500]}...", "content": content}
        except Exception as e:
            return {"success": False, "message": f"读取失败：{e}"}

    def _create_file(self, content, filename, task: Task = None):
        """创建文件 - 增强版，严格验证 + 拦截降级垃圾数据"""
        self._log(f"📝 开始创建文件：{filename}")
        try:
            if content is None or not isinstance(content, str):
                return {"success": False, "message": f"内容类型错误或为空：{type(content)}"}
            content = content.strip()
            if len(content) < 10:
                return {"success": False, "message": f"文件内容过短 ({len(content)} 字符)"}
            
            # 🌟 核心拦截：防止 LLM 调用失败时的“系统提示”被当成小说正文保存
            if content.startswith('[系统提示：LLM 调用失败') or content.startswith('[内容生成失败') or content.startswith('Error'):
                self._log(f"❌ 拦截垃圾数据：内容是错误/降级提示")
                return {"success": False, "message": "拦截到系统降级提示，拒绝保存为正式文件"}
            
            save_dir = self._determine_save_directory(filename, task)
            filepath = os.path.join(save_dir, filename)
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            
            if not os.access(os.path.dirname(filepath), os.W_OK):
                return {"success": False, "message": f"目录不可写：{os.path.dirname(filepath)}"}
            
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
            
            time.sleep(0.2)
            if not os.path.exists(filepath) or os.path.getsize(filepath) == 0:
                return {"success": False, "message": "文件写入后不存在或大小为 0"}
            
            file_size = os.path.getsize(filepath)
            self._log(f"✅ 文件创建成功：{filepath} ({file_size} 字节)")
            self._create_file_metadata(filepath, content)
            
            return {"success": True, "message": f"文件已保存到：{filepath}", "filepath": filepath, "content_length": len(content), "file_size": file_size}
        except Exception as e:
            self._log(f"❌ 创建失败：{e}")
            return {"success": False, "message": f"创建失败：{e}"}

    def _determine_save_directory(self, filename: str, task: Task = None) -> str:
        """🌟 解决关键词化限制：基于 Task 类型与宽泛关键词双重判断"""
        if task:
            if isinstance(task, NovelWritingTask):
                if 'outline' in task.action or '大纲' in filename: return self.outlines_dir
                return self.novels_dir
            elif isinstance(task, ResearchTask):
                return self.research_dir
                
        fname_lower = filename.lower()
        novel_kws = ['小说', '故事', '奇幻', '玄幻', '科幻', '武侠', '都市', 'chapter', 'novel', '第']
        outline_kws = ['大纲', '提纲', '概要', 'outline', 'summary']
        research_kws = ['研究', '调查', '分析', 'research', 'report']
        
        if any(kw in fname_lower for kw in novel_kws): return self.novels_dir
        if any(kw in fname_lower for kw in outline_kws): return self.outlines_dir
        if any(kw in fname_lower for kw in research_kws): return self.research_dir
        return self.save_dir

    def _create_file_metadata(self, filepath: str, content: str) -> None:
        metadata = {
            "filename": os.path.basename(filepath), "filepath": filepath, "content_length": len(content),
            "created_at": datetime.now().isoformat(), "file_size": os.path.getsize(filepath),
            "line_count": content.count('\n') + 1, "character_count": len(content)
        }
        try:
            with open(filepath + '.meta.json', 'w', encoding='utf-8') as f:
                json.dump(metadata, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self._log(f"⚠️ 创建元数据失败: {e}")

    def _get_weather(self):
        if self.plugin_manager and 'weather_plugin' in self.plugin_manager.plugins:
            return self.plugin_manager.plugins['weather_plugin'].execute('get_weather', {'city': 'current'})
        return {"success": False, "message": "天气插件未加载"}

    def _get_system_stats(self):
        try:
            import psutil
            return {"success": True, "message": f"CPU: {psutil.cpu_percent()}%, 内存: {psutil.virtual_memory().percent}%", 
                    "cpu_usage": psutil.cpu_percent(), "memory_usage": psutil.virtual_memory().percent}
        except:
            return {"success": False, "message": "无法获取系统统计"}

    def _execute_plugin(self, action, params):
        if self.plugin_manager: return self.plugin_manager.execute_action(action, params)
        return {"success": False, "message": "插件管理器未初始化"}

    def _create_novel_outline(self, task: NovelWritingTask) -> Dict[str, Any]:
        try:
            outline_content = self._generate_outline_content(task)
            filename = task.filename or f"{task.genre}_小说大纲_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            result = self._create_file(outline_content, filename, task)
            if result['success']:
                self._create_novel_project_structure(task, result['filepath'])
                result.update({'outline_length': len(outline_content), 'chapter_count': task.total_chapters, 'genre': task.genre})
            return result
        except Exception as e:
            return {"success": False, "message": f"创建大纲失败: {e}"}

    def _generate_outline_content(self, task: NovelWritingTask) -> str:
        """🌟 彻底消灭假模板：构建真实 Prompt 并调用 LLM"""
        raw_prompt = task.options.get('raw_user_prompt', task.user_prompt) if hasattr(task, 'options') and task.options else task.user_prompt
        raw_style = task.options.get('raw_style_description', task.writing_style) if hasattr(task, 'options') and task.options else task.writing_style
        
        prompt = f"""请创作一篇{task.genre}小说的详细大纲：
【用户原始需求（最高优先级）】
{raw_prompt}

【具体参数（仅作参考，允许根据剧情自然浮动）】
- 预估字数：{task.desired_length or '未指定'}字
- 预估章节数：{task.total_chapters}章
- 核心风格/基调：{raw_style}

大纲需要包含：故事梗概、主要角色、世界观设定、情节发展、章节划分。
请直接输出完整的大纲内容，不要包含任何解释性废话："""
        return self._call_llm_for_content(prompt, "大纲生成")

    def _create_novel_project_structure(self, task: NovelWritingTask, outline_path: str):
        project_name = os.path.splitext(os.path.basename(outline_path))[0].replace('_大纲', '')
        project_dir = os.path.join(self.novels_dir, project_name)
        for sub in ["章节", "角色", "设定", "笔记"]:
            os.makedirs(os.path.join(project_dir, sub), exist_ok=True)
        
        new_outline_path = os.path.join(project_dir, "作品大纲.txt")
        shutil.move(outline_path, new_outline_path)
        
        project_config = {
            "project_name": project_name, "genre": task.genre, "target_length": task.desired_length,
            "total_chapters": task.total_chapters, "writing_style": task.writing_style,
            "created_at": datetime.now().isoformat(), "outline_file": new_outline_path
        }
        with open(os.path.join(project_dir, "项目配置.json"), 'w', encoding='utf-8') as f:
            json.dump(project_config, f, indent=2, ensure_ascii=False)

    def _create_novel_chapter(self, task: NovelWritingTask) -> Dict[str, Any]:
        try:
            chapter_content = self._generate_chapter_content(task)
            filename = task.filename or f"第{task.chapter_index}章_{datetime.now().strftime('%H%M%S')}.txt"
            result = self._create_file(chapter_content, filename, task)
            if result['success']:
                result.update({'chapter_number': task.chapter_index, 'chapter_length': len(chapter_content), 'total_chapters': task.total_chapters})
                self._update_project_progress(task, result['filepath'])
            return result
        except Exception as e:
            return {"success": False, "message": f"创建章节失败: {e}"}

    def _generate_chapter_content(self, task: NovelWritingTask) -> str:
        """🌟 彻底消灭假模板：构建真实 Prompt 并调用 LLM"""
        raw_style = task.options.get('raw_style_description', task.writing_style) if hasattr(task, 'options') and task.options else task.writing_style
        prompt = f"""创作{task.genre}小说的第{task.chapter_index}章（共{task.total_chapters}章）：
【总体要求】
{task.user_prompt}

【本章具体要求】
- 建议字数：{task.desired_length or 2000}字左右（允许根据剧情张力自然浮动）
- 核心风格/基调：{raw_style}

本章需要：推动剧情、发展角色、保持连续性、包含冲突。
请直接输出本章完整的小说正文内容，绝对不要输出“本章内容开始”等占位符或解释性废话："""
        return self._call_llm_for_content(prompt, f"第{task.chapter_index}章生成")

    def _update_project_progress(self, task: NovelWritingTask, chapter_path: str):
        project_dir = self._find_project_directory(chapter_path)
        if not project_dir: return
        progress_file = os.path.join(project_dir, "创作进度.json")
        progress_data = {}
        if os.path.exists(progress_file):
            with open(progress_file, 'r', encoding='utf-8') as f: progress_data = json.load(f)
        
        chapters = progress_data.get('completed_chapters', [])
        chapters.append({"chapter_number": task.chapter_index, "filepath": chapter_path, "completed_at": datetime.now().isoformat()})
        progress_data['completed_chapters'] = chapters
        
        total = task.total_chapters
        completed = len(chapters)
        progress_data['progress'] = {"completed": completed, "total": total, "percent": (completed / total) * 100 if total > 0 else 0}
        
        with open(progress_file, 'w', encoding='utf-8') as f:
            json.dump(progress_data, f, indent=2, ensure_ascii=False)

    def _find_project_directory(self, filepath: str) -> Optional[str]:
        current_dir = os.path.dirname(filepath)
        for _ in range(3):
            if os.path.exists(os.path.join(current_dir, "项目配置.json")): return current_dir
            parent_dir = os.path.dirname(current_dir)
            if parent_dir == current_dir: break
            current_dir = parent_dir
        return None

    def _create_novel_summary(self, task: NovelWritingTask) -> Dict[str, Any]:
        try:
            summary_content = self._generate_summary_content(task)
            filename = task.filename or f"作品总结_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            result = self._create_file(summary_content, filename, task)
            if result['success']: result['summary_length'] = len(summary_content)
            return result
        except Exception as e:
            return {"success": False, "message": f"创建总结失败: {e}"}

    def _generate_summary_content(self, task: NovelWritingTask) -> str:
        prompt = f"""为以下作品创作总结：\n{task.user_prompt}\n要求：全面概括、分析角色、评价优缺点。字数500-1000字。\n请直接输出总结："""
        return self._call_llm_for_content(prompt, "总结生成")

    def _create_novel_content(self, task: NovelWritingTask) -> Dict[str, Any]:
        try:
            content = self._generate_novel_content(task)
            filename = task.filename or f"{task.genre}_内容_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            result = self._create_file(content, filename, task)
            if result['success']: result.update({'content_type': task.genre, 'word_count': len(content)})
            return result
        except Exception as e:
            return {"success": False, "message": f"创建内容失败: {e}"}

    def _generate_novel_content(self, task: NovelWritingTask) -> str:
        prompt = f"""创作{task.genre}作品：\n{task.user_prompt}\n风格：{task.writing_style}\n请直接输出完整内容："""
        return self._call_llm_for_content(prompt, "内容生成")

    def _modify_content(self, task: ContentModificationTask) -> Dict[str, Any]:
        try:
            modified_content = self._generate_modified_content(task)
            filename = task.filename or f"修改后_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            result = self._create_file(modified_content, filename, task)
            if result['success']:
                result.update({'modification_type': task.modification_type, 'original_length': len(task.original_content or ""), 'modified_length': len(modified_content)})
            return result
        except Exception as e:
            return {"success": False, "message": f"内容修改失败: {e}"}

    def _generate_modified_content(self, task: ContentModificationTask) -> str:
        prompt = f"""根据要求修改内容：\n原始内容：\n{task.original_content[:2000]}...\n修改要求：{task.user_prompt}\n类型：{task.modification_type}\n请输出修改后的完整内容："""
        return self._call_llm_for_content(prompt, "内容修改")

    def _create_general_content(self, task: Task) -> Dict[str, Any]:
        try:
            content = self._generate_general_content(task)
            filename = task.filename or f"内容_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            result = self._create_file(content, filename, task)
            if result['success']: result['content_length'] = len(content)
            return result
        except Exception as e:
            return {"success": False, "message": f"创建内容失败: {e}"}

    def _generate_general_content(self, task: Task) -> str:
        prompt = f"""根据要求创作内容：\n{task.user_prompt}\n请直接输出完整内容："""
        return self._call_llm_for_content(prompt, "通用内容生成")

    def get_file_info(self, filepath: str) -> Dict[str, Any]:
        try:
            if not os.path.exists(filepath): return {"success": False, "message": "文件不存在"}
            with open(filepath, 'r', encoding='utf-8') as f: content = f.read()
            return {"success": True, "filepath": filepath, "file_size": os.path.getsize(filepath), "content_length": len(content)}
        except Exception as e:
            return {"success": False, "message": f"获取文件信息失败: {e}"}

    def list_project_files(self, project_dir: str) -> Dict[str, Any]:
        try:
            if not os.path.exists(project_dir): return {"success": False, "message": "项目目录不存在"}
            files = [{"name": f, "path": os.path.join(r, f), "size": os.path.getsize(os.path.join(r, f))} 
                     for r, d, fs in os.walk(project_dir) for f in fs if not f.endswith('.meta.json')]
            return {"success": True, "project_dir": project_dir, "file_count": len(files), "files": files}
        except Exception as e:
            return {"success": False, "message": f"列出项目文件失败: {e}"}

    def backup_project(self, project_dir: str) -> Dict[str, Any]:
        try:
            if not os.path.exists(project_dir): return {"success": False, "message": "项目目录不存在"}
            backup_dir = project_dir + f"_备份_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            shutil.copytree(project_dir, backup_dir)
            return {"success": True, "original_dir": project_dir, "backup_dir": backup_dir}
        except Exception as e:
            return {"success": False, "message": f"项目备份失败: {e}"}

def create_file_with_metadata(content: str, filename: str, metadata: Dict[str, Any] = None) -> Dict[str, Any]:
    executor = ActionExecutor()
    result = executor._create_file(content, filename)
    if result['success'] and metadata:
        try:
            with open(result['filepath'] + '.meta.json', 'w', encoding='utf-8') as f:
                json.dump(metadata, f, indent=2, ensure_ascii=False)
        except Exception as e:
            executor._log(f"⚠️ 创建元数据失败: {e}")
    return result
