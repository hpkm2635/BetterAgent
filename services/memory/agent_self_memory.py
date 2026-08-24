import time
from typing import List, Dict, Any


class AgentSelfMemory:

    def __init__(self):
        self.self_events: Dict[int, List[Dict[str, Any]]] = {}

    def add_self_event(self, chat_id: int, description: str, emotion_tag: str) -> None:
        self.self_events.setdefault(chat_id, []).append({
            "description": description,
            "emotion_tag": emotion_tag,
            "timestamp": time.time(),
        })

    def get_recent_self_events(self, chat_id: int, limit: int = 3) -> List[Dict[str, Any]]:
        return self.self_events.get(chat_id, [])[-limit:]
