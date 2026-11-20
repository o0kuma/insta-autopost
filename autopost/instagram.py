"""Instagram API (Instagram 로그인 방식) 클라이언트와, 토큰이 없을 때 쓰는 테스트용 가짜 클라이언트.

필요 권한: instagram_business_basic, instagram_business_content_publish,
          instagram_business_manage_comments, instagram_business_manage_insights
"""
import hashlib
import time
from datetime import datetime, timedelta

import requests

from . import config, db

GRAPH_HOST = "https://graph.instagram.com"
TOKEN_REFRESH_EVERY = timedelta(days=7)  # 장기 토큰은 60일 유효. 1주마다 갱신.


class InstagramError(Exception):
    pass


# ---------------------------------------------------------------- 토큰 관리
def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def get_token() -> str:
    """.env 토큰이 새로 바뀌었으면 그걸 쓰고, 아니면 앱이 갱신해 둔 토큰을 쓴다."""
    env_token = config.IG_ACCESS_TOKEN
    if env_token and db.get_setting("ig_token_env_hash") != _hash(env_token):
        return env_token
    return db.get_setting("ig_access_token") or env_token


def is_dry_run() -> bool:
    return config.FORCE_DRY_RUN or not get_token()


def maybe_refresh_token() -> str | None:
    """마지막 갱신 후 7일이 지났으면 토큰을 갱신한다. 결과 메시지를 돌려준다."""
    if is_dry_run():
        return None
    token = get_token()
    env_hash = _hash(config.IG_ACCESS_TOKEN) if config.IG_ACCESS_TOKEN else ""
    if db.get_setting("ig_token_env_hash") != env_hash:
        # 새 토큰을 처음 본 시점 기록 (갓 발급된 토큰은 24시간이 지나야 갱신 가능)
        db.set_setting("ig_access_token", token)
        db.set_setting("ig_token_env_hash", env_hash)
        db.set_setting("ig_token_refreshed_at", datetime.now().isoformat(timespec="seconds"))
        return None
    last = db.get_setting("ig_token_refreshed_at")
    if last and datetime.now() - datetime.fromisoformat(last) < TOKEN_REFRESH_EVERY:
        return None
    new_token = InstagramClient(token).refresh_token()
    db.set_setting("ig_access_token", new_token)
    db.set_setting("ig_token_refreshed_at", datetime.now().isoformat(timespec="seconds"))
    return "인스타 토큰을 갱신했어요."


# ---------------------------------------------------------------- 실제 클라이언트
class InstagramClient:
    dry_run = False

    def __init__(self, token: str):
        self.token = token
        self.base = f"{GRAPH_HOST}/{config.IG_API_VERSION}"
        self._me: dict | None = None

    def _request(self, method: str, path: str, **params) -> dict:
        url = path if path.startswith("http") else f"{self.base}/{path.lstrip('/')}"
        params["access_token"] = self.token
        try:
            if method == "GET":
                resp = requests.get(url, params=params, timeout=30)
            else:
                resp = requests.post(url, data=params, timeout=60)
        except requests.RequestException as e:
            raise InstagramError(f"인스타 서버에 연결하지 못했어요: {e}") from e
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if resp.status_code >= 400 or "error" in data:
            err = data.get("error", {})
            msg = err.get("error_user_msg") or err.get("message") or resp.text[:200]
            raise InstagramError(f"{msg} (code {err.get('code', resp.status_code)})")
        return data

    # --- 계정
    def me(self) -> dict:
        if self._me is None:
            self._me = self._request("GET", "me", fields="user_id,username")
        return self._me

    @property
    def ig_id(self) -> str:
        return self.me()["user_id"]

    @property
    def username(self) -> str:
        return self.me().get("username", "")

    def refresh_token(self) -> str:
        data = self._request(
            "GET", f"{GRAPH_HOST}/refresh_access_token", grant_type="ig_refresh_token"
        )
        return data["access_token"]

    def publishing_limit(self) -> dict:
        data = self._request(
            "GET", f"{self.ig_id}/content_publishing_limit", fields="quota_usage,config"
        )
        item = (data.get("data") or [{}])[0]
        return {
            "used": item.get("quota_usage", 0),
            "total": item.get("config", {}).get("quota_total"),
        }

    # --- 게시
    def _create_container(self, **params) -> str:
        return self._request("POST", f"{self.ig_id}/media", **params)["id"]

    def _wait_until_ready(self, container_id: str, timeout: int = 300, interval: int = 3) -> None:
        deadline = time.time() + timeout
        while True:
            info = self._request("GET", container_id, fields="status_code,status")
            code = info.get("status_code")
            if code in ("FINISHED", "PUBLISHED"):
                return
            if code in ("ERROR", "EXPIRED"):
                raise InstagramError(f"인스타가 사진 처리에 실패했어요: {info.get('status') or code}")
            if time.time() > deadline:
                raise InstagramError("인스타의 사진 처리가 너무 오래 걸려요. 잠시 후 다시 시도하세요.")
            time.sleep(interval)

    def publish_post(self, image_urls: list[str], caption: str) -> dict:
        if len(image_urls) == 1:
            container = self._create_container(image_url=image_urls[0], caption=caption)
        else:
            children = []
            for url in image_urls:
                child = self._create_container(image_url=url, is_carousel_item="true")
                self._wait_until_ready(child)
                children.append(child)
            container = self._create_container(
                media_type="CAROUSEL", children=",".join(children), caption=caption
            )
        self._wait_until_ready(container)
        media_id = self._request("POST", f"{self.ig_id}/media_publish", creation_id=container)["id"]
        info = self._request("GET", media_id, fields="id,permalink,timestamp")
        return {"id": media_id, "permalink": info.get("permalink", "")}

    # --- 관리 (게시물, 댓글, 통계)
    def list_media(self, limit: int = 12) -> list[dict]:
        data = self._request(
            "GET", f"{self.ig_id}/media",
            fields="id,caption,media_type,media_url,thumbnail_url,permalink,timestamp,like_count,comments_count",
            limit=limit,
        )
        return data.get("data", [])

    def get_media_stats(self, media_id: str) -> dict:
        info = self._request("GET", media_id, fields="like_count,comments_count")
        stats = {"likes": info.get("like_count"), "comments": info.get("comments_count")}
        try:
            data = self._request("GET", f"{media_id}/insights", metric="reach,saved,shares,views")
            for item in data.get("data", []):
                values = item.get("values") or [{}]
                value = values[0].get("value", item.get("total_value", {}).get("value"))
                stats[item["name"]] = value
        except InstagramError:
            pass  # 인사이트 권한이 없거나 아직 집계 전이면 좋아요/댓글만 보여줌
        return stats

    def get_comments(self, media_id: str) -> list[dict]:
        data = self._request(
            "GET", f"{media_id}/comments",
            fields="id,text,username,timestamp,replies{id,text,username,timestamp}",
        )
        comments = data.get("data", [])
        for c in comments:
            c["replies"] = (c.get("replies") or {}).get("data", [])
        return comments

    def reply_to_comment(self, comment_id: str, message: str) -> str:
        return self._request("POST", f"{comment_id}/replies", message=message)["id"]


