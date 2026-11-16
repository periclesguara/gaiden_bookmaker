"""Official and production-safe print trim presets."""
from decimal import Decimal, InvalidOperation


# Amazon KDP's complete US paperback list, plus common IngramSpark custom
# presets that fit the provider's documented manufacturing ranges.
TRIM_CHOICES = (
    ("4.527559x7.086614", "11,5 × 18 cm · UICLAP pocket"),
    ("5.433071x8.267717", "13,8 × 21 cm · UICLAP"),
    ("6.299213x9.055118", "16 × 23 cm · UICLAP padrão"),
    ("7.874016x7.874016", "20 × 20 cm · UICLAP quadrado"),
    ("8.267717x11.692913", "21 × 29,7 cm (A4) · UICLAP"),
    ("4x6", "4 × 6 in · IngramSpark"),
    ("4.25x7", "4,25 × 7 in · IngramSpark"),
    ("4.37x7", "4,37 × 7 in · IngramSpark"),
    ("4.72x7.48", "4,72 × 7,48 in · IngramSpark"),
    ("5x7", "5 × 7 in · IngramSpark"),
    ("5x8", "5 × 8 in · Amazon / IngramSpark"),
    ("5.06x7.81", "5,06 × 7,81 in · Amazon / IngramSpark"),
    ("5.25x8", "5,25 × 8 in · Amazon / IngramSpark"),
    ("5.5x8", "5,5 × 8 in · IngramSpark"),
    ("5.5x8.5", "5,5 × 8,5 in · Amazon / IngramSpark"),
    ("5.83x8.27", "5,83 × 8,27 in (A5) · IngramSpark"),
    ("6x8", "6 × 8 in · IngramSpark"),
    ("6x9", "6 × 9 in · Amazon / IngramSpark"),
    ("6.14x9.21", "6,14 × 9,21 in · Amazon / IngramSpark"),
    ("6.14x9.252", "6,14 × 9,252 in · IngramSpark"),
    ("6.69x9.61", "6,69 × 9,61 in · Amazon / IngramSpark"),
    ("7x9", "7 × 9 in · IngramSpark"),
    ("7x10", "7 × 10 in · Amazon / IngramSpark"),
    ("7.44x9.69", "7,44 × 9,69 in · Amazon / IngramSpark"),
    ("7.5x9.25", "7,5 × 9,25 in · Amazon / IngramSpark"),
    ("8x8", "8 × 8 in · IngramSpark"),
    ("8x10", "8 × 10 in · Amazon / IngramSpark"),
    ("8.25x6", "8,25 × 6 in · Amazon / IngramSpark"),
    ("8.25x8.25", "8,25 × 8,25 in · Amazon / IngramSpark"),
    ("8.25x11", "8,25 × 11 in · Amazon / IngramSpark"),
    ("8.27x11.69", "8,27 × 11,69 in (A4) · Amazon"),
    ("8.5x8.5", "8,5 × 8,5 in · Amazon / IngramSpark"),
    ("8.5x11", "8,5 × 11 in · Amazon / IngramSpark"),
)

KDP_PAPERBACK_TRIMS = {
    "5x8", "5.06x7.81", "5.25x8", "5.5x8.5", "6x9", "6.14x9.21",
    "6.69x9.61", "7x10", "7.44x9.69", "7.5x9.25", "8x10", "8.25x6",
    "8.25x8.25", "8.25x11", "8.27x11.69", "8.5x8.5", "8.5x11",
}
KDP_HARDCOVER_TRIMS = {"5.5x8.5", "6x9", "6.14x9.21", "7x10", "8.25x11"}
UICLAP_TRIMS = {
    "4.527559x7.086614",  # 11.5 x 18 cm
    "5.433071x8.267717",  # 13.8 x 21 cm
    "6.299213x9.055118",  # 16 x 23 cm
    "7.874016x7.874016",  # 20 x 20 cm
    "8.267717x11.692913",  # 21 x 29.7 cm
}
INGRAM_TRIMS = set(dict(TRIM_CHOICES)) - UICLAP_TRIMS


def _dimensions(trim):
    try:
        width, height = trim.split("x", 1)
        return Decimal(width), Decimal(height)
    except (AttributeError, InvalidOperation, ValueError) as exc:
        raise ValueError("Formato de livro inválido.") from exc


def trim_is_supported(platform, binding, paper, trim):
    """Return whether a preset fits the provider's documented constraints."""
    if trim not in dict(TRIM_CHOICES):
        return False
    if platform == "amazon_kdp":
        allowed = KDP_HARDCOVER_TRIMS if binding == "hardcover" else KDP_PAPERBACK_TRIMS
        return binding in {"paperback", "hardcover"} and trim in allowed
    if platform == "uiclap":
        return binding == "paperback" and trim in UICLAP_TRIMS
    if platform != "ingramspark":
        return False
    if trim not in INGRAM_TRIMS:
        return False

    width, height = _dimensions(trim)
    if binding == "paperback":
        max_width, max_height = (Decimal("6.14"), Decimal("9.252")) if paper == "cream" else (Decimal("8.5"), Decimal("11"))
        return Decimal("4") <= width <= max_width and Decimal("6") <= height <= max_height
    if binding in {"hardcover", "hardcover_dust_jacket"}:
        max_width, max_height = (Decimal("6.14"), Decimal("9.252")) if paper == "cream" else (Decimal("8.5"), Decimal("11"))
        if binding == "hardcover_dust_jacket":
            max_width, max_height = min(max_width, Decimal("6.14")), min(max_height, Decimal("9.252"))
        return Decimal("5") <= width <= max_width and Decimal("7") <= height <= max_height
    return False


def validate_trim(platform, binding, paper, trim):
    if not trim_is_supported(platform, binding, paper, trim):
        raise ValueError("O formato escolhido não é oferecido por este fornecedor com a encadernação e o papel selecionados.")


def compatibility_map():
    """Serializable map used to keep the browser selector in sync."""
    result = {}
    for platform in ("ingramspark", "amazon_kdp", "uiclap"):
        for binding in ("paperback", "hardcover", "hardcover_dust_jacket"):
            for paper in ("white", "cream"):
                key = f"{platform}|{binding}|{paper}"
                result[key] = [value for value, _ in TRIM_CHOICES if trim_is_supported(platform, binding, paper, value)]
    return result
