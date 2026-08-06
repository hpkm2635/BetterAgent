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
SUBJECT_VISION_FRAME = "agent.vision_frame"
