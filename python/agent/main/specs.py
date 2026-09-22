# -*- coding: utf-8 -*-
"""业务 Agent 的声明式配置。"""

from __future__ import annotations

from dataclasses import dataclass

from ai.llm_task_name import LLMTaskName

from .prompt import MAIN_AGENT_SYSTEM_PROMPT


@dataclass(frozen=True)
class AgentSpec:
    """描述一个业务 Agent 如何加载上下文和能力。"""

    name: str
    task_name: LLMTaskName
    system_prompt: str
    description: str = ""
    skills: tuple[str, ...] = ("/skills/",)
    memory: tuple[str, ...] = ("/memories/AGENTS.md",)
    allowed_tools: tuple[str, ...] = ()
    temperature: float = 0.1
    max_tokens: int = 2048
    enable_compaction: bool = True
    streaming: bool = True
    use_checkpointer: bool = True  # Phase 2：无状态场景（report/evaluation/recommend）设 False


MAIN_AGENT_SPEC = AgentSpec(
    name="main_agent",
    task_name=LLMTaskName.MAIN_AGENT_ROUTER,
    system_prompt=MAIN_AGENT_SYSTEM_PROMPT,
    description=(
        "主 Agent：对话式场景的总入口。"
        "当用户意图涉及独立模块（report/evaluation/ppt/image_generate）时，"
        "按 dispatch_module 工具路由到对应模块；"
        "当用户意图涉及知识库问答/文档解析/写作辅助/网页搜索等对话场景时，"
        "按技能流程调用工具链。"
    ),
    # 主 agent 只暴露已实装且面向对话的工具。
    # 推荐原子工具（extract_profile 等 7 个）不暴露，避免它逐个串行调用变慢；
    # 推荐统一走 recommend_courses 一键工具（mode=pipeline，内部并行）。
    # streaming=True：让 /chat/stream 能透出 on_chat_model_stream token 事件；
    # 否则 deepagents 按 stream_mode="updates" 聚合，stream 端点只能拿到空 reply。
    streaming=True,
    allowed_tools=(
        "list_available_skills",
        "get_current_time",
        "dispatch_module",
        "recommend_courses",
        "adaptive_knowledge_retrieve",
        "parse_document",
        "chunk_document",
        "writing_assistant",
        "web_search",
        "image_generate",
        "image_generate_get",
        "image_recognize",
        "code_interpreter",
        "mindmap_generator",
        "render_report_batch",
        "inspect_score_excels",
        "get_academic_snapshot",
        "design_dimensions",
        "compute_radar_values",
        "generate_comment"

    ),
)
RECOMMENDATION_AGENT_SPEC = AgentSpec(
    name="recommendation_agent",
    description=(
        "公选课个性化推荐。当学生表达选课偏好（如 \"推荐几门不用考试的课\"、\"作业少的公选课\"）时委派："
        "按 recommend-courses 技能流程做 召回→硬约束过滤→重排→可行性→给出带理由的推荐；硬约束必须由确定性工具满足。"
    ),
    task_name=LLMTaskName.RECOMMEND_COURSES_TOOL,
    system_prompt=(
        "你是公选课推荐 Agent。只根据用户偏好和推荐工具返回的课程事实进行推荐，"
        "硬约束必须先满足，不能用主观排序绕过硬约束。"
    ),
    skills=("/skills/recommend-courses/",),
    memory=(),
    allowed_tools=("recommend_courses",),
    use_checkpointer=False,
)

REPORT_AGENT_SPEC = AgentSpec(
    name="report_agent",
    description=(
        "教师端批量成绩报告生成。当教师需要根据批量学科成绩 Excel 生成逐学生期末报告/成绩单"
        "（\"出报告\"/\"期末报告\"/\"道法成绩汇总\"）时委派：按 report-generation 技能流程解析合并→选模板→逐学生填表→"
        "渲染 PDF/HTML 并返回下载链接。若用户尚未提供可访问的多科 Excel，先引导到 /report 页面上传。"
    ),
    task_name=LLMTaskName.TRANSCRIPT_PARSER,
    system_prompt=(
        "你是学生成绩报告 Agent。只处理成绩数据分析与成绩单生成；"
        "数值统计必须交给确定性工具，不要凭语言模型心算。"
    ),
    skills=("/skills/report-generation/",),
    memory=(),
    allowed_tools=("inspect_score_excels", "render_report_batch"),
    use_checkpointer=False,
)

EVALUATION_AGENT_SPEC = AgentSpec(
    name="evaluation_agent",
    description=(
        "学生学业评价寄语生成（雷达画像+评语）。当教师要为指定学生生成评语/寄语/学期总结"
        "（如 \"给张三写学期评语\"）时委派：按 evaluation-writing 技能五层流程——get_academic_snapshot 取该生成绩"
        "（user 分区强隔离）→设计维度→算雷达→评语引用核验→落库；学生端不触发。"
    ),
    task_name=LLMTaskName.EVALUATION_GENERATOR,
    system_prompt=(
        "你是学生评价寄语 Agent。根据结构化成绩和评价类型生成有依据的寄语，"
        "不得编造输入中不存在的成绩或经历。"
    ),
    skills=("/skills/evaluation-writing/",),
    memory=(),
    # Phase 2 端点走直接管线；本 spec 为 Phase 3 chat 路由经 subagent 委派预留
    allowed_tools=("get_academic_snapshot", "design_dimensions", "compute_radar_values", "generate_comment"),
    use_checkpointer=False,
)

PPT_AGENT_SPEC = AgentSpec(
    name="ppt_agent",
    description=(
        "课程小组 PPT 规划。当学生需要制作 PPT/课件/汇报材料时委派：按 ppt-generation 技能先整理课件结构；"
        "chat 内不渲染 PPTX 文件，最终引导到 /ppt 独立页面完成画布交互。"
    ),
    task_name=LLMTaskName.MAIN_AGENT_ROUTER,
    system_prompt=(
        "你是课程小组 PPT 规划 Agent。先把用户需求整理为课件结构，"
        "只使用当前已注册的能力，不声称已经生成尚未实现的文件。"
    ),
    skills=("/skills/ppt-generation/",),
    memory=(),
    allowed_tools=("web_search",),
)
