# Implementation Plan - STS2 Gameplay Happy Path Fix & Robust JSON Tool Call Parser

Deep diagnostic analysis of `logs/betteragent_core_stdout.log`, `logs/sts2_poller.log`, and `logs/cognitive_service_stderr.log` revealed the exact root cause causing Qwen STS2 gameplay auto-pilot to stall.

## Diagnostic Findings & Root Cause Analysis

> [!IMPORTANT]
> **Root Cause 1: Non-Greedy JSON Regex Truncating Nested Multi-Line Tool Call Objects**
> - **Error Traceback** in `logs/cognitive_service_stderr.log`:
>   ```text
>   Failed to parse text-based tool call JSON: {
>     "tool": "get_game_state",
>     "arguments": {
>       "format": "json"
>     } (Expecting ',' delimiter: line 5 column 4 (char 71))
>   ```
> - **Mechanism**:
>   - In `OpenAIProvider._extract_text_tool_calls()`, the regex `pattern_json_block = r'(?:```(?:json)?\s*)?({(?:\s*"tool"\s*|\s*"name"\s*|\s*"function"\s*):.*?})(?:\s*```)?'` used non-greedy `.*?`.
>   - When Qwen emitted multi-line nested JSON (`"arguments": {"format": "json"}`), `.*?` stopped at the FIRST closing brace `}` inside `"arguments"`, cutting off the outer `}` of the JSON object.
>   - `json.loads()` failed, causing `process_json_block()` to return `False`.
>   - As a result, Qwen's tool calls were discarded, no actions reached STS2 game server, and `stream_reasoning_loop()` hit its 20-round budget limit.

> [!WARNING]
> **Root Cause 2: Autopilot Game Turn Loop Stalling**
> - Because Qwen's tool calls were rejected due to JSON truncation, `sts2_poller` remained stuck on `monster_player_turn`.
> - Every 50 seconds, `sts2_poller` re-fired `monster_player_turn` to NATS, causing an infinite stall loop without any cards being played or turns ended.

---

## User Review Required

> [!IMPORTANT]
> **Key Architectural Choices for Approval**:
> 1. **Balanced Bracket JSON Extractor (`_extract_json_objects`)**: Replace fragile non-greedy regexes with a depth-tracking, string-escaped JSON bracket parser in `OpenAIProvider`. This guarantees 100% precise extraction of nested multi-line JSON objects of arbitrary depth without truncation.
> 2. **Prose & Codeblock Co-Existence**: Retain support for XML `<tool_call>`, Markdown ````json ... ```` codeblocks, and `👉 combat_play_card(...)` prose syntax alongside balanced JSON parsing.

---

## Proposed Changes

### Component 1: Cognitive Service (`services/cognitive/`)

#### [MODIFY] [openai_provider.py](file:///d:/projects/BetterAgent/services/cognitive/providers/openai_provider.py)
- Implement `_extract_json_objects(text: str)` helper:
  - Scans `text` for `{` containing `"tool"`, `"name"`, or `"function"` keys.
  - Tracks brace depth (`{` / `}`) while respecting string quotes `"` and backslash escapes `\` to isolate full, untruncated JSON strings.
- Refactor `_extract_text_tool_calls()`:
  - First run `_extract_json_objects()` to extract all valid multi-line / nested JSON tool call blocks.
  - Replace parsed JSON blocks in text with empty strings.
  - Then run `pattern_prose` regex for `👉 func(...)` prose tool calls.
  - Return cleaned user-facing text and complete array of parsed `tool_calls`.

---

### Component 2: Verification Test Suite (`tests/`)

#### [MODIFY] [test_sprint9_provider_factory.py](file:///d:/projects/BetterAgent/tests/test_sprint9_provider_factory.py)
- Add unit test `test_openai_provider_nested_multiline_json_tool_call()`:
  - Feeds multi-line nested JSON (`{"tool": "get_game_state", "arguments": {"format": "json"}}`) into `generate_stream()`.
  - Verifies that `json.loads()` succeeds, no truncation occurs, and `tool_calls` contains `{"name": "get_game_state", "args": {"format": "json"}}`.

---

## Verification Plan

### Automated Tests
- Run Pytest for provider factory & tool call parser:
  ```powershell
  $env:PYTHONPATH="."; .\.venv\Scripts\pytest tests/test_sprint9_provider_factory.py -v
  ```
- Run full pytest regression suite across all 15 test files:
  ```powershell
  $env:PYTHONPATH="."; .\.venv\Scripts\pytest tests/ -v
  ```

### Manual Verification
- Launch microservices with `python runner.py` or `.\scripts\win_start.ps1`.
- Trigger game auto-pilot with `/game_start` in web UI (`http://localhost:5173`) while running STS2 singleplayer.
- Verify `logs/sts2_poller.log` and `logs/cognitive_service.log` to confirm Qwen successfully executes `get_game_state`, plays cards (`combat_play_card`), and ends turn (`combat_end_turn`), advancing the game state smoothly.
