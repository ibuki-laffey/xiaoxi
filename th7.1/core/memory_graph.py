# core/memory_graph.py
# ==============================================================================
# 🌟 长文本记忆图谱引擎 (Long-Context Memory Knowledge Graph)
# ==============================================================================
import os
import json
import re
import logging  # 🌟 致命修复：补全缺失的 logging 导入
from typing import Dict, List, Any, Set, Optional
from collections import deque

# 🌟 提取到模块级别，避免在方法内重复创建
LOGGER = logging.getLogger(__name__)

class MemoryGraph:
    """轻量级记忆图谱存储与多跳检索引擎"""
    
    def __init__(self, project_dir: str):
        self.graph_dir = os.path.join(project_dir, "03_设定", "记忆图谱")
        os.makedirs(self.graph_dir, exist_ok=True)
        self.graph_file = os.path.join(self.graph_dir, "knowledge_graph.json")
        
        self.nodes = {}  
        self.edges = []  
        self._load()

    def _load(self):
        if os.path.exists(self.graph_file):
            try:
                with open(self.graph_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.nodes = data.get("nodes", {})
                    self.edges = data.get("edges", [])
            except Exception as e:
                LOGGER.warning(f"MemoryGraph 加载失败，将使用空图谱: {e}")

    def save(self):
        """🌟 核心修复：改用原子写入，防止断电或进程被杀导致 JSON 损坏"""
        tmp_file = self.graph_file + ".tmp"
        try:
            with open(tmp_file, 'w', encoding='utf-8') as f:
                json.dump({"nodes": self.nodes, "edges": self.edges}, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_file, self.graph_file)
        except Exception as e:
            LOGGER.error(f"MemoryGraph 保存失败: {e}")
            if os.path.exists(tmp_file):
                try: os.remove(tmp_file)
                except: pass

    def upsert_node(self, name: str, entity_type: str, attributes: Dict[str, Any]) -> None:
        if not isinstance(attributes, dict):
            LOGGER.warning(f"MemoryGraph.upsert_node: attributes 非 dict（{type(attributes)}），已置空")
            attributes = {}

        if name in self.nodes:
            self.nodes[name]["attributes"].update(attributes)
            self.nodes[name]["type"] = entity_type
        else:
            self.nodes[name] = {"type": entity_type, "attributes": attributes}

    def add_edge(self, source: str, target: str, relation: str, attributes: Optional[Dict[str, Any]] = None) -> None:
        if attributes is None: 
            attributes = {}
        if not isinstance(attributes, dict):
            LOGGER.warning(f"MemoryGraph.add_edge: attributes 非 dict（{type(attributes)}），已置空")
            attributes = {}

        if source not in self.nodes: 
            self.upsert_node(source, "unknown", {})
        if target not in self.nodes: 
            self.upsert_node(target, "unknown", {})

        for edge in self.edges:
            if edge["source"] == source and edge["target"] == target and edge["relation"] == relation:
                if attributes: 
                    edge["attributes"].update(attributes)
                return

        self.edges.append({
            "source": source, "target": target, 
            "relation": relation, "attributes": attributes
        })

    def get_context_subgraph(self, seed_entities: List[str], max_hops: int = 2, max_nodes: int = 15) -> str:
        """🌟 核心：多跳子图检索 (BFS)"""
        if not seed_entities or not self.nodes: 
            return ""

        visited_nodes = set()
        queue = deque([(e, 0) for e in seed_entities if e in self.nodes])
        
        while queue and len(visited_nodes) < max_nodes:
            current, depth = queue.popleft()
            if current in visited_nodes or depth > max_hops: 
                continue
            visited_nodes.add(current)
            
            if depth < max_hops:
                for edge in self.edges:
                    if edge["source"] == current and edge["target"] not in visited_nodes:
                        queue.append((edge["target"], depth + 1))
                    elif edge["target"] == current and edge["source"] not in visited_nodes:
                        queue.append((edge["source"], depth + 1))

        if not visited_nodes: 
            return ""

        subgraph_text = "【🕸️ 当前剧情相关记忆图谱（隐藏关系与状态）】\n▶ 核心实体状态：\n"
        for node_name in visited_nodes:
            node = self.nodes[node_name]
            attrs = ", ".join([f"{k}:{v}" for k, v in node.get("attributes", {}).items()])
            subgraph_text += f"- [{node.get('type', 'unknown')}] {node_name}: {attrs}\n"
            
        subgraph_text += "\n▶ 实体间关系（务必在剧情中体现张力）：\n"
        for edge in self.edges:
            if edge["source"] in visited_nodes or edge["target"] in visited_nodes:
                attrs = f" ({', '.join([f'{k}:{v}' for k,v in edge.get('attributes', {}).items()])})" if edge.get("attributes") else ""
                subgraph_text += f"- {edge['source']} --[{edge['relation']}]--> {edge['target']}{attrs}\n"
                
        return subgraph_text


class GraphExtractor:
    """基于 LLM 的图谱动态抽取器"""
    
    def __init__(self, memory_graph: MemoryGraph, log_callback=None):
        self.graph = memory_graph
        self._log = log_callback or (lambda msg, **kwargs: print(msg))

    def _robust_extract_json(self, text: str) -> Optional[Dict]:
        """🌟 防弹 JSON 提取器"""
        if not text: return None
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
        text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE).strip()
        try: return json.loads(text)
        except: pass
        
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            json_str = match.group()
            json_str = re.sub(r",\s*([}\]])", r"\1", json_str)
            try: return json.loads(json_str)
            except: pass
        return None

    def extract_from_chapter(self, chapter_content: str, chapter_num: int):
        """从刚生成的章节正文中，抽取新的实体和关系，更新图谱
        【核心修复点】：添加对 LLM 返回的 nodes 和 edges 中 attributes 字段类型的验证
        问题：原代码直接假设 attributes 是字典，若 LLM 返回字符串等非字典类型
        将导致在 upsert_node 或 add_edge 中调用 .update() 时引发 AttributeError
        修改：检查 attributes 是否为字典，若不是则记录警告并使用空字典替代
        影响：防止因 LLM 输出格式异常导致的图谱抽取失败，提升系统容错性
        """
        from .config import call_ollama_safe
        
        # 截取正文前 2000 字和后 1000 字，节省 Token
        text_slice = chapter_content[:2000] + "\n...\n" + chapter_content[-1000:] if len(chapter_content) > 3000 else chapter_content
        
        prompt = f"""你是极其严谨的长篇小说知识图谱构建师。请阅读以下第 {chapter_num} 章的正文片段，提取其中**新增或发生变化**的实体（人物、物品、地点、组织）以及它们之间的关系。

【正文片段】
{text_slice}

【任务】
输出 JSON 对象，包含两个数组：
1. "nodes": 新增或状态更新的实体。格式：{{"name": "实体名", "type": "person/item/location/faction", "attributes": {{"境界": "元婴期", "状态": "重伤"}}}}
2. "edges": 实体间的新关系或关系变化。格式：{{"source": "A", "target": "B", "relation": "师徒/敌对/拥有/位于/暗恋", "attributes": {{"程度": "死仇"}}}}

注意：
- 只提取对后续剧情有影响的**核心实体和关键关系**，忽略路人甲和无关紧要的物品。
- 如果某实体的状态发生了变化（如主角突破了境界、武器损坏了），请在 nodes 中更新其 attributes。
- 如果没有提取到任何有价值的新信息，请返回空数组 {{ "nodes": [], "edges": [] }}。
只输出纯 JSON！绝不输出任何解释！"""

        try:
            resp = call_ollama_safe(
                [{"role": "system", "content": "纯 JSON API"}, {"role": "user", "content": prompt}], 
                options={"num_predict": 1000, "temperature": 0.2, "format": "json"}
            )
            raw = resp.get("content", "") if isinstance(resp, dict) else str(resp)
            
            # 🌟 使用类内的防弹提取器
            data = self._robust_extract_json(raw)
            if not data: return
            
            nodes = data.get("nodes", [])
            edges = data.get("edges", [])
            
            update_count = 0
            # 【核心修复】：验证 nodes 中每个元素的 attributes 类型
            for node in nodes:
                if "name" in node and "type" in node:
                    attributes = node.get("attributes", {})
                    if not isinstance(attributes, dict):
                        self._log(
                            f"⚠️ 记忆图谱抽取：节点 {node.get('name', '未知')} 的 attributes 不是字典（类型：{type(attributes)}），已忽略",
                            level="WARN"
                        )
                        attributes = {}
                    self.graph.upsert_node(node["name"], node["type"], attributes)
                    update_count += 1
                    
            # 【核心修复】：验证 edges 中每个元素的 attributes 类型
            for edge in edges:
                if "source" in edge and "target" in edge and "relation" in edge:
                    attributes = edge.get("attributes", {})
                    if not isinstance(attributes, dict):
                        self._log(
                            f"⚠️ 记忆图谱抽取：边 {edge.get('source', '未知')} -> {edge.get('target', '未知')} 的 attributes 不是字典（类型：{type(attributes)}），已忽略",
                            level="WARN"
                        )
                        attributes = {}
                    self.graph.add_edge(edge["source"], edge["target"], edge["relation"], attributes)
                    update_count += 1
                    
            if update_count > 0:
                self.graph.save()
                self._log(f"🕸️ 记忆图谱已更新：新增/更新 {len(nodes)} 个节点，{len(edges)} 条关系。")
        except Exception as e:
            self._log(f"⚠️ 记忆图谱抽取失败: {e}", level="WARN")
