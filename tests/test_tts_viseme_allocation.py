import pytest

from services.tts.viseme_generator import allocate_viseme_text_slice, text_to_visemes, VisemeRateEstimator


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


def test_viseme_rate_estimator_returns_default_for_unseen_key():
    estimator = VisemeRateEstimator(default_rate=3.0)
    assert estimator.get("GPTSoVITSClient") == 3.0


def test_viseme_rate_estimator_moves_toward_observed_rate():
    # Sentence of 10 chars that really took 5 real seconds to speak -> a
    # true rate of 2.0 chars/sec, much slower than the 4.5 cold-start guess
    # this is regression-testing against (the bug the estimator exists to
    # self-correct for: guessing too fast runs out of text before the
    # audio finishes and freezes the mouth for the remainder of playback).
    estimator = VisemeRateEstimator(default_rate=4.5, alpha=0.3)
    before = estimator.get("GPTSoVITSClient")

    estimator.observe("GPTSoVITSClient", text_len=10, total_duration_sec=5.0)
    after = estimator.get("GPTSoVITSClient")

    assert after < before  # moved down, toward the slower observed rate
    assert after == pytest.approx(0.3 * 2.0 + 0.7 * 4.5)


def test_viseme_rate_estimator_converges_toward_repeated_observations():
    estimator = VisemeRateEstimator(default_rate=4.5, alpha=0.3)
    for _ in range(30):
        estimator.observe("GPTSoVITSClient", text_len=10, total_duration_sec=5.0)
    assert estimator.get("GPTSoVITSClient") == pytest.approx(2.0, abs=0.05)


def test_viseme_rate_estimator_ignores_invalid_observations():
    estimator = VisemeRateEstimator(default_rate=3.0)
    estimator.observe("GPTSoVITSClient", text_len=0, total_duration_sec=5.0)
    estimator.observe("GPTSoVITSClient", text_len=10, total_duration_sec=0.0)
    estimator.observe("GPTSoVITSClient", text_len=10, total_duration_sec=-1.0)
    assert estimator.get("GPTSoVITSClient") == 3.0  # unchanged, still the default


def test_viseme_rate_estimator_clamps_extreme_observations():
    estimator = VisemeRateEstimator(default_rate=3.0, alpha=1.0, min_rate=1.0, max_rate=15.0)
    # A single garbled/edge-case observation (e.g. a near-instant chunk)
    # shouldn't be able to push the estimate outside sane bounds.
    estimator.observe("GPTSoVITSClient", text_len=1000, total_duration_sec=0.001)
    assert estimator.get("GPTSoVITSClient") == 15.0


def test_viseme_rate_estimator_keys_are_independent():
    estimator = VisemeRateEstimator(default_rate=3.0, alpha=1.0)
    estimator.observe("GPTSoVITSClient", text_len=10, total_duration_sec=2.0)  # rate 5.0
    estimator.observe("CosyVoiceClient", text_len=10, total_duration_sec=10.0)  # rate 1.0
    assert estimator.get("GPTSoVITSClient") == pytest.approx(5.0)
    assert estimator.get("CosyVoiceClient") == pytest.approx(1.0)
