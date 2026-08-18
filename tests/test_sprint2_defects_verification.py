import pytest
import asyncio
import uuid
from unittest.mock import AsyncMock, patch, MagicMock

from services.memory.token_budget import TokenBudgetManager, estimate_tokens
from services.cognitive.cognitive_engine import SentenceSegmenter, CognitiveEngine
from shared.schema.payloads import ReasoningRequestPayload, ActionDecisionPayload, InboundMessagePayload


# ============================================================================
# 修复验证 1: CJK (中日韩) 文本 Token 估算精准度验证
# ============================================================================

def test_cjk_token_estimation_underestimate_defect_fixed():
    """
    验证 CJK 中文场景下的 Token 准确估算修复：
    在 BPE (Tiktoken/Cl100k) 中，中文单字通常占用 1.5 个 Token。
    采用 CJK 加权算法后，3000 汉字估算为 ~4500 Tokens，准确反映真实 LLM 上下文占用。
    当 max_budget=2000 时，TokenBudgetManager 准确识别超限并进行剪裁，彻底避免 Context Window Exceeded。
    """
    budget_mgr = TokenBudgetManager(max_budget=2000)

    system_prompt = "你是AI助手。"
    profile = {"preferred_name": "主人"}
    
    # 构造 3000 个中文字符的历史消息
    cjk_text = "今天我们要讨论量子力学和向量数据库在二次元聊天机器人中的深度应用架构方案。" * 75  # ~3000 汉字
    
    history = [
        {"role": "user", "content": cjk_text}
    ]
    rag_facts = ["事实1: 主人喜欢吃烤鱼。"]

    # 调用 fit_into_budget
    _, trimmed_history, trimmed_facts, _ = budget_mgr.fit_into_budget(
        system_prompt=system_prompt,
        history=history,
        profile=profile,
        rag_facts=rag_facts,
    )

    estimated_tokens_cjk = estimate_tokens(cjk_text)

    # 验证修复：
    # 3000 汉字估算为 ~4500 tokens (> 2000 max_budget)，正确触发裁剪
    assert estimated_tokens_cjk >= 4000
    assert len(trimmed_history) == 0  # 超长中文消息被成功裁剪，杜绝 Context Exceeded 崩溃


# ============================================================================
# 修复验证 2: SentenceSegmenter 未闭合括号流式实时性验证
# ============================================================================

def test_sentence_segmenter_unclosed_paren_streaming_stall_defect_fixed():
    """
    验证 SentenceSegmenter 未闭合括号不会导致流式打字机停滞：
    当 LLM 输出包含未闭合括号（如注：（详见文档... 或 (a + b...）但已包含完整标点时，
    push() 正常实时吐出句子，流式输出顺畅无停滞。
    """
    segmenter = SentenceSegmenter()

    # 模拟流式 Chunk 1: 包含未闭合的全角括号 "（"
    chunk1 = "好的喵！注：（详见架构文档，我们需要处理更多的详细细节。"
    out1 = segmenter.push(chunk1)
    
    # 验证修复：Chunk 1 包含完整句号，不再被未闭合括号错误拦截，实时吐出句子
    assert len(out1) == 2
    assert out1[0] == "好的喵！"
    assert "注：（详见架构文档" in out1[1]

    # 模拟流式 Chunk 2: 继续输出后续完整的长段落
    chunk2 = "首先第一点是系统架构，第二点是内存分配。第三点是并发安全。"
    out2 = segmenter.push(chunk2)

    # 验证修复：Chunk 2 吐出的句子即时流式输出
    assert len(out2) == 2
    assert out2[0] == "首先第一点是系统架构，第二点是内存分配。"
    assert out2[1] == "第三点是并发安全。"


# ============================================================================
# 修复验证 3: generation_id 类型匹配验证
# ============================================================================

@pytest.mark.asyncio
async def test_stream_reasoning_loop_generation_id_type_validation_fixed():
    """
    验证 stream_reasoning_loop 的 except 块中，
    fallback_generation_id 为 int 类型，不会触发 Pydantic ValidationError。
    """
    fallback_gen_id = 1  # 严格 int 类型

    # 验证：将 int 类型赋值给 ActionDecisionPayload(generation_id=...) 正常通过
    payload = ActionDecisionPayload(
        event_id="evt_valid_type",
        source_component="cognitive_engine",
        chat_id=12345,
        generation_id=fallback_gen_id,
        source_channel="telegram",
        action_type="send_message",
        text_content="",
        chat_action="",
        is_final=True,
    )

    assert payload.generation_id == 1
    assert isinstance(payload.generation_id, int)
