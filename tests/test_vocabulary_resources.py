import hashlib
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = PROJECT_ROOT / "runtime"
RESOURCE_ROOT = RUNTIME_ROOT / "vocabulary_resources"
sys.path.insert(0, str(RUNTIME_ROOT))

from dictionary_lookup import DictionaryLookup


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def test_included_dictionary_matches_manifest_and_quality_gate():
    dictionary_path = RESOURCE_ROOT / "dictionary.sqlite3"
    manifest = json.loads(
        (RESOURCE_ROOT / "MANIFEST.json").read_text(encoding="utf-8")
    )

    assert manifest["schema_version"] == 1
    assert manifest["entry_count"] >= 50_000
    assert manifest["entry_count"] == (
        manifest["direct_entry_count"] + manifest["form_of_entry_count"]
    )
    assert dictionary_path.stat().st_size == manifest["dictionary_bytes"]
    assert sha256(dictionary_path) == manifest["dictionary_sha256"]

    lookup = DictionaryLookup(dictionary_path)
    assert lookup.lookup("actually")
    assert lookup.lookup("obnoxious")
    assert lookup.lookup("well-known")
    assert lookup.lookup("play")
    assert lookup.lookup("played") == lookup.lookup("play")
    assert lookup.lookup("gpt5") is None


if __name__ == "__main__":
    test_included_dictionary_matches_manifest_and_quality_gate()
    print("PASS: included vocabulary resource tests")
