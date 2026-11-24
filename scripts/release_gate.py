"""Release gate: version consistency, CI provenance, notes, checksums (issues #220/#245).

Subcommands (all take ``--root`` pointing at the repository checkout):

``check``
    Verify that a release version ``X.Y.Z`` is consistent across
    ``pyproject.toml``, ``src/scribeforge/__init__.py``,
    ``packaging/windows/ScribeForge.iss``, ``clients/flutter_desktop/pubspec.yaml``
    and that the ``CHANGELOG.md`` contains a non-empty ``## [X.Y.Z]`` section.
    Exit code 1 with an actionable message per mismatch.

``checks``
    Validate a GitHub check-runs payload (``gh api .../check-runs``) for the
    tagged commit: ``core``, ``flutter-desktop`` and ``flutter-desktop-windows``
    must each exist with conclusion ``success``; ``package`` and every
    ``Python 3.*`` compatibility check must be ``success`` when present; any
    failing check from another workflow blocks the release.

``sums``
    Verify ``dist/SHA256SUMS.txt`` (produced by the packaging job) against the
    files on disk, then emit a release-facing sums file whose entries use bare
    file names so users can run ``sha256sum -c`` after downloading assets.

``notes``
    Render GitHub Release notes: provenance, unsigned-disclosure block, asset
    checksum table and the ``CHANGELOG.md`` section for the version.

Every failure message states which file to edit or which CI check to re-run.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import tomllib

REQUIRED_CHECKS = ("core", "flutter-desktop", "flutter-desktop-windows")
CONDITIONAL_CHECK_PREFIXES = ("Python 3.",)
CONDITIONAL_CHECKS = ("package",)
FAILING_CONCLUSIONS = frozenset(
    {"failure", "timed_out", "cancelled", "action_required", "stale", "error"}
)
PENDING_STATUSES = frozenset({"queued", "in_progress", "pending", "waiting", "requested"})

_VERSION_RE = re.compile(r"^(\d+\.\d+\.\d+)$")
_PUBSPEC_RE = re.compile(r"^version:\s*(\d+\.\d+\.\d+)(?:\+\S+)?\s*$", re.MULTILINE)
_ISS_RE = re.compile(r'^#define\s+MyAppVersion\s+"(\d+\.\d+\.\d+)"', re.MULTILINE)
_INIT_RE = re.compile(r'^__version__\s*=\s*"(\d+\.\d+\.\d+)"', re.MULTILINE)
_CHANGELOG_HEADING_RE = re.compile(r"^## \[(\d+\.\d+\.\d+)\]", re.MULTILINE)
_CHANGELOG_SECTION_RE = re.compile(
    r"^## \[(\d+\.\d+\.\d+)\][^\n]*\n(.*?)(?=^## \[)", re.MULTILINE | re.DOTALL
)
_SUMS_LINE_RE = re.compile(r"^([0-9a-fA-F]{64}) [ *](.+)$")


class GateError(Exception):
    """A gate failure with an actionable message for the operator."""


def normalize_version(raw: str) -> str:
    """Strip a leading ``v`` and validate ``X.Y.Z``."""
    tag = raw.strip()
    tag = tag.removeprefix("v")
    if not _VERSION_RE.match(tag):
        raise GateError(f"版本格式无效：{raw!r}。需要 vX.Y.Z（例如 v0.1.1）。")
    return tag


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise GateError(f"缺少文件：{path}。请确认检出完整仓库。") from None


def read_pyproject_version(root: Path) -> str:
    text = _read(root / "pyproject.toml")
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise GateError(f"pyproject.toml 解析失败：{error}") from error
    version = data.get("project", {}).get("version")
    if not isinstance(version, str) or not _VERSION_RE.match(version):
        raise GateError(
            'pyproject.toml 缺少合法的 project.version。请在 [project] 段设置 version = "X.Y.Z"。'
        )
    return version


def read_package_version(root: Path) -> str:
    text = _read(root / "src" / "scribeforge" / "__init__.py")
    match = _INIT_RE.search(text)
    if not match:
        raise GateError('src/scribeforge/__init__.py 中未找到 __version__ = "X.Y.Z"。')
    return match.group(1)


def read_iss_version(root: Path) -> str:
    text = _read(root / "packaging" / "windows" / "ScribeForge.iss")
    match = _ISS_RE.search(text)
    if not match:
        raise GateError('packaging/windows/ScribeForge.iss 中未找到 #define MyAppVersion "X.Y.Z"。')
    return match.group(1)


def read_pubspec_version(root: Path) -> str:
    text = _read(root / "clients" / "flutter_desktop" / "pubspec.yaml")
    match = _PUBSPEC_RE.search(text)
    if not match:
        raise GateError("clients/flutter_desktop/pubspec.yaml 中未找到 version: X.Y.Z[+build]。")
    return match.group(1)


def changelog_section(root: Path, version: str) -> str:
    """Return the body of the ``## [version]`` CHANGELOG section."""
    text = _read(root / "CHANGELOG.md")
    for match in _CHANGELOG_SECTION_RE.finditer(text):
        if match.group(1) == version:
            body = match.group(2).strip()
            if not body:
                raise GateError(
                    f"CHANGELOG.md 的 [{version}] 小节为空。请在发布前写明本版本的变更内容。"
                )
            return body
    raise GateError(
        f"CHANGELOG.md 缺少 ## [{version}] 小节。请把 Unreleased 内容归档为该小节并补上发布日期。"
    )


