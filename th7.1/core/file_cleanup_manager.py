# core/file_cleanup_manager.py
# ==================== 文件清理管理器 ====================
# 功能：智能清理临时文件，需要用户确认

import os
import json
import shutil
from datetime import datetime
from typing import Dict, Any, List, Optional

class FileCleanupManager:
    """
    文件清理管理器
    负责：
    1. 分析文件价值
    2. 生成清理建议
    3. 等待用户确认
    4. 执行清理
    """
    
    def __init__(self, log_callback=None, controller=None):
        self.log_callback = log_callback
        self.controller = controller
        self._log("✅ 文件清理管理器已初始化")
    
    def _log(self, msg):
        if self.log_callback:
            self.log_callback(msg)
    
    def analyze_project_files(self, project_dir: str) -> Dict[str, Any]:
        """
        分析项目文件，生成清理建议
        ✅ 调用 AI 思考哪些文件重要
        """
        self._log("🔍 开始分析项目文件...")
        
        file_categories = {
            "core": [],      # 核心文件（保留）
            "reports": [],   # 报告文件（保留）
            "drafts": [],    # 草稿文件（可归档）
            "temp": [],      # 临时文件（可删除）
            "logs": [],      # 日志文件（可压缩）
            "backups": []    # 备份文件（保留最近）
        }
        
        for root, dirs, files in os.walk(project_dir):
            for filename in files:
                filepath = os.path.join(root, filename)
                file_size = os.path.getsize(filepath)
                
                # 分类
                if filename.endswith('_完整版.txt') or filename == 'project_config.json':
                    file_categories["core"].append({"path": filepath, "size": file_size})
                elif filename.endswith('_报告.txt') or filename.endswith('_总结.txt'):
                    file_categories["reports"].append({"path": filepath, "size": file_size})
                elif 'draft' in filename.lower() or '草稿' in filename:
                    file_categories["drafts"].append({"path": filepath, "size": file_size})
                elif filename.endswith('.tmp') or filename.startswith('debug_'):
                    file_categories["temp"].append({"path": filepath, "size": file_size})
                elif filename.endswith('.log'):
                    file_categories["logs"].append({"path": filepath, "size": file_size})
                elif 'backup' in filename.lower() or '备份' in filename:
                    file_categories["backups"].append({"path": filepath, "size": file_size})
        
        # 生成清理建议
        cleanup_proposal = {
            "project_dir": project_dir,
            "analyzed_at": datetime.now().isoformat(),
            "categories": file_categories,
            "recommendations": {
                "keep": file_categories["core"] + file_categories["reports"],
                "archive": file_categories["drafts"] + file_categories["logs"],
                "delete": file_categories["temp"],
                "review": file_categories["backups"]  # 需要用户决定保留哪些备份
            },
            "space_savings": {
                "delete_bytes": sum(f["size"] for f in file_categories["temp"]),
                "archive_bytes": sum(f["size"] for f in file_categories["drafts"] + file_categories["logs"])
            }
        }
        
        # 保存清理建议
        proposal_file = os.path.join(project_dir, "system", "cleanup_proposal.json")
        with open(proposal_file, 'w', encoding='utf-8') as f:
            json.dump(cleanup_proposal, f, indent=2, ensure_ascii=False)
        
        self._log(f"✅ 文件分析完成，生成清理建议")
        return cleanup_proposal
    
    def execute_cleanup(self, proposal: Dict[str, Any], confirmed_actions: List[str]) -> Dict[str, Any]:
        """
        执行清理（需要用户确认）
        confirmed_actions: ['delete', 'archive', ...]
        """
        results = {
            "deleted": [],
            "archived": [],
            "kept": [],
            "errors": []
        }
        
        recommendations = proposal.get("recommendations", {})
        
        # 执行删除
        if "delete" in confirmed_actions:
            for file_info in recommendations.get("delete", []):
                try:
                    os.remove(file_info["path"])
                    results["deleted"].append(file_info["path"])
                    self._log(f"🗑️ 已删除：{file_info['path']}")
                except Exception as e:
                    results["errors"].append({"path": file_info["path"], "error": str(e)})
        
        # 执行归档
        if "archive" in confirmed_actions:
            archive_dir = os.path.join(proposal["project_dir"], "99_备份", "archived")
            os.makedirs(archive_dir, exist_ok=True)
            
            for file_info in recommendations.get("archive", []):
                try:
                    filename = os.path.basename(file_info["path"])
                    archive_path = os.path.join(archive_dir, filename)
                    os.rename(file_info["path"], archive_path)
                    results["archived"].append(archive_path)
                    self._log(f"📦 已归档：{file_info['path']}")
                except Exception as e:
                    results["errors"].append({"path": file_info["path"], "error": str(e)})
        
        # 保留的文件
        for file_info in recommendations.get("keep", []):
            results["kept"].append(file_info["path"])
        
        return results
    
    def generate_cleanup_report(self, results: Dict[str, Any]) -> str:
        """生成清理报告"""
        report = []
        report.append("=" * 60)
        report.append("📋 文件清理报告")
        report.append("=" * 60)
        report.append(f"删除文件：{len(results['deleted'])}个")
        report.append(f"归档文件：{len(results['archived'])}个")
        report.append(f"保留文件：{len(results['kept'])}个")
        report.append(f"错误：{len(results['errors'])}个")
        
        if results["errors"]:
            report.append("\n错误详情:")
            for err in results["errors"]:
                report.append(f"  - {err['path']}: {err['error']}")
        
        report.append("=" * 60)
        
        report_text = "\n".join(report)
        
        # 保存报告
        report_file = os.path.join(os.path.dirname(results['kept'][0]) if results['kept'] else ".", "清理报告.txt")
        with open(report_file, 'w', encoding='utf-8') as f:
            f.write(report_text)
        
        return report_text
