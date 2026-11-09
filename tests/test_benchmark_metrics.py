import pytest

from scribeforge.benchmark.metrics import (
    character_error_counts,
    character_error_rate,
    error_rate_from_counts,
    line_error_counts,
    line_error_metrics,
    mean_confidence_interval,
    punctuation_accuracy,
    reading_order_errors,
    wilson_score_interval,
    word_error_counts,
    word_error_rate,
    word_tokenizer_for_language,
    z_score_for_confidence,
)


def test_character_error_rate_handles_substitution_insertion_and_deletion() -> None:
    assert character_error_rate("乌鸦", "鸟鸦") == 0.5
    assert character_error_rate("乌鸦", "乌黑鸦") == 0.5
    assert character_error_rate("乌鸦", "乌") == 0.5


def test_character_error_rate_is_zero_for_two_empty_strings() -> None:
    assert character_error_rate("", "") == 0.0


def test_word_error_rate_uses_whitespace_tokens() -> None:
    assert word_error_rate("the black bird", "the bird") == 1 / 3


def test_error_counts_expose_the_exact_rate_numerator_and_denominator() -> None:
    assert character_error_counts("abc", "axc") == (1, 3)
    assert character_error_counts("", "insert") == (6, 0)
    assert error_rate_from_counts(6, 0) == 1.0
    assert error_rate_from_counts(0, 0) == 0.0
    assert word_error_counts("one two", "one three", language="en") == (1, 2)
    assert word_error_counts("乌鸦", "鸟鸦", language="zh") is None


@pytest.mark.parametrize(("edits", "reference_units"), [(-1, 1), (1, -1)])
def test_error_rate_from_counts_rejects_negative_counts(
    edits: int, reference_units: int
) -> None:
    with pytest.raises(ValueError, match="non-negative"):
        error_rate_from_counts(edits, reference_units)


@pytest.mark.parametrize("language", ["zh", "zh-Hant", "cmn", "yue", "lzh", "ja", "und"])
def test_word_error_rate_is_not_applicable_without_a_declared_word_segmenter(
    language: str,
) -> None:
    assert word_error_rate("乌鸦 喝水", "乌鸦 喝了水", language=language) is None
    assert word_tokenizer_for_language(language) == "not-applicable"


def test_whitespace_wer_is_explicitly_case_and_hyphen_preserving() -> None:
    assert word_tokenizer_for_language("en-US") == "whitespace-v1"
    assert word_error_rate("Black-bird  two", "black-bird two", language="en-US") == 0.5
    # WER above one is valid when insertions exceed the reference token count;
    # it is not, by itself, evidence of a bad tokenizer.
    assert word_error_rate("one", "one two three", language="en") == 2.0


def test_line_metrics_distinguish_changed_lines_from_strict_lcs_counts() -> None:
    metrics = line_error_metrics(["乌鸦"], ["鸟鸦"])

    assert (metrics.missing_lines, metrics.extra_lines) == (1, 1)
    assert metrics.changed_lines == 1
    assert (metrics.net_missing_lines, metrics.net_extra_lines) == (0, 0)


def test_line_metrics_do_not_pair_unrelated_missing_and_extra_lines() -> None:
    metrics = line_error_metrics(
        ["第一行", "第二行", "第三行"],
        ["第一行", "第三行", "多余"],
    )

    assert (metrics.missing_lines, metrics.extra_lines) == (1, 1)
    assert metrics.changed_lines == 0
    assert (metrics.net_missing_lines, metrics.net_extra_lines) == (1, 1)


def test_changed_line_matching_is_one_to_one() -> None:
    metrics = line_error_metrics(["abcd", "abce"], ["abcf", "abcg"])

    assert (metrics.missing_lines, metrics.extra_lines) == (2, 2)
    assert metrics.changed_lines == 2
    assert (metrics.net_missing_lines, metrics.net_extra_lines) == (0, 0)


def test_punctuation_accuracy_scores_only_reference_punctuation() -> None:
    assert punctuation_accuracy("你好，世界。", "你好,世界。") == 0.5
    assert punctuation_accuracy("plain text", "plain text") == 1.0


