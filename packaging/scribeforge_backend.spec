from pathlib import Path

from PyInstaller.utils.hooks import collect_all


ROOT = Path(SPECPATH).parent
SOURCE = ROOT / "src"

pdfium_datas, pdfium_binaries, pdfium_hidden_imports = collect_all("pypdfium2")
opencc_datas, opencc_binaries, opencc_hidden_imports = collect_all("opencc")
worker_datas = [
    (str(SOURCE / "scribeforge" / "__init__.py"), "worker-src/scribeforge"),
    (str(SOURCE / "scribeforge" / "workers"), "worker-src/scribeforge/workers"),
    (
        str(SOURCE / "scribeforge" / "runtime" / "locks"),
        "worker-src/scribeforge/runtime/locks",
    ),
]

analysis = Analysis(
    [str(ROOT / "packaging" / "desktop_backend.py")],
    pathex=[str(SOURCE)],
    binaries=pdfium_binaries + opencc_binaries,
    datas=worker_datas + pdfium_datas + opencc_datas,
    hiddenimports=pdfium_hidden_imports + opencc_hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
archive = PYZ(analysis.pure)

executable = EXE(
    archive,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="scribeforge",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)

collection = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="backend",
)