def check_versions(root: Path, version: str) -> list[str]:
    """Return a list of human-readable mismatch messages (empty = consistent)."""
    errors: list[str] = []
    sources: tuple[tuple[str, str], ...] = (
        ("pyproject.toml", read_pyproject_version(root)),
        ("src/scribeforge/__init__.py", read_package_version(root)),
        ("packaging/windows/ScribeForge.iss", read_iss_version(root)),
        ("clients/flutter_desktop/pubspec.yaml", read_pubspec_version(root)),
    )
    for name, found in sources:
        if found != version:
            errors.append(
                f"{name} 的版本是 {found}，与发布版本 {version} 不一致。"
                f"请把该文件更新为 {version} 后重新打 tag。"
            )
    try:
        changelog_section(root, version)
    except GateError as error:
        errors.append(str(error))
    readme = _read(root / "README.md")
    if f"v{version}" not in readme:
        errors.append(f"README.md 未提到 v{version}。请更新 README 中的 current: v{version} 标注。")
    return errors


def _check_name_is_conditional(name: str) -> bool:
    if name in CONDITIONAL_CHECKS:
        return True
    return any(name.startswith(prefix) for prefix in CONDITIONAL_CHECK_PREFIXES)


def evaluate_check_runs(payload: dict) -> list[str]:
    """Validate a GitHub check-runs API payload for the tagged commit."""
    runs = payload.get("check_runs")
    if not isinstance(runs, list):
        raise GateError(
            "check-runs 载荷缺少 check_runs 数组。"
            "请用 gh api /repos/OWNER/REPO/commits/SHA/check-runs 重新导出。"
        )
    by_name: dict[str, list[dict]] = {}
    for run in runs:
        if not isinstance(run, dict) or not isinstance(run.get("name"), str):
            continue
        by_name.setdefault(run["name"], []).append(run)

    errors: list[str] = []
    for name in REQUIRED_CHECKS:
        candidates = by_name.get(name, [])
        if not candidates:
            errors.append(
                f"tag 指向的 commit 上没有 {name!r} 检查。"
                "该 commit 必须是 push 到 main 后 CI 完整跑过的提交；"
                "请等 main 的 core / flutter-desktop / flutter-desktop-windows 全部"
                "出现并成功后再打 tag。"
            )
            continue
        latest = max(candidates, key=lambda run: run.get("id") or 0)
        status = latest.get("status")
        conclusion = latest.get("conclusion")
        if status in PENDING_STATUSES:
            errors.append(
                f"必需检查 {name!r} 仍在 {status}。"
                "请等待 main 的 CI 完成（gh run watch）后再打 tag。"
            )
        elif conclusion != "success":
            url = latest.get("details_url") or "Actions 页"
            errors.append(
                f"必需检查 {name!r} 结论为 {conclusion!r}（{url}）。"
                "请先修复该检查并在 main 上变绿，再重新打 tag。"
            )

    for name, candidates in sorted(by_name.items()):
        if name in REQUIRED_CHECKS or not _check_name_is_conditional(name):
            continue
        latest = max(candidates, key=lambda run: run.get("id") or 0)
        status = latest.get("status")
        conclusion = latest.get("conclusion")
        if status in PENDING_STATUSES:
            errors.append(f"检查 {name!r} 仍在 {status}。请等待其完成后再发布。")
        elif conclusion in FAILING_CONCLUSIONS:
            url = latest.get("details_url") or "Actions 页"
            errors.append(
                f"检查 {name!r} 失败（{conclusion}，{url}）。"
                "main 不是全绿状态，不能从该 commit 发布。"
            )
    return errors


