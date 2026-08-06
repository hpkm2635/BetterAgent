import time
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, field_validator


class BasePayload(BaseModel):
    event_id: str = ""
    timestamp: float = Field(default_factory=time.time)
    source_component: str = ""


class InboundMessagePayload(BasePayload):
    chat_id: int
    user_id: int
    message_id: int
    source_channel: str = "telegram"  # "telegram" | "web"
    raw_text: Optional[str] = None
    file_path: Optional[str] = None
    reply_to_message_id: Optional[int] = None
    media_type: Optional[str] = None  # "voice" | "photo" | None
    voice_transcript: Optional[str] = None
    chat_type: str = "private"
    sender_username: Optional[str] = None
    image_description: Optional[str] = None
    sender_first_name: str = ""
    sender_last_name: Optional[str] = None
    sender_display_name: str = ""


class TickPayload(BasePayload):
    iso_time: str
    time_of_day: str  # "morning" | "afternoon" | "evening" | "night"
    idle_duration_seconds: float
    is_sleep_hours: bool
    tick_counter: int
    emotion_description: str = ""


class EnrichContextReqPayload(BasePayload):
    chat_id: int
    user_id: int
    inbound_message: Optional[InboundMessagePayload] = None
    current_state: str
    trigger_type: str  # "user_message" | "proactive" | "tick"
    emotion_description: str = ""
    personality_description: str = ""
    circadian_description: str = ""


class ReasoningRequestPayload(BasePayload):
    chat_id: int
    user_id: int
    system_prompt_override: Optional[str] = None
    short_term_history: List[Dict[str, Any]] = Field(default_factory=list)
    user_profile: Dict[str, Any] = Field(default_factory=dict)
    rag_facts: List[str] = Field(default_factory=list)
    proactive_reason: Optional[str] = None
    current_emotion: str = "NEUTRAL"
    mood_score: float = 1.0
    formatted_time_str: str = ""
    inbound_message: Optional[InboundMessagePayload] = None
    trigger_type: Optional[str] = None

    @field_validator("short_term_history", mode="before")
    @classmethod
    def val_history(cls, v):
        return v if v is not None else []

    @field_validator("user_profile", mode="before")
    @classmethod
    def val_profile(cls, v):
        return v if v is not None else {}

    @field_validator("rag_facts", mode="before")
    @classmethod
    def val_rag(cls, v):
        return v if v is not None else []


class ActionDecisionPayload(BasePayload):
    chat_id: int
    source_channel: str = "telegram"  # "telegram" | "web"
    action_type: str  # "send_message" | "CHAT_ACTION" | "send_sticker"
    text_content: Optional[str] = None
    typing_delay: float = 0.0
    media_type: Optional[str] = None  # "voice" | "photo" | None
    reply_to_message_id: Optional[int] = None
    voice_path: Optional[str] = None
    photo_path: Optional[str] = None
    chat_action: Optional[str] = "typing"
    sticker_id: Optional[str] = None
    reaction_emoji: Optional[str] = None


class ActionCompletedPayload(BasePayload):
    chat_id: int
    sent_message_id: Optional[int] = None
    action_decision: ActionDecisionPayload
    status: str  # "success" | "failed"
    sent_time: float = Field(default_factory=time.time)
    error_detail: Optional[str] = None


class ConsolidateMemoryReqPayload(BasePayload):
    user_id: int
    chat_id: int
    messages_to_consolidate: List[Dict[str, Any]] = Field(default_factory=list)
    trigger_reason: str = "memory_full"


class ErrorPayload(BasePayload):
    error_code: str
    error_message: str
    stack_trace: Optional[str] = None
    caused_by_event_id: Optional[str] = None
