import re
import json
from typing import List, Dict, Any
from datetime import datetime

try:
    from .task_definition import Task, NovelWritingTask, ContentModificationTask, ResearchTask, AnalysisTask, LearningTask, CreativeTask
except ImportError:
    from .task_definition import Task
    NovelWritingTask = Task
    ContentModificationTask = Task
    ResearchTask = Task
    AnalysisTask = Task
    LearningTask = Task
    CreativeTask = Task

class PromptAnalyzer:
    """自然语言提示词分析器（引入软约束与原始语境透传）"""
    
    def __init__(self):
        # 保留原有的关键词字典，仅用于“粗粒度路由”和“标签化”，绝不作为强限制
        self.creative_keywords = {
            'novel': ['小说', '故事', '长篇', '短篇', '中篇', '奇幻', '玄幻', '科幻', '武侠', '都市'],
            'modification': ['修改', '调整', '改写', '重写', '优化', '改进', '润色'],
            'research': ['研究', '学习', '了解', '调查', '探索', '查资料'],
            'analysis': ['分析', '评估', '评价', '评论', '解读', '解析'],
            'learning': ['学习', '掌握', '练习', '训练', '提高'],
            'creative': ['创意', '灵感', '发明', '创造', '设计', '构想']
        }
        
        self.length_patterns = [
            (r'(\d+)万字', lambda m: int(m.group(1)) * 10000),
            (r'(\d+)千字', lambda m: int(m.group(1)) * 1000),
            (r'(\d+)字', lambda m: int(m.group(1))),
            (r'(\d+)\s*章节', lambda m: int(m.group(1))),
            (r'第(\d+)章', lambda m: int(m.group(1))),
            (r'(\d+)\s*章', lambda m: int(m.group(1)))
        ]
        
        self.style_keywords = {
            '轻松': ['轻松', '幽默', '搞笑', '愉快', '欢乐'],
            '严肃': ['严肃', '沉重', '深刻', '正经', '庄重'],
            '黑暗': ['黑暗', '压抑', '恐怖', '惊悚', '残酷'],
            '浪漫': ['浪漫', '爱情', '温馨', '甜蜜', '感人'],
            '史诗': ['史诗', '宏大', '壮丽', '磅礴', '雄伟'],
            '简洁': ['简洁', '精炼', '简明', '直接', '干脆']
        }

    def analyze_prompt(self, user_prompt: str) -> Dict[str, Any]:
        """深度分析用户提示词，并强制保留原始语境"""
        return {
            'task_type': self._detect_task_type(user_prompt),
            'length_requirements': self._extract_length_requirements(user_prompt),
            'style_preferences': self._extract_style_preferences(user_prompt),
            'raw_style_description': self._extract_raw_style_description(user_prompt), # 🌟 新增：提取风格原话
            'content_constraints': self._extract_content_constraints(user_prompt),
            'structural_requirements': self._extract_structural_requirements(user_prompt),
            'quality_requirements': self._extract_quality_requirements(user_prompt),
            'contextual_elements': self._extract_contextual_elements(user_prompt),
            'urgency_level': self._detect_urgency(user_prompt),
            'complexity_level': self._assess_complexity(user_prompt),
            'raw_user_prompt': user_prompt # 🌟 核心防阉割：强制透传原始 Prompt
        }

    def _detect_task_type(self, prompt: str) -> str:
        prompt_lower = prompt.lower()
        for task_type, keywords in self.creative_keywords.items():
            if any(keyword in prompt_lower for keyword in keywords):
                return task_type
        return 'novel'

    def _extract_length_requirements(self, prompt: str) -> Dict[str, Any]:
        """提取长度要求（作为软约束参考）"""
        requirements = {'total_words': None, 'words_per_chapter': None, 'total_chapters': None, 'min_words': None, 'max_words': None}
        for pattern, converter in self.length_patterns:
            for match in re.finditer(pattern, prompt):
                value = converter(match)
                if '章' in match.group(0) and '字' not in match.group(0):
                    requirements['total_chapters'] = value
                else:
                    requirements['total_words'] = value
        for match in re.finditer(r'每章[约]?(\d+)字', prompt):
            requirements['words_per_chapter'] = int(match.group(1))
        for match in re.finditer(r'(\d+)[-~](\d+)字', prompt):
            requirements['min_words'] = int(match.group(1))
            requirements['max_words'] = int(match.group(2))
        return requirements

    def _extract_style_preferences(self, prompt: str) -> List[str]:
        """提取风格标签（仅用于路由）"""
        styles = []
        prompt_lower = prompt.lower()
        for style_name, keywords in self.style_keywords.items():
            if any(keyword in prompt_lower for keyword in keywords):
                styles.append(style_name)
        styles.extend(re.findall(r'风格[：:]\s*([^，。！？]+)', prompt))
        return styles

    # 🌟 核心创新：提取用户关于风格的“完整原话”，防止信息被字典阉割
    def _extract_raw_style_description(self, prompt: str) -> str:
        """提取风格描述的原始句子，保留 LLM 创作所需的丰富细节"""
        # 匹配“风格：xxx”、“要求xxx风格”、“基调是xxx”等长句
        patterns = [
            r'(?:风格|基调|文风|语调|氛围)[：:是为]?\s*([^。！？\n]{2,50})',
            r'(?:要求|希望|需要)[^。！？\n]*?(?:风格|文风|感觉)[^。！？\n]*?([^\n]{2,50})'
        ]
        for pat in patterns:
            match = re.search(pat, prompt)
            if match:
                return match.group(1).strip()
        
        # 如果没匹配到特定句式，但提示词较长，截取前 100 字作为语境参考
        if len(prompt) > 20:
            return prompt[:100]
        return ""

    def _extract_content_constraints(self, prompt: str) -> Dict[str, Any]:
        constraints = {'required_elements': [], 'prohibited_elements': [], 'character_requirements': [], 'setting_requirements': [], 'plot_requirements': []}
        constraints['required_elements'].extend(re.findall(r'要?包含[：:]\s*([^，。！？]+)', prompt))
        constraints['prohibited_elements'].extend(re.findall(r'不要[：:]\s*([^，。！？]+)', prompt))
        constraints['character_requirements'].extend(re.findall(r'角色[：:]\s*([^，。！？]+)', prompt))
        constraints['setting_requirements'].extend(re.findall(r'场景[：:]\s*([^，。！？]+)', prompt))
        constraints['plot_requirements'].extend(re.findall(r'剧情[：:]\s*([^，。！？]+)', prompt))
        return constraints

    def _extract_structural_requirements(self, prompt: str) -> Dict[str, Any]:
        structure = {'require_outline': False, 'require_chapters': False, 'require_summary': False, 'require_notes': False, 'file_format': 'txt'}
        prompt_lower = prompt.lower()
        structure['require_outline'] = any(word in prompt_lower for word in ['大纲', '提纲', '概要'])
        structure['require_chapters'] = any(word in prompt_lower for word in ['分章', '章节']) or bool(re.search(r'第.*章', prompt_lower))
        structure['require_summary'] = any(word in prompt_lower for word in ['总结', '摘要', '概要'])
        structure['require_notes'] = any(word in prompt_lower for word in ['注释', '备注', '说明'])
        format_matches = re.findall(r'\.(txt|md|docx|pdf)', prompt_lower)
        if format_matches: structure['file_format'] = format_matches[0]
        return structure

    def _extract_quality_requirements(self, prompt: str) -> Dict[str, Any]:
        quality = {'readability_level': '普通', 'originality_required': '中等', 'detail_level': '适中', 'accuracy_required': '普通', 'completeness_required': '完整'}
        prompt_lower = prompt.lower()
        if any(word in prompt_lower for word in ['易懂', '简单', '通俗']): quality['readability_level'] = '简单'
        elif any(word in prompt_lower for word in ['专业', '学术', '深度']): quality['readability_level'] = '专业'
        if any(word in prompt_lower for word in ['原创', '新颖', '独特']): quality['originality_required'] = '高'
        if any(word in prompt_lower for word in ['详细', '细致', '具体']): quality['detail_level'] = '详细'
        elif any(word in prompt_lower for word in ['简洁', '简要', '概括']): quality['detail_level'] = '简洁'
        return quality

    def _extract_contextual_elements(self, prompt: str) -> Dict[str, Any]:
        context = {'previous_references': [], 'character_references': [], 'location_references': [], 'event_references': [], 'theme_references': []}
        context['previous_references'].extend(re.findall(r'之前?的?[：:]\s*([^，。！？]+)', prompt))
        context['character_references'].extend(re.findall(r'角色[「\"\']([^」\"\']+)[」\"\']', prompt))
        context['location_references'].extend(re.findall(r'地点[「\"\']([^」\"\']+)[」\"\']', prompt))
        context['event_references'].extend(re.findall(r'事件[「\"\']([^」\"\']+)[」\"\']', prompt))
        return context

    def _detect_urgency(self, prompt: str) -> str:
        prompt_lower = prompt.lower()
        if any(word in prompt_lower for word in ['紧急', '马上', '立刻']): return '高'
        elif any(word in prompt_lower for word in ['尽快', '早点', '及时']): return '中'
        return '低'

    def _assess_complexity(self, prompt: str) -> str:
        word_count = len(prompt)
        requirement_count = len(re.findall(r'[，。；！？]', prompt))
        if word_count > 200 or requirement_count > 5: return '高'
        elif word_count > 100 or requirement_count > 3: return '中'
        return '低'

    def _extract_keywords(self, text: str) -> List[str]:
        words = re.findall(r'[\u4e00-\u9fa5]{2,4}', text)
        return list(set(words))[:3]


