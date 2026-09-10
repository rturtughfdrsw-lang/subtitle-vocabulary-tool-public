# Architecture

The application has three boundaries:

1. A Windows PowerShell 5.1 WinForms launcher owns interaction, process
   monitoring, and JSON presentation.
2. Python entry points (`worker.py` and `vocab_cli.py`) own subtitle and
   vocabulary workflows.
3. Local resources and SQLite databases provide dictionary lookup, frequency
   data, and user history.

The GUI does not parse subtitle formats, tokenize text, query dictionaries, or
execute SQL. Vocabulary imports flow through canonical subtitle text, a
content hash, tokenization/filtering, unique-word enrichment, and one database
transaction. Task-directory and single-file imports share the same duplicate
key.

Media tools are resolved from user/developer-provided files or `PATH`; this
source-only repository does not supply FFmpeg or other runtime binaries.
Optional runtime dependencies and models are likewise external to this tree.

The included dictionary resource is read-only and separate from the user's
vocabulary database. Its source, schema, hash, and license are recorded in
`runtime/vocabulary_resources/MANIFEST.json` and the adjacent attribution and
license files.