def load_and_evaluate_check_runs(path: Path) -> list[str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GateError(f"无法读取 check-runs 文件 {path}：{error}") from error
    return evaluate_check_runs(payload)


def parse_sums(text: str) -> list[tuple[str, str]]:
    """Parse a sha256sum-format file into ``(digest, name)`` pairs."""
    entries: list[tuple[str, str]] = []
    for line in text.splitlines():
        line = line.lstrip("\ufeff").strip()
        if not line:
            continue
        match = _SUMS_LINE_RE.match(line)
        if not match:
            raise GateError(f"SHA256SUMS 行格式无效：{line!r}。期望 `<64位十六进制>  <文件名>`。")
        entries.append((match.group(1).lower(), match.group(2)))
    if not entries:
        raise GateError("SHA256SUMS 为空；打包步骤应生成校验和文件。")
    return entries


def verify_and_normalize_sums(dist: Path, out: Path) -> list[str]:
    """Verify artifact sums and write a bare-filename release sums file."""
    sums_path = dist / "SHA256SUMS.txt"
    entries = parse_sums(_read(sums_path))
    import hashlib

    release_lines: list[str] = []
    errors: list[str] = []
    for digest, name in entries:
        # The Windows packaging job records repository-relative paths that may
        # use backslashes; the download step recreates that layout on Linux.
        normalized = name.replace("\\", "/")
        candidate = Path(normalized)
        file_path = candidate if candidate.is_absolute() else Path.cwd() / candidate
        if not file_path.is_file():
            # Also try relative to dist/ for bare-name sums files, then search
            # the downloaded tree by file name (layout may be flattened).
            alt = dist / candidate.name
            if alt.is_file():
                file_path = alt
            else:
                matches = sorted(dist.rglob(candidate.name)) if candidate.name else []
                if len(matches) == 1:
                    file_path = matches[0]
                else:
                    errors.append(
                        f"校验和指向的文件不存在：{name}。"
                        "请确认 download-artifact 步骤把 portable/、installer/ "
                        "与 SHA256SUMS.txt 下载到了同一布局。"
                    )
                    continue
        actual = hashlib.sha256(file_path.read_bytes()).hexdigest()
        if actual != digest:
            errors.append(
                f"{file_path.name} 的 SHA-256 与打包记录不符："
                f"记录 {digest}，实际 {actual}。产物可能损坏，请重新运行打包。"
            )
            continue
        release_lines.append(f"{actual}  {file_path.name}")
    if errors:
        return errors
    if not release_lines:
        return ["SHA256SUMS 中没有可校验的条目。"]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(release_lines) + "\n", encoding="utf-8")
    return []


