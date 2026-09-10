# Subtitle Vocabulary Tool

Subtitle Vocabulary Tool is a Windows desktop application for extracting
subtitles and building a local English vocabulary history. It supports local
subtitle files and workflows that can use download, hard-subtitle OCR, or
Whisper transcription.

This repository is the project's public **source-only** distribution. The
source is available under **GPL-3.0-or-later**; see [`LICENSE`](LICENSE).

## Current distribution status

- No Windows portable binary release is currently provided.
- FFmpeg CLI (`ffmpeg.exe` and `ffprobe.exe`) is not included in this source
  tree or in the current distribution.
- Runtime dependencies, models, and other third-party components are not
  bundled here. Install them separately according to their own terms before
  attempting to run the application.
- Third-party dependencies, models, and data retain their own licenses. The
  project GPL license does not relicense them; see
  [`THIRD_PARTY_LICENSES`](THIRD_PARTY_LICENSES) and the attribution files
  beside the included dictionary resource.

## Source layout

- `runtime/` — Python and PowerShell application source.
- `tests/` — source-level regression tests.
- `tools/` — source/resource build and verification helpers.
- `docs/` — architecture, project overview, and development notes.

## Development

Use Windows PowerShell 5.1 and a suitable Python installation. From the
repository root:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\verify.ps1 -Mode fast
```

The source verification command checks syntax and source-level tests. It does
not require a bundled Python runtime, models, GPU runtime, FFmpeg CLI, or
portable release artifacts.
