# Development

Run source verification from the repository root with Windows PowerShell 5.1:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\verify.ps1 -Mode fast
```

The source-only verification checks Python and PowerShell syntax and runs the
tests that do not require bundled models, GPU components, FFmpeg, or a
portable runtime. Runtime/integration checks belong to a later distribution
workstream and are intentionally outside this repository candidate.

User data, subtitle results, caches, generated databases, build trees, and
binary outputs must remain outside the source tree. Do not commit credentials,
cookies, API keys, local paths, or downloaded media.