def test_line_error_counts_detect_missing_and_extra_lines() -> None:
    missing, extra = line_error_counts(
        ["第一行", "第二行", "第三行"],
        ["第一行", "第三行", "多余"],
    )
    assert missing == 1
    assert extra == 1


def test_reading_order_errors_count_only_matched_lines_out_of_order() -> None:
    assert reading_order_errors(
        ["first", "second", "third"], ["third", "first", "second"]
    ) == 2
    assert reading_order_errors(["first", "second"], ["first"]) == 0
    assert reading_order_errors(
        ["same", "same", "last"], ["same", "last", "same"]
    ) == 1


def test_wilson_score_interval_matches_known_values() -> None:
    # Independently computed Wilson score bounds (z = 1.959963984540054).
    assert wilson_score_interval(0, 100) == pytest.approx((0.0, 0.036993), abs=1e-6)
    assert wilson_score_interval(2, 102) == pytest.approx((0.005394, 0.068693), abs=1e-6)
    assert wilson_score_interval(50, 100) == pytest.approx((0.403832, 0.596168), abs=1e-6)
    assert wilson_score_interval(1, 2) == pytest.approx((0.094531, 0.905469), abs=1e-6)


def test_wilson_score_interval_brackets_the_rate_and_shrinks_with_more_units() -> None:
    for errors, units in [(0, 5), (1, 2), (2, 102), (50, 100)]:
        interval = wilson_score_interval(errors, units)
        assert interval is not None
        rate = errors / units
        assert interval[0] <= rate <= interval[1]
        assert interval[0] >= 0.0

    narrow = wilson_score_interval(2, 102)
    wide = wilson_score_interval(2, 10)
    assert narrow is not None and wide is not None
    assert (narrow[1] - narrow[0]) < (wide[1] - wide[0])


def test_wilson_score_interval_supports_declared_confidence_levels() -> None:
    assert wilson_score_interval(2, 102, confidence=0.90) == pytest.approx(
        (0.006510, 0.057532), abs=1e-6
    )
    assert wilson_score_interval(2, 102, confidence=0.99) == pytest.approx(
        (0.003838, 0.094058), abs=1e-6
    )
    with pytest.raises(ValueError, match="confidence"):
        wilson_score_interval(2, 102, confidence=0.975)
    with pytest.raises(ValueError, match="confidence"):
        z_score_for_confidence(0.5)


def test_wilson_score_interval_has_no_interval_without_reference_units() -> None:
    assert wilson_score_interval(0, 0) is None
    assert wilson_score_interval(6, 0) is None


def test_wilson_score_interval_stays_defined_for_rates_above_one() -> None:
    # WER can exceed 1.0 through insertions; the binomial variance term is clamped
    # and the interval is widened to still bracket the point estimate.
    interval = wilson_score_interval(3, 2)
    assert interval == pytest.approx((0.513570, 1.5), abs=1e-6)


@pytest.mark.parametrize(("edits", "units"), [(-1, 1), (1, -1)])
def test_wilson_score_interval_rejects_negative_counts(edits: int, units: int) -> None:
    with pytest.raises(ValueError, match="non-negative"):
        wilson_score_interval(edits, units)


def test_mean_confidence_interval_uses_sample_dispersion() -> None:
    assert mean_confidence_interval([0.5, 0.01]) == pytest.approx((0.0, 0.735191), abs=1e-6)
    assert mean_confidence_interval([0.5, 0.01], confidence=0.90) == pytest.approx(
        (0.0, 0.657989), abs=1e-6
    )
    assert mean_confidence_interval([0.2, 0.2, 0.2]) == pytest.approx((0.2, 0.2), abs=1e-9)


def test_mean_confidence_interval_needs_at_least_two_values() -> None:
    assert mean_confidence_interval([]) is None
    assert mean_confidence_interval([1.0]) is None


def test_mean_confidence_interval_rejects_negative_values() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        mean_confidence_interval([0.5, -0.1])
    with pytest.raises(ValueError, match="confidence"):
        mean_confidence_interval([0.5, 0.1], confidence=0.5)