# ---------------------------------------------------------------- 테스트 모드
_DRY_REPLIES: dict[str, list[dict]] = {}
_SAMPLE_COMMENTS = [
    ("coffee_lover_92", "와 너무 맛있어 보여요!! 주말에 가봐도 되나요?"),
    ("mangwon_daily", "주차 가능한가요?"),
    ("yumi.eats", "분위기 너무 좋아요 😍 다음에 친구랑 갈게요"),
]


class DryRunInstagram:
    """토큰이 없을 때 쓰는 가짜 클라이언트. 아무것도 인스타에 올리지 않는다."""

    dry_run = True
    username = "test_mode"

    def publishing_limit(self) -> dict:
        return {"used": len(db.list_posts("published")), "total": 100}

    def publish_post(self, image_urls: list[str], caption: str) -> dict:
        time.sleep(1)
        media_id = f"dry_{int(time.time() * 1000)}"
        return {"id": media_id, "permalink": ""}

    def list_media(self, limit: int = 12) -> list[dict]:
        media = []
        for p in db.list_posts("published", limit=limit):
            if not (p["ig_media_id"] or "").startswith("dry_"):
                continue
            media.append({
                "id": p["ig_media_id"],
                "caption": p["caption"],
                "media_url": f"/media/{p['images'][0]}",
                "permalink": "",
                "timestamp": p["published_at"],
                "like_count": 10 + p["id"] * 7 % 40,
                "comments_count": len(_SAMPLE_COMMENTS),
            })
        return media

    def get_media_stats(self, media_id: str) -> dict:
        seed = int(media_id.split("_")[-1]) % 97
        return {"likes": 12 + seed % 30, "comments": len(_SAMPLE_COMMENTS),
                "reach": 150 + seed * 3, "saved": seed % 9, "shares": seed % 5, "views": 300 + seed * 5}

    def get_comments(self, media_id: str) -> list[dict]:
        comments = []
        for i, (user, text) in enumerate(_SAMPLE_COMMENTS):
            cid = f"{media_id}_c{i}"
            comments.append({
                "id": cid, "username": user, "text": text, "timestamp": "",
                "replies": _DRY_REPLIES.get(cid, []),
            })
        return comments

    def reply_to_comment(self, comment_id: str, message: str) -> str:
        reply = {"id": f"{comment_id}_r", "text": message, "username": self.username, "timestamp": ""}
        _DRY_REPLIES.setdefault(comment_id, []).append(reply)
        return reply["id"]


def get_client() -> InstagramClient | DryRunInstagram:
    return DryRunInstagram() if is_dry_run() else InstagramClient(get_token())
