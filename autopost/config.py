"""환경 변수(.env)와 가게 프로필(brand.json)을 읽어오는 곳.

로컬 PC와 클라우드(Render 등) 양쪽에서 같은 코드로 돌아가도록,
저장 위치·주소·시간대는 모두 환경 변수로 바꿀 수 있다.
"""
import json
import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


# --- 저장 위치 (클라우드에서는 영구 디스크 경로로 지정) ---
DATA_DIR = Path(_env("DATA_DIR") or BASE_DIR / "data")
UPLOAD_DIR = DATA_DIR / "uploads"        # 올린 원본 사진
PROCESSED_DIR = DATA_DIR / "processed"   # 인스타 규격으로 보정한 JPEG
DB_PATH = DATA_DIR / "app.db"
BRAND_PATH = DATA_DIR / "brand.json"     # 설정 화면에서 고친 가게 정보
BRAND_TEMPLATE_PATH = BASE_DIR / "brand.json"  # 처음 기본값

for _d in (DATA_DIR, UPLOAD_DIR, PROCESSED_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# --- 시간대 (서버가 UTC여도 가게 시간 기준으로 예약) ---
TIMEZONE = _env("TIMEZONE", "Asia/Seoul")

# --- Instagram (Instagram API with Instagram Login) ---
IG_API_VERSION = _env("IG_API_VERSION", "v27.0")
IG_ACCESS_TOKEN = _env("IG_ACCESS_TOKEN")      # 직접 발급한 토큰 (선택)
IG_APP_ID = _env("IG_APP_ID")                  # '인스타 연결' 버튼용 (Instagram 앱 ID)
IG_APP_SECRET = _env("IG_APP_SECRET")          # '인스타 연결' 버튼용 (Instagram 앱 시크릿)
FORCE_DRY_RUN = _env("DRY_RUN").lower() in ("1", "true", "yes", "on")

# --- 이 서버의 공개 주소 (Render는 RENDER_EXTERNAL_URL을 자동으로 넣어 줌) ---
PUBLIC_BASE_URL = (_env("PUBLIC_BASE_URL") or _env("RENDER_EXTERNAL_URL")).rstrip("/")

# --- 사진 공개 URL 호스팅 (선택. 공개 주소가 있으면 이 서버가 직접 사진을 제공) ---
CLOUDINARY_CLOUD_NAME = _env("CLOUDINARY_CLOUD_NAME")
CLOUDINARY_API_KEY = _env("CLOUDINARY_API_KEY")
CLOUDINARY_API_SECRET = _env("CLOUDINARY_API_SECRET")

# --- Claude ---
CLAUDE_MODEL = _env("CLAUDE_MODEL", "claude-opus-5-5")

# --- 웹 대시보드 ---
HOST = _env("HOST", "127.0.0.1")
PORT = int(_env("PORT", "5000"))
DASHBOARD_PASSWORD = _env("DASHBOARD_PASSWORD")
# 프록시(Render 등) 뒤에서 실제 접속 IP와 https 여부를 알기 위해
TRUST_PROXY = _env("TRUST_PROXY").lower() in ("1", "true", "yes") or bool(_env("RENDER"))


def _load_secret_key() -> str:
    """세션 암호화 키. 환경 변수가 없으면 한 번 만들어 DATA_DIR에 보관 (재시작해도 로그인 유지)."""
    if _env("FLASK_SECRET_KEY"):
        return _env("FLASK_SECRET_KEY")
    path = DATA_DIR / ".secret_key"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    key = secrets.token_hex(32)
    path.write_text(key, encoding="utf-8")
    return key


SECRET_KEY = _load_secret_key()

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
    for path in (BRAND_TEMPLATE_PATH, BRAND_PATH):
        if path.exists():
            with open(path, encoding="utf-8") as f:
                brand.update(json.load(f))
    return brand


def save_brand(brand: dict) -> None:
    with open(BRAND_PATH, "w", encoding="utf-8") as f:
        json.dump(brand, f, ensure_ascii=False, indent=2)


def cloudinary_configured() -> bool:
    return bool(CLOUDINARY_CLOUD_NAME and CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET)


def is_local_host() -> bool:
    """이 PC에서만 접속 가능한 주소로 띄웠는지 (그렇다면 비밀번호 없이도 허용)."""
    return HOST in ("127.0.0.1", "localhost", "::1")
