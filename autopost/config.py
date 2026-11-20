"""환경 변수(.env)와 가게 프로필(brand.json)을 읽어오는 곳."""
import json
import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"        # 올린 원본 사진
PROCESSED_DIR = DATA_DIR / "processed"   # 인스타 규격으로 보정한 JPEG
DB_PATH = DATA_DIR / "app.db"
BRAND_PATH = BASE_DIR / "brand.json"

for _d in (DATA_DIR, UPLOAD_DIR, PROCESSED_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


# --- Instagram (Instagram API with Instagram Login) ---
IG_API_VERSION = _env("IG_API_VERSION", "v27.0")
IG_ACCESS_TOKEN = _env("IG_ACCESS_TOKEN")
# 토큰이 있어도 DRY_RUN=1 이면 실제로 올리지 않고 흉내만 냄
FORCE_DRY_RUN = _env("DRY_RUN").lower() in ("1", "true", "yes", "on")

# --- 사진 공개 URL 호스팅 (인스타 API는 공개 URL로만 사진을 받음) ---
CLOUDINARY_CLOUD_NAME = _env("CLOUDINARY_CLOUD_NAME")
CLOUDINARY_API_KEY = _env("CLOUDINARY_API_KEY")
CLOUDINARY_API_SECRET = _env("CLOUDINARY_API_SECRET")

# --- Claude ---
CLAUDE_MODEL = _env("CLAUDE_MODEL", "claude-opus-5-5")

# --- 웹 대시보드 ---
HOST = _env("HOST", "127.0.0.1")
PORT = int(_env("PORT", "5000"))
SECRET_KEY = _env("FLASK_SECRET_KEY") or secrets.token_hex(16)

DEFAULT_BRAND = {
    "business_name": "OO카페",
    "industry": "동네 카페 (커피, 디저트)",
    "location": "서울 마포구 망원동",
    "tone": "친근하고 따뜻한 존댓말(~요체). 과하게 들뜨지 않게.",
    "emoji": "적당히 (문단마다 0~2개)",
    "fixed_hashtags": ["#망원동카페", "#망원카페"],
    "max_hashtags": 15,
    "call_to_action": "영업시간 11:00-21:00 · 매주 월요일 휴무",
    "avoid": "'최고', '1등' 같은 과장 표현. 할인·가격은 메모에 있을 때만 언급.",
    "posting_times": ["12:00", "19:00"],
    "image_fit": "crop",
}


def load_brand() -> dict:
    brand = dict(DEFAULT_BRAND)
    if BRAND_PATH.exists():
        with open(BRAND_PATH, encoding="utf-8") as f:
            brand.update(json.load(f))
    return brand


def save_brand(brand: dict) -> None:
    with open(BRAND_PATH, "w", encoding="utf-8") as f:
        json.dump(brand, f, ensure_ascii=False, indent=2)


def cloudinary_configured() -> bool:
    return bool(CLOUDINARY_CLOUD_NAME and CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET)
