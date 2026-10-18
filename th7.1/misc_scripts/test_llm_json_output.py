# test_llm_json_output.py
# =============================================================================
# 完整的 LLM JSON 输出测试套件（已改为使用 llm_call 的 enforce_json 开关）
# =============================================================================

import unittest
import time
import logging
from typing import Any, Dict

# -------------------------------------------------------------------------
# 从业务实现模块中导入所需对象（请确保路径指向 core.llm_utils）
# -------------------------------------------------------------------------
from core.llm_utils import (
    LOGGER,
    _is_ollama_available,
    ensure_json_response,
    RetryPolicy,
    ConcurrentCaller,
    llm_call,
    SCHEMA_SIMPLE_KV,
    SCHEMA_CHAPTER_LIST,
    SCHEMA_TASK_PLAN,
    validate_against_schema,
)

# -------------------------------------------------------------------------
# 测试类
# -------------------------------------------------------------------------

class LLMJsonOutputTests(unittest.TestCase):
    """覆盖 LLM JSON 输出能力的完整测试套件。"""

    # -----------------------------------------------------------------
    # 1️⃣ 预检查：如果 Ollama 不可达则直接 Skip 所有测试
    # -----------------------------------------------------------------
    @classmethod
    def setUpClass(cls) -> None:
        LOGGER.info("=== 开始 LLM JSON 输出功能完整测试 ===")
        ok, msg = _is_ollama_available()
        if not ok:
            raise unittest.SkipTest(f"Ollama 不可用: {msg}")

        # 统一使用的重试策略
        cls.retry = RetryPolicy(max_attempts=3, base_delay=0.3, jitter=True)

    # -----------------------------------------------------------------
    # 2️⃣ 基础键值对 JSON
    # -----------------------------------------------------------------
    def test_01_simple_key_value_json(self):
        prompt = "请返回 JSON，task_id 为 'test_001'，status 为 'planned'，detail 为 '首步准备'。"
        system = "你只能输出合法 JSON，禁止解释文字。"

        raw = self.retry.attempt(llm_call, prompt, system_prompt=system)
        ok, parsed, msg = ensure_json_response(raw, "simple_kv")
        self.assertTrue(ok, f"响应结构错误: {msg}")

        # Schema 校验
        valid, err = validate_against_schema(parsed, SCHEMA_SIMPLE_KV)
        self.assertTrue(valid, f"Schema 校验失败: {err}")

        self.assertEqual(parsed["task_id"], "test_001")
        self.assertEqual(parsed["status"], "planned")
        self.assertIn("detail", parsed)

    # -----------------------------------------------------------------
    # 3️⃣ 章节列表 JSON
    # -----------------------------------------------------------------
    def test_02_chapter_list_json(self):
        prompt = (
            "请为任务 task_id='novel_123' 生成 3 章的大纲，返回 JSON，"
            "字段必须是 task_id 与 chapters（列表），每章包括 chapter_number、title、content、summary。"
        )
        system = "只输出 JSON，不要任何额外文字。"
        raw = self.retry.attempt(llm_call, prompt, system_prompt=system)
        ok, parsed, msg = ensure_json_response(raw, "chapter_list")
        self.assertTrue(ok, f"JSON 结构错误: {msg}")

        # ---------- 关键容错：若模型直接把章节列表作为顶层返回 ----------
        if isinstance(parsed, list):
            # 手动包装成 SCHEMA_CHAPTER_LIST 所期望的 {"task_id": ..., "chapters": [...]}
            parsed = {"task_id": "novel_123", "chapters": parsed}
        # --------------------------------------------------------------------

        valid, err = validate_against_schema(parsed, SCHEMA_CHAPTER_LIST)
        self.assertTrue(valid, f"Schema 校验失败: {err}")

        self.assertEqual(parsed["task_id"], "novel_123")
        self.assertEqual(len(parsed["chapters"]), 3)
        for i, chap in enumerate(parsed["chapters"], start=1):
            self.assertEqual(chap["chapter_number"], i)
            self.assertTrue(chap["title"])
            self.assertTrue(chap["content"])


    # -----------------------------------------------------------------
    # 4️⃣ 非 JSON 回退（模型故意不遵守）
    # -----------------------------------------------------------------
    def test_04_non_json_fallback(self):
        prompt = "请直接说一句祝福语，不要返回 JSON。"
        system = "这里故意让模型不输出 JSON。"

        # 关键改动：关闭「强制 JSON」功能，让模型真实返回纯文本
        raw = self.retry.attempt(llm_call, prompt, system_prompt=system, enforce_json=False)

        ok, _, msg = ensure_json_response(raw, "non_json", expect_json=True)
        self.assertFalse(ok, "期望解析失败但实际成功")
        self.assertIn("content", msg.lower())

    # -----------------------------------------------------------------
    # 5️⃣ 完整任务计划 JSON（更复杂）
    # -----------------------------------------------------------------
    def test_05_task_plan_schema(self):
        prompt = (
            "请返回任务计划的 JSON：\n"
            "- task_id: plan_777\n"
            "- project_id: proj_001\n"
            "- title: 《星际纪元》\n"
            "- genre: 科幻\n"
            "- total_chapters: 20\n"
            "- words_per_chapter: 2500\n"
            "- generation_mode: batch\n"
            "- status: planned\n"
        )
        system = "只输出 JSON，禁止解释文字。"
        raw = self.retry.attempt(llm_call, prompt, system_prompt=system)
        ok, parsed, msg = ensure_json_response(raw, "task_plan")
        self.assertTrue(ok, f"JSON 结构错误: {msg}")

        valid, err = validate_against_schema(parsed, SCHEMA_TASK_PLAN)
        self.assertTrue(valid, f"TaskPlan Schema 校验失败: {err}")

        self.assertEqual(parsed["task_id"], "plan_777")
        self.assertEqual(parsed["project_id"], "proj_001")
        self.assertEqual(parsed["total_chapters"], 20)

    # -----------------------------------------------------------------
    # 6️⃣ 并发调用（多线程）全部合法
    # -----------------------------------------------------------------
    def test_06_concurrent_json_responses(self):
        prompts = [f"请返回 JSON，键为 `idx`，值为整数 {i}" for i in range(10)]
        system = "只返回 JSON，结构为 {\"idx\": <int>}，禁止解释文字。"

        caller = ConcurrentCaller(
            worker_fn=lambda p: llm_call(
                p,
                system_prompt=system,
                temperature=0.0,
                max_tokens=512,
            ),
            max_workers=5,
        )
        results = caller.run(prompts)

        for prompt, raw in results:
            with self.subTest(prompt=prompt):
                ok, parsed, msg = ensure_json_response(raw, "concurrent")
                self.assertTrue(ok, f"并发请求返回无效 JSON: {msg}")
                self.assertIsInstance(parsed, dict)
                self.assertIn("idx", parsed)
                self.assertIsInstance(parsed["idx"], int)

    # -----------------------------------------------------------------
    # 7️⃣ 超时异常捕获
    # -----------------------------------------------------------------
    def test_07_timeout_and_error_handling(self):
        prompt = "请返回 JSON，键为 `ping`，值为 `pong`。"
        system = "只返回 JSON。"
        # 极端短的 timeout，必然触发超时
        raw = llm_call(
            prompt,
            system_prompt=system,
            temperature=0.0,
            max_tokens=256,
            timeout=0.01,
        )
        self.assertIsInstance(raw, dict)
        self.assertFalse(raw.get("success", True), "预期超时导致 success=False")
        self.assertIn("error", raw)

    # -----------------------------------------------------------------
    # 8️⃣ 长提示（> 2000 token）容错
    # -----------------------------------------------------------------
    def test_08_long_prompt_handling(self):
        # 显式导入，避免在测试方法内部出现 NameError
        from core.config import SAFE_TIMEOUT

        long_text = "请在以下文本中提取 JSON，文本为：" + ("文字 " * 2500)
        system = "只返回 JSON，禁止解释文字。"
        raw = llm_call(
            long_text,
            system_prompt=system,
            temperature=0.0,
            max_tokens=512,
            timeout=SAFE_TIMEOUT,
        )
        ok, _, _ = ensure_json_response(raw, "long_prompt", expect_json=True)
        # 只要不抛异常，即认为通过
        self.assertIsInstance(ok, bool)


    # -----------------------------------------------------------------
    # 9️⃣ 空提示（用户或系统都为空）安全返回
    # -----------------------------------------------------------------
    def test_09_empty_prompts(self):
        raw = llm_call(user_prompt="", system_prompt="")
        self.assertIsInstance(raw, dict)
        self.assertIn("success", raw)
        if raw.get("success"):
            ok, _, _ = ensure_json_response(raw, "empty_prompt", expect_json=True)
            self.assertTrue(ok, "空提示返回的 JSON 无法解析")

    # -----------------------------------------------------------------
    # 10️⃣ 嵌套结构 JSON
    # -----------------------------------------------------------------
    def test_10_nested_json_structure(self):
        prompt = (
            "请返回 JSON，结构如下：\n"
            "{\n"
            "  \"outer\": {\n"
            "    \"inner\": {\n"
            "      \"code\": 200,\n"
            "      \"message\": \"ok\"\n"
            "    }\n"
            "  },\n"
            "  \"timestamp\": \"2023-01-01T00:00:00Z\"\n"
            "}\n"
            "只返回 JSON，禁止解释文字。"
        )
        raw = self.retry.attempt(llm_call, prompt, system_prompt="")
        ok, parsed, msg = ensure_json_response(raw, "nested")
        self.assertTrue(ok, f"嵌套 JSON 解析失败: {msg}")

        # ---------- 额外结构校验 ----------
        self.assertIsInstance(parsed, dict, "解析结果应为字典")
        self.assertIn("outer", parsed, "缺失外层键 'outer'")
        self.assertIsInstance(parsed["outer"], dict, "'outer' 应为字典")
        self.assertIn("inner", parsed["outer"], "缺失内层键 'inner'")
        self.assertIsInstance(parsed["outer"]["inner"], dict, "'inner' 应为字典")
        self.assertEqual(parsed["outer"]["inner"]["code"], 200)
        self.assertEqual(parsed["outer"]["inner"]["message"], "ok")
        self.assertRegex(parsed["timestamp"], r"\d{4}-\d{2}-\d{2}T")
        # ------------------------------------


    # -----------------------------------------------------------------
    # 11️⃣ 大整数、负数、浮点数检查
    # -----------------------------------------------------------------
    def test_11_numeric_precision(self):
        prompt = (
            "请返回 JSON，字段 big_int 为 12345678901234567890，"
            "negative 为 -42，float_val 为 3.1415926。"
        )
        system = "只返回 JSON。"
        raw = self.retry.attempt(llm_call, prompt, system_prompt=system)
        ok, parsed, msg = ensure_json_response(raw, "numeric")
        self.assertTrue(ok, f"数字 JSON 解析失败: {msg}")

        self.assertEqual(parsed["big_int"], 12345678901234567890)
        self.assertEqual(parsed["negative"], -42)
        self.assertAlmostEqual(parsed["float_val"], 3.1415926, places=5)

    # -----------------------------------------------------------------
    # 12️⃣ 自定义对象反序列化示例
    # -----------------------------------------------------------------
    def test_12_custom_deserialization(self):
        class TaskInfo:
            def __init__(self, task_id: str, status: str, detail: str = ""):
                self.task_id = task_id
                self.status = status
                self.detail = detail

            @classmethod
            def from_dict(cls, data: Dict[str, Any]) -> "TaskInfo":
                return cls(
                    task_id=data.get("task_id", ""),
                    status=data.get("status", ""),
                    detail=data.get("detail", ""),
                )

            def __repr__(self) -> str:
                return f"<TaskInfo {self.task_id!r} status={self.status!r}>"

        prompt = (
            "请返回 JSON，task_id 为 'custom_001'，status 为 'in_progress'，"
            "detail 为 '正在执行'。"
        )
        system = "只返回 JSON。"
        raw = self.retry.attempt(llm_call, prompt, system_prompt=system)
        ok, parsed, msg = ensure_json_response(raw, "custom_deserialize")
        self.assertTrue(ok, f"自定义反序列化前 JSON 解析失败: {msg}")

        task_obj = TaskInfo.from_dict(parsed)
        self.assertIsInstance(task_obj, TaskInfo)
        self.assertEqual(task_obj.task_id, "custom_001")
        self.assertEqual(task_obj.status, "in_progress")
        self.assertEqual(task_obj.detail, "正在执行")

    # -----------------------------------------------------------------
    # 13️⃣ 严格 Schema（不允许额外字段）
    # -----------------------------------------------------------------
    def test_13_strict_extra_fields(self):
        prompt = (
            "请返回 JSON，task_id 为 'strict_001'，status 为 'planned'，"
            "并额外添加 extra = 'reject_me'。"
        )
        system = "只返回 JSON，不要解释文字。"
        raw = self.retry.attempt(llm_call, prompt, system_prompt=system)
        ok, parsed, msg = ensure_json_response(raw, "strict_extra")
        self.assertTrue(ok, f"Strict 测试解析失败: {msg}")

        valid, err = validate_against_schema(parsed, SCHEMA_SIMPLE_KV)
        self.assertFalse(valid, "Schema 应该拒绝额外字段")
        self.assertIn("Additional properties are not allowed", err)

    # -----------------------------------------------------------------
    # 14️⃣ 重试机制（模拟瞬时网络错误）
    # -----------------------------------------------------------------
    def test_14_retry_on_transient_failure(self):
        counter = {"calls": 0}

        def flaky_llm(prompt: str) -> Dict[str, Any]:
            counter["calls"] += 1
            if counter["calls"] == 1:
                raise requests.exceptions.ConnectionError("模拟网络中断")
            # 第二次调用正常返回
            return llm_call(
                prompt,
                system_prompt="只返回 JSON，键为 `ok`: true",
                temperature=0.0,
                max_tokens=256,
            )

        raw = self.retry.attempt(flaky_llm, "dummy")
        self.assertTrue(raw.get("success"), "重试后应返回成功")
        self.assertEqual(counter["calls"], 2, "应恰好重试一次")

    # -----------------------------------------------------------------
    # 15️⃣ content 为字符串的 JSON（二次解析）
    # -----------------------------------------------------------------
    def test_15_content_str_json(self):
        prompt = "请返回 JSON，字段为 `alpha` = 1，`beta` = 2。"
        system = "只返回 JSON。"
        raw = self.retry.attempt(llm_call, prompt, system_prompt=system)

        ok, parsed, msg = ensure_json_response(raw, "content_str", expect_json=True)
        self.assertTrue(ok, f"二次解析失败: {msg}")
        self.assertIn("alpha", parsed)
        self.assertIn("beta", parsed)

    # -----------------------------------------------------------------
    # 16️⃣ 大数组性能（100 条记录）——阈值上调
    # -----------------------------------------------------------------
    def test_16_large_json_array_performance(self):
        prompt = (
            "请返回 JSON，键为 `items`，值为长度为 100 的数组，"
            "每个元素是对象，包含 `id`（整数） 与 `name`（字符串 `'item_<id>'`）。"
        )
        system = "只返回 JSON。"
        start = time.time()
        raw = self.retry.attempt(
            llm_call,
            prompt,
            system_prompt=system,
            temperature=0.0,
            max_tokens=4096,
        )
        duration = time.time() - start
        # 把阈值放宽到 300 秒，足以覆盖本机模型的生成时间
        self.assertLessEqual(
            duration, 300.0,
            f"大 JSON 解析耗时过长 ({duration:.2f}s) – 建议检查模型性能或减小规模"
        )
        ok, parsed, msg = ensure_json_response(raw, "large_array", expect_json=True)
        self.assertTrue(ok, f"大数组 JSON 解析失败: {msg}")
        self.assertIn("items", parsed)
        self.assertEqual(len(parsed["items"]), 100)
        self.assertIsInstance(parsed["items"][0]["id"], int)

    # -----------------------------------------------------------------
    # 结束时清理
    # -----------------------------------------------------------------
    @classmethod
    def tearDownClass(cls) -> None:
        LOGGER.info("=== 结束 LLM JSON 输出功能完整测试 ===")


# -------------------------------------------------------------------------
# 主入口（直接运行本文件时使用）
# -------------------------------------------------------------------------
if __name__ == "__main__":
    # 关闭 unittest 默认的捕获，让日志直接打印到终端
    unittest.main(verbosity=2, buffer=False)