class TaskGenerator:
    """任务生成器（注入软约束与语境透传）"""
    
    def __init__(self):
        self.analyzer = PromptAnalyzer()
    
    def _safe_create_task(self, task_class, **kwargs):
        try:
            return task_class(**kwargs)
        except TypeError:
            base_kwargs = {k: v for k, v in kwargs.items() if k in ['action', 'user_prompt', 'filename', 'desired_length']}
            return Task(**base_kwargs)

    def prompt_to_tasks(self, user_prompt: str, conversation_history: List[Dict] = None) -> List[Task]:
        analysis = self.analyzer.analyze_prompt(user_prompt)
        task_type = analysis['task_type']
        
        if task_type == 'novel': return self._create_novel_tasks(user_prompt, analysis)
        elif task_type == 'modification': return self._create_modification_tasks(user_prompt, analysis)
        elif task_type == 'research': return self._create_research_tasks(user_prompt, analysis)
        elif task_type == 'analysis': return self._create_analysis_tasks(user_prompt, analysis)
        elif task_type == 'learning': return self._create_learning_tasks(user_prompt, analysis)
        elif task_type == 'creative': return self._create_creative_tasks(user_prompt, analysis)
        return self._create_general_tasks(user_prompt, analysis)

    def _create_novel_tasks(self, user_prompt: str, analysis: Dict[str, Any]) -> List[Task]:
        tasks = []
        length_req = analysis['length_requirements']
        total_chapters = length_req.get('total_chapters') or 1
        words_per_chapter = length_req.get('words_per_chapter')
        total_words = length_req.get('total_words') or 0
        
        if not words_per_chapter and total_words > 0 and total_chapters > 1:
            words_per_chapter = total_words // total_chapters
            
        # 🌟 核心防阉割：优先使用“风格原话”，而不是干瘪的标签
        raw_style = analysis.get('raw_style_description', '')
        style_label = analysis['style_preferences'][0] if analysis['style_preferences'] else '自然流畅'
        genre = self._extract_genre(user_prompt)
        
        # 🌟 注入软约束选项：告诉下游 LLM 这些参数只是“建议”
        soft_options = {
            'strict_mode': False,  # 允许 LLM 根据剧情自然浮动字数
            'raw_user_prompt': user_prompt,
            'raw_style_description': raw_style
        }
        
        if analysis['structural_requirements'].get('require_outline', False):
            tasks.append(self._safe_create_task(
                NovelWritingTask, action='create_outline', user_prompt=user_prompt,
                filename=self._generate_filename(user_prompt, 'outline'),
                desired_length=analysis['structural_requirements'].get('outline_length', 500),
                genre=genre, require_outline=False, chapter_titles=self._generate_chapter_titles(total_chapters), 
                writing_style=raw_style if raw_style else style_label,
                options=soft_options
            ))
            
        for chapter_idx in range(1, total_chapters + 1):
            chapter_prompt = self._create_chapter_prompt(user_prompt, chapter_idx, total_chapters, analysis)
            tasks.append(self._safe_create_task(
                NovelWritingTask, action='create_chapter', user_prompt=chapter_prompt,
                filename=self._generate_filename(user_prompt, f'chapter_{chapter_idx}'),
                desired_length=words_per_chapter, total_chapters=total_chapters, chapter_index=chapter_idx, 
                genre=genre, writing_style=raw_style if raw_style else style_label,
                options=soft_options
            ))
            
        if analysis['structural_requirements'].get('require_summary', False):
            tasks.append(self._safe_create_task(Task, action='create_summary', user_prompt=user_prompt, filename=self._generate_filename(user_prompt, 'summary'), options=soft_options))
            
        return tasks

    def _create_modification_tasks(self, user_prompt: str, analysis: Dict[str, Any]) -> List[Task]:
        return [self._safe_create_task(
            ContentModificationTask, action='modify_content', user_prompt=user_prompt,
            modification_type=analysis.get('modification_type', 'general'), original_content=self._extract_content_to_modify(user_prompt),
            modification_scope=self._determine_modification_scope(user_prompt), preserve_elements=analysis['content_constraints'].get('required_elements', []),
            options={'strict_mode': False, 'raw_user_prompt': user_prompt}
        )]

    def _create_research_tasks(self, user_prompt: str, analysis: Dict[str, Any]) -> List[Task]:
        return [self._safe_create_task(
            ResearchTask, action='conduct_research', user_prompt=user_prompt, research_topic=self._extract_research_topic(user_prompt),
            depth=analysis['quality_requirements'].get('readability_level', '基础'), output_format=self._determine_research_output_format(user_prompt),
            options={'raw_user_prompt': user_prompt}
        )]

    def _create_analysis_tasks(self, user_prompt: str, analysis: Dict[str, Any]) -> List[Task]:
        return [self._safe_create_task(
            AnalysisTask, action='analyze_content', user_prompt=user_prompt, analysis_type=self._determine_analysis_type(user_prompt),
            target_content=self._extract_analysis_target(user_prompt), analysis_dimensions=analysis['content_constraints'].get('required_elements', []),
            options={'raw_user_prompt': user_prompt}
        )]

    def _create_learning_tasks(self, user_prompt: str, analysis: Dict[str, Any]) -> List[Task]:
        return [self._safe_create_task(
            LearningTask, action='learn_topic', user_prompt=user_prompt, learning_objective=self._extract_learning_objective(user_prompt),
            desired_level=analysis['quality_requirements'].get('completeness_required', '熟练掌握'), learning_method=self._determine_learning_method(user_prompt),
            options={'raw_user_prompt': user_prompt}
        )]

    def _create_creative_tasks(self, user_prompt: str, analysis: Dict[str, Any]) -> List[Task]:
        return [self._safe_create_task(
            CreativeTask, action='generate_ideas', user_prompt=user_prompt, creativity_type=self._determine_creativity_type(user_prompt),
            innovation_level=analysis['quality_requirements'].get('originality_required', '中等'),
            options={'raw_user_prompt': user_prompt}
        )]

    def _create_general_tasks(self, user_prompt: str, analysis: Dict[str, Any]) -> List[Task]:
        return [self._safe_create_task(
            Task, action='create_content', user_prompt=user_prompt, filename=self._generate_filename(user_prompt),
            desired_length=analysis['length_requirements'].get('total_words'), 
            options={'strict_mode': False, 'raw_user_prompt': user_prompt, 'style': analysis['style_preferences'], 'constraints': analysis['content_constraints']}
        )]

    def _generate_filename(self, user_prompt: str, suffix: str = None) -> str:
        keywords = self.analyzer._extract_keywords(user_prompt)
        base_name = '_'.join(keywords) if keywords else 'content'
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        return f"{base_name}_{suffix}_{timestamp}.txt" if suffix else f"{base_name}_{timestamp}.txt"

    def _extract_genre(self, prompt: str) -> str:
        genre_keywords = {'奇幻': ['奇幻', '魔法', '龙', '精灵'], '玄幻': ['玄幻', '修仙', '修真', '真气'], '科幻': ['科幻', '太空', '未来', '科技'], '武侠': ['武侠', '江湖', '武功', '门派'], '都市': ['都市', '现代', '城市', '现实']}
        prompt_lower = prompt.lower()
        for genre, keywords in genre_keywords.items():
            if any(keyword in prompt_lower for keyword in keywords): return genre
        return '奇幻'

    def _generate_chapter_titles(self, total_chapters: int) -> List[str]:
        return [f"第{i}章" for i in range(1, total_chapters + 1)] if total_chapters > 1 else []

    def _create_chapter_prompt(self, base_prompt: str, chapter_num: int, total_chapters: int, analysis: Dict[str, Any]) -> str:
        context_note = "开头章节，需要建立世界观和引入主角" if chapter_num == 1 else ("最终章，需要圆满结束故事" if chapter_num == total_chapters else "中间章节，承上启下")
        
        # 🌟 核心防阉割：在 Prompt 中同时注入“标签”和“用户原话”
        style_label = analysis['style_preferences'][0] if analysis['style_preferences'] else '自然流畅'
        raw_style = analysis.get('raw_style_description', '')
        style_instruction = f"符合整体风格标签：{style_label}"
        if raw_style:
            style_instruction += f"\n- 深度贯彻用户的具体风格要求：{raw_style}"
            
        return f"{base_prompt}\n\n【章节信息】\n这是第{chapter_num}/{total_chapters}章 - {context_note}\n\n【写作要求】\n- 保持与前后章节的连续性\n- 推动剧情发展\n- 发展角色关系\n- {style_instruction}\n\n请创作本章内容："

    def _extract_content_to_modify(self, prompt: str) -> str: return ""
    def _determine_modification_scope(self, prompt: str) -> str: return '部分' if ('部分' in prompt or '某些' in prompt) else '全部'
    def _extract_research_topic(self, prompt: str) -> str:
        matches = re.findall(r'研究[：:]\s*([^，。！？]+)', prompt)
        return matches[0] if matches else prompt[:100]
    def _determine_research_output_format(self, prompt: str) -> str: return '详细报告' if ('详细' in prompt or '全面' in prompt) else '总结'
    def _extract_analysis_target(self, prompt: str) -> str:
        matches = re.findall(r'分析[：:]\s*([^，。！？]+)', prompt)
        return matches[0] if matches else prompt[:100]
    def _determine_analysis_type(self, prompt: str) -> str:
        if '结构' in prompt: return '结构分析'
        elif '内容' in prompt: return '内容分析'
        elif '风格' in prompt: return '风格分析'
        return '综合分析'
    def _extract_learning_objective(self, prompt: str) -> str:
        matches = re.findall(r'学习[：:]\s*([^，。！？]+)', prompt)
        return matches[0] if matches else prompt[:100]
    def _determine_learning_method(self, prompt: str) -> str:
        if '实践' in prompt or '练习' in prompt: return '实践练习'
        elif '理论' in prompt or '知识' in prompt: return '理论学习'
        return '综合学习'
    def _extract_creative_objective(self, prompt: str) -> str:
        matches = re.findall(r'创意[：:]\s*([^，。！？]+)', prompt)
        return matches[0] if matches else prompt[:100]
    def _determine_creativity_type(self, prompt: str) -> str:
        if '改进' in prompt: return '改进'
        elif '组合' in prompt: return '组合创新'
        return '原创'

def prompt_to_tasks(user_prompt: str, conversation_history: List[Dict] = None) -> List[Task]:
    return TaskGenerator().prompt_to_tasks(user_prompt, conversation_history)

def analyze_prompt(user_prompt: str) -> Dict[str, Any]:
    return PromptAnalyzer().analyze_prompt(user_prompt)
