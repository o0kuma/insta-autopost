"""인스타 API는 '공개 URL'로만 사진을 받는다. 게시 직전에 사진의 공개 주소를 만든다.

- 클라우드에 배포돼 공개 주소(PUBLIC_BASE_URL)가 있으면: 이 서버가 /p/<파일명> 으로 직접 제공
- 아니면 Cloudinary에 올려서 주소를 받음 (로컬 PC에서 실제 게시할 때)
"""
import hashlib
import re
import time
from pathlib import Path

import requests

from . import config

FOLDER = "insta-autopost"
# 보정된 사진 파일명은 uuid4 hex(추측 불가능한 128비트 난수) + .jpg
PUBLIC_NAME_RE = re.compile(r"^[0-9a-f]{32}\.jpg$")


class HostingError(Exception):
    pass


def mode() -> str:
    """'cloudinary' | 'self' | 'none' — 설정 화면 표시용."""
    if config.cloudinary_configured():
        return "cloudinary"
    if config.PUBLIC_BASE_URL:
        return "self"
    return "none"


def public_url(path: Path) -> str:
    current = mode()
    if current == "cloudinary":
        return upload_to_cloudinary(path)
    if current == "self":
        if not PUBLIC_NAME_RE.match(path.name):
            raise HostingError(f"공개할 수 없는 파일 이름이에요: {path.name}")
        return f"{config.PUBLIC_BASE_URL}/p/{path.name}"
    raise HostingError(
        "사진의 공개 주소를 만들 수 없어요. 클라우드에 배포해서 PUBLIC_BASE_URL을 갖추거나, "
        ".env에 CLOUDINARY_CLOUD_NAME / API_KEY / API_SECRET을 채워 주세요."
    )


def upload_to_cloudinary(path: Path) -> str:
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
