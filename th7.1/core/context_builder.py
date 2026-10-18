# core/context_builder.py
# ==================== 上下文构建器（完整修复版 V5.0）====================
# 功能：管理章节上下文，智能裁决设定揭露，注入角色动态演变，防幻觉锁定

import os
import json
import re
import math
from datetime import datetime
from typing import Dict, Any, List, Optional

from .config import TASK_TRACKING_DIR, MAX_OUTPUT_TOKENS

def _atomic_write_json(filepath: str, data: Any):
    """工业级原子写入 JSON。"""
    tmp = filepath + ".tmp"
    try:
        dir_name = os.path.dirname(filepath)
        if dir_name: os.makedirs(dir_name, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp, filepath)
    except Exception:
        if os.path.exists(tmp):
            try: os.remove(tmp)
            except Exception: pass
        raise

def _robust_read_json(filepath: str) -> Optional[Any]:
    """防弹读取 JSON。"""
    if not os.path.exists(filepath): return None
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception: return None

class ContextBuilder:
    """上下文构建器：智能裁决设定、注入角色演变、防幻觉锁定。"""

    def __init__(self, log_callback=None, progress_tracker=None):
        self.log_callback = log_callback
        self.progress_tracker = progress_tracker
        self.context_cache_dir = os.path.join(TASK_TRACKING_DIR, "context_cache")
        os.makedirs(self.context_cache_dir, exist_ok=True)
        
        self.disk_cache = None
        self.world_state = None
        self.task_planner = None
        
        self._log("✅ 上下文构建器已初始化（V5.0 智能裁决 + 角色演变）")
    
    def _log(self, msg: str, level: str = "INFO"):
        if self.log_callback:
            try: self.log_callback(msg, level=level)
            except TypeError: self.log_callback(msg)

    def build_chapter_context(self, task_id: str, current_chapter: int) -> Dict[str, Any]:
        """构建章节生成上下文（核心入口）。"""
        import os
        import json 
        
        self._log(f"🧠 构建第{current_chapter}章上下文...")
        
        context = {
            'task_id': task_id, 'current_chapter': current_chapter,
            'previous_summaries': [], 'characters': [], 'world_setting': '',
            'last_chapter_key_events': [], 'outline_context': '',
            'chapter_outline': [], 'previous_context': ''
        }
        
        # 1. 获取前文摘要
        if self.progress_tracker:
            summaries = self.progress_tracker.get_chapter_summaries(task_id, up_to_chapter=current_chapter)
            context['previous_summaries'] = summaries or []
            self._log(f"   加载前文摘要：{len(context['previous_summaries'])}章")
            if context['previous_summaries']:
                context['last_chapter_key_events'] = context['previous_summaries'][-1].get('key_events', [])

        # 2. 🌟 终极防弹修复：5层穿透获取真实的 project_dir，绝不降级为当前目录
        project_dir = ""
        
        # 层1：从 ContextBuilder 自身的 task_plan 获取
        if hasattr(self, 'task_plan') and isinstance(self.task_plan, dict):
            project_dir = self.task_plan.get("project_dir", "")
            
        # 层2：从 AIController 获取 (如果持有引用)
        if not project_dir and hasattr(self, 'ai_controller') and self.ai_controller:
            project_dir = getattr(self.ai_controller, 'current_project_dir_path', '') or getattr(self.ai_controller, 'current_project_dir', '')
            
        # 层3：从 TaskPlanner 获取
        if not project_dir and hasattr(self, 'task_planner') and self.task_planner:
            plan = self.task_planner.get_plan(task_id) if hasattr(self.task_planner, 'get_plan') else {}
            project_dir = plan.get("project_dir", "") if isinstance(plan, dict) else ""
            
        # 层4：从 ProgressTracker 获取
        if not project_dir and hasattr(self, 'progress_tracker') and self.progress_tracker:
            progress = self.progress_tracker.get_progress(task_id)
            if progress and isinstance(progress, dict):
                project_dir = progress.get("project_dir", "")

        # 层5：🌟 终极兜底：通过 TaskPlanner 获取真实的 project_id，再用 _get_project_dir 解析
        if not project_dir:
            project_id = None
            if hasattr(self, 'task_planner') and self.task_planner:
                plan = self.task_planner.get_plan(task_id) if hasattr(self.task_planner, 'get_plan') else {}
                if isinstance(plan, dict):
                    project_id = plan.get("project_id")
            
            if not project_id:
                project_id = getattr(self.world_state, 'current_project_id', None)
                
            if project_id:
                project_dir = self._get_project_dir(str(project_id)) or ""

        # 层6：🌟 终极内存穿透（当磁盘路径全部失效时，直接从 AIController 的内存缓存中读取）
        if not project_dir and hasattr(self, 'ai_controller') and hasattr(self.ai_controller, '_current_active_task_plan'):
            active_plan = self.ai_controller._current_active_task_plan
            if isinstance(active_plan, dict):
                project_dir = active_plan.get("project_dir", "")
                if project_dir:
                    self._log("💡 [Context] 穿透获取成功：从 AIController 内存缓存中读取到 project_dir。")
                    # 🌟 致命修复：顺便把内存中的核心设定直接注入到 task_plan 中
                    if hasattr(self, 'task_plan') and isinstance(self.task_plan, dict):
                        for key in ["core_details", "forbidden_settings", "dynamic_identities", "writing_style", "narrative_pov"]:
                            if active_plan.get(key) and not self.task_plan.get(key):
                                self.task_plan[key] = active_plan[key]

        if not project_dir:
            self._log("❌ 致命错误：穷尽所有途径仍无法获取真实 project_dir，上下文构建可能残缺！", level="ERROR")


        chapter_outline_data, previous_context_text = self._load_current_volume_outline(project_dir, current_chapter)

        
        context['chapter_outline'] = chapter_outline_data
        summaries_text = "\n".join([f"第{s.get('chapter')}章: {s.get('summary', '')}" for s in context['previous_summaries']])
        context['previous_context'] = previous_context_text + "【前情提要】\n" + summaries_text
        
        # 🌟 核心缝合：注入里程碑记忆锚点
        try:
            from .milestone_tracker import MilestoneTracker
            if project_dir and os.path.isdir(project_dir):
                tracker = MilestoneTracker(project_dir, log_callback=self.log_callback if hasattr(self, 'log_callback') else None)
                anchors = tracker.get_context_anchors(current_chapter)
                if anchors:
                    context['previous_context'] += "\n\n" + anchors
        except Exception as e:
            if hasattr(self, '_log'): self._log(f"⚠️ 里程碑锚点注入失败: {e}", level="WARN")
            
        if chapter_outline_data:
            self._log(f"   📖 已懒加载当前卷大纲 ({len(chapter_outline_data)} 章节点)")

        # 3. 两阶段设定注入
        lore_context = ""
        try:
            if project_dir:
                index_path = os.path.join(project_dir, "03_设定", "lore_index.json")
                if os.path.exists(index_path):
                    with open(index_path, 'r', encoding='utf-8') as f:
                        lore_index = json.load(f)
                    index_text = "【📇 全局设定索引（仅摘要，用于宏观参考）】\n"
                    for cat, items in lore_index.items():
                        if items:
                            index_text += f"- {cat}: " + ", ".join([f"{i['name']}({i.get('brief', '')[:20]})" for i in items[:20]]) + "\n"
                    lore_context += index_text + "\n"
        except Exception as e:
            if hasattr(self, '_log'): self._log(f"⚠️ 设定索引读取失败: {e}", level="WARN")

        current_outline_text = json.dumps(chapter_outline_data, ensure_ascii=False) if chapter_outline_data else summaries_text[-500:]
        world_setting_text = self._load_and_filter_settings(project_dir, current_outline_text, current_chapter) if project_dir else ""
        context['world_setting'] = lore_context + world_setting_text

        # 🌟🌟🌟 核心新增：设定智能重组 (Setting Reorganizer) - 用 LLM 思考当前章节需要哪些设定 🌟🌟🌟
        core_details = self.task_plan.get("core_details", []) if hasattr(self, 'task_plan') and isinstance(self.task_plan, dict) else []
        if core_details and hasattr(self, 'ai_controller') and hasattr(self.ai_controller, '_retrieve_relevant_settings'):
            # 使用当前章大纲和前文摘要作为上下文，精准召回最相关的 15 条设定
            recall_context = current_outline_text + summaries_text[-1000:]
            core_details = self.ai_controller._retrieve_relevant_settings(recall_context, core_details, top_k=15)
        if core_details and chapter_outline_data:
            self._log("🧠 [设定重组] 启动 LLM 思考当前章节需要激活哪些核心设定...")
            reorganize_prompt = f"""你是小说设定调度专家。
【当前章节大纲】：{json.dumps(chapter_outline_data, ensure_ascii=False)}
【全局核心设定池】：{json.dumps(core_details, ensure_ascii=False)}

【任务】：
1. 思考并筛选出“当前章节”在写作时**必须用到**或**需要特别注意**的核心设定（如：主角的附身能力规则、当前的心理状态、特定的禁忌等）。
2. 将筛选出的设定转化为“写作指导指令”，告诉作者在本章应该如何运用这些设定。
3. 绝对不要输出无关的设定！

输出 JSON：{{"activated_guidelines": ["指导1：在本章中，主角使用附身能力时必须表现出...", "指导2：注意描写被附身者失去意识时的生理反应..."]}}
只输出纯 JSON！"""
            try:
                from .llm_utils import call_ollama_json
                reorg_resp = call_ollama_json([{"role": "user", "content": reorganize_prompt}], temperature=0.2, enforce_json=True)
                if isinstance(reorg_resp, dict) and reorg_resp.get("activated_guidelines"):
                    guidelines = reorg_resp["activated_guidelines"]
                    guideline_text = "\n\n【🎯 本章专属设定激活与写作指导（LLM深度思考结果）】\n"
                    for idx, g in enumerate(guidelines):
                        guideline_text += f"{idx+1}. {g}\n"
                    context['world_setting'] += guideline_text
                    self._log(f"✅ [设定重组] 成功生成 {len(guidelines)} 条本章专属写作指导。")
            except Exception as e:
                self._log(f"⚠️ [设定重组] LLM 调用失败: {e}", level="WARN")

        # 4. 智能组装角色上下文
        char_names = self._extract_mentioned_characters(context['previous_summaries'], chapter_outline_data)
        char_context_text = self._load_and_filter_characters(project_dir, char_names, current_outline_text) if project_dir else ""
        context['characters'] = char_names
        if char_context_text:
            context['world_setting'] += "\n\n【登场角色深度档案】\n" + char_context_text

        # 5. 全局核心设定基准与防幻觉协议
        _global_main_char = "主角"
        _global_genre = "通用"
        if self.task_planner:
            _plan = self.task_planner.get_plan(task_id) if hasattr(self.task_planner, 'get_plan') else {}
            _global_main_char = _plan.get("main_character", _global_main_char) or _global_main_char
            _global_genre = _plan.get("genre", _global_genre) or _global_genre
            
        _global_anchor = (
            f"\n\n【🔒 全局核心设定基准与防幻觉协议】\n"
            f"1. 当前状态档案中，主角名字为：【{_global_main_char}】，核心题材为：【{_global_genre}】。\n"
            f"2. 【防幻觉铁律】：除非用户在最近的对话中**明确要求**更改名字或引入新角色，否则你必须**严格且只**使用【{_global_main_char}】！\n"
            f"3. 绝对禁止自行脑补、替换为“林凡”、“叶凡”、“萧炎”等任何网文套路名字！遇到不知道名字的角色，用“那人”、“神秘人”代称！\n"
            f"4. 严格遵循上述【世界观设定】与【角色档案】中的“揭露逻辑约束”，绝对禁止在正文中直接抛出未满足条件的深层设定（Info-dumping）！\n"
        )
        context['world_setting'] += _global_anchor
        context['previous_context'] += _global_anchor

        # 6. 缓存上下文
        self._cache_context(task_id, current_chapter, context)
        return context



    def _load_and_filter_settings(self, project_dir: str, current_outline_text: str, current_chapter: int) -> str:
        """🌟 核心：读取分层设定，剔除冰山，LLM裁决揭露约束，只注入表层/里层。"""
        if not project_dir: return ""
        
        settings_file = os.path.join(project_dir, "03_设定", "分层世界观设定.json")
        all_settings = _robust_read_json(settings_file)
        if not all_settings or not isinstance(all_settings, list): return ""
        
        candidate_settings = []
        for s in all_settings:
            setting_type = s.get("setting_type", "base")
            evo_log = s.get("evolution_log", [])
            
            # 过滤未发生的剧情设定
            if setting_type == "plot" and not evo_log:
                continue
                
            layers = s.get("layers", [])
            valid_layers = [l for l in layers if l.get("layer_id") != "iceberg"] # 物理剔除冰山
            
            if valid_layers:
                candidate_settings.append({
                    "concept": s.get("concept"),
                    "setting_type": setting_type,
                    "layers": valid_layers,
                    "evo_log": evo_log
                })
        
        if not candidate_settings: return ""
        
        # LLM 裁决：判断哪些设定的 reveal_constraints 被当前大纲满足
        revealed_concepts = self._evaluate_setting_reveal(candidate_settings, current_outline_text)
        
        # 组装文本
        setting_texts = []
        for s in candidate_settings:
            concept = s["concept"]
            # 如果 LLM 裁决通过，或者没有约束（默认揭露），则注入
            if concept in revealed_concepts or not any(l.get("reveal_constraints") for l in s["layers"]):
                setting_texts.append(f"### {concept} [{s['setting_type'].upper()}]")
                for layer in s["layers"]:
                    setting_texts.append(f"- **{layer.get('layer_name', '')}**: {layer.get('content', '')}")
                    # 注入演变日志
                    for log in s.get("evo_log", []):
                        setting_texts.append(f"  - *[第{log.get('chapter')}章演变]* {log.get('content')}")
                setting_texts.append("")
                
        return "\n".join(setting_texts)

    def _evaluate_setting_reveal(self, candidate_settings: List[Dict], outline_text: str) -> List[str]:
        """调用 LLM 判断当前大纲是否满足设定的揭露逻辑约束。"""
        constraints_map = {}
        for s in candidate_settings:
            for l in s["layers"]:
                c = l.get("reveal_constraints", "")
                if c:
                    constraints_map[s["concept"]] = c
                    
        if not constraints_map:
            return [s["concept"] for s in candidate_settings]
            
        prompt = f"""你是严谨的网文主编。请判断以下【当前章节大纲/前情】是否满足了【设定揭露约束】。
【当前大纲/前情】：
{outline_text[:1500]}

【设定揭露约束】：
{json.dumps(constraints_map, ensure_ascii=False)}

请输出一个 JSON 数组，包含所有**已经满足揭露条件**的设定名称（concept）。如果都不满足，返回空数组 []。
只输出纯 JSON 数组！"""
        
        try:
            from .config import call_ollama_safe
            resp = call_ollama_safe(
                [{"role": "user", "content": prompt}],
                options={"num_predict": 1000, "temperature": 0.2, "format": "json"}
            )
            raw = resp.get("content", "") if isinstance(resp, dict) else str(resp)
            # 简单提取数组
            match = re.search(r'\[.*?\]', raw, re.DOTALL)
            if match:
                return json.loads(match.group())
        except Exception as e:
            self._log(f"⚠️ 设定揭露裁决失败，降级为全量注入: {e}", level="WARN")
            
        return list(constraints_map.keys())

    def _format_context_summary(self, context: Dict[str, Any]) -> str:
        """格式化上下文摘要（防弹兼容版）"""
        if not context or not isinstance(context, dict): return "无前情提要"
        summaries = []
        for s in context.get("previous_summaries", [])[-3:]:
            summaries.append(f"- 第{s.get('chapter', '?')}章: {s.get('summary', '')[:150]}...")
        for summary in context.get('recent_full_text', [])[-3:]:
            summaries.append(f"- {summary[:200]}...")
        for card in context.get('card_summaries', [])[-5:]:
            summaries.append(f"- 第{card.get('chapter', 0)}章：{card.get('summary', '')[:100]}...")
        return "\n".join(summaries) if summaries else "第一章，无前情提要"

    def _load_and_filter_characters(self, project_dir: str, char_names: List[str], outline_text: str) -> str:
        """🌟 核心：读取角色 JSON 卡，组装关系链、秘密分层与演变日志。"""
        if not project_dir or not char_names: return ""
        
        char_dir = os.path.join(project_dir, "03_设定", "角色卡")
        char_texts = []
        
        for name in char_names:
            safe_name = re.sub(r'[\\/*?:"<>|]', "", name) or "未知"
            json_path = os.path.join(char_dir, f"角色卡_{safe_name}.json")
            data = _robust_read_json(json_path)
            
            if not data:
                # Fallback: 尝试读取旧版 MD 尾部
                devs = self._read_character_developments_tail(project_dir, name, max_entries=2)
                if devs:
                    char_texts.append(f"### {name} (近期演变)\n" + "\n".join([f"- [第{d['chapter']}章] {d['description']}" for d in devs]))
                continue
                
            char_texts.append(f"### {name} ({data.get('role', '')})")
            char_texts.append(f"- **外貌/气质**: {data.get('appearance', '')}")
            char_texts.append(f"- **性格**: {', '.join(data.get('personality_traits', []))}")
            
            # 关系链
            rels = data.get("relationships", [])
            if rels:
                rel_str = " | ".join([f"{r.get('target')}({r.get('relation')})" for r in rels[:3]])
                char_texts.append(f"- **核心关系**: {rel_str}")
                
            # 秘密分层（默认只注入面具，如果大纲提及真相关键词则注入真相）
            sec = data.get("secret_layers", {})
            char_texts.append(f"- **公众面具**: {sec.get('public_mask', '')}")
            hidden = sec.get("hidden_truth", "")
            if hidden and any(kw in outline_text for kw in name.split()):
                char_texts.append(f"- **隐藏真相**: {hidden}")
                
            # 演变日志
            evo = data.get("evolution_log", [])
            if evo:
                for log in evo[-3:]:
                    char_texts.append(f"- *[第{log.get('chapter')}章 {log.get('type')}]* {log.get('content')}")
                    
            char_texts.append("")
            
        return "\n".join(char_texts)

    def _extract_mentioned_characters(self, summaries: List[Dict], outline: List[Dict]) -> List[str]:
        """从摘要和大纲中提取提及的角色名（简单启发式）。"""
        names = set()
        text = json.dumps(summaries + outline, ensure_ascii=False)
        # 简单提取中文名字（2-4字）
        matches = re.findall(r'[\u4e00-\u9fa5]{2,4}', text)
        # 过滤掉常见非人名词汇
        stop_words = ["主角", "反派", "配角", "路人", "宗门", "家族", "帝国", "世界", "魔法", "功法"]
        for m in matches:
            if m not in stop_words:
                names.add(m)
        return list(names)[:10] # 限制数量防 Token 爆炸

    def _get_project_dir(self, project_id: str) -> Optional[str]:
        """获取项目目录。🌟 增强版：支持通过真实的 project_id 或 task_id 反推物理路径。"""
        if not project_id:
            return None
            
        # 1. 尝试自定义实现
        if hasattr(self, '_get_project_dir_impl'):
            res = self._get_project_dir_impl(project_id)
            if res: return res
            
        # 2. 🌟 核心修复：如果传入的 project_id 实际上是 task_id (以 task_ 开头)，通过 TaskPlanner 反查真实的 project_dir
        if str(project_id).startswith("task_"):
            try:
                if hasattr(self, 'task_planner') and self.task_planner:
                    plan = self.task_planner.get_plan(project_id) if hasattr(self.task_planner, 'get_plan') else {}
                    if isinstance(plan, dict) and plan.get("project_dir"):
                        real_dir = plan["project_dir"]
                        if os.path.isdir(real_dir):
                            return real_dir
            except Exception:
                pass
            
        # 3. 使用 ProjectPathManager 解析真实的 project_id (如 1e6b13f33858)
        if not str(project_id).startswith("task_"):
            try:
                from .project_path_manager import ProjectPathManager
                if ProjectPathManager and hasattr(ProjectPathManager, 'resolve_project_dir'):
                    resolved = ProjectPathManager.resolve_project_dir(str(project_id))
                    if resolved and resolved != "invalid_project":
                        base_dir = getattr(self, 'base_dir', None)
                        if not base_dir:
                            try:
                                from . import config
                                base_dir = getattr(config, 'BASE_DIR', os.getcwd())
                            except Exception:
                                base_dir = os.getcwd()
                                
                        full_path = os.path.join(base_dir, "novel_projects", resolved) if not os.path.isabs(resolved) else resolved
                        if os.path.isdir(full_path):
                            return full_path
            except Exception:
                pass
            
        # 4. 尝试 paths 模块
        try:
            from .paths import get_project_dir
            res = get_project_dir(project_id)
            if res and os.path.isdir(res): return res
        except Exception:
            pass
            
        # 5. 最后的物理路径兜底
        if hasattr(self, 'world_state') and hasattr(self.world_state, 'state_dir'):
            fallback = os.path.join(os.path.dirname(self.world_state.state_dir), "novel_projects", str(project_id))
            if os.path.isdir(fallback):
                return fallback
                
        return None

    def _load_current_volume_outline(self, project_dir: str, current_chapter: int) -> tuple:
        """懒加载大纲：全书主干 + 当前卷近期节点。"""
        if not project_dir: return [], ""
        
        from . import config
        outline_dir = os.path.join(project_dir, getattr(config, 'OUTLINE_DIR_NAME', '01_大纲'))
        chapter_outline_data = []
        previous_context_text = ""
        
        # 1. 全局主干
        master_file = os.path.join(outline_dir, "master_outline.json")
        if os.path.exists(master_file):
            master = _robust_read_json(master_file)
            if master:
                previous_context_text += f"【全书核心主线】\n核心冲突: {master.get('core_conflict', '')}\n最终结局: {master.get('climax_and_ending', '')}\n\n"
                
        # 2. 当前卷
        chapters_per_vol = getattr(config, 'CHAPTERS_PER_VOLUME', 25)
        vol_idx = math.ceil(current_chapter / chapters_per_vol) if chapters_per_vol > 0 else 1
        vol_file = os.path.join(outline_dir, f"volume_{vol_idx:02d}_outline.json")
        
        if os.path.exists(vol_file):
            vol_data = _robust_read_json(vol_file)
            if isinstance(vol_data, list):
                chapter_outline_data = [c for c in vol_data if abs(c.get('chapter_num', 0) - current_chapter) <= 2]
                
        return chapter_outline_data, previous_context_text

    def _read_character_developments_tail(self, project_dir: str, character_name: str, max_entries: int = 3) -> List[Dict]:
        """兼容旧版：从 MD 角色卡尾部读取演变日志。"""
        if not project_dir: return []
        char_dir = os.path.join(project_dir, "03_设定", "角色卡")
        safe_name = re.sub(r'[\\/*?:"<>|]', "", character_name)
        if not safe_name: return []
        
        card_path = os.path.join(char_dir, f"角色卡_{safe_name}.md")
        if not os.path.isfile(card_path): return []
        
        try:
            with open(card_path, 'rb') as f:
                f.seek(0, os.SEEK_END)
                read_size = min(2000, f.tell())
                f.seek(-read_size, os.SEEK_END)
                tail_text = f.read(read_size).decode('utf-8', errors='ignore')
                
            pattern = r'=== \[(.*?)\] 第(\d+)章 ===\n(.*?)\n\n'
            matches = re.findall(pattern, tail_text, re.DOTALL)
            recent = matches[-max_entries:] if len(matches) > max_entries else matches
            return [{'timestamp': t, 'chapter': int(c), 'description': d} for t, c, d in reversed(recent)]
        except Exception:
            return []

    def build_continuation_context(self, continuation_guide: Dict, generated_chapters: List[Dict]) -> Dict:
        """构建续写任务的上下文。"""
        context = {
            'source_novel': continuation_guide.get('source_novel', ''),
            'last_chapter_summary': continuation_guide.get('last_chapter_summary', ''),
            'last_1000_words': continuation_guide.get('last_1000_words', ''),
            'characters': continuation_guide.get('characters', []),
            'world_setting': continuation_guide.get('world_setting', ''),
            'generated_chapters': [{'chapter': c.get('chapter'), 'summary': c.get('summary')} for c in generated_chapters]
        }
        return context

    def get_previous_chapter_summary(self, task_id: str, current_chapter: int) -> Optional[str]:
        if not self.progress_tracker: return None
        summaries = self.progress_tracker.get_chapter_summaries(task_id, up_to_chapter=current_chapter)
        return summaries[-1].get('summary', '') if summaries else None

    def _cache_context(self, task_id: str, chapter_num: int, context: Dict):
        cache_file = os.path.join(self.context_cache_dir, f"context_{task_id}_ch{chapter_num}.json")
        _atomic_write_json(cache_file, context)

    def get_cached_context(self, task_id: str, chapter_num: int) -> Optional[Dict]:
        cache_file = os.path.join(self.context_cache_dir, f"context_{task_id}_ch{chapter_num}.json")
        return _robust_read_json(cache_file)

    def clear_context_cache(self, task_id: str) -> int:
        cleared = 0
        for filename in os.listdir(self.context_cache_dir):
            if filename.startswith(f"context_{task_id}_"):
                try: os.remove(os.path.join(self.context_cache_dir, filename)); cleared += 1
                except Exception: pass
        self._log(f"🧹 清理了{cleared}条上下文缓存")
        return cleared

    def build_summary_for_chapter(self, content: str, chapter_num: int) -> str:
        return content[:500] + "..." if len(content) > 500 else content

    def estimate_context_size(self, context: Dict) -> int:
        total = 0
        for v in context.values():
            if isinstance(v, str): total += len(v)
            elif isinstance(v, list): total += sum(len(json.dumps(i, ensure_ascii=False)) if isinstance(i, dict) else len(i) for i in v)
        return total

    def extract_key_events(self, content: str, chapter_num: int) -> List[str]: return []
    def extract_character_mentions(self, content: str) -> List[str]: return []

    def build_character_context(self, task_id: str, character_name: str) -> Dict:
        context = {'character_name': character_name, 'appearances': [], 'development': []}
        if self.progress_tracker:
            for s in self.progress_tracker.get_chapter_summaries(task_id):
                if character_name in s.get('summary', ''):
                    context['appearances'].append({'chapter': s.get('chapter'), 'summary': s.get('summary')})
        return context

    def build_world_setting_context(self, task_id: str) -> Dict:
        return {'settings': self.world_state.get_settings() if self.world_state else [], 'revealed_levels': []}
