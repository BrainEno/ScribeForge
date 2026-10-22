# Runtime Manager

The Runtime Manager exists so ScribeForge behaves like a normal desktop application, not a Python project. Users should not need to install Python, CUDA toolkits, MinerU, PaddleOCR, virtual environments, or model files manually. Runtime setup requires an internet connection for the initial bootstrap, packages, and model downloads.

## User experience

Run `scribeforge setup` before the first book is processed. The standalone
wizard does not require a project store; it detects hardware, shows each backend
state, and offers install-all plus per-backend repair actions. Downloads and model
initialization run outside the Tk event loop, and phase/status messages are
streamed into the activity panel.

On first launch the user should see setup progress such as:

```text
Detecting hardware
Preparing local OCR runtime
Installing document parser
Installing secondary OCR engine
Preparing language models
Verifying local inference
Ready
```

Terms such as Python, virtualenv, CUDA wheel, pip, MinerU and PaddleOCR belong in an advanced diagnostics panel, not in the primary setup flow.

## Managed lifecycle

The current lifecycle is:

1. detect hardware;
2. resolve the bundled `uv` bootstrap tool;
3. create private MinerU and PaddleOCR environments;
4. install compatible packages;
5. prefetch required OCR models;
6. verify model integrity and runtime startup;
7. mark the backend `ready` only after health checks pass.

Progress is emitted as structured runtime events to the setup UI. Failures persist
the phase and step that failed so the UI can offer **Repair**. Command output is
captured and surfaced as a bounded error detail instead of depending on a console
window being available.

When `uv` is not already bundled or on the developer's PATH, setup downloads the
platform-specific archive from the official Astral `uv` GitHub release, verifies
the release API's SHA-256 digest, and writes only the executable into
`runtime/tools`. It does not run a remote installer script or modify global
Python environments.

## Private storage

All managed data stays under the ScribeForge runtime root:

```text
<per-user application data>/ScribeForge/runtime/
  tools/
    python/
    uv[.exe]
  envs/
    mineru/
    paddleocr/
  models/
    mineru/
    paddleocr/
  cache/
    uv/
    huggingface/
    modelscope/
    paddle/
  runtime-state.json
```

Environment variables route MinerU and PaddleX model storage, Hugging Face / ModelScope downloads, Paddle framework data, `uv` cache, and managed Python installations into these directories.

## Model preparation

MinerU uses its official 4.x model commands to download and verify the `standard` tier. PaddleOCR preloads the PP-OCRv6 general OCR pipeline inside its private environment; PaddleX model caching is redirected to `runtime/models/paddleocr`.

Model preparation is explicit during setup rather than being allowed to surprise the user during the first OCR job.

## Install and repair

`scribeforge setup` opens the wizard at the per-user runtime path. Pass
`--runtime-root <path>` to override it; `process` and `benchmark` use the same
per-user default unless overridden. **Install / repair all** runs both backend
lifecycles independently: a failure in one backend is persisted and reported, but
does not prevent the other backend from completing. The per-backend buttons replay
only the selected backend's installation, model preparation, and health checks.
Repair never deletes source books or project data.

## Safety rules

- Never install into the user's global Python.
- Never mutate a developer Conda/venv environment.
- Never silently switch to a cloud OCR service.
- Never select an unverified GPU package merely because an NVIDIA card exists.
- A failed backend must leave the other backend usable and its failure state inspectable.
- Installation logs must be exportable without including book contents.
