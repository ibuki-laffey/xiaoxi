import os
import json
from datetime import datetime
from typing import Dict, Any, List, Optional

from .config import (
    LOGS_DIR, TASK_TRACKING_DIR, PROFESSIONAL_LOG_ENABLED,
    LOG_FORMAT, LOG_LEVELS, PROJECT_LOG_ISOLATION
)

class ActionLogger:
    """动作日志记录器：支持分类、轮转、工程隔离、JSONL 格式。"""

    def __init__(self, log_callback=None, project_id: str = None):
        self.log_callback = log_callback
        self.project_id = project_id
        self.max_log_size = 200 * 1024 * 1024
        self.max_log_files = 200
        self.current_log_file = None
        self.current_log_size = 0
        self.current_log_type = "system"
        
        self.professional_log_enabled = PROFESSIONAL_LOG_ENABLED
        self.log_format = LOG_FORMAT
        self.log_levels = LOG_LEVELS
        self.project_log_isolation = PROJECT_LOG_ISOLATION

        if self.project_log_isolation and project_id:
            self.base_log_dir = os.path.join(TASK_TRACKING_DIR, "projects", project_id, "logs")
        else:
            self.base_log_dir = LOGS_DIR
        os.makedirs(self.base_log_dir, exist_ok=True)

        self.log_types = {
            "system": "系统日志", "engine": "工程日志", "learning": "学习日志", "error": "错误日志",
            "performance": "性能日志", "reasoning": "思考日志", "state_change": "状态变更日志", "interruption": "中断日志"
        }
        self._init_log_file("system")

    def _init_log_file(self, log_type="system") -> None:
        self.current_log_type = log_type
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        ext = ".jsonl" if (self.professional_log_enabled and self.log_format == "jsonl") else ".log"
        self.current_log_file = os.path.join(self.base_log_dir, f"{log_type}_{timestamp}{ext}")
        self.current_log_size = 0

    def _rotate_log_if_needed(self) -> None:
        if self.current_log_file and os.path.exists(self.current_log_file):
            try:
                self.current_log_size = os.path.getsize(self.current_log_file)
                if self.current_log_size > self.max_log_size:
                    archive_name = self.current_log_file + '.archive'
                    if os.path.exists(archive_name):
                        try: os.remove(archive_name)
                        except Exception: pass
                    os.rename(self.current_log_file, archive_name)
                    self._init_log_file(self.current_log_type)
            except Exception as e:
                print(f"⚠️ 日志轮转失败：{e}")

    def _cleanup_old_logs(self) -> None:
        try:
            for log_type in self.log_types.keys():
                log_files = []
                for filename in os.listdir(self.base_log_dir):
                    if filename.startswith(f"{log_type}_") and (filename.endswith('.log') or filename.endswith('.jsonl')):
                        filepath = os.path.join(self.base_log_dir, filename)
                        log_files.append((filepath, os.path.getmtime(filepath)))
                log_files.sort(key=lambda x: x[1], reverse=True)
                for filepath, _ in log_files[self.max_log_files:]:
                    try: os.remove(filepath)
                    except Exception: pass
        except Exception as e:
            print(f"⚠️ 清理旧日志失败：{e}")

    # 🌟 核心修复：修正缩进，使其正确属于 ActionLogger 类
    def log(
        self, action: str, details: str = "", result: str = "", log_type: str = "system",
        project_id: str = None, level: str = "INFO", reasoning_trace: Dict[str, Any] = None,
        state_diff: Dict[str, Any] = None,
    ) -> None:
        """记录日志（增强版）。"""
        try:
            if log_type != self.current_log_type:
                self._init_log_file(log_type)
            self._rotate_log_if_needed()
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            if self.professional_log_enabled and self.log_format == "jsonl":
                log_entry = {
                    "timestamp": datetime.now().isoformat(), "level": level, "action": action,
                    "details": details[:5000] if details else "", "result": result[:2000] if result else "",
                    "log_type": log_type, "project_id": project_id or self.project_id,
                }
                if reasoning_trace: log_entry["reasoning_trace"] = reasoning_trace
                if state_diff: log_entry["state_diff"] = state_diff
                log_line = json.dumps(log_entry, ensure_ascii=False) + "\n"
            else:
                log_line = f"[{timestamp}] [{level}] [{log_type.upper()}] {action}\n"
                if details: log_line += f"  详情：{details[:500]}...\n"
                if result: log_line += f"  结果：{result[:200]}...\n"
                if project_id: log_line += f"  项目：{project_id}\n"
                log_line += "-" * 50 + "\n"

            with open(self.current_log_file, "a", encoding="utf-8") as f:
                f.write(log_line)
                f.flush()
                os.fsync(f.fileno())

            self.current_log_size = os.path.getsize(self.current_log_file)
            if callable(self.log_callback) and level in ["ERROR", "WARN", "REASONING"]:
                self.log_callback(log_line.strip())
        except Exception as e:
            print(f"⚠️ 日志写入失败：{e}")

    def log_reasoning(self, action: str, reasoning_trace: Dict[str, Any], project_id: str = None) -> None:
        self.log(action=action, details="思考轨迹", log_type="reasoning", project_id=project_id, level="REASONING", reasoning_trace=reasoning_trace)

    def log_state_change(self, action: str, state_diff: Dict[str, Any], project_id: str = None) -> None:
        self.log(action=action, details="状态变更", log_type="state_change", project_id=project_id, level="INFO", state_diff=state_diff)

    def log_error(self, action: str, error: str, traceback: str = "", project_id: str = None) -> None:
        self.log(action=action, details=error, result=traceback, log_type="error", project_id=project_id, level="ERROR")

    def get_log_files(self, log_type: str = None) -> List[Dict[str, Any]]:
        log_files = []
        for filename in os.listdir(self.base_log_dir):
            if filename.endswith('.log') or filename.endswith('.log.archive') or filename.endswith('.jsonl'):
                if log_type is None or filename.startswith(f"{log_type}_"):
                    filepath = os.path.join(self.base_log_dir, filename)
                    log_files.append({"filename": filename, "filepath": filepath, "size": os.path.getsize(filepath), "modified": datetime.fromtimestamp(os.path.getmtime(filepath)).isoformat()})
        return sorted(log_files, key=lambda x: x["modified"], reverse=True)

    def export_logs(self, output_dir: str = None, log_type: str = None) -> Dict[str, Any]:
        if output_dir is None: output_dir = os.path.join(self.base_log_dir, "exports")
        os.makedirs(output_dir, exist_ok=True)
        log_files = self.get_log_files(log_type)
        exported = []
        for log_file in log_files[:10]:
            try:
                output_path = os.path.join(output_dir, log_file["filename"])
                with open(log_file["filepath"], 'r', encoding='utf-8') as f: content = f.read()
                with open(output_path, 'w', encoding='utf-8') as f: f.write(content)
                exported.append(output_path)
            except Exception: pass
        return {"success": True, "exported": exported}

    def search_logs(self, keyword: str, log_type: str = None) -> List[Dict[str, Any]]:
        results = []
        log_files = self.get_log_files(log_type)
        for log_file in log_files[:20]:
            try:
                with open(log_file["filepath"], 'r', encoding='utf-8') as f: content = f.read()
                if keyword in content:
                    matching_lines = [line[:200] for line in content.split('\n') if keyword in line]
                    results.append({"file": log_file["filename"], "matches": matching_lines[:10]})
            except Exception: pass
        return results

    def get_statistics(self) -> Dict[str, Any]:
        stats = {"total_files": 0, "total_size": 0, "by_type": {}}
        for log_type in self.log_types.keys():
            type_files = [f for f in os.listdir(self.base_log_dir) if f.startswith(f"{log_type}_")]
            type_size = sum(os.path.getsize(os.path.join(self.base_log_dir, f)) for f in type_files if os.path.exists(os.path.join(self.base_log_dir, f)))
            stats["by_type"][log_type] = {"file_count": len(type_files), "total_size": type_size, "total_size_mb": type_size / (1024 * 1024)}
            stats["total_files"] += len(type_files)
            stats["total_size"] += type_size
        stats["total_size_mb"] = stats["total_size"] / (1024 * 1024)
        return stats

    def get_current_log_path(self) -> str:
        return self.current_log_file

