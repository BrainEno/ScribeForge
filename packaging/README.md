# Frozen desktop backend

The end-user desktop layout includes a side-by-side frozen CLI. It does not use or modify a system Python installation:

```text
ScribeForge/
  scribeforge_desktop(.exe)
  backend/
    scribeforge(.exe)
    _internal/
      worker-src/scribeforge/workers/*.py
      worker-src/scribeforge/runtime/locks/*-py312-universal.txt
      ... frozen Python and native PDF dependencies ...
```

`worker-src` is intentional: MinerU and PaddleOCR run in separately managed Python environments and cannot import modules from PyInstaller's private archive. The runtime adapter puts this ordinary source directory on the Worker `PYTHONPATH`.

Build the backend from the repository root with Python 3.12:

```bash
python -m pip install -e . pyinstaller==6.16.0
python -m PyInstaller --noconfirm --clean packaging/scribeforge_backend.spec
```

For a Windows portable bundle, copy `dist/backend` into the Flutter release directory as `backend/`. `packaging/windows/ScribeForge.iss` packages that release directory as a per-user installer. The Windows workflow performs these steps and runs frozen-backend smoke checks, including the presence and SHA-256 entries of all six runtime locks. OCR models are still downloaded explicitly by the in-app initializer and are not bundled.
