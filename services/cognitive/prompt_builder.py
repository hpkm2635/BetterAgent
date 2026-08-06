from typing import List, Dict, Any, Optional
from persona.catgirl_prompt import CATGIRL_BASE_PROMPT, build_sleepy_prompt
from shared.schema.payloads import ReasoningRequestPayload


class PromptBuilder:

    @staticmethod
    def build_system_prompt(payload: ReasoningRequestPayload) -> str:
        if payload.system_prompt_override:
            return payload.system_prompt_override

        prompt_parts = [
            CATGIRL_BASE_PROMPT,
            "",
            payload.current_emotion,  # Contains emotion + personality + circadian description
        ]

        if payload.user_profile:
            pref = payload.user_profile.get("preferred_name", "主人")
            prompt_parts.append(f"[称呼习惯] 你称呼对方为：{pref}")

        if payload.rag_facts:
            prompt_parts.append("[长史记忆/相关背景信息]:")
            for fact in payload.rag_facts:
                prompt_parts.append(f"- {fact}")

        return "\n".join(prompt_parts)

    @staticmethod
    def build_messages(
            payload: ReasoningRequestPayload) -> List[Dict[str, Any]]:
        messages = []
        for msg in payload.short_term_history:
            messages.append({
                "role": msg.get("role", "user"),
                "content": msg.get("content", ""),
            })

        if payload.inbound_message and payload.inbound_message.raw_text:
            # Avoid duplicate if it's already in short_term_history
            if not messages or messages[-1]["content"] != payload.inbound_message.raw_text:
                messages.append({
                    "role": "user",
                    "content": payload.inbound_message.raw_text,
                })

        return messages
