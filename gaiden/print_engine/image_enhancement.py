"""Deterministic, local raster preparation for PrintBook artifacts only."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


PROFILES = {"none", "print_safe", "detail", "production_300", "uiclap_300"}
MAX_OUTPUT_PIXELS = 40_000_000


def enhance_print_image(
    source: Path,
    target: Path,
    profile: str = "print_safe",
    *,
    minimum_width_px: int | None = None,
    minimum_height_px: int | None = None,
) -> dict:
    """Prepare an image for print without resizing or fabricating resolution."""
    if profile not in PROFILES:
        raise ValueError("Perfil de melhoria de imagem inválido.")
    source, target = Path(source), Path(target)
    if source.resolve() == target.resolve():
        raise ValueError("A imagem original não pode ser sobrescrita.")
    data = np.fromfile(source, dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
    if image is None or image.ndim not in {2, 3}:
        raise ValueError("O OpenCV não conseguiu ler a imagem de impressão.")
    height, width = image.shape[:2]
    output_width, output_height = width, height
    scale = 1.0
    if profile in {"production_300", "uiclap_300"}:
        if width * height > MAX_OUTPUT_PIXELS:
            raise ValueError("A imagem original excede o limite seguro de pixels para melhoria.")
        if not minimum_width_px or not minimum_height_px or minimum_width_px < 0 or minimum_height_px < 0:
            raise ValueError("O perfil 300 ppi exige dimensões finais positivas.")
        scale = max(minimum_width_px / width, minimum_height_px / height, 1.0)
        output_width = int(np.ceil(width * scale))
        output_height = int(np.ceil(height * scale))
        if output_width * output_height > MAX_OUTPUT_PIXELS:
            raise ValueError("A imagem ampliada excede o limite seguro de pixels.")
    alpha = image[:, :, 3] if image.ndim == 3 and image.shape[2] == 4 else None
    color = image[:, :, :3] if image.ndim == 3 else cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    if profile != "none":
        lab = cv2.cvtColor(color, cv2.COLOR_BGR2LAB)
        light, channel_a, channel_b = cv2.split(lab)
        clip = 1.35 if profile in {"print_safe", "uiclap_300"} else 1.8
        light = cv2.createCLAHE(clipLimit=clip, tileGridSize=(8, 8)).apply(light)
        color = cv2.cvtColor(cv2.merge((light, channel_a, channel_b)), cv2.COLOR_LAB2BGR)
        if profile in {"detail", "production_300", "uiclap_300"}:
            color = cv2.fastNlMeansDenoisingColored(color, None, 3, 3, 7, 21)
        blur = cv2.GaussianBlur(color, (0, 0), 0.8)
        amount = 0.35 if profile in {"print_safe", "uiclap_300"} else 0.6
        color = cv2.addWeighted(color, 1 + amount, blur, -amount, 0)
    if scale > 1:
        color = cv2.resize(color, (output_width, output_height), interpolation=cv2.INTER_LANCZOS4)
        if alpha is not None:
            alpha = cv2.resize(alpha, (output_width, output_height), interpolation=cv2.INTER_LANCZOS4)
        blur = cv2.GaussianBlur(color, (0, 0), 0.65)
        color = cv2.addWeighted(color, 1.45, blur, -0.45, 0)
    if alpha is not None:
        color = np.dstack((color, alpha))
    suffix = target.suffix.lower()
    extension = ".png" if suffix == ".png" else ".jpg"
    params = [cv2.IMWRITE_PNG_COMPRESSION, 3] if extension == ".png" else [cv2.IMWRITE_JPEG_QUALITY, 96]
    ok, encoded = cv2.imencode(extension, color, params)
    if not ok:
        raise ValueError("O OpenCV não conseguiu gravar a imagem de impressão.")
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded.tofile(target)
    return {
        "engine": f"opencv-{cv2.__version__}",
        "profile": profile,
        "source_width_px": width,
        "source_height_px": height,
        "width_px": output_width,
        "height_px": output_height,
        "resized": scale > 1,
        "scale_factor": round(scale, 4),
        "resolution_note": "interpolated" if scale > 1 else "native",
    }
