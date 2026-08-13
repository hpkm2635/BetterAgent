import time
import re
import logging
from typing import List, Dict, Any, Tuple, Optional
from shared.schema.payloads import ReasoningRequestPayload, ActionDecisionPayload
from shared.config_loader import get_config_val
from services.cognitive.providers.gemini_provider import GeminiProvider
from services.cognitive.providers.claude_provider import ClaudeProvider
from services.cognitive.tool_registry import ToolRegistry
from services.cognitive.prompt_builder import PromptBuilder
from services.cognitive.tools.validation import is_safe_media_filename
from services.cognitive.mcp.presenter_manager import PresenterSessionManager

logger = logging.getLogger("cognitive_engine")


def parse_thought_and_clean_text(raw_text: str) -> Tuple[str, str]:
    if not raw_text:
        return "", ""

    thought = ""
    match = re.search(r"<thought>(.*?)</thought>", raw_text, re.DOTALL)
    if match:
        thought = match.group(1).strip()
        clean_text = re.sub(r"<thought>(.*?)</thought>", "", raw_text, flags=re.DOTALL).strip()
    else:
        clean_text = raw_text.strip()

    # 1. Strip ReAct / JSON action blocks
    clean_text = re.sub(r"\{\s*\"action\"\s*:\s*\"[^\"]+\".*?\}", "", clean_text, flags=re.DOTALL)
    clean_text = re.sub(r"\{\s*\"(action_type|action|sticker_id)\"[\s\S]*?\}", "", clean_text)
    
    # 2. Strip Python pseudocode calls like print(telegram_action(...)) or print(...)
    clean_text = re.sub(r"print\s*\(\s*(?:telegram_action|generate_image|generate_tts_speech)[\s\S]*?\)", "", clean_text)
    clean_text = re.sub(r"print\s*\([^)]*\)", "", clean_text)

    # 3. Strip stray XML function calling tags like </function_call>, <function_call>, </function_c etc.
    clean_text = re.sub(r"</?function_call[^>]*>", "", clean_text)
    clean_text = re.sub(r"</?function_c[^>]*>", "", clean_text)
    clean_text = re.sub(r"</?[a-zA-Z0-9_]+_action[^>]*>", "", clean_text)

    return thought, clean_text.strip()


def clean_action_descriptions(text: str) -> str:
    """
    Zero Hardcoding Structural Protection Algorithm:
    1. Mask Markdown Links: [title](url) -> __MD_LINK_X__
    2. Mask Numbered/Bullet Lists: (1), (a), [1], (一) -> __NUM_LIST_X__
    3. Universal Clean: Strip remaining action/gesture parens (（...）, (...), 【...】, [...], *...*)
    4. Restore Placeholders
    """
    if not text:
        return ""
    placeholders = {}

    def mask_md_link(match):
        key = f"__MD_LINK_{len(placeholders)}__"
        placeholders[key] = match.group(0)
        return key

    masked = re.sub(r"\[[^\]]+\]\([^\)]+\)", mask_md_link, text)

    def mask_num_list(match):
        key = f"__NUM_LIST_{len(placeholders)}__"
        placeholders[key] = match.group(0)
        return key

    masked = re.sub(r"[\(\（\[【]\s*([0-9a-zA-Z一二三四五六七八九十]+)\s*[\)\）\]】]", mask_num_list, masked)

    # Clean remaining stage directions / action descriptions
    cleaned = re.sub(r"（[^）]*）", "", masked)
    cleaned = re.sub(r"\([^\)]*\)", "", cleaned)
    cleaned = re.sub(r"【[^】]*】", "", cleaned)
    cleaned = re.sub(r"\[[^\]]*\]", "", cleaned)
    cleaned = re.sub(r"\*[^\*]*\*", "", cleaned)

    for key, original in placeholders.items():
        cleaned = cleaned.replace(key, original)

    return cleaned.strip()


