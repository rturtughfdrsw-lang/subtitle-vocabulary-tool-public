from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CacheStats:
    count: int
    total_bytes: int
    files: tuple[Path, ...] = ()

    def as_json(self) -> dict[str, int]:
        return {
            "count": self.count,
            "total_bytes": self.total_bytes,
        }


def _is_download_cache(path: Path) -> bool:
    name = path.name.lower()
    return name.endswith(".part") or name.endswith(".ytdl")


def scan_cache(root: Path, older_than_hours: float = 24) -> CacheStats:
    resolved_root = root.resolve(strict=True)
    cutoff = time.time() - max(0.0, float(older_than_hours)) * 3600
    found: list[Path] = []
    total = 0
    for path in resolved_root.rglob("*"):
        if not path.is_file() or path.is_symlink() or not _is_download_cache(path):
            continue
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(resolved_root)
            stat = resolved.stat()
        except (OSError, ValueError):
            continue
        if stat.st_mtime > cutoff:
            continue
        found.append(resolved)
        total += stat.st_size
    return CacheStats(len(found), total, tuple(sorted(found)))


def clean_cache(root: Path, older_than_hours: float = 24) -> CacheStats:
    candidates = scan_cache(root, older_than_hours)
    removed: list[Path] = []
    total = 0
    for path in candidates.files:
        try:
            size = path.stat().st_size
            path.unlink()
        except OSError:
            continue
        removed.append(path)
        total += size
    return CacheStats(len(removed), total, tuple(removed))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("scan", "clean"))
    parser.add_argument("root", type=Path)
    parser.add_argument("--older-than-hours", type=float, default=24)
    args = parser.parse_args()
    if args.action == "clean":
        stats = clean_cache(args.root, args.older_than_hours)
    else:
        stats = scan_cache(args.root, args.older_than_hours)
    print(json.dumps(stats.as_json(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
