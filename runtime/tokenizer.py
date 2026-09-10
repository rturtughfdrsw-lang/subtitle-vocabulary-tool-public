import unicodedata


TOKENIZER_VERSION = "en-v2"

_APOSTROPHE_TRANSLATION = str.maketrans(
    {
        "’": "'",
        "‘": "'",
        "ʼ": "'",
        "＇": "'",
    }
)
_HYPHEN_TRANSLATION = str.maketrans(
    {
        "‐": "-",
        "‑": "-",
        "‒": "-",
        "–": "-",
        "—": "-",
        "−": "-",
        "﹘": "-",
        "﹣": "-",
        "－": "-",
    }
)


def _is_latin_letter(character: str) -> bool:
    return character.isalpha() and "LATIN" in unicodedata.name(character, "")


def _is_token_character(character: str) -> bool:
    return character.isdigit() or _is_latin_letter(character)


def is_valid_token(candidate: str) -> bool:
    if not candidate or "'" in candidate or any(
        character.isdigit() for character in candidate
    ):
        return False
    segments = candidate.split("-")
    return all(
        segment and all(_is_latin_letter(character) for character in segment)
        for segment in segments
    )


def tokenize(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFC", text)
    normalized = normalized.translate(_APOSTROPHE_TRANSLATION)
    normalized = normalized.translate(_HYPHEN_TRANSLATION)
    tokens: list[str] = []
    current: list[str] = []

    for index, character in enumerate(normalized):
        if _is_token_character(character):
            current.append(character)
            continue
        if (
            character in {"'", "-"}
            and current
            and index + 1 < len(normalized)
            and _is_token_character(normalized[index + 1])
        ):
            current.append(character)
            continue
        if current:
            candidate = "".join(current)
            if is_valid_token(candidate):
                tokens.append(candidate.casefold())
            current = []

    if current:
        candidate = "".join(current)
        if is_valid_token(candidate):
            tokens.append(candidate.casefold())
    return tokens
