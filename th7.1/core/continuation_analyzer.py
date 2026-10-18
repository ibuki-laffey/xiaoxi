import os
import json
import re
from datetime import datetime
from typing import Dict, Any, List, Optional
from .config import KNOWLEDGE_BASE_DIR, TASK_TRACKING_DIR, OLLAMA_URL, MODEL_NAME, SAFE_TIMEOUT

class ContinuationAnalyzer:
    """续写分析器：分析已有小说，提取关键信息供续写使用。"""
    
    def __init__(self, log_callback=None, model_client=None):
        self.log_callback = log_callback
        self.model_client = model_client
        self.analysis_cache_dir = os.path.join(TASK_TRACKING_DIR, "continuation_analysis")
        os.makedirs(self.analysis_cache_dir, exist_ok=True)
        self._log("✅ 续写分析器已初始化（完整修复版 + 角色关系 + 剧情脉络）")
    
    def _log(self, msg):
        if self.log_callback: self.log_callback(msg)
    
    def analyze_novel_file(self, file_path: str) -> Dict[str, Any]:
        """分析已有小说文件，分块读取避免内存溢出。"""
        self._log(f"📖 开始分析原作：{file_path}")
        if not os.path.exists(file_path):
            return {"success": False, "error": "文件不存在"}
        
        try:
            file_size = os.path.getsize(file_path)
            self._log(f"   文件大小：{file_size / 1024 / 1024:.2f} MB")
            
            analysis = {"success": True, "file_path": file_path, "total_size": file_size, "analyzed_at": datetime.now().isoformat()}
            analysis.update(self._analyze_file_beginning(file_path))
            analysis.update(self._analyze_file_ending(file_path))
            analysis.update(self._detect_chapter_structure(file_path))
            analysis.update(self._extract_characters_and_setting(file_path))
            analysis.update(self._extract_character_relationships(file_path))
            analysis.update(self._analyze_plot_structure(file_path))
            analysis['continuation_guide'] = self._generate_continuation_guide(analysis)
            self._cache_analysis(analysis, file_path)
            
            self._log(f"✅ 原作分析完成 | 题材：{analysis.get('genre', '未知')} | 章节数：{analysis.get('chapter_count', '未知')} | 角色数：{len(analysis.get('characters', []))}")
            return analysis
        except Exception as e:
            self._log(f"❌ 原作分析失败：{e}")
            return {"success": False, "error": str(e)}
    
    def _analyze_file_beginning(self, file_path: str) -> Dict[str, Any]:
        """分析文件开头（题材、风格）"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                beginning = f.read(10000)
            
            prompt = f"""分析以下小说开头的内容：
{beginning[:5000]}...
请返回 JSON：
{{"genre": "题材", "writing_style": "写作风格描述", "narrative_perspective": "叙事视角", "time_period": "时代背景", "main_location": "主要地点", "tone": "整体基调"}}"""
            
            result = self._call_llm_for_json(prompt)
            return result if result else {}
        except Exception:
            return {}
    
    def _analyze_file_ending(self, file_path: str) -> Dict[str, Any]:
        """分析文件结尾（用于续写衔接），修复字节截断乱码问题。"""
        try:
            with open(file_path, 'rb') as f:
                f.seek(0, os.SEEK_END)
                file_size = f.tell()
                read_size = min(20000, file_size)
                if file_size > 0:
                    f.seek(-read_size, os.SEEK_END)
                raw_bytes = f.read()
            ending = raw_bytes.decode('utf-8', errors='ignore')
            
            prompt = f"""总结以下小说结尾部分的情节：
{ending[:5000]}...
请返回 JSON：
{{"last_event": "最后发生的关键事件", "character_status": "主要角色当前状态", "pending_threads": ["未解决的悬念"], "story_direction": "故事当前走向"}}"""
            
            result = self._call_llm_for_json(prompt)
            if result:
                return {
                    'last_chapter_summary': result.get('last_event', ''),
                    'character_status': result.get('character_status', ''),
                    'pending_threads': result.get('pending_threads', []),
                    'story_direction': result.get('story_direction', ''),
                    'last_500_words': ending[-500:] if len(ending) > 500 else ending
                }
            return {'last_chapter_summary': ending[:500], 'last_500_words': ending[-500:] if len(ending) > 500 else ending}
        except Exception:
            return {}
    
    def _detect_chapter_structure(self, file_path: str) -> Dict[str, Any]:
        """检测章节结构，移除易误判的纯数字列表正则。"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read(50000)
            
            patterns = [
                (r'第[一二三四五六七八九十百千万零\d]+章', 'chinese'),
                (r'第[一二三四五六七八九十百千万零\d]+[回节]', 'chinese_alt'),
                (r'Chapter\s*\d+', 'english')
            ]
            
            chapters = []
            detected_pattern = None
            for pattern, pattern_type in patterns:
                matches = re.findall(pattern, content)
                if matches:
                    chapters = matches
                    detected_pattern = pattern_type
                    break
            
            return {'has_chapters': len(chapters) > 0, 'chapter_count': len(chapters), 'chapter_pattern': detected_pattern, 'last_chapter': chapters[-1] if chapters else None}
        except Exception:
            return {'has_chapters': False, 'chapter_count': 0}
    
    def _extract_characters_and_setting(self, file_path: str) -> Dict[str, Any]:
        """提取角色和世界观设定"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read(20000)
            
            prompt = f"""从以下小说内容中提取主要角色和世界观设定：
{content[:10000]}...
请返回 JSON：
{{"characters": [{{"name": "角色名", "role": "主角/配角/反派", "brief": "简要描述"}}], "world_setting": {{"power_system": "能力体系", "social_structure": "社会结构", "special_rules": ["特殊规则"]}}}}"""
            
            result = self._call_llm_for_json(prompt)
            return {'characters': result.get('characters', []), 'world_setting': result.get('world_setting', {})} if result else {'characters': [], 'world_setting': {}}
        except Exception:
            return {'characters': [], 'world_setting': {}}
    
    def _extract_character_relationships(self, file_path: str) -> Dict[str, Any]:
        """提取角色关系"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read(30000)
            
            prompt = f"""从以下小说内容中提取角色关系：
{content[:15000]}...
请返回 JSON：
{{"relationships": [{{"character1": "角色1", "character2": "角色2", "relationship": "关系类型", "description": "关系描述"}}]}}"""
            
            result = self._call_llm_for_json(prompt)
            return {'character_relationships': result.get('relationships', [])} if result else {'character_relationships': []}
        except Exception:
            return {'character_relationships': []}
    
    def _analyze_plot_structure(self, file_path: str) -> Dict[str, Any]:
        """分析剧情脉络"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read(50000)
            
            prompt = f"""分析以下小说的剧情结构：
{content[:20000]}...
请返回 JSON：
{{"plot_arcs": [{{"arc_name": "剧情线名称", "status": "状态", "description": "描述"}}], "main_conflict": "主要冲突", "resolved_conflicts": ["已解决"], "unresolved_conflicts": ["未解决"], "key_turning_points": ["转折点"]}}"""
            
            result = self._call_llm_for_json(prompt)
            if result:
                return {
                    'plot_structure': result.get('plot_arcs', []), 'main_conflict': result.get('main_conflict', ''),
                    'resolved_conflicts': result.get('resolved_conflicts', []), 'unresolved_conflicts': result.get('unresolved_conflicts', []),
                    'key_turning_points': result.get('key_turning_points', [])
                }
            return {'plot_structure': [], 'main_conflict': '', 'resolved_conflicts': [], 'unresolved_conflicts': [], 'key_turning_points': []}
        except Exception:
            return {'plot_structure': [], 'main_conflict': '', 'resolved_conflicts': [], 'unresolved_conflicts': [], 'key_turning_points': []}
    
    def _generate_continuation_guide(self, analysis: Dict[str, Any]) -> Dict[str, Any]:
        """生成续写指导文档"""
        return {
            "source_novel": analysis.get('file_path', ''), "genre": analysis.get('genre', '通用'),
            "writing_style": analysis.get('writing_style', '自然流畅'), "narrative_perspective": analysis.get('narrative_perspective', '第三人称'),
            "characters": analysis.get('characters', []), "character_relationships": analysis.get('character_relationships', []),
            "world_setting": analysis.get('world_setting', {}), "plot_structure": analysis.get('plot_structure', []),
            "main_conflict": analysis.get('main_conflict', ''), "unresolved_conflicts": analysis.get('unresolved_conflicts', []),
            "last_chapter_summary": analysis.get('last_chapter_summary', ''), "character_status": analysis.get('character_status', ''),
            "pending_threads": analysis.get('pending_threads', []), "story_direction": analysis.get('story_direction', ''),
            "last_500_words": analysis.get('last_500_words', ''), "chapter_count": analysis.get('chapter_count', 0),
            "writing_guidelines": ["保持原作题材一致", "保持原作写作风格", "角色性格保持一致", "世界观设定不冲突", "情节与原作结尾连贯", "适当呼应前文伏笔", "注意角色关系发展", "延续未解决的冲突线"]
        }
    
    def _cache_analysis(self, analysis: Dict[str, Any], file_path: str):
        import hashlib
        cache_key = hashlib.md5(file_path.encode()).hexdigest()[:12]
        cache_file = os.path.join(self.analysis_cache_dir, f"analysis_{cache_key}.json")
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump(analysis, f, indent=2, ensure_ascii=False)
    
    def get_cached_analysis(self, file_path: str) -> Optional[Dict[str, Any]]:
        import hashlib
        cache_key = hashlib.md5(file_path.encode()).hexdigest()[:12]
        cache_file = os.path.join(self.analysis_cache_dir, f"analysis_{cache_key}.json")
        if not os.path.exists(cache_file): return None
        try:
            with open(cache_file, 'r', encoding='utf-8') as f: return json.load(f)
        except Exception: return None
    
    def is_continuation_task(self, user_prompt: str) -> bool:
        continuation_keywords = ['续写', '继续写', '接着写', '后续', '第二部', '番外', '后传', '延续', '接续', '接上文', '续集']
        file_keywords = ['文件', 'txt', '小说', '这本书', '这本', '路径', 'path', '文档']
        has_continuation = any(k in user_prompt for k in continuation_keywords)
        has_file = any(k in user_prompt for k in file_keywords) or bool(re.search(r'[A-Za-z]:\\|/', user_prompt))
        return has_continuation and has_file
    
    def extract_file_path(self, user_prompt: str) -> Optional[str]:
        win_match = re.search(r'[A-Za-z]:\\(?:[^\s\\"<>|]+\\)*[^\s\\"<>|]+', user_prompt)
        if win_match: return win_match.group(0)
        unix_match = re.search(r'/(?:[^\s/<>|]+/)*[^\s/<>|]+', user_prompt)
        if unix_match: return unix_match.group(0)
        quoted_match = re.search(r'["\']([^"\']+\.(?:txt|md|docx))["\']', user_prompt, re.IGNORECASE)
        if quoted_match: return quoted_match.group(1)
        return None
    
    def _call_llm_for_json(self, prompt: str) -> Dict[str, Any]:
        """🌟 核心修复：统一调用 LLM 并安全提取 JSON，彻底废弃错误的 _parse_user_request"""
        try:
            import requests
            resp = requests.post(OLLAMA_URL, json={
                "model": MODEL_NAME,
                "messages": [
                    {"role": "system", "content": "你是一个严谨的数据分析API，只输出合法的纯JSON，绝不输出Markdown标记或解释性废话。"},
                    {"role": "user", "content": prompt}
                ],
                "stream": False,
                "options": {"temperature": 0.3, "num_predict": 2048}
            }, timeout=SAFE_TIMEOUT)
            
            if resp.status_code == 200:
                content = resp.json().get('message', {}).get('content', '').strip()
                content = re.sub(r'^```(?:json)?\s*', '', content, flags=re.MULTILINE)
                content = re.sub(r'\s*```$', '', content, flags=re.MULTILINE).strip()
                
                try: return json.loads(content)
                except json.JSONDecodeError: pass
                
                match = re.search(r'\{.*\}', content, re.DOTALL)
                if match:
                    try: return json.loads(match.group())
                    except json.JSONDecodeError: pass
        except Exception as e:
            self._log(f"❌ LLM 调用或 JSON 解析失败：{e}")
        return {}
    
    def export_analysis_report(self, analysis: Dict[str, Any], output_path: str = None) -> Dict[str, Any]:
        if output_path is None:
            output_path = os.path.join(self.analysis_cache_dir, f"analysis_report_{datetime.now().strftime('%Y%m%d')}.txt")
        try:
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write("# 原作分析报告\n\n")
                f.write(f"文件：{analysis.get('file_path', '未知')}\n")
                f.write(f"分析时间：{analysis.get('analyzed_at', '未知')}\n\n")
                f.write("## 基本信息\n\n")
                f.write(f"题材：{analysis.get('genre', '未知')}\n")
                f.write(f"写作风格：{analysis.get('writing_style', '未知')}\n")
                f.write(f"章节数：{analysis.get('chapter_count', '未知')}\n\n")
                f.write("## 角色设定\n\n")
                for char in analysis.get('characters', []):
                    f.write(f"- {char.get('name', '未知')}: {char.get('brief', '')}\n")
                f.write("\n## 角色关系\n\n")
                for rel in analysis.get('character_relationships', []):
                    f.write(f"- {rel.get('character1', '')} ↔ {rel.get('character2', '')}: {rel.get('relationship', '')}\n")
                f.write("\n## 世界观设定\n\n")
                world = analysis.get('world_setting', {})
                f.write(f"能力体系：{world.get('power_system', '未知')}\n")
                f.write(f"社会结构：{world.get('social_structure', '未知')}\n\n")
                f.write("## 剧情结构\n\n")
                f.write(f"主要冲突：{analysis.get('main_conflict', '未知')}\n未解决冲突:\n")
                for conflict in analysis.get('unresolved_conflicts', []):
                    f.write(f"  - {conflict}\n")
                f.write("\n## 续写指导\n\n")
                f.write(f"结尾摘要：{analysis.get('last_chapter_summary', '未知')}\n写作建议:\n")
                for guideline in analysis.get('continuation_guide', {}).get('writing_guidelines', []):
                    f.write(f"  - {guideline}\n")
            return {"success": True, "filepath": output_path}
        except Exception as e:
            return {"success": False, "error": str(e)}
