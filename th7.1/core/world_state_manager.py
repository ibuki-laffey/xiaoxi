import os
import json
import re
import hashlib
import shutil
from datetime import datetime
from typing import Dict, Any, List, Optional

from .config import (
    KNOWLEDGE_BASE_DIR, TASK_TRACKING_DIR,
    CHARACTER_GROWTH_ENABLED, WORLD_TRUTH_LEVELS,
    FORESHADOWING_AUTO_TRACK, INTERNAL_LOG_ENABLED,
    PROFESSIONAL_LOG_ENABLED, ATOMIC_OPERATION_ENABLED
)
from . import config

class WorldStateManager:
    """世界状态管理器：多世界设定、真相层级、角色状态、成长轨迹、伏笔追踪。"""
    
    def __init__(self, log_callback=None):
        self.log_callback = log_callback
        self.state_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "world_states")
        os.makedirs(self.state_dir, exist_ok=True)
        self.current_project_id = None
        self.current_world_id = "_main"
        
        self.character_growth_enabled = CHARACTER_GROWTH_ENABLED
        self.world_truth_levels = WORLD_TRUTH_LEVELS
        self.foreshadowing_auto_track = FORESHADOWING_AUTO_TRACK
        self.internal_log_enabled = INTERNAL_LOG_ENABLED
        self.professional_log_enabled = PROFESSIONAL_LOG_ENABLED
        self.atomic_operation_enabled = ATOMIC_OPERATION_ENABLED
        
        self.setting_priority_cache = {}
        self.backup_dir = os.path.join(self.state_dir, "backups")
        os.makedirs(self.backup_dir, exist_ok=True)
        
        self._log("✅ 世界状态管理器已初始化（完整增强版 - 质量优先）")
    
    def _log(self, msg: str, level: str = "INFO"):
        if self.log_callback:
            timestamp = datetime.now().isoformat()
            if self.professional_log_enabled:
                log_entry = {"timestamp": timestamp, "level": level, "module": "WorldStateManager", "message": msg}
                msg = json.dumps(log_entry, ensure_ascii=False)
            else:
                msg = f"[{timestamp}] [{level}] {msg}"
            self.log_callback(msg)
    
    def set_current_project(self, project_id: str):
        self.current_project_id = project_id
        self.current_world_id = "_main"
        self._log(f"🌍 切换到项目：{project_id}")
        project_dir = os.path.join(self.state_dir, project_id)
        os.makedirs(project_dir, exist_ok=True)
    
    def set_current_world(self, world_id: str):
        self.current_world_id = world_id
        self._log(f"🌀 切换到世界：{world_id}")
    
    def _get_state_file_path(self, state_type: str) -> str:
        if not self.current_project_id:
            return os.path.join(self.state_dir, "global", f"{state_type}.json")
        
        project_dir = os.path.join(self.state_dir, self.current_project_id)
        os.makedirs(project_dir, exist_ok=True)
        
        if self.current_world_id != "_main":
            world_dir = os.path.join(project_dir, "worlds", self.current_world_id)
            os.makedirs(world_dir, exist_ok=True)
            return os.path.join(world_dir, f"{state_type}.json")
        
        return os.path.join(project_dir, f"{state_type}.json")
    
    def _load_state(self, state_type: str) -> Dict[str, Any]:
        filepath = self._get_state_file_path(state_type)
        if not os.path.exists(filepath):
            return {"data": [], "metadata": {"created_at": datetime.now().isoformat()}}
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            if state_type == "characters":
                data = self._validate_and_enrich_state(data, "character")
            elif state_type == "world":
                data = self._validate_and_enrich_state(data, "world")
            return data
        except json.JSONDecodeError as e:
            self._log(f"⚠️ 状态文件损坏，尝试从备份恢复：{e}", level="WARN")
            return self._restore_from_backup(state_type)
        except Exception as e:
            self._log(f"❌ 状态文件加载失败：{e}", level="ERROR")
            return {"data": [], "metadata": {"created_at": datetime.now().isoformat()}}
    
    def _save_state(self, state_type: str, data: Dict[str, Any]):
        filepath = self._get_state_file_path(state_type)
        data["metadata"]["last_updated"] = datetime.now().isoformat()
        
        if state_type == "characters" and "data" in data and isinstance(data["data"], list):
            for idx, char in enumerate(data["data"]):
                if isinstance(char, dict) and "id" not in char:
                    char_name = char.get("name", f"char_{idx}")
                    safe_id = re.sub(r'[^a-zA-Z0-9_]', '', str(char_name)) or f"char_{idx}"
                    char["id"] = safe_id
                    self._log(f"⚠️ 角色 {char_name} 缺少 id 字段，已自动补齐为: {safe_id}", level="WARN")
                
        self._backup_state(state_type)
        
        if self.atomic_operation_enabled:
            tmp_filepath = filepath + '.tmp'
            try:
                # 🌟 工业级原子写入：加入 flush 和 fsync 防止断电损坏
                with open(tmp_filepath, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                    f.flush()
                    os.fsync(f.fileno())
                
                if os.path.exists(tmp_filepath) and os.path.getsize(tmp_filepath) > 0:
                    os.replace(tmp_filepath, filepath)
                else:
                    if os.path.exists(tmp_filepath): os.remove(tmp_filepath)
                    raise Exception("状态文件写入失败或为空")
            except Exception as e:
                self._log(f"❌ 原子保存失败：{e}", level="ERROR")
                if os.path.exists(tmp_filepath):
                    try: os.remove(tmp_filepath)
                    except Exception: pass
        else:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
    
    def _backup_state(self, state_type: str):
        filepath = self._get_state_file_path(state_type)
        if not os.path.exists(filepath): return
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_filename = f"{state_type}_{timestamp}.json"
        backup_filepath = os.path.join(self.backup_dir, backup_filename)
        
        try:
            shutil.copy2(filepath, backup_filepath)
            self._log(f"💾 状态已备份：{state_type}", level="DEBUG")
            self._cleanup_old_backups(state_type)
        except Exception as e:
            self._log(f"⚠️ 备份失败：{e}", level="WARN")
    
    def _cleanup_old_backups(self, state_type: str, keep_count: int = 10):
        backup_files = []
        for filename in os.listdir(self.backup_dir):
            if filename.startswith(f"{state_type}_") and filename.endswith('.json'):
                filepath = os.path.join(self.backup_dir, filename)
                backup_files.append((filepath, os.path.getmtime(filepath)))
        
        if len(backup_files) > keep_count:
            backup_files.sort(key=lambda x: x[1], reverse=True)
            for filepath, _ in backup_files[keep_count:]:
                try: os.remove(filepath)
                except Exception: pass
    
    def _restore_from_backup(self, state_type: str) -> Dict[str, Any]:
        backup_files = []
        for filename in os.listdir(self.backup_dir):
            if filename.startswith(f"{state_type}_") and filename.endswith('.json'):
                filepath = os.path.join(self.backup_dir, filename)
                backup_files.append((filepath, os.path.getmtime(filepath)))
        
        if not backup_files:
            self._log(f"⚠️ 无备份可恢复：{state_type}", level="WARN")
            return {"data": [], "metadata": {"created_at": datetime.now().isoformat()}}
        
        backup_files.sort(key=lambda x: x[1], reverse=True)
        latest_backup = backup_files[0][0]
        
        try:
            with open(latest_backup, 'r', encoding='utf-8') as f:
                restored_data = json.load(f)
            self._log(f"🔄 已从备份恢复：{state_type}")
            return restored_data
        except Exception as e:
            self._log(f"❌ 恢复备份失败：{e}", level="ERROR")
            return {"data": [], "metadata": {"created_at": datetime.now().isoformat()}}
    
    def add_setting(self, setting: Dict[str, Any]) -> str:
        setting_id = hashlib.md5(f"{setting.get('name', '')}{datetime.now()}".encode()).hexdigest()[:12]
        setting["id"] = setting_id
        setting["created_at"] = datetime.now().isoformat()
        setting["status"] = "active"
        if "truth_level" not in setting: setting["truth_level"] = 1
        if "unlock_chapter" not in setting: setting["unlock_chapter"] = 0
        setting["world_id"] = self.current_world_id
        if "priority" not in setting: setting["priority"] = 5
        self.setting_priority_cache[setting_id] = setting["priority"]
        
        state = self._load_state("settings")
        state["data"].append(setting)
        self._save_state("settings", state)
        
        self._log(f"📝 添加设定：{setting.get('name', '未知')}（层级{setting['truth_level']}，优先级{setting['priority']}）")
        return setting_id
    
    def get_settings(self, current_chapter: int = 0, max_truth_level: int = 3) -> List[Dict[str, Any]]:
        state = self._load_state("settings")
        settings = []
        for setting in state["data"]:
            if setting.get("world_id") != self.current_world_id: continue
            if setting.get("unlock_chapter", 0) > current_chapter: continue
            if setting.get("truth_level", 1) > max_truth_level: continue
            if setting.get("status") != "active": continue
            settings.append(setting)
        settings.sort(key=lambda x: x.get("priority", 5), reverse=True)
        return settings
    
    def update_setting(self, setting_id: str, updates: Dict[str, Any]) -> bool:
        state = self._load_state("settings")
        for setting in state["data"]:
            if setting["id"] == setting_id:
                if "evolution_history" not in setting: setting["evolution_history"] = []
                setting["evolution_history"].append({"updated_at": datetime.now().isoformat(), "changes": updates, "previous_state": setting.copy()})
                setting.update(updates)
                setting["last_updated"] = datetime.now().isoformat()
                if "priority" in updates: self.setting_priority_cache[setting_id] = updates["priority"]
                self._save_state("settings", state)
                self._log(f"✏️ 更新设定：{setting.get('name', '未知')}")
                return True
        return False
    
    def set_setting_priority(self, setting_id: str, priority: int, reason: str = ""):
        self.setting_priority_cache[setting_id] = priority
        self._log(f"📊 设定优先级已设置：{setting_id} → {priority}（原因：{reason}）")
        state = self._load_state("settings")
        for setting in state["data"]:
            if setting["id"] == setting_id:
                setting["priority"] = priority
                setting["priority_reason"] = reason
                setting["priority_set_at"] = datetime.now().isoformat()
                break
        self._save_state("settings", state)
    
    def get_setting_priority(self, setting_id: str) -> int:
        return self.setting_priority_cache.get(setting_id, 5)
    
    def check_setting_conflict(self, new_setting: Dict[str, Any]) -> List[Dict[str, Any]]:
        conflicts = []
        current_settings = self._load_state("settings")["data"]
        for existing in current_settings:
            if existing.get("name") == new_setting.get("name") and existing.get("world_id") == self.current_world_id:
                conflicts.append({"type": "name_conflict", "existing": existing, "new": new_setting})
            if self._check_logical_conflict(existing, new_setting):
                conflicts.append({"type": "logical_conflict", "existing": existing, "new": new_setting})
            existing_priority = self.get_setting_priority(existing.get("id", ""))
            new_priority = new_setting.get("priority", 5)
            if existing_priority > new_priority and self._is_incompatible(existing, new_setting):
                conflicts.append({"type": "priority_conflict", "existing": existing, "new": new_setting, "message": f"高优先级设定（{existing_priority}）与新设定（{new_priority}）冲突"})
        return conflicts
    
    def _check_logical_conflict(self, existing: Dict, new: Dict) -> bool:
        forbidden_combinations = [
            (["低武", "武侠"], ["核武器", "高科技", "机甲"]),
            (["古代", "封建"], ["手机", "电脑", "互联网"]),
            (["魔法世界"], ["科学解释", "物理定律"])
        ]
        existing_tags = existing.get("tags", [])
        new_tags = new.get("tags", [])
        for group1, group2 in forbidden_combinations:
            has_group1 = any(t in existing_tags for t in group1) or any(t in new_tags for t in group1)
            has_group2 = any(t in existing_tags for t in group2) or any(t in new_tags for t in group2)
            if has_group1 and has_group2: return True
        return False
    
    def _is_incompatible(self, existing: Dict, new: Dict) -> bool:
        contradiction_pairs = [("死亡", "复活"), ("消失", "出现"), ("破坏", "修复"), ("开始", "结束")]
        existing_content = existing.get("content", "") + " " + existing.get("name", "")
        new_content = new.get("content", "") + " " + new.get("name", "")
        for pair in contradiction_pairs:
            if (pair[0] in existing_content and pair[1] in new_content) or (pair[1] in existing_content and pair[0] in new_content):
                return True
        return False

    def create_character_growth_trajectory(self, character_id: str, trajectory: Dict[str, Any]) -> str:
        trajectory_id = hashlib.md5(f"{character_id}{datetime.now()}".encode()).hexdigest()[:12]
        trajectory_data = {"id": trajectory_id, "character_id": character_id, "created_at": datetime.now().isoformat(), "status": "active", "trajectory": trajectory}
        state = self._load_state("character_growth")
        state["data"].append(trajectory_data)
        self._save_state("character_growth", state)
        self._log(f"📈 创建角色成长轨迹：{character_id}")
        return trajectory_id
    
    def get_character_growth_trajectory(self, character_id: str) -> Optional[Dict[str, Any]]:
        state = self._load_state("character_growth")
        for trajectory in state["data"]:
            if trajectory.get("character_id") == character_id:
                return trajectory.get("trajectory", {})
        return None
    
    def update_character_growth_stage(self, character_id: str, chapter_num: int, stage_data: Dict[str, Any]) -> bool:
        trajectory = self.get_character_growth_trajectory(character_id)
        if not trajectory: return False
        
        growth_stages = trajectory.get("growth_stages", [])
        current_stage = None
        for stage in growth_stages:
            chapters_range = stage.get("chapters", "")
            if chapters_range:
                try:
                    start, end = map(int, chapters_range.replace('第', '').replace('章', '').split('-'))
                    if start <= chapter_num <= end:
                        current_stage = stage
                        break
                except Exception: continue
        
        if current_stage:
            current_stage["current_chapter"] = chapter_num
            current_stage["stage_data"] = stage_data
            current_stage["updated_at"] = datetime.now().isoformat()
            state = self._load_state("character_growth")
            for t in state["data"]:
                if t.get("character_id") == character_id:
                    t["trajectory"] = trajectory
                    t["last_updated"] = datetime.now().isoformat()
                    self._save_state("character_growth", state)
                    break
            self._log(f"📈 更新角色成长阶段：{character_id} 第{chapter_num}章")
            return True
        return False
    
    def initialize_project_states(self, project_info: dict, project_dir: str) -> bool:
        """基于 LLM 动态生成真实、准确的项目初始状态。"""
        from .config import call_ollama_safe

        title = project_info.get('title', '未知')
        genre = project_info.get('genre', '通用')
        main_char = project_info.get('main_character', '主角')
        world_type = project_info.get('world_type', '默认世界')

        self._log(f"🧠 正在为项目《{title}》推理并生成真实的初始角色与世界状态...")

        prompt = f"""你是一个专业的小说设定架构师。请根据以下项目信息，推理并生成初始的角色状态和世界状态。
项目名：{title} | 题材：{genre} | 主角名：{main_char} | 世界类型：{world_type}

请严格输出一个 JSON 对象，包含两个键："character" 和 "world"。
"character" 必须包含：id, name, role, status, health, mana, belief, age, gender, appearance, personality, motivation, background, traits, relationships, inventory, skills, current_location, emotion, sanity。
"world" 必须包含：id, name, setting, rules, history, factions, magic_system, technology_level, current_events。
要求：所有字段内容必须与题材深度契合。只输出纯 JSON，不要包含任何 Markdown 标记或解释性文本。"""

        try:
            messages = [
                {"role": "system", "content": "你是一个严格的 JSON 输出机器，只输出合法 JSON。"},
                {"role": "user", "content": prompt}
            ]
            resp = call_ollama_safe(messages)
            raw_response = resp.get("content", "") if isinstance(resp, dict) else str(resp)
            
            # 🌟 健壮的 JSON 提取：首尾括号定位 + 尾部逗号清理
            text = re.sub(r"^```(?:json)?\s*", "", raw_response.strip(), flags=re.MULTILINE)
            text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE).strip()
            start_idx = text.find('{')
            end_idx = text.rfind('}')
            if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                json_str = text[start_idx:end_idx + 1]
                json_str = re.sub(r",\s*([}\]])", r"\1", json_str)
                states = json.loads(json_str)
            else:
                states = json.loads(text)
            
            system_dir = os.path.join(project_dir, "system")
            os.makedirs(system_dir, exist_ok=True)

            # 🌟 工业级原子写入
            if "character" in states:
                states["character"]["metadata"] = {"created_at": datetime.now().isoformat(), "last_updated": datetime.now().isoformat()}
                char_path = os.path.join(system_dir, "character_state.json")
                tmp_char = char_path + ".tmp"
                with open(tmp_char, "w", encoding="utf-8") as f:
                    json.dump(states["character"], f, ensure_ascii=False, indent=2)
                    f.flush(); os.fsync(f.fileno())
                os.replace(tmp_char, char_path)
                    
            if "world" in states:
                states["world"]["metadata"] = {"created_at": datetime.now().isoformat(), "last_updated": datetime.now().isoformat()}
                world_path = os.path.join(system_dir, "world_state.json")
                tmp_world = world_path + ".tmp"
                with open(tmp_world, "w", encoding="utf-8") as f:
                    json.dump(states["world"], f, ensure_ascii=False, indent=2)
                    f.flush(); os.fsync(f.fileno())
                os.replace(tmp_world, world_path)
                    
            # 🌟 防弹同步全局状态
            try:
                local_char_file = os.path.join(system_dir, "character_state.json")
                local_world_file = os.path.join(system_dir, "world_state.json")
                
                char_global = getattr(config, 'CHARACTER_FILE', None)
                world_global = getattr(config, 'WORLD_FILE', None)
                
                if os.path.exists(local_char_file) and char_global:
                    shutil.copy2(local_char_file, char_global)
                    self._log(f"🔄 已将真实角色状态同步至全局: {char_global}")
                if os.path.exists(local_world_file) and world_global:
                    shutil.copy2(local_world_file, world_global)
                    self._log(f"🔄 已将真实世界状态同步至全局: {world_global}")
            except Exception as sync_e:
                self._log(f"⚠️ 同步全局状态文件失败 (不影响项目内状态): {sync_e}", level="WARN")

            self._log("✅ 真实初始状态推理并保存成功！")
            return True
        except Exception as e:
            self._log(f"❌ 初始状态 LLM 推理失败: {e}", level="ERROR")
            return False

    def _validate_and_enrich_state(self, state_data: dict, state_type: str = "character") -> dict:
        if not isinstance(state_data, dict): return state_data
        enriched_data = state_data.copy()

        structural_defaults = {
            "character": {'id': 'char_default', 'health': 100, 'mana': 100, 'sanity': 100, 'age': 20, 'traits': [], 'relationships': [], 'inventory': [], 'skills': []},
            "world": {'id': 'world_default', 'rules': [], 'factions': [], 'current_events': []}
        }
        for field, default_val in structural_defaults.get(state_type, {}).items():
            if field not in enriched_data or enriched_data[field] is None:
                enriched_data[field] = default_val

        semantic_required_fields = {
            "character": ['name', 'belief', 'motivation', 'personality', 'background'],
            "world": ['name', 'setting', 'history', 'magic_system']
        }
        missing_semantic = []
        for field in semantic_required_fields.get(state_type, []):
            val = enriched_data.get(field)
            if not val or (isinstance(val, str) and val.strip() in ["", "未知", "待定义", "无", "普通"]):
                missing_semantic.append(field)
                
        if missing_semantic:
            error_msg = f"❌ 质量校验阻断：{state_type} 状态缺失核心语义字段或内容空洞: {missing_semantic}。请重新触发 LLM 生成真实设定！"
            self._log(error_msg, level="ERROR")
            raise ValueError(error_msg)
        return enriched_data

    def check_character_growth_consistency(self, character_id: str, chapter_num: int, content: str) -> Dict[str, Any]:
        trajectory = self.get_character_growth_trajectory(character_id)
        if not trajectory: return {"success": True, "message": "无成长轨迹设定"}
        
        growth_stages = trajectory.get("growth_stages", [])
        current_stage = None
        for stage in growth_stages:
            chapters_range = stage.get("chapters", "")
            if chapters_range:
                try:
                    start, end = map(int, chapters_range.replace('第', '').replace('章', '').split('-'))
                    if start <= chapter_num <= end:
                        current_stage = stage
                        break
                except Exception: continue
        
        if not current_stage: return {"success": True, "message": "当前章节无成长阶段要求"}
        
        keywords = current_stage.get("keywords", [])
        match_count = sum(1 for kw in keywords if kw in content)
        match_rate = match_count / len(keywords) if keywords else 0
        is_consistent = match_rate >= 0.5
        
        return {
            "success": is_consistent,
            "message": "符合" if is_consistent else "不符合当前成长阶段",
            "match_rate": match_rate,
            "current_stage": current_stage.get("stage", ""),
            "suggestions": current_stage.get("suggestions", []) if not is_consistent else []
        }
    
    def create_world_truth_levels(self, world_id: str, truth_levels: Dict[str, Any]) -> str:
        config_id = hashlib.md5(f"{world_id}{datetime.now()}".encode()).hexdigest()[:12]
        config_data = {"id": config_id, "world_id": world_id, "created_at": datetime.now().isoformat(), "status": "active", "truth_levels": truth_levels}
        state = self._load_state("world_truth_levels")
        state["data"].append(config_data)
        self._save_state("world_truth_levels", state)
        self._log(f"🌐 创建世界观真相层级：{world_id}")
        return config_id
    
    def get_world_truth_levels(self, world_id: str = None) -> Optional[Dict[str, Any]]:
        if world_id is None: world_id = self.current_world_id
        state = self._load_state("world_truth_levels")
        for cfg in state["data"]:
            if cfg.get("world_id") == world_id:
                return cfg.get("truth_levels", {})
        return None
    def get_current_truth_level(self, chapter_num: int) -> int:
        """获取当前章节应揭露的真相层级（1=表面，2=中层，3=真相）。"""
        truth_levels = self.get_world_truth_levels()
        if not truth_levels: return 1
        
        reveal_plan = truth_levels.get("reveal_plan", [])
        current_level = 1
        for reveal in reveal_plan:
            if reveal.get("chapter", 0) <= chapter_num:
                current_level = max(current_level, reveal.get("level", 1))
        return current_level
    
    def check_premature_reveal(self, content: str, chapter_num: int) -> Dict[str, Any]:
        """检查是否提前泄露真相。"""
        current_level = self.get_current_truth_level(chapter_num)
        truth_levels = self.get_world_truth_levels()
        if not truth_levels: return {"success": True, "message": "无真相层级设定"}
        
        higher_level_keywords = []
        for level_name, level_data in truth_levels.items():
            if level_name.startswith("level_"):
                try:
                    level_num = int(level_name.split("_")[1])
                    if level_num > current_level:
                        if isinstance(level_data, str): higher_level_keywords.extend(level_data.split())
                        elif isinstance(level_data, dict): higher_level_keywords.extend(level_data.get("keywords", []))
                except Exception: continue
        
        found_keywords = [kw for kw in higher_level_keywords if kw in content]
        if found_keywords:
            return {"success": False, "message": f"检测到提前泄露的真相关键词：{', '.join(found_keywords)}", "premature_reveal": True, "found_keywords": found_keywords, "current_level": current_level}
        return {"success": True, "message": "未检测到提前泄露", "premature_reveal": False, "current_level": current_level}
    
    def add_character(self, character: Dict[str, Any]) -> str:
        """添加角色。"""
        character_id = hashlib.md5(f"{character.get('name', '')}{datetime.now()}".encode()).hexdigest()[:12]
        character["id"] = character_id
        character["created_at"] = datetime.now().isoformat()
        character["status"] = "active"
        character["world_id"] = self.current_world_id
        
        state = self._load_state("characters")
        state["data"].append(character)
        self._save_state("characters", state)
        self._log(f"👤 添加角色：{character.get('name', '未知')}")
        return character_id
    
    def get_characters(self, status_filter: List[str] = None) -> List[Dict[str, Any]]:
        """获取角色列表。"""
        state = self._load_state("characters")
        characters = []
        for char in state.get("data", []):
            if not isinstance(char, dict): continue
            if char.get("world_id") != self.current_world_id: continue
            if status_filter and char.get("status") not in status_filter: continue
            characters.append(char)
        return characters
    
    def update_character_status(self, character_id: str, new_status: str, reason: str = "") -> bool:
        """更新角色状态（如死亡、受伤）。"""
        state = self._load_state("characters")
        for char in state.get("data", []):
            if isinstance(char, dict) and char.get("id") == character_id:
                char["status"] = new_status
                char["status_change_reason"] = reason
                char["status_changed_at"] = datetime.now().isoformat()
                self._save_state("characters", state)
                self._log(f"🔄 角色状态更新：{char.get('name', '未知')} → {new_status}")
                return True
        return False
    
    def get_character_status(self, character_id: str, chapter_num: int = None) -> Optional[Dict[str, Any]]:
        """获取角色状态，支持获取特定章节的状态（用于健康检查）。"""
        state = self._load_state("characters")
        for char in state.get("data", []):
            if isinstance(char, dict) and char.get("id") == character_id:
                if chapter_num and char.get("status_history"):
                    for history in char["status_history"]:
                        if history.get("chapter", 0) <= chapter_num:
                            return history.get("status", char.get("status"))
                return char
        return None
    
    def get_expected_character_status(self, character_id: str, chapter_num: int) -> Optional[str]:
        """获取角色在特定章节的预期状态，用于健康检查动态状态验证。"""
        trajectory = self.get_character_growth_trajectory(character_id)
        if not trajectory: return None
        
        for stage in trajectory.get("growth_stages", []):
            chapters_range = stage.get("chapters", "")
            if chapters_range:
                try:
                    start, end = map(int, chapters_range.replace('第', '').replace('章', '').split('-'))
                    if start <= chapter_num <= end:
                        return stage.get("expected_status", "active")
                except Exception: continue
        return "active"
    
    def get_character_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """根据名称获取角色。"""
        for char in self.get_characters():
            if char.get("name") == name: return char
        return None
    
    def add_foreshadowing(self, foreshadowing: Dict[str, Any]) -> str:
        """添加伏笔。"""
        foreshadowing_id = hashlib.md5(f"{foreshadowing.get('content', '')}{datetime.now()}".encode()).hexdigest()[:12]
        foreshadowing["id"] = foreshadowing_id
        foreshadowing["created_at"] = datetime.now().isoformat()
        foreshadowing["status"] = "pending"
        foreshadowing["world_id"] = self.current_world_id
        
        state = self._load_state("foreshadowing")
        state["data"].append(foreshadowing)
        self._save_state("foreshadowing", state)
        self._log(f"🎯 添加伏笔：{foreshadowing.get('content', '未知')[:50]}...")
        return foreshadowing_id
    
    def resolve_foreshadowing(self, foreshadowing_id: str, resolution: str) -> bool:
        """回收伏笔。"""
        state = self._load_state("foreshadowing")
        for foreshadow in state.get("data", []):
            if isinstance(foreshadow, dict) and foreshadow.get("id") == foreshadowing_id:
                foreshadow["status"] = "resolved"
                foreshadow["resolution"] = resolution
                foreshadow["resolved_at"] = datetime.now().isoformat()
                self._save_state("foreshadowing", state)
                self._log(f"✅ 伏笔已回收：{foreshadow.get('content', '未知')[:50]}...")
                return True
        return False
    
    def get_pending_foreshadowing(self) -> List[Dict[str, Any]]:
        """获取当前世界所有未解决的伏笔（用于注入给下一卷大纲生成，防割裂核心）。"""
        state = self._load_state("foreshadowing")
        return [f for f in state.get("data", []) if isinstance(f, dict) and f.get("status") == "pending" and f.get("world_id") == self.current_world_id]
    
    def get_foreshadowing_by_chapter(self, chapter_num: int) -> List[Dict[str, Any]]:
        """获取特定章节相关的伏笔。"""
        state = self._load_state("foreshadowing")
        related = []
        for foreshadow in state.get("data", []):
            if isinstance(foreshadow, dict) and foreshadow.get("world_id") == self.current_world_id:
                setup_chapter = foreshadow.get("setup_chapter", 0)
                resolve_chapter = foreshadow.get("resolve_chapter", 0)
                if setup_chapter <= chapter_num <= resolve_chapter:
                    related.append(foreshadow)
        return related
    
    def get_foreshadowing_ledger(self) -> Dict[str, Any]:
        """获取伏笔账本，用于健康检查伏笔追踪。"""
        state = self._load_state("foreshadowing")
        pending, resolved, abandoned = [], [], []
        for foreshadow in state.get("data", []):
            if not isinstance(foreshadow, dict) or foreshadow.get("world_id") != self.current_world_id: continue
            status = foreshadow.get("status")
            if status == "pending": pending.append(foreshadow)
            elif status == "resolved": resolved.append(foreshadow)
            elif status == "abandoned": abandoned.append(foreshadow)
            
        return {"pending": pending, "resolved": resolved, "abandoned": abandoned, "total": len(state.get("data", [])), "pending_count": len(pending), "resolved_count": len(resolved), "abandoned_count": len(abandoned)}
    
    def create_world(self, world_id: str, world_config: Dict[str, Any]) -> bool:
        """创建新世界（用于副本流），使用工业级原子写入。"""
        world_dir = os.path.join(self.state_dir, self.current_project_id, "worlds", world_id)
        os.makedirs(world_dir, exist_ok=True)
        
        config_file = os.path.join(world_dir, "world_config.json")
        world_config["world_id"] = world_id
        world_config["created_at"] = datetime.now().isoformat()
        
        tmp_file = config_file + ".tmp"
        try:
            with open(tmp_file, 'w', encoding='utf-8') as f:
                json.dump(world_config, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_file, config_file)
            self._log(f"🌍 创建新世界：{world_id}")
            return True
        except Exception as e:
            if os.path.exists(tmp_file):
                try: os.remove(tmp_file)
                except Exception: pass
            self._log(f"❌ 创建新世界失败：{e}", level="ERROR")
            return False
    
    def get_world_list(self) -> List[Dict[str, Any]]:
        """获取世界列表，安全读取配置。"""
        worlds = []
        project_dir = os.path.join(self.state_dir, self.current_project_id, "worlds")
        if os.path.exists(project_dir):
            for world_id in os.listdir(project_dir):
                config_file = os.path.join(project_dir, world_id, "world_config.json")
                if os.path.exists(config_file):
                    try:
                        with open(config_file, 'r', encoding='utf-8') as f:
                            worlds.append(json.load(f))
                    except Exception: continue
        return worlds
    
    def get_summary(self) -> Dict[str, Any]:
        """获取世界状态摘要，防弹处理 data 键。"""
        settings = self._load_state("settings").get("data", [])
        characters = self._load_state("characters").get("data", [])
        foreshadowing = self._load_state("foreshadowing").get("data", [])
        character_growth = self._load_state("character_growth").get("data", [])
        world_truth_levels = self._load_state("world_truth_levels").get("data", [])
        
        return {
            "project_id": self.current_project_id, "current_world": self.current_world_id,
            "total_settings": len(settings), "total_characters": len(characters),
            "active_characters": len([c for c in characters if isinstance(c, dict) and c.get("status") == "active"]),
            "pending_foreshadowing": len([f for f in foreshadowing if isinstance(f, dict) and f.get("status") == "pending"]),
            "resolved_foreshadowing": len([f for f in foreshadowing if isinstance(f, dict) and f.get("status") == "resolved"]),
            "character_growth_trajectories": len(character_growth), "truth_level_configs": len(world_truth_levels)
        }
    
    def export_world_state(self, filepath: str) -> bool:
        """导出完整世界状态，使用工业级原子写入。"""
        try:
            export_data = {
                "settings": self._load_state("settings"), "characters": self._load_state("characters"),
                "foreshadowing": self._load_state("foreshadowing"), "character_growth": self._load_state("character_growth"),
                "world_truth_levels": self._load_state("world_truth_levels"), "exported_at": datetime.now().isoformat()
            }
            tmp_filepath = filepath + ".tmp"
            with open(tmp_filepath, 'w', encoding='utf-8') as f:
                json.dump(export_data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_filepath, filepath)
            self._log(f"💾 世界状态已导出：{filepath}")
            return True
        except Exception as e:
            self._log(f"❌ 导出失败：{e}", level="ERROR")
            return False
    
    def import_world_state(self, filepath: str) -> bool:
        """导入世界状态，安全读取。"""
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                import_data = json.load(f)
            for state_type in ["settings", "characters", "foreshadowing", "character_growth", "world_truth_levels"]:
                if state_type in import_data:
                    self._save_state(state_type, import_data[state_type])
            self._log(f"📥 世界状态已导入：{filepath}")
            return True
        except Exception as e:
            self._log(f"❌ 导入失败：{e}", level="ERROR")
            return False
    
    def clear_all_states(self, confirm: bool = False) -> Dict[str, Any]:
        """清空所有状态（危险操作）。"""
        if not confirm: return {"success": False, "message": "需要确认参数 confirm=True"}
        cleared = 0
        for state_type in ["settings", "characters", "foreshadowing", "character_growth", "world_truth_levels"]:
            state = {"data": [], "metadata": {"created_at": datetime.now().isoformat(), "cleared_at": datetime.now().isoformat()}}
            self._save_state(state_type, state)
            cleared += 1
        self.setting_priority_cache.clear()
        self._log(f"🗑️ 已清空 {cleared} 个状态类型")
        return {"success": True, "cleared_count": cleared}
    
    def get_health_check_data(self, current_chapter: int) -> Dict[str, Any]:
        """获取健康检查所需的全部数据，用于 AI 控制器进行工程健康检查。"""
        return {
            "settings": self.get_settings(current_chapter), "characters": self.get_characters(),
            "foreshadowing_ledger": self.get_foreshadowing_ledger(), "current_truth_level": self.get_current_truth_level(current_chapter),
            "character_growth_trajectories": self._load_state("character_growth"), "world_truth_levels": self.get_world_truth_levels()
        }
    
    def validate_world_state_integrity(self) -> Dict[str, Any]:
        """验证世界状态完整性，用于健康检查发现潜在问题。"""
        issues, warnings = [], []
        
        for setting in self._load_state("settings").get("data", []):
            if not isinstance(setting, dict): continue
            conflicts = self.check_setting_conflict(setting)
            if conflicts: issues.append({"type": "setting_conflict", "setting_id": setting.get("id"), "conflicts": conflicts})
        
        for char in self._load_state("characters").get("data", []):
            if isinstance(char, dict) and char.get("status") == "dead" and char.get("appearances_after_death"):
                warnings.append({"type": "character_appearance_after_death", "character_id": char.get("id"), "character_name": char.get("name")})
        
        foreshadowing = self._load_state("foreshadowing").get("data", [])
        pending_count = len([f for f in foreshadowing if isinstance(f, dict) and f.get("status") == "pending"])
        if pending_count > 50:
            warnings.append({"type": "too_many_pending_foreshadowing", "count": pending_count, "suggestion": "建议回收部分伏笔或标记为废弃"})
        
        return {"success": len(issues) == 0, "issues": issues, "warnings": warnings, "validated_at": datetime.now().isoformat()}

    def get_world_state_dict(self, current_chapter: int) -> Dict[str, Any]:
        """汇总当前世界状态为字典，供章节生成 prompt 使用。"""
        return {
            "settings": self.get_settings(current_chapter), "characters": self.get_characters(),
            "foreshadowing_ledger": self.get_foreshadowing_ledger(), "current_truth_level": self.get_current_truth_level(current_chapter),
            "world_truth_levels": self.get_world_truth_levels(),
            "world_rules": [s.get("content") for s in self.get_settings(current_chapter) if isinstance(s, dict) and s.get("type") == "world_rule"],
        }
