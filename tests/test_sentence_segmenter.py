from services.cognitive.cognitive_engine import SentenceSegmenter


def _drain(segmenter: SentenceSegmenter, deltas):
    out = []
    for d in deltas:
        out.extend(segmenter.push(d))
    out.extend(segmenter.flush())
    return out


def test_filename_dot_is_not_a_sentence_boundary():
    # Regression test: this exact text used to split into
    # "我已经帮你读取了 `services/c" + "py` 的前 50 行代码了。" because the
    # '.' in "cognitive_engine.py" was treated as an English full stop --
    # each malformed half then got sent to TTS as its own (broken) sentence.
    segmenter = SentenceSegmenter()
    text = "我已经帮你读取了 `services/cognitive/cognitive_engine.py` 的前 50 行代码了。"
    sentences = _drain(segmenter, [text])
    assert sentences == [text]


def test_filename_dot_split_across_stream_deltas():
    # Same case, but pushed token-by-token like a real streaming response,
    # including the delta boundary landing right after the dot.
    segmenter = SentenceSegmenter()
    chunks = ["我已经帮你读取了 `services/cognitive/cognitive_engine.", "py` 的前 50 行代码了。"]
    sentences = _drain(segmenter, chunks)
    assert sentences == ["我已经帮你读取了 `services/cognitive/cognitive_engine.py` 的前 50 行代码了。"]


def test_unclosed_backtick_span_waits_for_its_closer():
    # While the closing backtick hasn't streamed in yet, punctuation inside
    # the span (e.g. the '.' in a module path fragment) must not trigger a
    # slice -- that's what produced the "`parse_thought_an" 8-chunk fragment
    # in production (see test_filename_dot_split_across_stream_deltas).
    segmenter = SentenceSegmenter()
    mid_span = segmenter.push("该函数是 `services.cognitive")
    assert mid_span == []

    full = _drain(segmenter, [".engine`，负责清理文本。"])
    # The dots inside the still-open span must not have caused a slice --
    # the identifier survives intact in whichever sentence it lands in,
    # never torn at "cognitive" / ".engine".
    assert "".join(full) == "该函数是 `services.cognitive.engine`，负责清理文本。"
    assert not any(s.endswith("cognitive") or s.startswith(".engine") for s in full)


def test_ordinary_sentence_end_still_splits():
    segmenter = SentenceSegmenter()
    sentences = segmenter.push("你好呀主人。今天天气不错喵~")
    assert sentences == ["你好呀主人。", "今天天气不错喵~"]


def test_decimal_number_is_not_split():
    segmenter = SentenceSegmenter()
    sentences = _drain(segmenter, ["版本号是 3.14 喵。"])
    assert sentences == ["版本号是 3.14 喵。"]


def test_short_comma_fragments_still_wait_for_more_text():
    segmenter = SentenceSegmenter()
    sentences = segmenter.push("嗯，")
    assert sentences == []
