import re

# Comprehensive Unicode Emoji regex pattern covering standard emojis, symbols, and pictographs
_EMOJI_PATTERN = re.compile(
    r"[\U00010000-\U0010ffff"
    r"\u2600-\u27BF"
    r"\u2300-\u23FF"
    r"\u2B50\u2B55\u203C\u2049\u2139\u2194-\u2199\u21A9-\u21AA"
    r"\u231A-\u231B\u2328\u23CF\u23E9-\u23F3\u23F8-\u23FA\u24C2"
    r"\u25AA-\u25AB\u25B6\u25C0\u25FB-\u25FE\u2600-\u27EF\u2934-\u2935"
    r"\u2B05-\u2B07\u2B1B-\u2B1C\u3030\u303D\u3297\u3299"
    r"\ufe00-\ufe0f\u200d]"
)


def clean_tts_text(text: str) -> str:
    """
    Cleans and sanitizes raw LLM output text for TTS voice synthesis:
    1. Removes all Unicode Emoji pictographs (e.g. ❤️, 😽).
    2. Strips residual code artifacts, JSON braces, markdown fence tags (```json, ```, </think>, <tool_call>).
    3. Rejects literal 'None', 'undefined', 'null' strings or empty/non-pronounceable strings.
    Returns cleaned pronounceable text, or empty string "" if nothing pronounceable remains.
    """
    if not text or not isinstance(text, str):
        return ""

    s = text.strip()
    if not s or s.lower() in ("none", "null", "undefined"):
        return ""

    # Remove thoughts and tags
    s = re.sub(r"</?(?:thought|think)>", "", s)
    s = re.sub(r"</?(?:tool_call|tool_use)>", "", s)
    s = re.sub(r"```(?:json)?[\s\S]*?```", "", s)
    s = re.sub(r"```", "", s)

    # Remove residual JSON fragments / single braces
    s = re.sub(r"\{\s*\"[^\"]+\"[\s\S]*?\}", "", s)
    s = re.sub(r"^[}\s{]+$", "", s)

    # Remove Emojis
    s = _EMOJI_PATTERN.sub("", s)

    # Remove extra spaces
    s = re.sub(r"\s+", " ", s).strip()

    # Check if any pronounceable Chinese / English / Alphanumeric characters remain
    if not re.search(r"[\u4e00-\u9fa5a-zA-Z0-9]", s):
        return ""

    return s
