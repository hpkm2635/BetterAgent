import time
from typing import List, Dict, Any


class AgentSelfMemory:

    def __init__(self):
        self.self_events: List[Dict[str, Any]] = []

    def add_self_event(self, description: str, emotion_tag: str) -> None:
        self.self_events.append({
            "description": description,
            "emotion_tag": emotion_tag,
            "timestamp": time.time(),
        })

    def get_recent_self_events(self, limit: int = 3) -> List[Dict[str, Any]]:
        return self.self_events[-limit:]
