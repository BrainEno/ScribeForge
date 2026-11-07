# Runtime Manager

The Runtime Manager exists so ScribeForge behaves like a normal desktop application, not a Python project. Users should not need to install Python, CUDA toolkits, MinerU, PaddleOCR, virtual environments, or model files manually. Runtime setup requires an internet connection for the initial bootstrap, packages, and model downloads.

## User experience

Run `scribeforge setup` before the first book is processed. The standalone
wizard does not require a project store; it detects hardware, shows each backend
state, and offers install-all plus per-backend repair actions. Downloads and model
initialization run outside the UI event loop, and phase/status messages are
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
2. resolve or install the hash-locked `uv` bootstrap tool;
3. create private MinerU and PaddleOCR environments;
4. install compatible packages;
5. prefetch required OCR models;
6. verify model integrity and runtime startup;
7. mark the backend `ready` only after health checks pass.

Progress is emitted as structured runtime events to the setup UI. Failures persist
the phase and step that failed so the UI can offer **Repair**. Cancellation is
also persisted as `cancelled`, with the interrupted step. Command output uses
bounded, disk-backed temporary files; only a bounded diagnostic tail is retained
in an exception. A console window is not required.

### Deadlines, output limits, and cancellation

Install commands default to a **30-minute overall deadline per step**. Model
preparation defaults to **2 hours**, and startup/health checks default to
**5 minutes**. Retries share the same overall budget; three attempts do not get
three fresh deadlines. Automation can override every setup step with:

```sh
scribeforge setup --json --step-timeout 1800
```

The timeout must be finite and greater than zero. Hardware probes have their own
configurable deadline (10 seconds by default). Single-page OCR has a separate
600-second default, configurable with `process --worker-timeout <seconds>` (and
the corresponding `repair` / `benchmark` options).

Setup subprocess stdout and stderr each have a 4 MiB disk-backed limit. OCR worker
JSON stdout defaults to 64 MiB and worker diagnostics to 4 MiB. Engine commands
inside a worker forward diagnostics to the parent's bounded stderr instead of
accumulating a second unbounded `capture_output` buffer.

For machine-driven setup, `--cancelable` listens for this stdin control line:

```json
{"schema_version":1,"type":"cancel"}
```

Cancellation and deadlines terminate the active process tree (`taskkill /T /F`
plus a direct-kill fallback on Windows; a new process group and SIGTERM/SIGKILL
on POSIX). Setup then emits its final `type: "result"` JSON record and exits with
status **130**. Other non-zero setup statuses retain their existing meanings.
Cancellation takes effect at control points; an in-process network read is not
represented as an instantly killable child process.

A failed or cancelled backend can be repaired without deleting project data.
Previously persisted OCR pages, evidence, and human decisions are not rewritten
by runtime setup or cancellation. No claim is made that a partially installed
virtual environment is rolled back; see the supply-chain limitations below.

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
      verified/
        PP-OCRv4_mobile_det/
        PP-OCRv4_mobile_rec/
  cache/
    uv/
    huggingface/
    modelscope/
    paddle/
  runtime-state.json
