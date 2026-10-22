from pathlib import Path

from scribeforge.runtime.paths import default_runtime_root, runtime_root_for_store


def test_windows_runtime_uses_local_app_data(tmp_path: Path) -> None:
    root = default_runtime_root(
        home=tmp_path,
        environment={"LOCALAPPDATA": str(tmp_path / "Local")},
        os_name="windows",
    )

    assert root == tmp_path / "Local" / "ScribeForge" / "runtime"


def test_macos_runtime_uses_application_support(tmp_path: Path) -> None:
    root = default_runtime_root(home=tmp_path, environment={}, os_name="macos")

    assert root == tmp_path / "Library" / "Application Support" / "ScribeForge" / "runtime"


def test_linux_runtime_respects_absolute_xdg_data_home(tmp_path: Path) -> None:
    data_home = tmp_path / "xdg"
    root = default_runtime_root(
        home=tmp_path,
        environment={"XDG_DATA_HOME": str(data_home)},
        os_name="linux",
    )

    assert root == data_home / "ScribeForge" / "runtime"


def test_linux_runtime_falls_back_for_relative_xdg_data_home(tmp_path: Path) -> None:
    root = default_runtime_root(
        home=tmp_path,
        environment={"XDG_DATA_HOME": "relative"},
        os_name="linux",
    )

    assert root == tmp_path / ".local" / "share" / "ScribeForge" / "runtime"



def test_existing_project_runtime_state_is_preserved(tmp_path: Path) -> None:
    store_root = tmp_path / "book.scribeforge"
    legacy = store_root / "runtime"
    legacy.mkdir(parents=True)
    (legacy / "runtime-state.json").write_text('{"schema_version": 1}', encoding="utf-8")

    root = runtime_root_for_store(
        store_root,
        home=tmp_path,
        environment={},
        os_name="linux",
    )

    assert root == legacy


def test_project_without_legacy_runtime_uses_shared_user_directory(tmp_path: Path) -> None:
    store_root = tmp_path / "book.scribeforge"

    root = runtime_root_for_store(
        store_root,
        home=tmp_path,
        environment={},
        os_name="linux",
    )

    assert root == tmp_path / ".local" / "share" / "ScribeForge" / "runtime"
