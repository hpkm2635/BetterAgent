import re
from typing import List, Dict, Any, Tuple, Optional
from shared.config_loader import get_config_val

# Regex matching CJK Unified Ideographs, Extensions, and Punctuation
_CJK_REGEX = re.compile(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef\u3400-\u4dbf]")


def estimate_tokens(text: str) -> int:
    """
    Accurately estimate BPE tokens for mixed CJK & English text.
    - CJK characters (Chinese/Japanese/Korean): ~1.5 tokens per char in BPE tokenizers
    - ASCII / Non-CJK characters: ~0.25 tokens per char (4 chars = 1 token)
    """
    if not text:
        return 0
    cjk_count = len(_CJK_REGEX.findall(text))
    non_cjk_count = len(text) - cjk_count
    return max(1, int(cjk_count * 1.5 + non_cjk_count * 0.25))


class TokenBudgetManager:

    def __init__(self, max_budget: Optional[int] = None):
        self.max_budget = max_budget if max_budget is not None else int(get_config_val("memory.token_budget_max", 6000))

    def fit_into_budget(
        self,
        system_prompt: str,
        history: List[Dict[str, Any]],
        profile: Dict[str, Any],
        rag_facts: List[str],
        kb_facts: Optional[List[str]] = None,
    ) -> Tuple[str, List[Dict[str, Any]], List[str], List[str]]:
        """Trim history, RAG facts, and KB facts so total context stays strictly under self.max_budget."""
        total_tokens = estimate_tokens(system_prompt or "") + estimate_tokens(str(profile or ""))

        # Truncate Campus KB facts if budget is exceeded
        trimmed_kb_facts = []
        for fact in (kb_facts or []):
            fact_tokens = estimate_tokens(fact)
            if total_tokens + fact_tokens < self.max_budget:
                trimmed_kb_facts.append(fact)
                total_tokens += fact_tokens

        # Truncate personal RAG facts if budget is exceeded
        trimmed_facts = []
        for fact in rag_facts:
            fact_tokens = estimate_tokens(fact)
            if total_tokens + fact_tokens < self.max_budget:
                trimmed_facts.append(fact)
                total_tokens += fact_tokens

        # Retain latest history
        trimmed_history = []
        for msg in reversed(history):
            msg_tokens = estimate_tokens(msg.get("content", ""))
            if total_tokens + msg_tokens < self.max_budget:
                trimmed_history.insert(0, msg)
                total_tokens += msg_tokens
            else:
                break

        return system_prompt, trimmed_history, trimmed_facts, trimmed_kb_facts
