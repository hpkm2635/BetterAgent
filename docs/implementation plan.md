# LLM Provider Expansion & Factory Pattern Implementation Plan

This plan details the design and implementation for scaling BetterAgent's LLM Provider architecture. It introduces a unified **`ProviderFactory`**, an **`OpenAIProvider`** (supporting OpenAI, DeepSeek, Qwen/DashScope, Moonshot, Ollama, and vLLM via OpenAI-compatible REST API), updated base class probes (`health_check`, `get_context_window`), Thinking Delta isolation (DeepSeek-R1 / Qwen-Thought), OpenAI Tool Call ID matching, and proxy security/privacy warning mechanisms.

## User Review Required

> [!IMPORTANT]
> - **OpenAI SDK Dependency**: Implementing `OpenAIProvider` requires adding `openai>=1.0.0` to Python dependencies (`pyproject.toml` / `requirements.txt`).
> - **Thinking Delta Isolation**: For reasoning models (DeepSeek-R1, Qwen2.5-Coder-Thought), `delta.reasoning_content` is yielded as `{"type": "thinking_delta", "text": ...}`. This separates thinking thoughts from final speech text, preventing TTS from attempting to read out internal reasoning tokens.
> - **Tool Call ID Consistency**: OpenAI API strictly requires matching `tool_call_id` in subsequent `role: "tool"` messages. We maintain a deterministic ID mapping in `_build_messages()`.
> - **Security & Privacy Warning**: Using custom non-official `base_url` endpoints (third-party relay proxies) will trigger a high-visibility terminal warning during service initialization to protect project maintainers from issue reports caused by third-party proxy failures.

## Open Questions

None. All technical design requirements have been incorporated into this plan.

---

## Proposed Changes

### Provider Core & Base Architecture

#### [MODIFY] [base.py](file:///d:/projects/BetterAgent/services/cognitive/providers/base.py)
- Add abstract method `async health_check(self) -> bool` to probe API key and connectivity.
- Add `get_context_window(self) -> int` returning the model's maximum Token budget (default 128,000).
- Add `supports_tool_calling(self) -> bool` (default `True`).
- Add helper method `_check_and_log_security_notice(base_url: str, official_domains: List[str])` to log security/privacy warnings when third-party endpoints are configured.

#### [NEW] [openai_provider.py](file:///d:/projects/BetterAgent/services/cognitive/providers/openai_provider.py)
- Implement `OpenAIProvider(BaseLLMProvider)` driven by `AsyncOpenAI`.
- Support configuration for `api_key`, `base_url`, `model`, `temperature`, and `max_tokens`.
- Implement `_build_messages()`:
  - Converts internal message format (including `vision_frame` base64 data URIs) into OpenAI chat completion messages.
  - **Tool Call ID History Mapping**: Builds deterministic `call_xxx` ID mappings between `assistant` tool calls and subsequent `role: "tool"` responses to guarantee OpenAI 400 Bad Request validation passes.
- Implement `_build_tools()` converting internal JSON schemas into OpenAI `tools` format (`type: "function"`).
- Implement `generate_stream()`:
  - **Thinking Delta Extraction**: Checks `hasattr(delta, "reasoning_content")` and yields `{"type": "thinking_delta", "text": delta.reasoning_content}` for DeepSeek-R1 / Qwen-Thought.
  - Streaming text delta parsing (`delta.content`), tool calls accumulation, and `cancel_event` collaborative cancellation.
- Add robust exception handling for non-JSON/HTML 502/504 gateway proxy errors.
- Implement `health_check()` via lightweight `/models` list request or single-token ping.

#### [NEW] [factory.py](file:///d:/projects/BetterAgent/services/cognitive/providers/factory.py)
- Implement `ProviderFactory` with class registration and dynamic provider instantiation.
- Support named presets: `gemini`, `claude`, `openai`, `deepseek`, `qwen`, `ollama`.
- Read configuration from `config/config.yaml` (`llm.default_provider`, `llm.openai`, `llm.deepseek`, `llm.ollama`, etc.).
- Maintain singleton instance cache to prevent redundant client initializations.

---

### Integration & Configuration

#### [MODIFY] [cognitive_engine.py](file:///d:/projects/BetterAgent/services/cognitive/cognitive_engine.py)
- Update `CognitiveEngine.__init__()` to use `ProviderFactory` for lazy/dynamic provider instantiation instead of hardcoded `self.providers = {"gemini": ..., "claude": ...}`.
- In `stream_reasoning_loop`, handle `thinking_delta` stream events by appending reasoning text into `<thought>...</thought>` blocks so it is excluded from TTS audio generation.
- Use `provider.get_context_window()` when evaluating token trimming budgets.

#### [MODIFY] [config.yaml](file:///d:/projects/BetterAgent/config/config.yaml)
- Add provider configuration blocks for `openai`, `deepseek`, `qwen`, and `ollama`:
  ```yaml
  llm:
    default_provider: "gemini"
    gemini:
      model: "gemini-3.1-flash-lite"
    claude:
      model: "claude-3-5-sonnet-20241022"
    openai:
      model: "gpt-4o"
      base_url: "https://api.openai.com/v1"
    deepseek:
      model: "deepseek-chat"
      base_url: "https://api.deepseek.com/v1"
    ollama:
      model: "qwen2.5-coder:7b"
      base_url: "http://127.0.0.1:11434/v1"
  ```

---

### Verification & Test Suite

#### [NEW] [test_sprint9_provider_factory.py](file:///d:/projects/BetterAgent/tests/test_sprint9_provider_factory.py)
- Test `ProviderFactory.get_provider()` for registered providers and fallbacks.
- Test `OpenAIProvider.generate_stream()` with mock AsyncOpenAI stream events:
  - Test text delta & function tool calls.
  - Test `reasoning_content` -> `thinking_delta` extraction for DeepSeek-R1.
- Test `OpenAIProvider._build_messages()` multi-modal `vision_frame` formatting and Tool Call ID mapping consistency.
- Test non-official `base_url` security notice warning log triggers.
- Test non-JSON HTML 502 proxy error handling and fallback messages.

---

## Verification Plan

### Automated Tests
- Run `pytest tests/test_sprint9_provider_factory.py -v`
- Run full test suite: `pytest tests/ -v`

### Manual Verification
- Test switching `llm.default_provider` in `config/config.yaml` to `openai` / `deepseek` / `ollama` and verifying startup health check and DeepSeek-R1 reasoning content isolation.
