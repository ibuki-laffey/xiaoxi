# core/task_executor.py
import os
import json
import re
from datetime import datetime
from typing import Dict, Any, List, Optional

from .config import TASK_TRACKING_DIR, NOVEL_PROJECTS_DIR, KNOWLEDGE_BASE_DIR
from .task_definition import Task, NovelWritingTask, ContentModificationTask

class TaskExecutor:
    """任务执行器：协调子模块执行各类创作任务，包含剧情反哺设定钩子。"""
    
    def __init__(self, log_callback=None, controller=None):
        self.log_callback = log_callback
        self.controller = controller
        
        self.task_planner = None
        self.outline_manager = None
        self.chapter_generator = None
        self.quality_inspector = None
        self.progress_tracker = None
        self.error_handler = None
        self.context_builder = None
        self.continuation_analyzer = None
        self.reasoning_engine = None
        self.project_generator = None
        self.action_executor = None
        self.learning_engine = None
    
    def _log(self, msg: str, level: str = "INFO") -> None:
        if self.log_callback:
            try: self.log_callback(msg, level=level)
            except TypeError: self.log_callback(msg)
    
    def execute_intent(self, intent: Dict[str, Any], user_prompt: str) -> Dict[str, Any]:
        """根据意图执行相应操作。"""
        primary_intent = intent.get('primary_intent', 'chat')
        
        intent_handlers = {
            'project_creation': self._handle_project_creation,
            'outline_generation': self._handle_outline_generation,
            'outline_confirmation': self._handle_outline_confirmed,
            'batch_generation': self._handle_batch_generation,
            'continue_generation': self._handle_resume_task,
            'auto_generation': self._handle_auto_generation,
        }
        
        handler = intent_handlers.get(primary_intent)
        if handler: return handler(user_prompt)
        
        if self.continuation_analyzer and self.continuation_analyzer.is_continuation_task(user_prompt):
            return self._handle_continuation_task(user_prompt)
        
        if any(k in user_prompt for k in ['写小说', '创作', '生成', '写一篇']):
            return self._handle_creation_task(user_prompt)
        
        if hasattr(self, '_detect_actions'):
            detected_actions = self._detect_actions(user_prompt)
            if detected_actions and detected_actions != ['chat']:
                return self._handle_action_execution(user_prompt, detected_actions)
        
        return None
    
    def _handle_project_creation(self, user_prompt: str) -> Dict[str, Any]:
        """处理项目创建。"""
        self._log("📁 检测到项目创建请求...")
        if not self.reasoning_engine or not self.project_generator:
            return {"action": "chat", "explanation": "❌ 项目创建模块未初始化"}
        
        project_analysis = self.reasoning_engine.analyze_project_type(user_prompt)
        result = self.project_generator.create_novel_project(user_prompt, project_analysis)
        
        task_plan = None
        if result.get('success'):
            if self.task_planner:
                try:
                    task_plan = self.task_planner.parse_user_request(user_prompt)
                    task_plan['project_id'] = result.get('project_id')
                    self.task_planner.update_plan_status(task_plan['task_id'], 'planned', {'project_id': result.get('project_id'), 'outline_created': False})
                except Exception as e:
                    self._log(f"⚠️ 创建任务计划失败: {e}", level="WARN")
            
            return {
                "action": "project_created",
                "explanation": f"""✅ 项目模板已创建完成！
📁 项目位置：{result['project_dir']}
📋 已创建内容：项目说明、大纲模板、角色设定、前3章模板、配置文件。
💡 下一步建议：回复"生成大纲"开始创作大纲。""",
                "project_dir": result['project_dir'],
                "project_info": result,
                "task_id": task_plan['task_id'] if task_plan else None
            }
        return {"action": "chat", "explanation": f"❌ 项目创建失败：{result.get('error', '未知错误')}"}
    
    def _handle_outline_generation(self, user_prompt: str) -> Dict[str, Any]:
        """处理大纲生成。"""
        self._log("📋 检测到大纲生成指令...")
        if not self.task_planner or not self.outline_manager:
            return {"action": "chat", "explanation": "❌ 大纲模块未初始化"}
        
        pending_tasks = self.task_planner.list_pending_tasks()
        if not pending_tasks:
            if self.progress_tracker:
                pending_progress = self.progress_tracker.list_pending_tasks()
                if pending_progress: task_plan = pending_progress[0]
                else: return {"action": "chat", "explanation": "❌ 未找到待处理的任务，请先创建项目"}
            else: return {"action": "chat", "explanation": "❌ 未找到待处理的任务，请先创建项目"}
        else:
            task_plan = pending_tasks[0]
        
        task_status = task_plan.get('status', 'planned')
        task_id = task_plan.get('task_id', '')
        
        if task_status == 'outline_generated':
            outline_data = self.outline_manager.get_outline(task_id)
            if outline_data:
                return {"action": "chat", "explanation": "📋 大纲已生成完毕，请查看项目文件夹中的大纲文件。\n\n回复\"确认大纲\"开始正文创作。"}
        
        outline_res = self.outline_manager.generate_outline(task_plan)
        
        if outline_res.get('success'):
            # 🌟 核心修复：增加 isinstance 防弹检查
            outline_data = outline_res.get('outline', outline_res) if isinstance(outline_res, dict) else {}
            title = outline_data.get('title', outline_res.get('title', '')) if isinstance(outline_data, dict) else ''
            genre = outline_data.get('genre', outline_res.get('genre', '')) if isinstance(outline_data, dict) else ''
            chapters = outline_data.get('chapters', outline_data.get('chapter_outlines', outline_res.get('chapter_outlines', []))) if isinstance(outline_data, dict) else []
            
            self.task_planner.update_plan_status(task_id, 'outline_generated', {'outline_id': title, 'outline_generated_at': datetime.now().isoformat()})
            
            return {
                "action": "chat",
                "explanation": f"""📋 大纲已生成完成！
📖 书名：{title}
📑 章节数：{len(chapters) if chapters else 0}
🏷️ 题材：{genre}
请查看文件确认无误后，回复"确认大纲"开始生成正文。""",
                "outline": outline_res,
                "task_id": task_id
            }
        return {"action": "chat", "explanation": f"❌ 大纲生成失败：{outline_res.get('error', '未知错误')}"}
    
    def _handle_outline_confirmed(self, user_prompt: str) -> Dict[str, Any]:
        """处理大纲确认。"""
        self._log("✅ 大纲已确认，开始生成正文...")
        if not self.task_planner or not self.outline_manager:
            return {"action": "chat", "explanation": "❌ 模块未初始化"}
        
        pending_tasks = self.task_planner.list_pending_tasks()
        if not pending_tasks: return {"action": "chat", "explanation": "❌ 未找到待确认的任务"}
        
        task_plan = None
        for task in pending_tasks:
            if task.get('status') in ['outline_generated', 'outline_confirmed']:
                task_plan = task
                break
        
        if not task_plan: return {"action": "chat", "explanation": "❌ 未找到待确认的大纲（状态应为 outline_generated）"}
        
        outline_data = self.outline_manager.get_outline(task_plan['task_id'])
        if not outline_data: return {"action": "chat", "explanation": "❌ 大纲数据不存在，请先生成大纲"}
        
        task_id = task_plan['task_id']
        self.task_planner.update_plan_status(task_id, 'in_progress', {'outline_confirmed_at': datetime.now().isoformat()})
        self.outline_manager.confirm_outline(task_id)
        
        generation_mode = task_plan.get('generation_mode', '逐章')
        batch_size = task_plan.get('batch_size', 1)
        
        if generation_mode == '分批':
            end_chapter = min(batch_size, task_plan.get('total_chapters', 1))
            return self._generate_chapters_batch(task_plan, 1, end_chapter)
        else:
            return self._generate_chapters_batch(task_plan, 1, 1)
    
    def _handle_batch_generation(self, user_prompt: str) -> Dict[str, Any]:
        """处理批量生成。"""
        self._log("📋 智能识别：批量生成指令")
        if self.task_planner:
            pending_tasks = self.task_planner.list_pending_tasks()
            if pending_tasks:
                task_plan = pending_tasks[0]
                task_plan['generation_mode'] = '一次性'
                task_plan['batch_size'] = task_plan.get('total_chapters', 1)
                self.task_planner._save_plan(task_plan)
        return self._handle_outline_confirmed(user_prompt)
    
    def _handle_resume_task(self, user_prompt: str) -> Dict[str, Any]:
        """处理继续生成任务。"""
        self._log("▶️ 检测到继续生成指令...")
        if not self.progress_tracker or not self.task_planner:
            return {"action": "chat", "explanation": "❌ 模块未初始化"}
        
        pending_tasks = self.progress_tracker.list_pending_tasks()
        if not pending_tasks: return {"action": "chat", "explanation": "❌ 未找到未完成的任务"}
        
        progress = pending_tasks[0]
        task_id = progress.get('task_id', '')
        task_plan = self.task_planner.get_plan(task_id)
        if not task_plan: return {"action": "chat", "explanation": "❌ 任务计划不存在"}
        
        next_chapter = self.progress_tracker.get_next_chapter(task_id)
        
        # 🌟 核心修复：优化正则，精准区分“生成5章”（批量）和“生成第5章”（单章）
        batch_match = re.search(r'生成\s*(\d+)\s*章', user_prompt)
        if batch_match:
            match_start = batch_match.start()
            prefix = user_prompt[max(0, match_start-2):match_start]
            if '第' not in prefix:
                batch_size = int(batch_match.group(1))
                end_chapter = min(next_chapter + batch_size - 1, task_plan.get('total_chapters', 1))
            else:
                end_chapter = next_chapter
        else:
            end_chapter = next_chapter
        
        return self._generate_chapters_batch(task_plan, next_chapter, end_chapter)
    
    def _handle_auto_generation(self, user_prompt: str) -> Dict[str, Any]:
        """处理全自动生成请求。"""
        self._log("🚀 检测到全自动生成请求...")
        if not self.controller: return {"action": "chat", "explanation": "❌ 控制器未初始化"}
        if not self.controller.current_project_id:
            return {"action": "chat", "explanation": "❌ 请先创建项目后再使用全自动生成功能"}
        
        self.controller.auto_generation_mode = True
        return {"action": "auto_generation_started", "explanation": "✅ 全自动生成已启动，将在后台持续创作，完成后向您汇报。", "project_id": self.controller.current_project_id}
    
    def _handle_continuation_task(self, user_prompt: str) -> Dict[str, Any]:
        """处理续写任务。"""
        self._log("📖 检测到续写任务...")
        if not self.continuation_analyzer: return {"action": "chat", "explanation": "❌ 续写分析器未初始化"}
        
        file_path = self.continuation_analyzer.extract_file_path(user_prompt)
        if not file_path: return {"action": "chat", "explanation": "❌ 未找到小说文件路径"}
        
        analysis = self.continuation_analyzer.analyze_novel_file(file_path)
        if not analysis.get('success'): return {"action": "chat", "explanation": f"❌ 原作分析失败：{analysis.get('error', '')}"}
        
        if not self.task_planner: return {"action": "chat", "explanation": "❌ 任务规划器未初始化"}
        
        task_plan = self.task_planner.parse_user_request(user_prompt)
        task_plan['is_continuation'] = True
        task_plan['continuation_guide'] = analysis.get('continuation_guide', {})
        
        if self.progress_tracker: self.progress_tracker.create_progress_record(task_plan)
        start_chapter = analysis.get('chapter_count', 0) + 1
        
        return {
            "action": "chat",
            "explanation": f"📖 续写任务已创建\n\n原作：{os.path.basename(file_path)}\n原作章节：{analysis.get('chapter_count', 0)}章\n续写从：第{start_chapter}章开始",
            "task_id": task_plan['task_id'],
            "continuation_guide": analysis.get('continuation_guide', {})
        }
    
    def _handle_creation_task(self, user_prompt: str) -> Dict[str, Any]:
        """处理创作任务。"""
        self._log("📝 检测到创作任务...")
        if not self.task_planner or not self.outline_manager:
            return {"action": "chat", "explanation": "❌ 模块未初始化"}
        
        task_plan = self.task_planner.parse_user_request(user_prompt)
        if self.task_planner: self.task_planner.generate_quality_rules(task_plan)
        if self.progress_tracker: self.progress_tracker.create_progress_record(task_plan)
        
        outline_res = self.outline_manager.generate_outline(task_plan)
        
        if self.task_planner:
            self.task_planner.update_plan_status(task_plan['task_id'], 'outline_confirmed', {'outline_id': outline_res.get('title', '') if isinstance(outline_res, dict) else ''})
        
        # 🌟 核心修复：增加 isinstance 防弹检查
        outline_data = outline_res.get('outline', outline_res) if isinstance(outline_res, dict) else {}
        chapters = outline_data.get('chapters', outline_data.get('chapter_outlines', outline_res.get('chapter_outlines', []))) if isinstance(outline_data, dict) else []
        
        return {
            "action": "outline_review",
            "explanation": f"""📋 大纲已生成，请确认后再开始正文创作。
📖 书名：{outline_data.get('title', outline_res.get('title', '')) if isinstance(outline_data, dict) else ''}
📑 章节数：{len(chapters) if chapters else 0}
🏷️ 题材：{outline_data.get('genre', outline_res.get('genre', '')) if isinstance(outline_data, dict) else ''}
回复"继续"或"确认大纲"开始生成。""",
            "outline": outline_res,
            "task_id": task_plan['task_id']
        }
    
    def _generate_chapters_batch(self, task_plan: Dict[str, Any], start_chapter: int, end_chapter: int) -> Dict[str, Any]:
        """批量生成章节（带反馈纠错重试机制 + 剧情反哺设定钩子）。"""
        if not self.chapter_generator or not self.progress_tracker:
            return {"action": "chat", "explanation": "❌ 章节生成模块未初始化"}
        
        task_id = task_plan['task_id']
        results = []
        self._log(f"📚 开始生成：第{start_chapter}章 至 第{end_chapter}章")
        
        for chapter_num in range(start_chapter, end_chapter + 1):
            result = self.chapter_generator.generate_chapter(task_id=task_id, chapter_num=chapter_num, task_plan=task_plan, continuation_guide=None)
            
            if result.get('success'):
                if self.quality_inspector:
                    inspection = self.quality_inspector.inspect_chapter(content=result.get('content', ''), chapter_num=chapter_num, quality_rules=task_plan.get('quality_rules', {}), task_plan=task_plan)
                    
                    rewrite_count = 0
                    # 🌟 核心修复：增加 inspection 类型防弹检查，防止 AttributeError
                    while inspection and isinstance(inspection, dict) and not inspection.get('passed') and rewrite_count < 3 and result.get('success'):
                        self._log(f"⚠️ 第{chapter_num}章质检未通过，带反馈重试第{rewrite_count+1}次...", level="WARN")
                        
                        feedback = inspection.get('feedback') or inspection.get('suggestions') or "请根据上一版的问题进行实质性修改，提升画面感和逻辑。"
                        
                        result = self.chapter_generator.generate_chapter(
                            task_id=task_id, chapter_num=chapter_num, task_plan=task_plan, 
                            continuation_guide={"rewrite_feedback": feedback}
                        )
                        if not result.get('success'): break
                        
                        inspection = self.quality_inspector.inspect_chapter(content=result.get('content', ''), chapter_num=chapter_num, quality_rules=task_plan.get('quality_rules', {}), task_plan=task_plan)
                        rewrite_count += 1
                
                self.progress_tracker.update_progress(task_id=task_id, chapter_num=chapter_num, success=True, chapter_summary=result.get('summary', ''), filepath=result.get('filepath', ''))
                
                # 🌟 核心新增：剧情反哺设定（后置钩子）
                self._extract_and_update_lore(task_id, chapter_num, result.get('content', ''))
                
                results.append({'chapter': chapter_num, 'success': True})
            else:
                if self.error_handler:
                    self.error_handler.record_error(task_id=task_id, chapter_num=chapter_num, error_type='generation_failed', error_message=result.get('error', ''))
                if self.progress_tracker:
                    self.progress_tracker.update_progress(task_id=task_id, chapter_num=chapter_num, success=False, error=result.get('error', ''))
                results.append({'chapter': chapter_num, 'success': False})
        
        success_count = sum(1 for r in results if r.get('success'))
        
        if end_chapter >= task_plan.get('total_chapters', 1):
            if self.progress_tracker: self.progress_tracker.pause_task(task_id)
            return {"action": "chat", "explanation": f"🎉 小说生成完成！\n\n总章节：{task_plan.get('total_chapters')}章\n成功：{success_count}章", "task_id": task_id, "status": "completed"}
        else:
            next_chapter = end_chapter + 1
            return {"action": "chat", "explanation": f"✅ 已生成第{start_chapter}-{end_chapter}章（成功{success_count}章）\n\n回复'继续生成'写第{next_chapter}章。", "task_id": task_id, "status": "in_progress", "next_chapter": next_chapter}

    # --------------------------------------------------------------------------
    # 🌟 核心新增：剧情反哺设定（后置钩子）
    # --------------------------------------------------------------------------
    def _extract_and_update_lore(self, task_id: str, chapter_num: int, content: str):
        """自动提取本章变化并更新 DeepLoreExpander 设定卡。"""
        if not self.controller or not hasattr(self.controller, 'deep_lore_expander'):
            return
            
        expander = self.controller.deep_lore_expander
        if not expander:
            return

        self._log(f"🔄 [LoreUpdate] 正在提取第{chapter_num}章的剧情演变并反哺设定...")
        
        prompt = f"""你是严谨的网文设定管理员。请分析以下第{chapter_num}章的正文内容，提取其中发生的“角色状态变化”和“世界观设定变化”。
【本章正文】：
{content[:3000]}...

【提取要求】：
1. 角色变化：主角或配角获得了新武器/新能力、性格发生了重大转变、与其他角色的关系破裂或建立。
2. 设定变化：解锁了新地图/新区域、某个组织发生了内乱/覆灭、出现了新的核心物品/功法。
3. 如果没有明显变化，请返回空数组。

输出严格的 JSON 对象：
{{
  "character_updates": [
    {{"name": "角色名", "type": "装备获取/性格转变/关系变化/能力觉醒", "content": "具体变化描述(50字内)"}}
  ],
  "setting_updates": [
    {{"concept": "设定/物品/地点名称", "type": "区域解锁/组织变动/物品升级/新设定出现", "content": "具体变化描述(50字内)"}}
  ]
}}
只输出纯 JSON 对象！"""

        try:
            from .config import call_ollama_safe
            
            resp = call_ollama_safe(
                [{"role": "user", "content": prompt}],
                options={"num_predict": 1000, "temperature": 0.2, "format": "json"}
            )
            raw = resp.get("content", "") if isinstance(resp, dict) else str(resp)
            
            # 防弹提取 JSON
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            if not match: return
            
            json_str = re.sub(r",\s*([}\]])", r"\1", match.group())
            data = json.loads(json_str)
            
            # 更新角色
            for char_upd in data.get("character_updates", []):
                name = char_upd.get("name")
                if name:
                    expander.update_character_with_plot(
                        name=name, chapter_num=chapter_num, 
                        update_type=char_upd.get("type", "剧情演变"), 
                        content=char_upd.get("content", "")
                    )
                    
            # 更新设定
            for set_upd in data.get("setting_updates", []):
                concept = set_upd.get("concept")
                if concept:
                    expander.update_setting_with_plot(
                        concept=concept, chapter_num=chapter_num,
                        update_type=set_upd.get("type", "剧情演变"),
                        content=set_upd.get("content", "")
                    )
                    
            self._log(f"✅ [LoreUpdate] 第{chapter_num}章设定反哺完成！")
        except Exception as e:
            self._log(f"⚠️ [LoreUpdate] 提取或更新设定失败: {e}", level="WARN")
    
    def _handle_action_execution(self, user_prompt: str, detected_actions: List[str]) -> Dict[str, Any]:
        """处理动作执行。"""
        if not self.action_executor: return {"action": "chat", "explanation": "❌ 动作执行器未初始化"}
        
        all_results, failed_actions = [], []
        for detected_action in detected_actions:
            if detected_action == 'chat': continue
            operation_result = self.action_executor.execute_action(detected_action, {})
            if operation_result.get("success"): all_results.append((detected_action, operation_result))
            else: failed_actions.append({'action': detected_action, 'reason': operation_result.get('message', '未知错误')})
        
        if all_results and not failed_actions:
            return {"action": "chat", "explanation": self._generate_multi_response(all_results), "need_confirm": False}
        elif all_results and failed_actions:
            return {"action": "chat", "explanation": f"{self._generate_multi_response(all_results)}\n\n部分操作失败", "need_confirm": False}
        else:
            return {"action": "chat", "explanation": "操作失败", "need_confirm": False}
    
    def _handle_learning_request(self, user_prompt: str) -> Dict[str, Any]:
        """处理学习请求。"""
        self._log("📚 触发主动学习程序")
        if not self.learning_engine: return {"action": "chat", "explanation": "❌ 学习引擎未初始化"}
        
        knowledge_gap = self.learning_engine.identify_knowledge_gap(user_prompt, {"success": False, "message": "用户要求学习"})
        resources = self.learning_engine.find_learning_resources(knowledge_gap)
        method = self.learning_engine.select_learning_method(knowledge_gap, resources)
        learning_result = self.learning_engine.execute_learning(knowledge_gap, method, resources)
        
        return {"action": "chat", "explanation": f"✅ 学习完成：{learning_result.get('message', '已学习相关知识')}", "need_confirm": False}
    
    def _generate_multi_response(self, results: List) -> str:
        """生成多动作回复。"""
        lines = []
        for act, res in results:
            if act == 'get_weather': lines.append(f"🌤️ 天气：{res.get('message', '处理中')}")
            elif act == 'get_time': lines.append(f"⏰ 时间：{res.get('time', '处理中')}")
            elif act == 'create_file': lines.append(f"📄 文件：{res.get('filepath', '处理中')}")
            else: lines.append(f"✅ {act}: {res.get('message', '')}")
        return "\n\n".join(lines)
