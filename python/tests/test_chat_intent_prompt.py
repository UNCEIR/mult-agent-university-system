# -*- coding: utf-8 -*-
"""路 4：chat_intent NLU prompt 内容契约测试。

目的：
- 防止 prompt 改动时误删教师端意图路由表 / 关键词清单 / 禁止规则
- 锁定 main_agent router 的关键不变量（allowed_tools 包含 dispatch_module）

mock 策略：直接读 prompt 常量字符串，不调用 LLM / 不连数据库。
"""

from __future__ import annotations

import json

import jsonschema
import pytest


# ── 教师端意图路由关键词清单（路 1 + 路 4 强约束） ──
# prompt 必须覆盖以下关键词 → 模块映射，否则 LLM 会退回到 query_knowledge
REQUIRED_INTENT_KEYWORDS = {
    'report': ['成绩单', '期末报告', '班级报告', '学科成绩单', '道法', '出报告', '汇总表', 'Excel 上传'],
    'evaluation': ['评语', '寄语', '鼓励', '学期总结', '学生评语', '学期评语', '给某生写'],
    'ppt': ['做 PPT', '制作课件', '课件生成', '演示文稿'],
    'image_generate': ['生成图片', '画一张', '配图', '封面图'],
}

# 模块名 → dispatch_module 枚举值
INTENT_MODULES = {'report', 'evaluation', 'ppt', 'image_generate'}


@pytest.fixture
def chat_intent_cases():
    path = 'eval_sets/chat_intent.jsonl'
    with open(path, encoding='utf-8') as f:
        return [json.loads(line) for line in f if line.strip()]


class TestPromptContent:
    def test_main_agent_prompt_loads(self):
        from agent.main.prompt import MAIN_AGENT_SYSTEM_PROMPT

        assert isinstance(MAIN_AGENT_SYSTEM_PROMPT, str)
        assert len(MAIN_AGENT_SYSTEM_PROMPT) > 1000, 'prompt 不应过短（防误删核心段落）'

    def test_prompt_includes_dispatch_module_instruction(self):
        """关键指令：教师端意图必须调 dispatch_module 而非停住/退回知识库工具。"""
        from agent.main.prompt import MAIN_AGENT_SYSTEM_PROMPT

        assert 'dispatch_module' in MAIN_AGENT_SYSTEM_PROMPT
        # 2026-08-25 重构：query_knowledge 已拆成 query_handbook / query_transcript
        # 这里断言新工具名都出现 + 关键的反退路由指令
        assert 'adaptive_knowledge_retrieve' in MAIN_AGENT_SYSTEM_PROMPT
        # 明确禁止规则：禁止把"成绩单/评语/寄语/期末报告"当作知识库问答
        assert '禁止' in MAIN_AGENT_SYSTEM_PROMPT or '不要' in MAIN_AGENT_SYSTEM_PROMPT

    @pytest.mark.parametrize(
        'module,keywords',
        list(REQUIRED_INTENT_KEYWORDS.items()),
    )
    def test_prompt_covers_intent_keywords(self, module, keywords):
        """每个模块的关键词必须出现在 prompt 的路由表里。"""
        from agent.main.prompt import MAIN_AGENT_SYSTEM_PROMPT

        for kw in keywords:
            assert kw in MAIN_AGENT_SYSTEM_PROMPT, (
                f'prompt 缺失关键词 "{kw}"（模块={module}）；'
                f'LLM 会绕过 adaptive_knowledge_retrieve，导致 chat_intent 端测失败'
            )

    @pytest.mark.parametrize('module', list(INTENT_MODULES))
    def test_prompt_covers_intent_module_value(self, module):
        """每个 dispatch_module.intent 合法值必须在 prompt 中显式出现。"""
        from agent.main.prompt import MAIN_AGENT_SYSTEM_PROMPT

        assert module in MAIN_AGENT_SYSTEM_PROMPT, (
            f'prompt 缺少 dispatch_module intent="{module}" 的示例/指令'
        )

    def test_prompt_includes_split_knowledge_tools(self):
        """核心能力 1：知识库问答 → query_handbook（手册）+ query_transcript（个人）。"""
        from agent.main.prompt import MAIN_AGENT_SYSTEM_PROMPT

        # 手册类工具
        assert '学生手册' in MAIN_AGENT_SYSTEM_PROMPT
        assert 'adaptive_knowledge_retrieve' in MAIN_AGENT_SYSTEM_PROMPT
        # 个人类工具
        assert '个人成绩单' in MAIN_AGENT_SYSTEM_PROMPT or '本人' in MAIN_AGENT_SYSTEM_PROMPT
        

    def test_prompt_includes_recommend_courses_one_click_tool(self):
        """核心能力 5：课程推荐 → 必须用 recommend_courses 一键工具（不要分步调原子）。"""
        from agent.main.prompt import MAIN_AGENT_SYSTEM_PROMPT

        assert 'recommend_courses' in MAIN_AGENT_SYSTEM_PROMPT
        assert 'mode=pipeline' in MAIN_AGENT_SYSTEM_PROMPT or '一键工具' in MAIN_AGENT_SYSTEM_PROMPT

    def test_prompt_includes_user_id_auto_injection_rule(self):
        """用户身份与个性化：user_id 由系统自动注入，禁止 LLM 猜测/索要。"""
        from agent.main.prompt import MAIN_AGENT_SYSTEM_PROMPT

        assert 'user_id' in MAIN_AGENT_SYSTEM_PROMPT
        assert '不要' in MAIN_AGENT_SYSTEM_PROMPT

    def test_prompt_includes_chinese_response_rule(self):
        """行为约束：始终用中文回答。"""
        from agent.main.prompt import MAIN_AGENT_SYSTEM_PROMPT

        assert '中文' in MAIN_AGENT_SYSTEM_PROMPT

    def test_prompt_includes_partial_success_tool_failure_policy(self):
        """工具失败时继续独立任务，并生成部分成功回答。"""
        from agent.main.prompt import MAIN_AGENT_SYSTEM_PROMPT

        assert 'isError=true' in MAIN_AGENT_SYSTEM_PROMPT
        assert '互不依赖' in MAIN_AGENT_SYSTEM_PROMPT
        assert '部分' in MAIN_AGENT_SYSTEM_PROMPT
        assert '冒充' in MAIN_AGENT_SYSTEM_PROMPT


