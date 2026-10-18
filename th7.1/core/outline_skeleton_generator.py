import os
import json
import hashlib
from typing import Dict, Any
from .config import TASK_TRACKING_DIR
from .vector_memory import VectorMemory
from .log_mixin import LogMixin

class OutlineSkeletonGenerator(LogMixin):
    """生成大纲骨架（章节标题 + 目标字数），不依赖 LLM。"""

    def __init__(self, log_callback=None):
        super().__init__(log_callback)
        self.vm = VectorMemory(log_callback=log_callback)
        self.skeleton_dir = os.path.join(TASK_TRACKING_DIR, "outlines")
        os.makedirs(self.skeleton_dir, exist_ok=True)

    def _hash_task(self, task_id: str) -> str:
        return hashlib.sha256(task_id.encode()).hexdigest()[:12]

    def _default_title(self, task_plan: Dict[str, Any]) -> str:
        genre = task_plan.get("genre", "小说")
        return f"《{genre}·未命名》"

    def generate(self, task_plan: Dict[str, Any]) -> Dict[str, Any]:
        task_id = task_plan.get("task_id")
        if not task_id:
            self._log("❌ skeleton 生成缺少 task_id", level="ERROR")
            return {"success": False, "error": "task_id 缺失"}

        total_chapters = int(task_plan.get("total_chapters", 1))
        words_per = int(task_plan.get("words_per_chapter", 2500))

        skeleton = {
            "title": task_plan.get("title") or self._default_title(task_plan),
            "genre": task_plan.get("genre", "通用"),
            "total_chapters": total_chapters,
            "words_per_chapter": words_per,
            "chapters": [],
        }

        for i in range(1, total_chapters + 1):
            skeleton["chapters"].append({"title": f"第 {i} 章", "target_word_count": words_per, "summary": "", "keywords": []})

        skeleton_path = os.path.join(self.skeleton_dir, f"skeleton_{task_id}.json")
        tmp_path = skeleton_path + ".tmp"
        try:
            # 🌟 工业级原子写入：加入 fsync 防止断电导致文件半写损坏
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(skeleton, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, skeleton_path)
            self._log(f"✅ 大纲雏形已生成：{skeleton_path}")
            return {"success": True, "skeleton_path": skeleton_path, "skeleton": skeleton}
        except Exception as e:
            if os.path.exists(tmp_path):
                try: os.remove(tmp_path)
                except Exception: pass
            self._log(f"❌ 写入雏形文件失败: {e}", level="ERROR")
            return {"success": False, "error": str(e)}

    def try_vector_retrieval(self, task_plan: Dict[str, Any]) -> Dict[str, Any]:
        query = {"genre": task_plan.get("genre", ""), "words_per_chapter": task_plan.get("words_per_chapter", 2500), "total_chapters": task_plan.get("total_chapters", 1)}
        candidates = self.vm.search_content(json.dumps(query), limit=3)
        if not candidates: return {"success": False}
        
        candidate_id = candidates[0]["content_id"]
        outline_path = os.path.join(self.skeleton_dir, f"skeleton_{candidate_id}.json")
        if os.path.exists(outline_path):
            try:
                with open(outline_path, "r", encoding="utf-8") as f:
                    skeleton = json.load(f)
                dst = os.path.join(self.skeleton_dir, f"skeleton_{task_plan['task_id']}.json")
                
                # 🌟 修复非原子写入：改为 tmp + fsync + replace
                tmp_dst = dst + ".tmp"
                with open(tmp_dst, "w", encoding="utf-8") as f:
                    json.dump(skeleton, f, ensure_ascii=False, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_dst, dst)
                
                self._log(f"🔎 向量检索得到相似雏形并写入: {dst}")
                return {"success": True, "skeleton_path": dst, "skeleton": skeleton}
            except Exception as e:
                self._log(f"⚠️ 向量检索后读取雏形失败: {e}", level="WARN")
        return {"success": False}
