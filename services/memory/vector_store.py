import math
import time
from typing import List, Dict, Any, Optional


class VectorMemoryStore:

    def __init__(self,
                 collection_name: str = "catgirl_memories",
                 decay_lambda: float = 0.01):
        self.collection_name = collection_name
        self.decay_lambda = decay_lambda
        # Memory storage fallback for demonstration / standalone mode
        self.in_memory_docs: List[Dict[str, Any]] = []

    async def search_relevant_memories(
        self,
        user_id: int,
        query_text: str,
        top_k: int = 5,
        score_threshold: float = 0.7
    ) -> List[str]:
        now = time.time()
        scored_results = []

        for doc in self.in_memory_docs:
            if doc.get("user_id") != user_id:
                continue

            # Compute dummy vector similarity heuristic
            similarity = 0.85
            delta_t = now - doc.get("timestamp", now)

            # Ebbinghaus Decay Formula
            decay_factor = math.exp(-self.decay_lambda * (delta_t / 3600.0))
            final_score = similarity * decay_factor

            if final_score >= score_threshold:
                scored_results.append((final_score, doc["text"]))

        scored_results.sort(key=lambda x: x[0], reverse=True)
        return [text for score, text in scored_results[:top_k]]

    async def add_memory_segment(self, user_id: int, text: str,
                                 metadata: Optional[Dict[str, Any]]) -> str:
        doc_id = f"mem_{int(time.time() * 1000)}"
        self.in_memory_docs.append({
            "id": doc_id,
            "user_id": user_id,
            "text": text,
            "metadata": metadata or {},
            "timestamp": time.time(),
        })
        return doc_id