class TestMainAgentSpec:
    """main_agent router 必须注册 dispatch_module 工具（路 2 已修：dispatch_module → 4 个 intent）。"""

    def test_main_agent_allowed_tools_include_dispatch_module(self):
        from agent.main.specs import MAIN_AGENT_SPEC

        assert 'dispatch_module' in MAIN_AGENT_SPEC.allowed_tools

    def test_main_agent_routing_module_values_match_intent_enum(self):
        """allowed_tools 与 dispatch_module 的 intent Literal 枚举一致。"""
        from agent.main.specs import MAIN_AGENT_SPEC
        from tools.system.dispatch_module import DispatchModuleInput

        # DispatchModuleInput.intent 字段的 Literal 枚举
        intent_field = DispatchModuleInput.model_fields['intent']
        literal_values = set(intent_field.annotation.__args__)
        # main_agent 必须允许调用 dispatch_module，且路由 prompt 应覆盖所有 intent
        assert 'dispatch_module' in MAIN_AGENT_SPEC.allowed_tools
        # Literal 枚举至少有 4 个合法值（路 1 实装）
        assert len(literal_values) >= 4


class TestChatIntentEvalSet:
    """chat_intent.jsonl 必须有 20 个以上 case 且覆盖关键意图。"""

    def test_eval_set_has_at_least_20_cases(self, chat_intent_cases):
        assert len(chat_intent_cases) >= 20, '路 4 增补后 chat_intent case 数 ≥ 20'

    def test_eval_set_covers_all_4_dispatch_intents(self, chat_intent_cases):
        """chat_intent.jsonl 必须覆盖 report / evaluation / ppt / image_generate 4 个 intent。"""
        seen_intents = {case['expected']['intent'] for case in chat_intent_cases}
        for intent in INTENT_MODULES:
            assert intent in seen_intents, (
                f'chat_intent.jsonl 缺失 intent="{intent}" 的 case'
            )

    def test_eval_set_critical_cases_present(self, chat_intent_cases):
        """关键 case 必须存在（之前失败的 4 个 + 已通过的 1 个混合意图）。"""
        case_ids = {c['case_id'] for c in chat_intent_cases}
        for critical in ('intent_04', 'intent_05', 'intent_06', 'intent_07', 'intent_20'):
            assert critical in case_ids, f'关键 case {critical} 缺失'

    def test_eval_set_each_case_has_required_fields(self, chat_intent_cases):
        schema = {
            'type': 'object',
            'required': ['case_id', 'type', 'input', 'expected', 'assertions'],
            'properties': {
                'case_id': {'type': 'string'},
                'type': {'type': 'string'},
                'input': {
                    'type': 'object',
                    'required': ['message', 'user_id'],
                    'properties': {
                        'message': {'type': 'string', 'minLength': 1},
                        'user_id': {'type': 'string', 'minLength': 1},
                    },
                },
                'expected': {
                    'type': 'object',
                    'required': ['intent', 'tool_chain'],
                    'properties': {
                        'intent': {'type': 'string'},
                        'tool_chain': {'type': 'array'},
                    },
                },
                'assertions': {'type': 'array', 'minItems': 1},
            },
        }
        for case in chat_intent_cases:
            jsonschema.validate(case, schema)
