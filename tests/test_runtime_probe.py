from scribeforge.runtime.probe import parse_nvidia_smi


def test_parse_nvidia_smi_extracts_first_gpu() -> None:
    profile = parse_nvidia_smi("NVIDIA GeForce RTX 5080, 560.94, 16384\n")

    assert profile == ("NVIDIA GeForce RTX 5080", "560.94", 16384)


def test_parse_nvidia_smi_returns_none_for_empty_output() -> None:
    assert parse_nvidia_smi("") is None
