from typing import List, Dict, Any, Tuple


class TokenBudgetManager:

    def __init__(self, max_budget: int = 4000):
        self.max_budget = max_budget

    def fit_into_budget(
        self,
        system_prompt: str,
        history: List[Dict[str, Any]],
        profile: Dict[str, Any],
        rag_facts: List[str],
    ) -> Tuple[str, List[Dict[str, Any]], List[str]]:
        # Heuristic token counting (~4 chars per token)
        total_tokens = len(system_prompt) // 4

        # Truncate RAG facts if budget is exceeded
        trimmed_facts = []
        for fact in rag_facts:
            fact_tokens = len(fact) // 4
            if total_tokens + fact_tokens < self.max_budget:
                trimmed_facts.append(fact)
                total_tokens += fact_tokens

        # Retain latest history
        trimmed_history = []
        for msg in reversed(history):
            msg_tokens = len(msg.get("content", "")) // 4
            if total_tokens + msg_tokens < self.max_budget:
                trimmed_history.insert(0, msg)
                total_tokens += msg_tokens
            else:
                break

        return system_prompt, trimmed_history, trimmed_facts
