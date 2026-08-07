"""
NATS Subject constants shared across services.
"""

SUBJECT_TICK = "agent.tick"
SUBJECT_INBOUND_MESSAGE = "agent.inbound_message"
SUBJECT_ENRICH_CONTEXT_REQ = "agent.enrich_context_req"
SUBJECT_REASONING_REQUEST = "agent.reasoning_request"
SUBJECT_REASONING_COMPLETED = "agent.reasoning_completed"
SUBJECT_ACTION_DECISION = "agent.action_decision"
SUBJECT_ACTION_COMPLETED = "agent.action_completed"
SUBJECT_CONSOLIDATE_MEMORY_REQ = "agent.consolidate_memory_req"
SUBJECT_ERROR = "agent.error"

# Digital Human & Realtime Multimodal Subjects
SUBJECT_SPEECH_START = "agent.speech.start"
SUBJECT_SPEECH_END = "agent.speech.end"
SUBJECT_USER_INTERRUPT = "agent.user.interrupt"
SUBJECT_AUDIO_CHUNK = "agent.audio.chunk"
SUBJECT_VISEME_DATA = "agent.viseme.data"
SUBJECT_EMOTION_UPDATE = "agent.emotion.update"
SUBJECT_VISION_FRAME = "agent.vision.frame"

# 7 Realtime Cancelable Streaming Subjects
SUBJECT_STT_STREAM_CHUNK = "agent.stt.stream_chunk"
SUBJECT_STT_STREAM_FINAL = "agent.stt.stream_final"
SUBJECT_TTS_STREAM_CHUNK = "agent.tts.stream_chunk"
SUBJECT_TTS_STREAM_END = "agent.tts.stream_end"
SUBJECT_STREAM_CANCEL_REQ = "agent.stream.cancel_req"
SUBJECT_STREAM_CANCEL_ACK = "agent.stream.cancel_ack"
SUBJECT_STREAM_STATE_CHANGE = "agent.stream.state_change"


