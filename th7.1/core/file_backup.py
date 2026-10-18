import os
import shutil
import json
import sqlite3
import hashlib
from datetime import datetime, timedelta
from .config import KNOWLEDGE_BASE_DIR

class FileBackupManager:
    """文件备份管理器：支持自动备份、手动备份、恢复、清理。"""
    
    def __init__(self, log_callback=None):
        self.log_callback = log_callback
        self.backups_dir = os.path.join(KNOWLEDGE_BASE_DIR, "backups")
        self.db_path = os.path.join(KNOWLEDGE_BASE_DIR, "backups.db")
        os.makedirs(self.backups_dir, exist_ok=True)
        self._init_database()
        self._log("✅ 文件备份管理器已初始化")
    
    def _log(self, msg: str, level: str = "INFO"):
        if self.log_callback:
            try: self.log_callback(msg, level=level)
            except TypeError: self.log_callback(msg)

    def _init_database(self):
        """初始化备份数据库，使用上下文管理器防止锁死。"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS backup_records (
                    id TEXT PRIMARY KEY,
                    original_file TEXT NOT NULL,
                    backup_file TEXT NOT NULL,
                    backup_reason TEXT,
                    file_hash TEXT,
                    file_size INTEGER,
                    created_at TEXT,
                    restored INTEGER DEFAULT 0,
                    restored_at TEXT,
                    expires_at TEXT
                )
            ''')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_original ON backup_records(original_file)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_expires ON backup_records(expires_at)')
            conn.commit()
    
    def create_backup(self, file_path: str, reason: str = "manual", keep_days: int = 30) -> dict:
        """创建文件备份。"""
        if not os.path.exists(file_path):
            return {"success": False, "message": "文件不存在"}
        
        backup_id = hashlib.md5(f"{file_path}{datetime.now().isoformat()}".encode()).hexdigest()[:16]
        filename = os.path.basename(file_path)
        # 🌟 修复时间格式：使用 %Y-%m-%d %H:%M:%S 完美兼容 SQLite 的 CURRENT_TIMESTAMP
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_filename = f"{filename}.{timestamp}.bak"
        backup_path = os.path.join(self.backups_dir, backup_filename)
        
        try:
            shutil.copy2(file_path, backup_path)
            file_hash = self._calculate_file_hash(file_path)
            file_size = os.path.getsize(file_path)
            expires_at = (datetime.now() + timedelta(days=keep_days)).strftime('%Y-%m-%d %H:%M:%S')
            created_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            self._record_backup(backup_id, file_path, backup_path, reason, file_hash, file_size, created_at, expires_at)
            self._log(f"💾 创建备份：{filename} → {backup_filename}")
            return {"success": True, "backup_id": backup_id, "backup_path": backup_path, "expires_at": expires_at}
        except Exception as e:
            self._log(f"❌ 创建备份失败：{e}")
            return {"success": False, "error": str(e)}
    
    def _record_backup(self, backup_id, original_file, backup_file, reason, file_hash, file_size, created_at, expires_at):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                INSERT INTO backup_records 
                (id, original_file, backup_file, backup_reason, file_hash, file_size, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (backup_id, original_file, backup_file, reason, file_hash, file_size, created_at, expires_at))
            conn.commit()
    
    def _calculate_file_hash(self, filepath: str) -> str:
        """🌟 修复 OOM：使用 1MB 分块读取计算 MD5，防止大文件撑爆内存。"""
        hasher = hashlib.md5()
        with open(filepath, 'rb') as f:
            while chunk := f.read(1024 * 1024):
                hasher.update(chunk)
        return hasher.hexdigest()
    
    def restore_backup(self, backup_id: str, target_path: str = None) -> dict:
        """恢复备份。"""
        backup_record = self._get_backup_record(backup_id)
        if not backup_record:
            return {"success": False, "message": "备份记录不存在"}
        
        backup_file = backup_record["backup_file"]
        if not os.path.exists(backup_file):
            return {"success": False, "message": "备份文件物理丢失"}
        
        restore_path = target_path if target_path else backup_record["original_file"]
        os.makedirs(os.path.dirname(restore_path), exist_ok=True)
        
        try:
            shutil.copy2(backup_file, restore_path)
            self._mark_restored(backup_id)
            self._log(f"🔄 恢复备份：{backup_id} → {restore_path}")
            return {"success": True, "restore_path": restore_path, "restored_at": datetime.now().isoformat()}
        except Exception as e:
            self._log(f"❌ 恢复备份失败：{e}")
            return {"success": False, "error": str(e)}
    
    def _get_backup_record(self, backup_id: str) -> dict:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute('SELECT * FROM backup_records WHERE id = ?', (backup_id,))
            row = cursor.fetchone()
        if row:
            return {
                "id": row[0], "original_file": row[1], "backup_file": row[2], "backup_reason": row[3],
                "file_hash": row[4], "file_size": row[5], "created_at": row[6], "restored": row[7],
                "restored_at": row[8], "expires_at": row[9]
            }
        return None
    
    def _mark_restored(self, backup_id: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('UPDATE backup_records SET restored = 1, restored_at = ? WHERE id = ?', 
                         (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), backup_id))
            conn.commit()
    
    def list_backups(self, original_file: str = None, include_expired: bool = False) -> list:
        with sqlite3.connect(self.db_path) as conn:
            if original_file:
                cursor = conn.execute('SELECT * FROM backup_records WHERE original_file = ? ORDER BY created_at DESC', (original_file,))
            elif include_expired:
                cursor = conn.execute('SELECT * FROM backup_records ORDER BY created_at DESC')
            else:
                # 🌟 修复时间比较：统一格式后直接字符串比较
                now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                cursor = conn.execute('SELECT * FROM backup_records WHERE expires_at > ? ORDER BY created_at DESC', (now_str,))
            
            backups = []
            for row in cursor.fetchall():
                backups.append({
                    "id": row[0], "original_file": row[1], "backup_file": row[2], "backup_reason": row[3],
                    "file_hash": row[4], "file_size": row[5], "created_at": row[6], "restored": row[7],
                    "restored_at": row[8], "expires_at": row[9]
                })
        return backups
    
    def cleanup_expired_backups(self) -> dict:
        """清理过期备份。"""
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        deleted_count = 0
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute('SELECT id, backup_file FROM backup_records WHERE expires_at < ?', (now_str,))
            expired = cursor.fetchall()
            
            for backup_id, backup_file in expired:
                if os.path.exists(backup_file):
                    try: os.remove(backup_file)
                    except Exception: pass
                conn.execute('DELETE FROM backup_records WHERE id = ?', (backup_id,))
                deleted_count += 1
            conn.commit()
            
        if deleted_count > 0:
            self._log(f"🧹 清理了 {deleted_count} 个过期备份")
        return {"success": True, "deleted": deleted_count}
    
    def get_statistics(self) -> dict:
        """获取备份统计信息。"""
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with sqlite3.connect(self.db_path) as conn:
            total = conn.execute('SELECT COUNT(*) FROM backup_records').fetchone()[0]
            not_restored = conn.execute('SELECT COUNT(*) FROM backup_records WHERE restored = 0').fetchone()[0]
            expired = conn.execute('SELECT COUNT(*) FROM backup_records WHERE expires_at < ?', (now_str,)).fetchone()[0]
            total_size = conn.execute('SELECT SUM(file_size) FROM backup_records').fetchone()[0] or 0
            
        return {
            "total_backups": total, "not_restored": not_restored, "expired": expired,
            "total_size_bytes": total_size, "total_size_mb": total_size / (1024 * 1024)
        }
    
    def backup_core_files(self, files_to_backup: list) -> dict:
        results = {"success": 0, "failed": 0, "details": []}
        for file_path in files_to_backup:
            if os.path.exists(file_path):
                result = self.create_backup(file_path, reason="core_file_auto")
                if result["success"]: results["success"] += 1
                else: results["failed"] += 1
                results["details"].append({"file": file_path, "result": result})
            else:
                results["failed"] += 1
                results["details"].append({"file": file_path, "result": {"success": False, "message": "文件不存在"}})
        return results

    def auto_backup_before_modify(self, file_path: str) -> dict:
        self._log(f"🔒 修改前自动备份：{file_path}")
        return self.create_backup(file_path, reason="before_modify", keep_days=90)
