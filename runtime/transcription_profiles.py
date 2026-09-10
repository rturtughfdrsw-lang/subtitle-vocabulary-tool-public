from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TranscriptionProfile:
    model_name: str
    device: str = "cpu"
    compute_type: str = "int8"
    beam_size: int = 1
    best_of: int = 1


PROFILES = {
    "standard": TranscriptionProfile(model_name="small"),
    "fast": TranscriptionProfile(model_name="base"),
}


def get_transcription_profile(name: str) -> TranscriptionProfile:
    key = str(name or "standard").strip().lower()
    try:
        return PROFILES[key]
    except KeyError as exc:
        raise ValueError(f"未知语音识别配置：{name}") from exc
