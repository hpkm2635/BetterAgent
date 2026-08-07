import re
import logging
from typing import List, Dict, Any

logger = logging.getLogger("viseme_generator")
_PYPINYIN_WARNED = False

# VRM 5 Mouth Shapes: aa, ih, ou, ee, oh
FINAL_TO_VISEME = {
    # aa: open mouth / 'a' sound
    "a": "aa", "ia": "aa", "ua": "aa", "va": "aa", "an": "aa", "ian": "aa", "uan": "aa", "van": "aa", "ang": "aa", "iang": "aa", "uang": "aa",
    # ih: wide teeth / 'i' sound
    "i": "ih", "in": "ih", "ing": "ih",
    # ou: rounded puckered lips / 'u' sound
    "u": "ou", "ui": "ou", "un": "ou",
    # ee: smiling / 'e' sound
    "e": "ee", "ie": "ee", "ve": "ee", "er": "ee", "ei": "ee", "en": "ee", "eng": "ee",
    # oh: round open / 'o' sound
    "o": "oh", "uo": "oh", "ong": "oh", "iong": "oh", "ou": "oh", "ao": "oh", "iao": "oh",
}

# Standard VRM Viseme Shape Names to ID mapping
VISEME_SHAPE_TO_ID = {
    "aa": 0,
    "ih": 1,
    "ou": 2,
    "ee": 3,
    "oh": 4,
}

DIGIT_TO_CHINESE = {
    "0": "零", "1": "一", "2": "二", "3": "三", "4": "四",
    "5": "五", "6": "六", "7": "七", "8": "八", "9": "九",
}

ENGLISH_LETTER_TO_VISEME = {
    "a": "aa", "u": "ou",
    "i": "ih", "y": "ih",
    "o": "oh", "w": "oh",
    "e": "ee",
}


def preprocess_text(text: str) -> str:
    """Converts digits to Chinese words and normalizes text for phoneme extraction."""
    res = []
    for char in text:
        if char in DIGIT_TO_CHINESE:
            res.append(DIGIT_TO_CHINESE[char])
        else:
            res.append(char)
    return "".join(res)


def text_to_visemes(text: str, duration_seconds: float = 1.0) -> List[Dict[str, Any]]:
    """
    Converts Chinese/English/Numeric text to VRM 5 BlendShape visemes timeline.
    Prevents mouth freezing on English words or digits.
    Returns list of dicts: [{"time_offset": 0.05, "viseme_id": 0, "shape": "aa"}, ...]
    """
    if not text or duration_seconds <= 0:
        return []

    # 1. Preprocess digits to Chinese words
    normalized_text = preprocess_text(text)

    # Clean punctuation and whitespace
    clean_chars = [c for c in normalized_text if c.isalnum() or '\u4e00' <= c <= '\u9fa5']
    if not clean_chars:
        return []

    viseme_shapes = []

    try:
        from pypinyin import pinyin, Style
        for char in clean_chars:
            if '\u4e00' <= char <= '\u9fa5':
                # Chinese character
                raw = pinyin(char, style=Style.FINALS)
                final = raw[0][0].lower() if raw and raw[0] and raw[0][0] else "a"
                viseme_shapes.append(FINAL_TO_VISEME.get(final, "aa"))
            else:
                # English letter
                lower_char = char.lower()
                if lower_char in ENGLISH_LETTER_TO_VISEME:
                    viseme_shapes.append(ENGLISH_LETTER_TO_VISEME[lower_char])
                else:
                    # English consonants alternate between 'ee' and 'ih' for natural dynamic mouth movement
                    viseme_shapes.append("ee" if len(viseme_shapes) % 2 == 0 else "ih")
    except Exception as err:
        global _PYPINYIN_WARNED
        if not _PYPINYIN_WARNED:
            logger.warning(f"pypinyin fallback to character-based heuristic: {err}")
            _PYPINYIN_WARNED = True
        for i, char in enumerate(clean_chars):
            lower_char = char.lower()
            if lower_char in ENGLISH_LETTER_TO_VISEME:
                viseme_shapes.append(ENGLISH_LETTER_TO_VISEME[lower_char])
            else:
                viseme_shapes.append("ee" if i % 2 == 0 else "ih")

    total_frames = len(viseme_shapes)
    if total_frames == 0:
        return []

    time_per_frame = duration_seconds / float(total_frames)

    visemes = []
    for i, shape in enumerate(viseme_shapes):
        viseme_id = VISEME_SHAPE_TO_ID.get(shape, 0)
        time_offset = round(i * time_per_frame, 3)

        visemes.append({
            "time_offset": time_offset,
            "viseme_id": viseme_id,
            "shape": shape,
        })

    return visemes
