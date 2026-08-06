import time
import re
import logging
from typing import List, Dict, Any, Tuple, Optional
from shared.schema.payloads import ReasoningRequestPayload, ActionDecisionPayload
from services.cognitive.providers.gemini_provider import GeminiProvider
from services.cognitive.providers.claude_provider import ClaudeProvider
from services.cognitive.tool_registry import ToolRegistry
from services.cognitive.prompt_builder import PromptBuilder

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


class CognitiveEngine:

    def __init__(self, default_provider_name: str = "gemini"):
        self.tool_registry = ToolRegistry()
        self.providers = {
            "gemini": GeminiProvider(),
            "claude": ClaudeProvider(),
        }
        self.default_provider = self.providers.get(default_provider_name,
                                                   self.providers["gemini"])
        self.latest_vision_frames: Dict[int, Dict[str, Any]] = {}

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

        # Determine source_channel from inbound_message or default to "telegram"
        src_channel = "telegram"
        if payload.inbound_message and getattr(payload.inbound_message, "source_channel", None):
            src_channel = payload.inbound_message.source_channel

        # Execute any tool calls from LLM
        for call in tool_calls:
            tool_name = call.get("name")
            tool_args = call.get("args", {})
            tool = self.tool_registry.get_tool(tool_name)
            if tool:
                tool_output = await tool.execute(**tool_args)

                # Formulate action decision depending on tool
                if tool_name == "generate_tts_speech":
                    actions.append(
                        ActionDecisionPayload(
                            event_id=payload.event_id,
                            source_component="cognitive_engine",
                            chat_id=payload.chat_id,
                            source_channel=src_channel,
                            action_type="send_voice",
                            voice_path=tool_output.get("voice_path"),
                            text_content=tool_output.get("text"),
                            media_type="voice",
                            chat_action="record_audio",
                        ))
                elif tool_name == "generate_image":
                    actions.append(
                        ActionDecisionPayload(
                            event_id=payload.event_id,
                            source_component="cognitive_engine",
                            chat_id=payload.chat_id,
                            source_channel=src_channel,
                            action_type="send_photo",
                            photo_path=tool_output.get("photo_path"),
                            text_content=None,  # Caption will come from LLM text response below
                            media_type="photo",
                        ))
                elif tool_name == "telegram_action":
                    actions.append(
                        ActionDecisionPayload(
                            event_id=payload.event_id,
                            source_component="cognitive_engine",
                            chat_id=payload.chat_id,
                            source_channel=src_channel,
                            action_type=tool_output.get("action_type",
                                                        "send_message"),
                            sticker_id=tool_output.get("sticker_id"),
                            reaction_emoji=tool_output.get("reaction_emoji"),
                        ))

        # Main text response payload
        raw_text = result.get("text", "")

        # Parse embedded sticker JSON blocks if any
        sticker_match = re.search(r"\{\s*\"(action_type|action)\"\s*:\s*\"(sticker|send_sticker)\".*?\"sticker_id\"\s*:\s*\"([^\"]+)\"\s*\}", raw_text, re.DOTALL)
        if not sticker_match:
            sticker_match = re.search(r"\{\s*\"sticker_id\"\s*:\s*\"([^\"]+)\"\s*\}", raw_text, re.DOTALL)

        if sticker_match:
            sticker_id = sticker_match.group(3) if len(sticker_match.groups()) >= 3 else sticker_match.group(1)
            actions.append(
                ActionDecisionPayload(
                    event_id=payload.event_id,
                    source_component="cognitive_engine",
                    chat_id=payload.chat_id,
                    source_channel=src_channel,
                    action_type="send_sticker",
                    sticker_id=sticker_id,
                )
            )

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
                    source_channel=src_channel,
                    action_type="send_message",
                    text_content=clean_text,
                    chat_action="typing",
                ))

        return actions