class SentenceSegmenter:
    """
    Sentence-level punctuation segmenter with <thought> & JSON state machine barrier protection.
    Prevents mental thoughts (<thought>...</thought>), JSON blocks, and action descriptions from leaking to TTS/NATS!
    """

    PUNCTUATIONS = set(["。", "！", "？", "~", "\n", "；", "，", ".", "!", "?", ";", ","])

    def __init__(self):
        self.buffer = ""
        self.in_thought = False

    def push(self, delta: str) -> List[str]:
        if not delta:
            return []

        self.buffer += delta

        # 1. Thought Tag State Machine Barrier Check
        if "<thought>" in self.buffer:
            self.in_thought = True

        if self.in_thought:
            if "</thought>" in self.buffer:
                # Exit thought block and strip thought content from buffer
                self.buffer = re.sub(r"[\s\S]*?</thought>", "", self.buffer).lstrip()
                self.in_thought = False
            else:
                # Still inside thought block, suppress sentence slicing
                return []

        # 2. JSON Code Block Barrier Check
        if "```" in self.buffer or "{" in self.buffer:
            if "}" in self.buffer or "```" in self.buffer:
                self.buffer = re.sub(r"```(?:json)?[\s\S]*?```", "", self.buffer)
                self.buffer = re.sub(r"\{\s*\"[^\"]+\"[\s\S]*?\}", "", self.buffer).lstrip()

        # 3. Clean fully closed action descriptions from buffer
        self.buffer = clean_action_descriptions(self.buffer)

        # 4. Unclosed Action Parenthesis Barrier Check
        # If inside unclosed action description (e.g. '（耳朵抖了抖喵'), wait for closing parenthesis before slicing.
        # Backticks count too: an inline code span like '`services/foo.py`' is exactly
        # where the naive dot-splitting below would otherwise slice a filename/identifier
        # in half (see the bug this barrier and the dot-guard below were added to fix).
        has_unclosed_paren = (
            ("（" in self.buffer and "）" not in self.buffer) or
            ("(" in self.buffer and ")" not in self.buffer) or
            ("【" in self.buffer and "】" not in self.buffer) or
            ("[" in self.buffer and "]" not in self.buffer) or
            (self.buffer.count("`") % 2 == 1)
        )
        if has_unclosed_paren:
            return []

        # 4.5 Sanitize multi-dot ellipses in buffer before slicing (e.g. '......', '...', '…')
        # Prevents '......' from being chopped into 6 individual single-period ('.') fragments that break TTS!
        self.buffer = re.sub(r"\.{2,}", "，", self.buffer)
        self.buffer = re.sub(r"…+", "，", self.buffer)

        # 5. Sentence Punctuation Slicing for User-Facing Text
        sentences = []
        idx = 0
        for i, char in enumerate(self.buffer):
            if char in self.PUNCTUATIONS:
                # A bare '.' between two word characters is very likely a filename/
                # module extension ('cognitive_engine.py') or a decimal/abbreviation,
                # not an English sentence end -- slicing there is what produced
                # "我已经帮你读取了 `services/c" + "py` 的前 50 行代码了。" as two
                # separate malformed TTS requests. If the char after the dot hasn't
                # streamed in yet either, hold off rather than guess: waiting a few
                # more characters is cheap, slicing wrong here is not.
                if char == "." and i > 0 and self.buffer[i - 1].isalnum() and (
                    i + 1 >= len(self.buffer) or self.buffer[i + 1].isalnum()
                ):
                    continue

                chunk = self.buffer[idx:i + 1].strip()
                if char in (",", "，") and len(chunk) < 15:
                    continue
                raw_sentence = chunk
                if raw_sentence:
                    sentence = re.sub(r"</?thought>", "", raw_sentence).strip()
                    sentence = clean_action_descriptions(sentence)
                    if sentence:
                        sentences.append(sentence)
                idx = i + 1

        if idx > 0:
            self.buffer = self.buffer[idx:]

        return sentences

    def flush(self) -> List[str]:
        """Flushes remaining text in buffer upon stream completion."""
        if self.in_thought:
            return []

        cleaned = re.sub(r"[\s\S]*?</thought>", "", self.buffer) if "</thought>" in self.buffer else self.buffer
        cleaned = re.sub(r"```(?:json)?[\s\S]*?```", "", cleaned)
        cleaned = re.sub(r"\{\s*\"[^\"]+\"[\s\S]*?\}", "", cleaned).strip()
        cleaned = re.sub(r"</?thought>", "", cleaned).strip()
        cleaned = clean_action_descriptions(cleaned)

        self.buffer = ""
        return [cleaned] if cleaned else []


