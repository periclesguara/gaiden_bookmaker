"""Pure ISBN validation shared by the PrintBook adapters."""


def isbn13(value: str) -> str:
    normalized = value.replace("-", "").replace(" ", "")
    if (
        len(normalized) != 13
        or not normalized.isascii()
        or not normalized.isdigit()
        or normalized[:3] not in {"978", "979"}
    ):
        raise ValueError("Informe um ISBN-13 editorial válido.")
    if sum(int(character) * (1 if index % 2 == 0 else 3) for index, character in enumerate(normalized)) % 10:
        raise ValueError("Dígito verificador do ISBN incorreto.")
    return normalized
