# Runtime Manager

The Runtime Manager exists so ScribeForge behaves like a normal desktop application, not a Python project.

## User experience target

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

## Current backend foundation

The core now provides:

- hardware profile data and NVIDIA detection;
- deterministic installation planning;
- separate private environments for MinerU and PaddleOCR;
- conservative Paddle CPU/GPU package selection based on driver capability;
- a preference for a bundled `uv` bootstrap executable;
- atomic backend install state with failed-step recording;
- command execution without shell interpolation.

## Next slices

1. Add version/health probes for each installed backend.
2. Add resumable repair/reinstall behavior and progress events.
3. Add shared model/cache environment configuration.
4. Add explicit model prefetch and checksum/version metadata where upstream supports it.
5. Package the correct `uv` binary with desktop releases.
6. Expose setup/repair through the desktop first-run wizard.

## Safety rules

- Never install into the user's global Python.
- Never mutate a developer Conda/venv environment.
- Never silently switch to a cloud OCR service.
- Never select an unverified GPU package merely because an NVIDIA card exists.
- A failed backend must leave the other backend usable and its failure state inspectable.
- Installation logs must be exportable without including book contents.