class CognitiveEngine:

    # Bounds the "call tool -> feed result back -> generate again" loop in
    # stream_reasoning_loop so a tool-happy model can't spin forever.
    MAX_TOOL_ROUNDS = 4

    def __init__(self, default_provider_name: str = "gemini"):
        self.presenter_manager = PresenterSessionManager(
            server_commands=self._load_presenter_server_commands(),
            idle_timeout_seconds=get_config_val("mcp.presenter.idle_timeout_seconds", 600),
        )
        self.tool_registry = ToolRegistry(presenter_manager=self.presenter_manager)
        self.providers = {
            "gemini": GeminiProvider(),
            "claude": ClaudeProvider(),
        }
        self.default_provider = self.providers.get(default_provider_name,
                                                   self.providers["gemini"])
        self.latest_vision_frames: Dict[int, Dict[str, Any]] = {}

    @staticmethod
    def _load_presenter_server_commands() -> Dict[str, List[str]]:
        import sys
        commands: Dict[str, List[str]] = {}
        for target in ("ppt", "vscode"):
            cmd = get_config_val(f"mcp.presenter.{target}.command")
            if cmd:
                cmd_list = list(cmd)
                if cmd_list and cmd_list[0] in ("python", "python3"):
                    cmd_list[0] = sys.executable
                commands[target] = cmd_list
        return commands

    def update_vision_frame(self, chat_id: int, image_base64: str, source_type: str = "screen", format: str = "jpeg"):
        self.latest_vision_frames[chat_id] = {
            "image_base64": image_base64,
            "source_type": source_type,
            "format": format,
            "timestamp": time.time(),
        }

    def get_valid_vision_frame(self, chat_id: int) -> Optional[Dict[str, Any]]:
        frame = self.latest_vision_frames.get(chat_id)
        if not frame:
            return None
        # TTL check: 30 seconds max to prevent stale zombie frames & privacy leaks
        if time.time() - frame.get("timestamp", 0) > 30.0:
            logger.info(f"⏳ Vision frame for chat_id={chat_id} expired (>30s TTL), purging stale frame.")
            del self.latest_vision_frames[chat_id]
            return None
        return frame

    @staticmethod
    def _build_local_tool_action(
        tool_name: str,
        tool_output: Dict[str, Any],
        payload: ReasoningRequestPayload,
        gen_id: int,
        src_channel: str,
    ) -> Optional[ActionDecisionPayload]:
        """
        Maps a *fire-and-forget* local embodiment tool's output onto a
        structured ActionDecisionPayload. Only TTS/image/telegram_action live
        here -- they don't need their result shown back to the model, so no
        round trip is required (the model's own text from this same
        generation round is used as the accompanying message). Returns None
        for anything else (e.g. presenter_mode, MCP tools), signalling the
        caller that this call instead needs the round-trip path.
        """
        if tool_name == "generate_tts_speech":
            return ActionDecisionPayload(
                event_id=payload.event_id,
                source_component="cognitive_engine",
                chat_id=payload.chat_id,
                generation_id=gen_id,
                source_channel=src_channel,
                action_type="send_voice",
                voice_path=tool_output.get("voice_path"),
                text_content=tool_output.get("text"),
                media_type="voice",
                chat_action="record_audio",
            )
        if tool_name == "generate_image":
            return ActionDecisionPayload(
                event_id=payload.event_id,
                source_component="cognitive_engine",
                chat_id=payload.chat_id,
                generation_id=gen_id,
                source_channel=src_channel,
                action_type="send_photo",
                photo_path=tool_output.get("photo_path"),
                text_content=None,  # Caption will come from LLM text response below
                media_type="photo",
            )
        if tool_name == "telegram_action":
            return ActionDecisionPayload(
                event_id=payload.event_id,
                source_component="cognitive_engine",
                chat_id=payload.chat_id,
                generation_id=gen_id,
                source_channel=src_channel,
                action_type=tool_output.get("action_type", "send_message"),
                sticker_id=tool_output.get("sticker_id"),
                reaction_emoji=tool_output.get("reaction_emoji"),
            )
        return None

    @staticmethod
    def _append_tool_round_trip(
        messages: List[Dict[str, Any]],
        tool_name: str,
        tool_args: Dict[str, Any],
        tool_output: Dict[str, Any],
        thought_signature: Optional[bytes] = None,
    ) -> List[Dict[str, Any]]:
        """
        Appends a (function_call, function_response) turn pair so the next
        generate_stream() round shows the model what the tool actually
        returned, instead of it improvising -- this is what makes
        ppt_get_slide_text-style read tools ground the model's narration in
        real content. See GeminiProvider._messages_to_contents.
        """
        messages = list(messages)
        fc_meta: Dict[str, Any] = {"name": tool_name, "args": tool_args}
        if thought_signature:
            fc_meta["thought_signature"] = thought_signature
        messages.append({
            "role": "model",
            "content": "",
            "metadata": {"function_call": fc_meta},
        })
        messages.append({
            "role": "user",
            "content": "",
            "metadata": {"function_response": {"name": tool_name, "response": tool_output}},
        })
        return messages

    def _unknown_tool_error(self, chat_id: int, tool_name: str) -> Dict[str, Any]:
        """
        Builds the round-trip error payload for a tool name that matched
        neither the local ToolRegistry nor an active presenter session.

        This is the LLM's *only* recovery signal for two very different
        situations: it hallucinated a plausible-but-wrong name for a real
        presenter tool (e.g. "vscode_search_content" instead of
        "vscode_search"), or it tried to use a presenter tool before ever
        calling presenter_mode(activate). A bare "unknown tool" string gives
        it nothing to act on and the model tends to narrate the failure to
        the user instead of self-correcting -- so tell it explicitly what IS
        available (or how to make something available) in the same turn.
        """
        active_names = [s["name"] for s in self.presenter_manager.get_active_tool_schemas(chat_id)]
        if active_names:
            detail = (
                f"unknown tool '{tool_name}'. It does not exist -- do not retry it. "
                f"Tools currently available in this session: {', '.join(active_names)}."
            )
        else:
            detail = (
                f"unknown tool '{tool_name}'. No presenter session is active for this chat, so no "
                "ppt_*/vscode_* tool exists yet. Call presenter_mode(action='activate', "
                "target='ppt' or 'vscode', root_path=<deck or workspace directory>) first, then retry "
                "using one of the tool names it reports as available."
            )
        return {"error": True, "detail": detail}

    async def execute_reasoning_loop(
            self, payload: ReasoningRequestPayload
    ) -> List[ActionDecisionPayload]:
        # Lazy Evaluation & Token Conservation: If triggered by routine TICK and no proactive flag, skip LLM call
        if payload.trigger_type == "tick" and not getattr(payload, "is_proactive_opportunity", False):
            logger.debug(f"Tick event for chat_id={payload.chat_id} skipped LLM reasoning (Lazy Evaluation Token Conservation)")
            return []

        # Fast-Path System Command Handlers
        inbound_text = (payload.inbound_message.raw_text.strip()
                        if payload.inbound_message and payload.inbound_message.raw_text
                        else "")
        
        cmd = inbound_text.lower()
        if cmd == "/ping":
            return [
                ActionDecisionPayload(
                    event_id=payload.event_id,
                    source_component="cognitive_engine",
                    chat_id=payload.chat_id,
                    action_type="send_message",
                    text_content="pong 喵~ 🏓 (BetterAgent 系统运行正常！)",
                    chat_action="typing",
                )
            ]
        elif cmd in ("/health", "/status"):
            history_len = len(payload.short_term_history)
            rag_count = len(payload.rag_facts)
            status_text = (
                f"🐱 **BetterAgent 猫娘健康度指标**\n\n"
                f"• **系统状态**: 正常在线 🟢\n"
                f"• **触发模式**: {payload.trigger_type or 'user_message'}\n"
                f"• **短期记忆缓冲**: {history_len} 条\n"
                f"• **RAG 检索事实数**: {rag_count} 条\n"
                f"• **猫娘状态**: {payload.current_emotion or '正常'}\n"
            )
            return [
                ActionDecisionPayload(
                    event_id=payload.event_id,
                    source_component="cognitive_engine",
                    chat_id=payload.chat_id,
                    action_type="send_message",
                    text_content=status_text,
                    chat_action="typing",
                )
            ]
        elif cmd == "/help":
            help_text = (
                "🐾 **BetterAgent 猫娘指令与交互说明** 🐾\n\n"
                "• `/ping` - 探针基础存活检测\n"
                "• `/health` 或 `/status` - 查看猫娘系统健康度与情绪参数\n"
                "• `/help` - 显示此帮助信息\n\n"
                "💡 **日常互动**: 直接发文字聊天、求抱抱、夸奖猫娘，或者要求猫娘画图、发语音包喵~"
            )
            return [
                ActionDecisionPayload(
                    event_id=payload.event_id,
                    source_component="cognitive_engine",
                    chat_id=payload.chat_id,
                    action_type="send_message",
                    text_content=help_text,
                    chat_action="typing",
                )
            ]

        # Determine source_channel from inbound_message
        src_channel = "telegram"
        if payload.inbound_message and getattr(payload.inbound_message, "source_channel", None):
            src_channel = payload.inbound_message.source_channel

        # Channel-Aware Tools: Filter schemas depending on channel (Exclude telegram_action on Web)
        all_schemas = self.tool_registry.get_all_schemas()
        if src_channel == "web":
            tools_schema = [t for t in all_schemas if t.get("name") != "telegram_action"]
        else:
            tools_schema = all_schemas

        system_prompt = PromptBuilder.build_system_prompt(payload)
        messages = PromptBuilder.build_messages(payload)

        # Token Explosion Protection: Strip vision_frame metadata from ALL past history messages
        for msg in messages:
            if isinstance(msg.get("metadata"), dict) and "vision_frame" in msg["metadata"]:
                del msg["metadata"]["vision_frame"]

        # Attach latest Vision Frame ONLY to the last User message if within 30s TTL
        vision_frame = self.get_valid_vision_frame(payload.chat_id)
        if vision_frame and messages:
            if "metadata" not in messages[-1]:
                messages[-1]["metadata"] = {}
            messages[-1]["metadata"]["vision_frame"] = vision_frame
            logger.info(f"📷 Attached fresh Vision Frame ({vision_frame.get('source_type')}) to latest LLM message for chat_id={payload.chat_id}")

        # Run LLM reasoning
        result = await self.default_provider.generate(
            messages=messages,
            tools_schema=tools_schema,
            system_prompt=system_prompt,
        )

        actions: List[ActionDecisionPayload] = []
        tool_calls = result.get("tool_calls", [])
        raw_text = result.get("text", "")

        # Fallback: Parse embedded ReAct JSON tool calls from text if native tool_calls is empty
        if not tool_calls and '"action":' in raw_text and '"action_input":' in raw_text:
            import json
            try:
                json_match = re.search(r"\{\s*\"action\"\s*:\s*\"([^\"]+)\".*?\"action_input\"\s*:\s*(.*?)\s*\}", raw_text, re.DOTALL)
                if json_match:
                    act_name = json_match.group(1)
                    act_input_str = json_match.group(2).strip()
                    if act_input_str.startswith('"') and act_input_str.endswith('"'):
                        try:
                            act_input_str = json.loads(act_input_str)
                        except Exception:
                            pass
                    act_args = json.loads(act_input_str) if isinstance(act_input_str, str) and act_input_str.startswith('{') else {"prompt": act_input_str}
                    tool_calls = [{"name": act_name, "args": act_args}]
            except Exception as pe:
                logger.warning(f"Failed to parse ReAct JSON tool call: {pe}")

        # Determine source_channel & generation_id from payload
        src_channel = "telegram"
        gen_id = getattr(payload, "generation_id", 1)
        if payload.inbound_message:
            if getattr(payload.inbound_message, "source_channel", None):
                src_channel = payload.inbound_message.source_channel
            if getattr(payload.inbound_message, "generation_id", None):
                gen_id = payload.inbound_message.generation_id

        # Execute any tool calls from LLM
        for call in tool_calls:
            tool_name = call.get("name")
            tool_args = call.get("args", {})
            tool = self.tool_registry.get_tool(tool_name)
            if tool:
                tool_output = await tool.execute(**tool_args)
                action = self._build_local_tool_action(tool_name, tool_output, payload, gen_id, src_channel)
                if action is not None:
                    actions.append(action)

        # Main text response payload
        raw_text = result.get("text", "")

        # Parse embedded sticker JSON blocks if any
        sticker_match = re.search(r"\{\s*\"(action_type|action)\"\s*:\s*\"(sticker|send_sticker)\".*?\"sticker_id\"\s*:\s*\"([^\"]+)\"\s*\}", raw_text, re.DOTALL)
        if not sticker_match:
            sticker_match = re.search(r"\{\s*\"sticker_id\"\s*:\s*\"([^\"]+)\"\s*\}", raw_text, re.DOTALL)

        if sticker_match:
            sticker_id = sticker_match.group(3) if len(sticker_match.groups()) >= 3 else sticker_match.group(1)
            # This path bypasses TelegramActionTool entirely (it's a
            # fallback for when the model leaks a tool-call-shaped JSON blob
            # into plain text instead of using real function calling), so it
            # must apply the same untrusted-filename check independently.
            # See docs/SECURITY.md.
            if is_safe_media_filename(sticker_id):
                actions.append(
                    ActionDecisionPayload(
                        event_id=payload.event_id,
                        source_component="cognitive_engine",
                        chat_id=payload.chat_id,
                        generation_id=gen_id,
                        source_channel=src_channel,
                        action_type="send_sticker",
                        sticker_id=sticker_id,
                    )
                )
            else:
                logger.warning(f"Rejected unsafe sticker_id parsed from raw LLM text: {sticker_id!r}")

        # Robustly clean markdown code blocks and multi-line JSON objects from text response
        cleaned_raw_text = re.sub(r"```(?:json)?\s*[\s\S]*?```", "", raw_text, flags=re.MULTILINE)
        cleaned_raw_text = re.sub(r"\{\s*\"(action_type|action|sticker_id)\"[\s\S]*?\}", "", cleaned_raw_text)
        cleaned_raw_text = cleaned_raw_text.strip()

        thought, clean_text = parse_thought_and_clean_text(cleaned_raw_text)

        if thought:
            logger.info(f"CoT Inner Monologue for chat_id={payload.chat_id}:\n{thought}")

        if clean_text:
            actions.append(
                ActionDecisionPayload(
                    event_id=payload.event_id,
                    source_component="cognitive_engine",
                    chat_id=payload.chat_id,
                    generation_id=gen_id,
                    source_channel=src_channel,
                    action_type="send_message",
                    text_content=clean_text,
                    chat_action="typing",
                ))

        return actions

    async def stream_reasoning_loop(
        self,
        payload: ReasoningRequestPayload,
        cancel_event: Optional[Any] = None,
    ):
        """
        Yields sentence-level ActionDecisionPayload chunks in real-time as LLM streams text deltas.
        Supports cancellation via cancel_event.

        Also drives a bounded (MAX_TOOL_ROUNDS) tool-call round trip: a fire-and-
        forget local tool (TTS/image/telegram_action) maps straight to an
        ActionDecisionPayload with no round trip, same as before. Anything else
        -- presenter_mode, or an active presenter's MCP tools (ppt_*/vscode_*) --
        has its result fed back into `messages` and triggers another
        generate_stream() round, so the model's next text is grounded in the
        real tool result instead of guessed.
        """
        if payload.trigger_type == "tick" and not getattr(payload, "is_proactive_opportunity", False):
            return

        src_channel = "telegram"
        gen_id = getattr(payload, "generation_id", 1)
        if payload.inbound_message:
            if getattr(payload.inbound_message, "source_channel", None):
                src_channel = payload.inbound_message.source_channel
            if getattr(payload.inbound_message, "generation_id", None):
                gen_id = payload.inbound_message.generation_id

        system_prompt = PromptBuilder.build_system_prompt(payload)
        messages = PromptBuilder.build_messages(payload)

        for msg in messages:
            if isinstance(msg.get("metadata"), dict) and "vision_frame" in msg["metadata"]:
                del msg["metadata"]["vision_frame"]

        vision_frame = self.get_valid_vision_frame(payload.chat_id)
        if vision_frame and messages:
            if "metadata" not in messages[-1]:
                messages[-1]["metadata"] = {}
            messages[-1]["metadata"]["vision_frame"] = vision_frame

        segmenter = SentenceSegmenter()

        try:
            for round_idx in range(self.MAX_TOOL_ROUNDS):
                # Recomputed every round, not just once up front: a
                # presenter_mode(activate) call in round N must make its
                # ppt_*/vscode_* tools visible to round N+1 in this same
                # turn, not just to some future reasoning call.
                all_schemas = self.tool_registry.get_all_schemas() + self.presenter_manager.get_active_tool_schemas(payload.chat_id)
                tools_schema = [t for t in all_schemas if t.get("name") != "telegram_action"] if src_channel == "web" else all_schemas

                stream_gen = self.default_provider.generate_stream(
                    messages=messages,
                    tools_schema=tools_schema,
                    system_prompt=system_prompt,
                    cancel_event=cancel_event,
                )

                pending_calls: List[Dict[str, Any]] = []
                cancelled = False

                async for event in stream_gen:
                    if cancel_event and cancel_event.is_set():
                        logger.info(f"⚡ stream_reasoning_loop cancelled for chat_id={payload.chat_id}")
                        cancelled = True
                        break

                    if event.get("type") == "tool_calls":
                        pending_calls.extend(event.get("calls", []))
                        continue

                    sentences = segmenter.push(event.get("delta", ""))
                    for s in sentences:
                        sentence_str = s.strip()
                        if sentence_str:
                            yield ActionDecisionPayload(
                                event_id=payload.event_id,
                                source_component="cognitive_engine",
                                chat_id=payload.chat_id,
                                generation_id=gen_id,
                                source_channel=src_channel,
                                action_type="send_message",
                                text_content=sentence_str,
                                chat_action="typing",
                                is_final=False,
                            )

                if cancelled:
                    return

                if not pending_calls:
                    break

                # Heartbeat: a round that produced only tool_calls (no text) is
                # about to spend real time executing them -- MCP tool calls can
                # involve a subprocess cold start, and each further
                # generate_stream() round is its own LLM round trip -- with
                # nothing published to NATS in between, Go core's per-chat
                # watchdog (ThinkingTimeoutDuration / whatever window a prior
                # chunk set) has no way to know this turn is still alive and
                # will force the state machine back to IDLE mid-turn (see
                # engine/state_machine.go's deadman switch). This empty,
                # non-final chunk carries no visible text -- WebGateway/gotd
                # adapter only forward non-empty TextContent to the user -- but
                # both still extend the watchdog window on any non-final
                # ActionDecision, which is exactly what's needed here.
                yield ActionDecisionPayload(
                    event_id=payload.event_id,
                    source_component="cognitive_engine",
                    chat_id=payload.chat_id,
                    generation_id=gen_id,
                    source_channel=src_channel,
                    action_type="send_message",
                    text_content="",
                    chat_action="typing",
                    is_final=False,
                )

                needs_another_round = False
                for call in pending_calls:
                    tool_name = call.get("name")
                    tool_args = call.get("args", {}) or {}
                    tool = self.tool_registry.get_tool(tool_name)

                    if tool is not None:
                        if tool_name == "presenter_mode":
                            tool_output = await tool.execute(**tool_args, chat_id=payload.chat_id)
                        else:
                            tool_output = await tool.execute(**tool_args)

                        action = self._build_local_tool_action(tool_name, tool_output, payload, gen_id, src_channel)
                        if action is not None:
                            yield action
                        else:
                            messages = self._append_tool_round_trip(messages, tool_name, tool_args, tool_output, thought_signature=call.get("thought_signature"))
                            needs_another_round = True
                    else:
                        mcp_output = await self.presenter_manager.call_tool(payload.chat_id, tool_name, tool_args)
                        if mcp_output is None:
                            logger.warning(f"Received unknown tool call from LLM: {tool_name!r} (chat_id={payload.chat_id})")
                            mcp_output = self._unknown_tool_error(payload.chat_id, tool_name)
                        messages = self._append_tool_round_trip(messages, tool_name, tool_args, mcp_output, thought_signature=call.get("thought_signature"))
                        needs_another_round = True

                if not needs_another_round:
                    break
            else:
                # Round budget exhausted while a tool call still needed a
                # round trip -- without this, the turn would just end here:
                # segmenter.flush() has nothing in it (every round so far was
                # tool_calls, no text), so the user gets a silent empty
                # final marker and no reply at all. Force one last round
                # with tools_schema=[] -- the model literally cannot call
                # another tool, so it must wrap up in plain text using
                # whatever it already learned from the tool results appended
                # to `messages` across the rounds above.
                logger.warning(f"stream_reasoning_loop hit MAX_TOOL_ROUNDS for chat_id={payload.chat_id}, forcing text-only wrap-up round")
                wrapup_stream = self.default_provider.generate_stream(
                    messages=messages,
                    tools_schema=[],
                    system_prompt=system_prompt,
                    cancel_event=cancel_event,
                )
                async for event in wrapup_stream:
                    if cancel_event and cancel_event.is_set():
                        logger.info(f"⚡ stream_reasoning_loop cancelled during wrap-up for chat_id={payload.chat_id}")
                        return
                    if event.get("type") == "tool_calls":
                        continue  # tools_schema=[] should preclude this; ignore defensively
                    sentences = segmenter.push(event.get("delta", ""))
                    for s in sentences:
                        sentence_str = s.strip()
                        if sentence_str:
                            yield ActionDecisionPayload(
                                event_id=payload.event_id,
                                source_component="cognitive_engine",
                                chat_id=payload.chat_id,
                                generation_id=gen_id,
                                source_channel=src_channel,
                                action_type="send_message",
                                text_content=sentence_str,
                                chat_action="typing",
                                is_final=False,
                            )

            final_sentences = segmenter.flush()
            final_clean = [s.strip() for s in final_sentences if s and s.strip()]

            if final_clean:
                total_final = len(final_clean)
                for i, sentence in enumerate(final_clean):
                    is_last = (i == total_final - 1)
                    yield ActionDecisionPayload(
                        event_id=payload.event_id,
                        source_component="cognitive_engine",
                        chat_id=payload.chat_id,
                        generation_id=gen_id,
                        source_channel=src_channel,
                        action_type="send_message",
                        text_content=sentence,
                        chat_action="typing",
                        is_final=is_last,
                    )
            else:
                # If no sentence emitted in flush, emit empty final marker payload
                yield ActionDecisionPayload(
                    event_id=payload.event_id,
                    source_component="cognitive_engine",
                    chat_id=payload.chat_id,
                    generation_id=gen_id,
                    source_channel=src_channel,
                    action_type="send_message",
                    text_content="",
                    chat_action="typing",
                    is_final=True,
                )
        except Exception as err:
            logger.error(f"Error in stream_reasoning_loop: {err}")
