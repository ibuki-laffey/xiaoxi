import requests
import json
import re
from typing import Dict, Any, Optional

class GemmaOllamaClient:
    """
    Ollama 客户端（兼容所有模型）。
    🌟 核心重构：彻底废弃手动拼接 XML 标签的模板化行为，改用 Ollama 原生 /api/chat 接口。
    这确保了 System Prompt 被模型原生理解，完美释放 LLM 的灵活性与主动性。
    """
    def __init__(self, base_url: str = "http://localhost:11434", model_name: str = "gemma4"):
        self.base_url = base_url
        self.model_name = model_name
        self.api_url = f"{self.base_url}/api/chat"

    def chat(self, user_prompt: str, system_prompt: str = "你是一个专业的 AI 助手。", 
             temperature: float = 0.7, require_json: bool = False, num_ctx: int = 16000, timeout: int = 300) -> Dict[str, Any]:
        
        # 动态注入 JSON 约束和 CoT 思考约束（不破坏原生 message 结构）
        json_instruction = "\n\n【输出格式要求】：最终结果必须是合法的纯 JSON 格式，不要包含任何 Markdown 标记（如 ```json）。" if require_json else ""
        cot_instruction = "\n\n【独立思考强制规则】：\n1. 你必须在 <thinking> 和 </thinking> 标签内，详细写下你的分析过程。\n2. 思考完成后，在标签外部输出最终结果。"
        
        messages = [
            {"role": "system", "content": system_prompt + cot_instruction + json_instruction},
            {"role": "user", "content": user_prompt}
        ]

        payload = {
            "model": self.model_name,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature, "num_ctx": num_ctx, "top_p": 0.9}
        }

        try:
            response = requests.post(self.api_url, json=payload, timeout=timeout)
            response.raise_for_status()
            result = response.json()
            raw_response = result.get("message", {}).get("content", "")
            
            thinking_match = re.search(r"<thinking>(.*?)</thinking>", raw_response, re.DOTALL)
            thinking_process = thinking_match.group(1).strip() if thinking_match else "未捕获到独立思考过程"
            final_response = re.sub(r"<thinking>.*?</thinking>", "", raw_response, flags=re.DOTALL).strip()
            
            if require_json:
                parsed_json = self._extract_and_repair_json(final_response)
                return {"success": True, "thinking": thinking_process, "data": parsed_json, "raw": final_response}
            
            return {"success": True, "thinking": thinking_process, "data": final_response, "raw": final_response}

        except Exception as e:
            return {"success": False, "thinking": "", "data": None, "error": str(e)}

    def _extract_and_repair_json(self, text: str) -> Optional[Dict]:
        """安全提取并修复 JSON，彻底废弃危险的单引号替换。"""
        try: return json.loads(text)
        except json.JSONDecodeError: pass

        # 清理 Markdown 代码块
        text = re.sub(r"```(?:json)?\s*", "", text)
        text = re.sub(r"```\s*", "", text)
        
        # 提取最外层的 {} 或 []
        brace_match = re.search(r"\{.*\}", text, re.DOTALL)
        bracket_match = re.search(r"\[.*\]", text, re.DOTALL)
        
        target_str = brace_match.group(0) if brace_match else (bracket_match.group(0) if bracket_match else text)

        # 🌟 安全修复：仅清理尾部多余的逗号，绝不替换单引号（防止破坏 value 中的合法单引号）
        target_str = re.sub(r",\s*([}\]])", r"\1", target_str)
        
        try: return json.loads(target_str)
        except json.JSONDecodeError: return None