```

Environment variables route MinerU and PaddleX model storage, Hugging Face /
ModelScope downloads, Paddle framework data, `uv` cache, and managed Python
installations into these directories. Managed Paddle workers additionally receive
`SCRIBEFORGE_PADDLE_MODELS_ROOT`; they re-verify the locked weight before invoking
the engine and pass explicit detection/recognition model directories.

## Bootstrap and package identities

When `uv` is not already bundled or on a developer's PATH, setup downloads only
the platform archive for **uv 0.13.7** from the official Astral GitHub release.
The expected digest is committed in `runtime/tools.py`; setup does **not** query
or follow a mutable `latest` release. It verifies SHA-256 before extracting one
safe executable path, writes a temporary file, and atomically replaces the private
`runtime/tools/uv[.exe]`. It does not run a remote installer script or modify a
global Python environment.

| Platform archive | SHA-256 |
| --- | --- |
| `uv-x86_64-pc-windows-msvc.zip` | `0cdc5b38fe5a8168ed6a25814f5ba43c21d313dd3187314fccb37b7cec46e021` |
| `uv-aarch64-pc-windows-msvc.zip` | `505d9e65e7692bac3ddf98c66754aa26c5050c409c184af30a881a0a72cc85e3` |
| `uv-x86_64-apple-darwin.tar.gz` | `20c47e4c1f630b8abaff8c291fa9b9aa300fef92c4fbff95adf198b55287cda7` |
| `uv-aarch64-apple-darwin.tar.gz` | `81b811b57e4624df7ed3aac148e0359100d9962f3d9dce00aec533dc2e086731` |
| `uv-x86_64-unknown-linux-gnu.tar.gz` | `c5221ff41b7892bfc223543883600125f6a47fe845093e0f5bdff4f359196d83` |
| `uv-aarch64-unknown-linux-gnu.tar.gz` | `71279f8d3c67241e807b9d8db872d1bf541579fb9f08b7d1eb1682196d1e57dc` |

The planner fixes managed Python to 3.12, PaddlePaddle to 3.2.0, MinerU to 4.1.7,
PaddleOCR / PaddleX to 3.3.0, and optional Surya OCR to 0.14.0. MinerU model
preparation explicitly selects Hugging Face, ONNX small models, and the llama.cpp
VLM engine; workers use the matching local source/backend settings instead of an
implicit auto-selected combination.

**These direct version pins are not a complete dependency lock.** Transitive
wheels and platform-specific GPU artifacts are not yet installed with a reviewed,
hash-complete uv lockfile.

## Paddle model preparation

The default PP-OCRv4 pipeline keeps `return_word_box` enabled because newer
Transformers recognizers do not expose PaddleX's native word-box output. Model
preparation is explicit during setup rather than surprising the user during the
first OCR job.

The managed assets are pinned as follows. Revision and weight metadata came from
the official Hugging Face repository API; the size and SHA-256 below refer to the
LFS `inference.pdiparams` payload, not a Git blob OID.

| Model repository | Immutable revision | Weight bytes | `inference.pdiparams` SHA-256 |
| --- | --- | ---: | --- |
| `PaddlePaddle/PP-OCRv4_mobile_det` | `3cc09f3a5b424e8e010abc7a4271aea12999c2f7` | 4,692,937 | `54a85087b4d31fa3ea4e4aba100169a1ec3e3274cd3352b9068b3cfccbca7829` |
| `PaddlePaddle/PP-OCRv4_mobile_rec` | `a2deb05cb05813be2da3365672881aa496d1452b` | 10,766,823 | `948b8d2ca6ad4bd7b64c0ee3fb838a06db87d4cf57137173ce409c995df21012` |

For each asset, setup:

1. reuses the private directory only if all required paths exist and the weight
   size and SHA-256 match;
2. otherwise downloads that exact Hugging Face revision into a unique staging
   directory;
3. rejects missing paths, size mismatches, and digest mismatches;
4. moves any previous directory to a unique backup and atomically renames the
   verified staging directory into place;
5. restores the previous directory if publication fails, and removes staging on
   both success and failure.

Network errors, injected disk-full / permission-denied failures, damaged cached
weights, and publication rollback have fixture tests. These tests use tiny fake
models. They do not prove that the real PP-OCRv4 snapshots were downloaded or that
real OCR inference succeeded.

### Updating a Paddle model lock

1. Select and review an immutable Hugging Face commit for both model behavior and
   license compatibility.
2. Obtain the LFS SHA-256 and byte size from the official repository API at that
   same revision. Do not copy a non-LFS tree `oid` into the SHA-256 field.
3. Update `PADDLE_MODELS`, its source-focused tests, and this table together.
4. Run the fake-model corruption/rollback tests.
5. Separately perform an opt-in real download and real PaddleOCR health/inference
   check in a disposable private runtime. Record that result independently from
   unit-test evidence.

### Supply-chain limitations (issue #71 remains open)

The implementation above is intentionally not described as a complete solution
to #71. The remaining gaps are:

- Paddle's non-weight files (`config.json`, `inference.json`, `inference.yml`)
  currently receive required-path checks, not independent SHA-256 verification.
  A fixed revision alone is not a downloaded-content hash.
- MinerU, PaddleOCR-VL, and Surya still rely on upstream model preparation and
  path/startup checks; their model trees are not covered by the Paddle weight
  verifier.
- A complete, per-platform transitive Python dependency lock with hashes has not
  been generated and enforced.
- Model-directory publication is transactional, and uv executable replacement is
  atomic, but virtual-environment package installation is not staged as a
  transaction. A failed package install can leave a partial environment; Repair
  is required.

## Install and repair

`scribeforge setup` opens the wizard at the per-user runtime path. Pass
`--runtime-root <path>` to override it; `process` uses the same per-user default
unless a prior project already has a persisted `<store>/runtime/runtime-state.json`,
in which case that legacy runtime is retained. `benchmark` uses the per-user
default. Each command accepts `--runtime-root` to override it. **Install / repair
all** runs both backend lifecycles independently: a failure in one backend is
persisted and reported, but does not prevent the other backend from completing.
The per-backend buttons replay only the selected backend's installation, model
preparation, and health checks. Repair never deletes source books or project data.

## Safety rules

- Never install into the user's global Python.
- Never mutate a developer Conda/venv environment.
- Never silently switch to a cloud OCR service.
- Never select an unverified GPU package merely because an NVIDIA card exists.
- A failed backend must leave the other backend usable and its failure state inspectable.
- Installation logs must be exportable without including book contents.
- Never present fixture downloads, mocked process tests, or CI smoke tests as real
  model/OCR accuracy or real-user Windows validation.
