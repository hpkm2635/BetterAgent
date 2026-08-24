import pytest

from services.tts.viseme_generator import allocate_viseme_text_slice, text_to_visemes


def test_allocate_viseme_text_slice_empty_text_returns_empty():
    slice_text, remaining = allocate_viseme_text_slice("", 1.0)
    assert slice_text == ""
    assert remaining == ""


def test_allocate_viseme_text_slice_zero_or_negative_duration_returns_empty():
    slice_text, remaining = allocate_viseme_text_slice("图书馆周一至周五开放", 0.0)
    assert slice_text == ""
    assert remaining == "图书馆周一至周五开放"

    slice_text, remaining = allocate_viseme_text_slice("图书馆周一至周五开放", -1.0)
    assert slice_text == ""
    assert remaining == "图书馆周一至周五开放"


def test_allocate_viseme_text_slice_always_slices_at_least_one_char():
    # A tiny duration shouldn't round down to zero characters and stall.
    slice_text, remaining = allocate_viseme_text_slice("图书馆", 0.001, chars_per_sec=4.5)
    assert len(slice_text) >= 1
    assert slice_text + remaining == "图书馆"


def test_allocate_viseme_text_slice_never_exceeds_remaining_text():
    slice_text, remaining = allocate_viseme_text_slice("你好", 100.0, chars_per_sec=4.5)
    assert slice_text == "你好"
    assert remaining == ""


def test_allocate_viseme_text_slice_sequential_calls_reconstruct_a_prefix():
    original = "图书馆周一至周五开放至二十二点，全年无休。"
    remaining = original
    consumed = ""
    # Simulate several streamed sub-chunks, each with a real (short) duration.
    for duration in (0.08, 0.12, 0.05, 0.2, 0.15):
        chunk_text, remaining = allocate_viseme_text_slice(remaining, duration)
        consumed += chunk_text
        assert original.startswith(consumed)
        assert consumed + remaining == original


def test_allocate_viseme_text_slice_feeds_text_to_visemes_scoped_to_chunk():
    # Regression check for the actual bug: previously every sub-chunk got
    # visemes for the *entire* sentence compressed into its own short
    # duration. Now each sub-chunk's viseme count should be proportional to
    # just its own slice of the sentence, not the whole thing.
    sentence = "图书馆周一至周五开放至二十二点"
    remaining = sentence
    chunk_text, remaining = allocate_viseme_text_slice(remaining, 0.1, chars_per_sec=4.5)
    visemes = text_to_visemes(chunk_text, 0.1)

    full_sentence_visemes = text_to_visemes(sentence, 0.1)
    assert len(visemes) < len(full_sentence_visemes)
    assert len(visemes) == len(chunk_text)
