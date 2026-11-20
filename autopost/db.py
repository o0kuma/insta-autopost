"""게시물 대기열과 설정값을 SQLite에 저장."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime

from .config import DB_PATH

TIME_FMT = "%Y-%m-%dT%H:%M"  # <input type="datetime-local"> 형식과 동일

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at   TEXT NOT NULL,
    memo         TEXT NOT NULL DEFAULT '',
    images       TEXT NOT NULL,              -- 보정된 JPEG 파일명 JSON 배열
    caption      TEXT NOT NULL DEFAULT '',
    hashtags     TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'draft', -- draft | approved | publishing | published | failed
    scheduled_at TEXT,
    published_at TEXT,
    ig_media_id  TEXT,
    permalink    TEXT,
    error        TEXT
);
CREATE INDEX IF NOT EXISTS idx_posts_status ON posts(status, scheduled_at);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

POST_FIELDS = {
    "memo", "images", "caption", "hashtags", "status", "scheduled_at",
    "published_at", "ig_media_id", "permalink", "error",
}


def now_str() -> str:
    return datetime.now().strftime(TIME_FMT)


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as c:
        c.executescript(SCHEMA)


def _row_to_post(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    post = dict(row)
    post["images"] = json.loads(post["images"])
    return post


def create_post(images: list[str], memo: str) -> int:
    with connect() as c:
        cur = c.execute(
            "INSERT INTO posts (created_at, memo, images) VALUES (?, ?, ?)",
            (now_str(), memo, json.dumps(images)),
        )
        return cur.lastrowid


def get_post(post_id: int) -> dict | None:
    with connect() as c:
        return _row_to_post(c.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone())


def list_posts(status: str, limit: int = 100) -> list[dict]:
    order = {
        "draft": "created_at DESC",
        "approved": "scheduled_at ASC",
        "publishing": "scheduled_at ASC",
        "published": "published_at DESC",
        "failed": "created_at DESC",
    }.get(status, "id DESC")
    with connect() as c:
        rows = c.execute(
            f"SELECT * FROM posts WHERE status = ? ORDER BY {order} LIMIT ?", (status, limit)
        ).fetchall()
    return [_row_to_post(r) for r in rows]


def count_by_status() -> dict[str, int]:
    with connect() as c:
        rows = c.execute("SELECT status, COUNT(*) AS n FROM posts GROUP BY status").fetchall()
    return {r["status"]: r["n"] for r in rows}


def update_post(post_id: int, **fields) -> None:
    unknown = set(fields) - POST_FIELDS
    if unknown:
        raise ValueError(f"unknown post fields: {unknown}")
    if "images" in fields:
        fields["images"] = json.dumps(fields["images"])
    cols = ", ".join(f"{k} = ?" for k in fields)
    with connect() as c:
        c.execute(f"UPDATE posts SET {cols} WHERE id = ?", (*fields.values(), post_id))


def delete_post(post_id: int) -> None:
    with connect() as c:
        c.execute("DELETE FROM posts WHERE id = ?", (post_id,))


def mark_publishing(post_id: int, from_statuses: tuple[str, ...]) -> bool:
    """다른 스레드와 겹쳐 두 번 올라가지 않도록 상태를 원자적으로 바꾼다."""
    marks = ",".join("?" * len(from_statuses))
    with connect() as c:
        cur = c.execute(
            f"UPDATE posts SET status = 'publishing', error = NULL WHERE id = ? AND status IN ({marks})",
            (post_id, *from_statuses),
        )
        return cur.rowcount == 1


def claim_due_posts() -> list[int]:
    """예약 시간이 지난 승인 게시물을 'publishing'으로 바꾸고 id 목록을 돌려준다."""
    with connect() as c:
        ids = [r["id"] for r in c.execute(
            "SELECT id FROM posts WHERE status = 'approved' AND scheduled_at <= ? ORDER BY scheduled_at",
            (now_str(),),
        ).fetchall()]
    return [i for i in ids if mark_publishing(i, ("approved",))]


def reset_stuck_publishing() -> None:
    """앱이 게시 도중 꺼졌던 경우, 재시작 시 실패로 돌려서 사람이 확인하게 한다."""
    with connect() as c:
        c.execute(
            "UPDATE posts SET status = 'failed', error = '게시 도중 앱이 종료됐어요. 인스타에 올라갔는지 확인 후 다시 시도하세요.' "
            "WHERE status = 'publishing'"
        )


def taken_slots() -> set[str]:
    with connect() as c:
        rows = c.execute(
            "SELECT scheduled_at FROM posts WHERE status IN ('approved', 'publishing') AND scheduled_at IS NOT NULL"
        ).fetchall()
    return {r["scheduled_at"] for r in rows}


def get_setting(key: str, default: str | None = None) -> str | None:
    with connect() as c:
        row = c.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with connect() as c:
        c.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
