import pytest

from scribeforge.runtime.models import InstallStep


def test_install_step_rejects_zero_attempts() -> None:
    with pytest.raises(ValueError, match="attempts"):
        InstallStep("download", ("tool", "download"), attempts=0)
