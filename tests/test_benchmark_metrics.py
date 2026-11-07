import pytest

from scribeforge.benchmark.metrics import (
    character_error_rate,
    line_error_counts,
    line_error_metrics,
    punctuation_accuracy,
    reading_order_errors,
    word_error_rate,
    word_tokenizer_for_language,
)


def test_character_error_rate_handles_substitution_insertion_and_deletion() -> None:
    assert character_error_rate("乌鸦", "鸟鸦") == 0.5
    assert character_error_rate("乌鸦", "乌黑鸦") == 0.5
    assert character_error_rate("乌鸦", "乌") == 0.5


def test_character_error_rate_is_zero_for_two_empty_strings() -> None:
    assert character_error_rate("", "") == 0.0


def test_word_error_rate_uses_whitespace_tokens() -> None:
    assert word_error_rate("the black bird", "the bird") == 1 / 3


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
