# ==================== 启动扫描器 ====================
import os
import json
import time
from datetime import datetime, timedelta
from .config import LOGS_DIR, KNOWLEDGE_BASE_DIR, TASK_TRACKING_DIR


class StartupScanner:
    """
    启动扫描器
    启动时扫描所有相关文件，进行查漏补错学习
    分析日志、失败记录、对话历史等
    """
    
    def __init__(self, log_callback=None, knowledge_manager=None, 
                 learning_engine=None, permanent_memory=None):
        self.log_callback = log_callback
        self.knowledge_manager = knowledge_manager
        self.learning_engine = learning_engine
        self.permanent_memory = permanent_memory
        self.scan_report_file = os.path.join(LOGS_DIR, "self_check.log")
        os.makedirs(LOGS_DIR, exist_ok=True)
        self._log("✅ 启动扫描器已初始化")
    
    def _log(self, msg):
        if self.log_callback:
            self.log_callback(msg)
    
    def full_startup_scan(self, learning_time_minutes=15):
        """完整启动扫描"""
        self._log("🚀 开始启动扫描...")
        start_time = datetime.now()
        scan_results = {
            "start_time": start_time.isoformat(),
            "end_time": None,
            "duration_minutes": 0,
            "scans_performed": [],
            "issues_found": [],
            "learning_triggered": [],
            "recommendations": []
        }
        
        # 1. 扫描永久规则
        self._log("📋 扫描永久规则...")
        rule_scan = self._scan_permanent_rules()
        scan_results["scans_performed"].append({"type": "permanent_rules", "result": rule_scan})
        
        # 2. 扫描知识库
        self._log("📚 扫描知识库...")
        knowledge_scan = self._scan_knowledge_base()
        scan_results["scans_performed"].append({"type": "knowledge_base", "result": knowledge_scan})
        
        # 3. 扫描学习日志
        self._log("📖 扫描学习日志...")
        learning_scan = self._scan_learning_logs()
        scan_results["scans_performed"].append({"type": "learning_logs", "result": learning_scan})
        
        # 4. 扫描操作日志
        self._log("⚙️ 扫描操作日志...")
        operation_scan = self._scan_operation_logs()
        scan_results["scans_performed"].append({"type": "operation_logs", "result": operation_scan})
        
        # 5. 扫描失败记录
        self._log("❌ 扫描失败记录...")
        failure_scan = self._scan_failure_records()
        scan_results["scans_performed"].append({"type": "failure_records", "result": failure_scan})
        
        # 6. 扫描对话历史
        self._log("💬 扫描对话历史...")
        conversation_scan = self._scan_conversation_history()
        scan_results["scans_performed"].append({"type": "conversation_history", "result": conversation_scan})
        
        # 7. 扫描插件状态
        self._log("🔌 扫描插件状态...")
        plugin_scan = self._scan_plugin_status()
        scan_results["scans_performed"].append({"type": "plugin_status", "result": plugin_scan})
        
        # 8. 扫描备份状态
        self._log("💾 扫描备份状态...")
        backup_scan = self._scan_backup_status()
        scan_results["scans_performed"].append({"type": "backup_status", "result": backup_scan})
        
        # 9. 执行启动学习
        if learning_time_minutes > 0:
            self._log(f"📖 执行启动学习（{learning_time_minutes}分钟）...")
            learning_result = self._execute_startup_learning(learning_time_minutes)
            scan_results["learning_triggered"].append(learning_result)
        
        # 10. 生成建议
        scan_results["recommendations"] = self._generate_recommendations(scan_results)
        
        end_time = datetime.now()
        scan_results["end_time"] = end_time.isoformat()
        scan_results["duration_minutes"] = (end_time - start_time).total_seconds() / 60
        
        self._save_scan_report(scan_results)
        self._log(f"✅ 启动扫描完成，耗时 {scan_results['duration_minutes']:.1f} 分钟")
        return scan_results
    
    def _scan_permanent_rules(self):
        if not self.permanent_memory: return {"status": "skipped"}
        stats = self.permanent_memory.get_statistics()
        validation = self.permanent_memory.validate_rules()
        return {"status": "completed", "total_rules": stats.get("total_rules", 0), "invalid_rules": validation.get("invalid", 0)}
    
    def _scan_knowledge_base(self):
        if not self.knowledge_manager: return {"status": "skipped"}
        stats = self.knowledge_manager.get_statistics()
        self.knowledge_manager.apply_time_decay()
        return {"status": "completed", "total_knowledge": stats.get("total", 0), "verified_knowledge": stats.get("verified", 0)}
    
    def _scan_learning_logs(self):
        log_file = os.path.join(TASK_TRACKING_DIR, "learning_logs", "learning_history.json")
        if not os.path.exists(log_file): return {"status": "no_logs"}
        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                logs = json.load(f).get("logs", [])
            total = len(logs)
            success = sum(1 for l in logs if l.get("success"))
            return {"status": "completed", "total_sessions": total, "success_rate": success/total if total>0 else 0}
        except: return {"status": "error"}
    
    def _scan_operation_logs(self):
        log_files = [os.path.join(LOGS_DIR, f) for f in os.listdir(LOGS_DIR) if f.startswith("ai_actions_") and f.endswith(".log")]
        if not log_files: return {"status": "no_logs"}
        total, success = 0, 0
        for log_file in log_files[:5]:
            try:
                with open(log_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        total += 1
                        if '成功' in line: success += 1
            except: continue
        return {"status": "completed", "total_operations": total, "success_rate": success/total if total>0 else 0}
    
    def _scan_failure_records(self):
        failure_file = os.path.join(KNOWLEDGE_BASE_DIR, "failures.db")
        if not os.path.exists(failure_file): return {"status": "no_records"}
        try:
            import sqlite3
            conn = sqlite3.connect(failure_file)
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) FROM failures')
            total = cursor.fetchone()[0]
            conn.close()
            return {"status": "completed", "total_failures": total, "needs_learning": total > 0}
        except: return {"status": "error"}
    
    def _scan_conversation_history(self):
        new_rules_found = 0
        if not os.path.exists(KNOWLEDGE_BASE_DIR): return {"status": "no_records"}
        for filename in os.listdir(KNOWLEDGE_BASE_DIR):
            if filename.startswith('conversation_') and filename.endswith('.json'):
                try:
                    with open(os.path.join(KNOWLEDGE_BASE_DIR, filename), 'r', encoding='utf-8') as f:
                        msgs = json.load(f).get('messages', [])
                        for m in msgs:
                            if m.get('role') == 'user' and self.permanent_memory:
                                rules = self.permanent_memory.identify_rules_from_text(m['content'])
                                new_rules_found += len(rules)
                except: continue
        return {"status": "completed", "potential_rules_found": new_rules_found}

    
    def _scan_plugin_status(self):
        from .plugin_auto_loader import PluginAutoLoader
        try:
            loader = PluginAutoLoader(self.log_callback)
            stats = loader.get_statistics()
            return {"status": "completed", "total_plugins": stats.get("total_plugins", 0), "active_plugins": stats.get("active_plugins", 0)}
        except: return {"status": "error"}
    
    def _scan_backup_status(self):
        from .file_backup import FileBackupManager
        try:
            backup_mgr = FileBackupManager(self.log_callback)
            stats = backup_mgr.get_statistics()
            return {"status": "completed", "total_backups": stats.get("total_backups", 0)}
        except: return {"status": "error"}
    
    def _execute_startup_learning(self, time_budget_minutes):
        """执行启动学习 - 真正学习有用知识"""
        start_time = time.time()
        learning_results = {
            "topics_learned": [], 
            "time_spent_minutes": 0, 
            "success": False,
            "learning_methods_used": [],
            "knowledge_gained": 0,
            "solutions_learned": 0
        }
    
        if not self.knowledge_manager:
            learning_results["success"] = True
            return learning_results
    
        try:
            # 1. 学习常见任务的解决方案
            self._log("📖 学习常见任务解决方案")
            common_tasks = [
                "如何创建和保存文件",
                "如何生成长文本内容", 
                "如何查询天气信息",
                "如何执行系统命令",
                "如何管理文件和文件夹"
            ]
        
            for task in common_tasks:
                if (time.time() - start_time) / 60 >= time_budget_minutes * 0.6:
                    break
            
                # 为每个常见任务创建解决方案知识
                solution_id = self._learn_task_solution(task)
                if solution_id:
                    learning_results["solutions_learned"] += 1
                    learning_results["topics_learned"].append({
                        "topic": task,
                        "type": "solution",
                        "solution_id": solution_id
                    })
        
            learning_results["learning_methods_used"].append("solution_learning")
            learning_results["success"] = True
        
        except Exception as e:
            self._log(f"❌ 启动学习失败：{e}")
            learning_results["error"] = str(e)
     
        finally:
            learning_results["time_spent_minutes"] = (time.time() - start_time) / 60
            return learning_results

    def _learn_task_solution(self, task_description):
        """学习特定任务的解决方案"""
        try:
            # 这里可以调用AI模型来生成解决方案
            # 或者使用预定义的解决方案模板
        
            solution_content = f"""
    # 解决方案：{task_description}

    ## 执行步骤：
    1. 分析用户请求的具体要求
    2. 确定合适的执行方法和工具
    3. 准备必要的参数和资源
    4. 执行核心操作
    5. 验证执行结果
    6. 处理可能的错误情况
    7. 向用户返回最终结果

    ## 常用工具：
    - 文件操作插件
    - 内容生成AI
    - 系统命令执行

    ## 注意事项：
    - 确保文件路径正确
    - 验证内容质量
    - 处理权限问题
    - 提供用户反馈
    """
        
            # 保存到知识库
            result = self.knowledge_manager.add_knowledge(
                topic=f"解决方案：{task_description}",
                content=solution_content,
                content_type="solution",
                source="startup_learning",
                tags=["solution", "common_task"]
            )
        
            return result.get("knowledge_id")
    
        except:
            return None


    def _learn_from_conversation_history(self):
        """从对话历史学习用户偏好"""
        learned = []
        if not os.path.exists(KNOWLEDGE_BASE_DIR):
            return learned
    
        try:
            for filename in os.listdir(KNOWLEDGE_BASE_DIR):
                if filename.startswith('conversation_') and filename.endswith('.json'):
                    try:
                        with open(os.path.join(KNOWLEDGE_BASE_DIR, filename), 'r', encoding='utf-8') as f:
                            data = json.load(f)
                    
                        for msg in data.get('messages', []):
                            if msg.get('role') == 'user':
                                content = msg.get('content', '')
                                # 学习用户名
                                if "叫我" in content or "称呼我" in content:
                                    learned.append({"topic": "user_preference", "type": "user_name"})
                                # 学习偏好
                                if "喜欢" in content or "不喜欢" in content:
                                    learned.append({"topic": "user_preference", "type": "likes_dislikes"})
                                # 学习习惯
                                if "经常" in content or "总是" in content:
                                    learned.append({"topic": "user_habit", "type": "frequent_actions"})
                
                    except:
                        continue
        
            return learned[:10]  # 返回前10条
    
        except:
            return []

    def _analyze_failure_patterns(self):
        """分析失败模式"""
        patterns = []
        failure_file = os.path.join(KNOWLEDGE_BASE_DIR, "failures.db")
    
        if os.path.exists(failure_file):
            try:
                import sqlite3
                conn = sqlite3.connect(failure_file)
                cursor = conn.cursor()
            
                # 获取常见的失败类型
                cursor.execute('''
                    SELECT error_reason, COUNT(*) as count 
                    FROM failures 
                    GROUP BY error_reason 
                    ORDER BY count DESC 
                    LIMIT 5
                ''')
            
                for row in cursor.fetchall():
                    patterns.append({
                        "topic": "failure_pattern",
                        "error": row[0],
                        "frequency": row[1]
                    })
            
                conn.close()
        
            except:
                pass
    
        return patterns
    
    
    def _generate_recommendations(self, scan_results):
        recs = []
        for scan in scan_results["scans_performed"]:
            if scan["type"] == "knowledge_base" and scan["result"].get("verified", 0) < 0.5:
                recs.append({"priority": "high", "type": "knowledge_verification", "message": "知识库验证率较低，建议执行深度验证"})
            if scan["type"] == "failure_records" and scan["result"].get("needs_learning"):
                recs.append({"priority": "high", "type": "failure_learning", "message": "存在未解决的失败记录，建议触发学习"})
        return recs
    
    def _save_scan_report(self, scan_results):
        with open(self.scan_report_file, 'w', encoding='utf-8') as f:
            json.dump(scan_results, f, indent=2, ensure_ascii=False)
    
    def apply_recommendations(self, recommendations):
        applied = 0
        for rec in recommendations:
            if rec["type"] == "failure_learning" and self.learning_engine:
                # 触发一次学习
                self.learning_engine.execute_learning({}, {}, [])
                applied += 1
        return {"success": True, "applied": applied}