class ProjectLogger:
    """工程专用日志记录器：支持工程隔离。"""
    def __init__(self, log_callback=None):
        self.log_callback = log_callback
        self.project_logs_dir = os.path.join(TASK_TRACKING_DIR, "project_logs")
        os.makedirs(self.project_logs_dir, exist_ok=True)
        self.professional_log_enabled = PROFESSIONAL_LOG_ENABLED

    def _log(self, msg: str) -> None:
        if self.log_callback: self.log_callback(msg)

    def init_project_log(self, project_id: str) -> str:
        project_log_dir = os.path.join(self.project_logs_dir, project_id)
        os.makedirs(project_log_dir, exist_ok=True)
        ext = ".jsonl" if self.professional_log_enabled else ".log"
        log_file = os.path.join(project_log_dir, f"project{ext}")
        
        with open(log_file, 'w', encoding='utf-8') as f:
            if self.professional_log_enabled:
                f.write(json.dumps({"type": "header", "project_id": project_id, "created_at": datetime.now().isoformat(), "version": "2.0"}, ensure_ascii=False) + '\n')
            else:
                f.write(f"工程日志 - {project_id}\n创建时间：{datetime.now().isoformat()}\n{'='*60}\n\n")
            f.flush(); os.fsync(f.fileno())
        self._log(f"📋 工程日志已初始化：{project_id}")
        return log_file

    def log(self, project_id: str, action: str, details: str = "", result: str = "", level: str = "INFO", reasoning_trace: Dict[str, Any] = None, state_diff: Dict[str, Any] = None) -> None:
        ext = ".jsonl" if self.professional_log_enabled else ".log"
        log_file = os.path.join(self.project_logs_dir, project_id, f"project{ext}")
        if not os.path.exists(log_file): self.init_project_log(project_id)
        timestamp = datetime.now().isoformat()

        if self.professional_log_enabled:
            log_entry = {"timestamp": timestamp, "level": level, "action": action, "details": details[:5000] if details else "", "result": result[:2000] if result else ""}
            if reasoning_trace: log_entry["reasoning_trace"] = reasoning_trace
            if state_diff: log_entry["state_diff"] = state_diff
            log_line = json.dumps(log_entry, ensure_ascii=False) + '\n'
        else:
            log_line = f"[{timestamp}] [{level}] {action}\n"
            if details: log_line += f"  详情：{details[:500]}...\n"
            if result: log_line += f"  结果：{result[:200]}...\n"
            log_line += "-" * 50 + "\n"

        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(log_line)
            f.flush(); os.fsync(f.fileno())

    def get_project_log(self, project_id: str) -> Dict[str, Any]:
        ext = ".jsonl" if self.professional_log_enabled else ".log"
        log_file = os.path.join(self.project_logs_dir, project_id, f"project{ext}")
        if not os.path.exists(log_file): return {"success": False, "error": "工程日志不存在"}
        try:
            with open(log_file, 'r', encoding='utf-8') as f: content = f.read()
            return {"success": True, "content": content, "filepath": log_file}
        except Exception as e: return {"success": False, "error": str(e)}

    def export_project_log(self, project_id: str, output_path: str = None) -> Dict[str, Any]:
        if output_path is None: output_path = os.path.join(self.project_logs_dir, project_id, f"project_export_{datetime.now().strftime('%Y%m%d')}.txt")
        log_result = self.get_project_log(project_id)
        if not log_result["success"]: return log_result
        try:
            with open(output_path, 'w', encoding='utf-8') as f: f.write(log_result["content"])
            return {"success": True, "filepath": output_path}
        except Exception as e: return {"success": False, "error": str(e)}

action_logger = ActionLogger()
project_logger = ProjectLogger()
