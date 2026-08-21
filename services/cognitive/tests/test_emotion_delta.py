import pytest
from services.cognitive.cognitive_engine import parse_emotion_delta_from_text, SentenceSegmenter


def test_parse_emotion_delta_from_text_valid():
    raw = "太棒了喵！主人的夸奖让我很高兴~\n[EMOTION_DELTA: d_valence=+0.2, d_arousal=0.1, d_affection=+1.5, is_jealous=false]"
    clean_text, delta = parse_emotion_delta_from_text(raw)

    assert clean_text == "太棒了喵！主人的夸奖让我很高兴~"
    assert delta is not None
    assert delta["delta_valence"] == 0.2
    assert delta["delta_arousal"] == 0.1
    assert delta["delta_affection"] == 1.5
    assert delta["is_jealous"] is False


def test_parse_emotion_delta_jealous():
    raw = "亨！主人居然去陪其他猫咪了... [EMOTION_DELTA: d_valence=-0.2, d_affection=-0.5, is_jealous=true]"
    clean_text, delta = parse_emotion_delta_from_text(raw)

    assert clean_text == "亨！主人居然去陪其他猫咪了..."
    assert delta is not None
    assert delta["delta_valence"] == -0.2
    assert delta["is_jealous"] is True


def test_sentence_segmenter_tail_buffer_stripping():
    segmenter = SentenceSegmenter()

    chunks = [
        "主人你真好喵！",
        "（蹭蹭主人的手）",
        "\n[EMO",
        "TION_DEL",
        "TA: d_val",
        "ence=+0.",
        "2, d_aff",
        "ection=+",
        "1.0, is_",
        "jealous=",
        "false]"
    ]

    emitted_sentences = []
    for c in chunks:
        emitted_sentences.extend(segmenter.push(c))

    flushed = segmenter.flush()
    emitted_sentences.extend(flushed)

    # Ensure no sentence chunk pushed to TTS contains EMOTION_DELTA
    for s in emitted_sentences:
        assert "[EMOTION_DELTA" not in s

    # Verify that the emotion delta was correctly extracted upon completion
    assert segmenter.last_emotion_delta is not None
    assert segmenter.last_emotion_delta["delta_valence"] == 0.2
    assert segmenter.last_emotion_delta["delta_affection"] == 1.0
    assert segmenter.last_emotion_delta["is_jealous"] is False
