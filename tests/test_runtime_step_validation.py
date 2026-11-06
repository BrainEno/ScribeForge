import pytest

from scribeforge.runtime.models import InstallStep


def test_install_step_rejects_zero_attempts() -> None:
    with pytest.raises(ValueError, match="attempts"):
        InstallStep("download", ("tool", "download"), attempts=0)


def test_install_step_rejects_non_finite_or_zero_timeout() -> None:
    with pytest.raises(ValueError, match="timeout"):
        InstallStep("download", ("tool", "download"), timeout_seconds=0)
    with pytest.raises(ValueError, match="timeout"):
        InstallStep("download", ("tool", "download"), timeout_seconds=float("nan"))
