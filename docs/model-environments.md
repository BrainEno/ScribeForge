# Model Environments

ScribeForge keeps heavyweight OCR runtimes outside the core environment and treats them as **application-managed private runtimes**. A normal packaged user should never need to open a terminal to install Python, MinerU, PaddleOCR, or a virtual environment.

## Runtime layout

```text
<ScribeForgeData>/runtime/
  tools/        # bundled bootstrap tools such as uv
  envs/
    mineru/
    paddleocr/
  models/
  cache/
  runtime-state.json
```

The release package should bundle a trusted `uv` executable in `runtime/tools`. Development builds may fall back to a system `uv`, but production UX must not depend on that fallback.

## MinerU worker

MinerU 4.x supports Python >=3.10,<3.15. ScribeForge provisions Python 3.12 and a dedicated environment, then installs a tested `mineru>=4.0,<5` release. Performance extras are added only after platform-specific verification; the base install remains the compatibility fallback.

## PaddleOCR worker

PaddleOCR receives its own Python 3.12 environment. The runtime planner chooses PaddlePaddle CPU or an NVIDIA wheel from the detected driver capability, then installs `paddleocr>=3,<4`. Current first-pass planning uses PaddlePaddle 3.2.0 package indexes for CPU, CUDA 11.8, or CUDA 12.6.

ScribeForge targets local inference and does **not** use the hosted `paddleocr api` command.

## Detection and safe fallback

The runtime probe attempts to collect OS, architecture, NVIDIA GPU name, NVIDIA driver, VRAM, and RAM. If GPU capability cannot be established safely, installation falls back to a CPU runtime instead of guessing a GPU package.

## State, repair, and privacy

Installation state is persisted atomically per backend. A failed step is recorded so the future first-run wizard can offer targeted retry/repair instead of dumping a Python traceback.

Workers receive local image paths and return OCR JSON through stdout. Network/cloud OCR is never silently selected as a fallback. Model downloading will be explicit application-managed setup work and will use the shared model/cache directories.
