from pathlib import Path

from PIL import Image


def test_windows_icon_has_native_multisize_frames_and_is_installed_after_scaffolding():
    root = Path(__file__).resolve().parents[1]
    asset = root / "clients/flutter_desktop/assets/branding/app_icon.ico"
    with Image.open(asset) as icon:
        assert icon.format == "ICO"
        assert {(16, 16), (32, 32), (48, 48), (256, 256)} <= icon.ico.sizes()
    for path in (
        root / "clients/flutter_desktop/tool/bootstrap_desktop.sh",
        root / "clients/flutter_desktop/tool/bootstrap_desktop.ps1",
        root / ".github/workflows/windows-desktop.yml",
    ):
        script = path.read_text(encoding="utf-8")
        assert script.index("flutter create") < script.index("assets/branding/app_icon.ico")
        assert "windows/runner/resources/app_icon.ico" in script
