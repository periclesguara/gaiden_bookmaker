import re
from pathlib import Path


_NUMBERED_IMAGE = re.compile(
    r"^(?:(?:chapter|ch|capitulo|capítulo|image|img)[ _-]*)?"
    r"(?P<chapter>\d+)(?:[ _.:\-]+(?P<slot>\d+))?"
    r"(?:[ _.-]+[a-z][a-z0-9 _.-]*)?$",
    re.IGNORECASE,
)


def illustration_number(path: Path) -> tuple[int, int] | None:
    match = _NUMBERED_IMAGE.fullmatch(path.stem)
    if match is None:
        return None
    chapter = int(match['chapter'])
    slot = int(match['slot'] or 1)
    return (chapter, slot) if chapter > 0 and slot > 0 else None


def numbered_gallery(images: list[Path]) -> dict[tuple[int, int], Path]:
    mapping = {}
    for image in images:
        number = illustration_number(image)
        if number is None:
            raise ValueError(f'Imagem sem capítulo/posição explícitos: {image.name}')
        if number in mapping:
            raise ValueError(f'Numeração de imagem ambígua: {mapping[number].name}, {image.name}')
        mapping[number] = image
    return mapping
