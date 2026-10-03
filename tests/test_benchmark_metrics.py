from scribeforge.benchmark.metrics import (
    character_error_rate,
    line_error_counts,
    punctuation_accuracy,
    word_error_rate,
)


def test_character_error_rate_handles_substitution_insertion_and_deletion() -> None:
    assert character_error_rate("乌鸦", "鸟鸦") == 0.5
    assert character_error_rate("乌鸦", "乌黑鸦") == 0.5
    assert character_error_rate("乌鸦", "乌") == 0.5


def test_character_error_rate_is_zero_for_two_empty_strings() -> None:
    assert character_error_rate("", "") == 0.0


def test_word_error_rate_uses_whitespace_tokens() -> None:
    assert word_error_rate("the black bird", "the bird") == 1 / 3


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
