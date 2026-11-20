"""올린 사진을 인스타 API 규격(JPEG, 4:5 ~ 1.91:1)으로 보정."""
import uuid
from pathlib import Path

from PIL import Image, ImageOps

try:  # 아이폰 HEIC 사진 지원
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:  # pragma: no cover
    pass

MIN_RATIO = 4 / 5     # 세로로 가장 긴 비율
MAX_RATIO = 1.91      # 가로로 가장 긴 비율
MAX_WIDTH = 1440
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}


def clamp_ratio(ratio: float) -> float:
    return min(max(ratio, MIN_RATIO), MAX_RATIO)


def _to_rgb(img: Image.Image) -> Image.Image:
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        return bg
    return img.convert("RGB")


def _crop_to_ratio(img: Image.Image, ratio: float) -> Image.Image:
    w, h = img.size
    if w / h > ratio:  # 너무 넓음 → 좌우 자르기
        new_w = round(h * ratio)
        left = (w - new_w) // 2
        return img.crop((left, 0, left + new_w, h))
    new_h = round(w / ratio)  # 너무 김 → 위아래 자르기
    top = (h - new_h) // 2
    return img.crop((0, top, w, top + new_h))


def _pad_to_ratio(img: Image.Image, ratio: float) -> Image.Image:
    w, h = img.size
    if w / h > ratio:
        canvas = Image.new("RGB", (w, round(w / ratio)), (255, 255, 255))
        canvas.paste(img, (0, (canvas.height - h) // 2))
    else:
        canvas = Image.new("RGB", (round(h * ratio), h), (255, 255, 255))
        canvas.paste(img, ((canvas.width - w) // 2, 0))
    return canvas


def prepare_images(src_paths: list[Path], out_dir: Path, fit: str = "crop") -> list[str]:
    """원본 사진들을 보정해 out_dir에 저장하고 파일명 목록을 돌려준다.

    여러 장(캐러셀)이면 인스타가 첫 장 비율로 나머지를 자르므로,
    모든 사진을 첫 장의 (허용 범위로 보정된) 비율에 맞춘다.
    """
    target_ratio = None
    names = []
    for src in src_paths:
        with Image.open(src) as raw:
            img = _to_rgb(ImageOps.exif_transpose(raw))
        if target_ratio is None:
            target_ratio = clamp_ratio(img.width / img.height)
        if abs(img.width / img.height - target_ratio) > 0.005:
            img = _pad_to_ratio(img, target_ratio) if fit == "pad" else _crop_to_ratio(img, target_ratio)
        if img.width > MAX_WIDTH:
            img = img.resize((MAX_WIDTH, round(MAX_WIDTH / target_ratio)), Image.Resampling.LANCZOS)
        name = f"{uuid.uuid4().hex}.jpg"
        img.save(out_dir / name, "JPEG", quality=90, optimize=True)
        names.append(name)
    return names
