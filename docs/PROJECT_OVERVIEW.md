# Project overview

Subtitle Vocabulary Tool is a Windows application with two user-facing areas:
subtitle extraction and vocabulary statistics. It accepts existing subtitle
files and can orchestrate download, hard-subtitle OCR, and Whisper workflows
when the user supplies compatible runtime dependencies.

Vocabulary counts are cumulative within a selected collection. Identical
canonical subtitle content is counted once per collection. Definitions and
English frequency values are lexical metadata; per-import counts support
review and reversible accounting.

This repository is a source-only publication. It contains application source,
tests, source/resource helpers, documentation, and the included
Kaikki/English Wiktionary-derived dictionary resource with its attribution.
It does not contain a portable Python runtime, models, GPU runtime, FFmpeg CLI,
or Windows binary release. ECDICT is only an optional local integration point
and is not included.

The project-authored source is licensed under GPL-3.0-or-later. Third-party
software, models, and data remain under their own licenses.