def render_release_notes(
    *,
    version: str,
    tag: str,
    sha: str,
    changelog_body: str,
    sums_entries: list[tuple[str, str]] | None,
    run_url: str | None = None,
) -> str:
    """Render bilingual GitHub Release notes for the version."""
    provenance = (
        f"- 标签：`{tag}` → commit `{sha}`（位于 main，"
        "core / flutter-desktop / flutter-desktop-windows 检查通过）"
    )
    if run_url:
        provenance += f"\n- 构建：[Actions 运行记录]({run_url})"
    asset_rows = ""
    if sums_entries:
        rows = "\n".join(f"| `{Path(name).name}` | `{digest}` |" for digest, name in sums_entries)
        asset_rows = (
            "\n## 资产与 SHA-256 / Assets and checksums\n\n"
            "| 文件 | SHA-256 |\n| --- | --- |\n"
            f"{rows}\n\n"
            "下载后可校验：`sha256sum -c SHA256SUMS.txt`（或 Windows 上 "
            "`Get-FileHash` 对比）。\n"
        )
    else:
        asset_rows = (
            "\n## 资产与 SHA-256 / Assets and checksums\n\n"
            "本 Release 附件中的 `SHA256SUMS.txt` 提供全部资产的 SHA-256。\n"
        )
    return f"""# ScribeForge {tag}

{provenance}

## 安全与签名 / Security and signing

- **本 Release 的安装包与便携 ZIP 未签名**（仓库暂无代码签名证书）。
  Windows SmartScreen 可能提示“未知发布者”；这是未签名构建的正常表现。
- 下载后请对照下方 SHA-256（或附件 `SHA256SUMS.txt`）校验文件完整性，
  再运行安装器或解压便携包。
- **These assets are unsigned** (no code-signing certificate yet). SmartScreen
  warnings are expected for unsigned builds — verify the SHA-256 checksums
  before running the installer.
{asset_rows}
## 下载建议 / Download guide

- 普通用户：下载 `ScribeForge-Setup-x64.exe`（每用户安装，无需管理员）。
- 需要免安装运行：下载 `ScribeForge-windows-x64.zip` 解压后运行
  `scribeforge_desktop.exe`。
- CI 临时测试包（GitHub Actions artifact）不是正式发布附件，可能过期且未含校验说明。

## 变更 / Changes

{changelog_body}
"""


def collect_sums_for_notes(sums_path: Path) -> list[tuple[str, str]] | None:
    if not sums_path.is_file():
        return None
    return parse_sums(_read(sums_path))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="release_gate.py", description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path.cwd())
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="verify version consistency")
    check.add_argument("--version", required=True)

    checks = sub.add_parser("checks", help="verify GitHub check-runs payload")
    checks.add_argument("--file", type=Path, required=True)

    sums = sub.add_parser("sums", help="verify and normalize SHA256SUMS")
    sums.add_argument("--dist", type=Path, required=True)
    sums.add_argument("--out", type=Path, required=True)

    notes = sub.add_parser("notes", help="render release notes")
    notes.add_argument("--version", required=True)
    notes.add_argument("--tag", required=True)
    notes.add_argument("--sha", required=True)
    notes.add_argument("--sums", type=Path, default=None)
    notes.add_argument("--run-url", default=None)
    notes.add_argument("--out", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "check":
            version = normalize_version(args.version)
            errors = check_versions(args.root, version)
            for error in errors:
                print(f"::error title=Release gate::{error}", file=sys.stderr)
            if errors:
                return 1
            print(f"版本 {version} 在 pyproject/pubspec/iss/README/CHANGELOG 一致。")
            return 0
        if args.command == "checks":
            errors = load_and_evaluate_check_runs(args.file)
            for error in errors:
                print(f"::error title=Release gate::{error}", file=sys.stderr)
            if errors:
                return 1
            print("tag 所指 commit 的必需 CI 检查全部成功。")
            return 0
        if args.command == "sums":
            errors = verify_and_normalize_sums(args.dist, args.out)
            for error in errors:
                print(f"::error title=Release gate::{error}", file=sys.stderr)
            if errors:
                return 1
            print(f"校验和通过，已写出 {args.out}。")
            return 0
        if args.command == "notes":
            version = normalize_version(args.version)
            body = changelog_section(args.root, version)
            entries = collect_sums_for_notes(args.sums) if args.sums else None
            text = render_release_notes(
                version=version,
                tag=args.tag,
                sha=args.sha,
                changelog_body=body,
                sums_entries=entries,
                run_url=args.run_url,
            )
            args.out.write_text(text, encoding="utf-8")
            print(f"发布说明已写入 {args.out}。")
            return 0
    except GateError as error:
        print(f"::error title=Release gate::{error}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
