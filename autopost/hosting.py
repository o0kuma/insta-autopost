"""인스타 API는 '공개 URL'로만 사진을 받기 때문에, 게시 직전에 Cloudinary에 올린다."""
import hashlib
import time
from pathlib import Path

import requests

from . import config

FOLDER = "insta-autopost"


class HostingError(Exception):
    pass


def upload_image(path: Path) -> str:
    if not config.cloudinary_configured():
        raise HostingError(
            "사진을 올릴 Cloudinary 설정이 없어요. .env에 CLOUDINARY_CLOUD_NAME / API_KEY / API_SECRET을 채워 주세요."
        )
    timestamp = int(time.time())
    # 서명 규칙: 파라미터를 알파벳순으로 이어붙이고 API secret을 붙여 SHA-1
    to_sign = f"folder={FOLDER}&timestamp={timestamp}{config.CLOUDINARY_API_SECRET}"
    signature = hashlib.sha1(to_sign.encode()).hexdigest()
    url = f"https://api.cloudinary.com/v1_1/{config.CLOUDINARY_CLOUD_NAME}/image/upload"
    with open(path, "rb") as f:
        resp = requests.post(
            url,
            data={
                "api_key": config.CLOUDINARY_API_KEY,
                "timestamp": timestamp,
                "folder": FOLDER,
                "signature": signature,
            },
            files={"file": (path.name, f, "image/jpeg")},
            timeout=60,
        )
    try:
        data = resp.json()
    except ValueError:
        data = {}
    if resp.status_code >= 400 or "secure_url" not in data:
        msg = data.get("error", {}).get("message") or resp.text[:200]
        raise HostingError(f"Cloudinary 업로드 실패: {msg}")
    return data["secure_url"]
