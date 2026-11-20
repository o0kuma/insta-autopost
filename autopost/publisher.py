"""예약 시간이 된 게시물을 실제로 올리는 부분과 백그라운드 스케줄러."""
import logging
import threading
import time
from datetime import datetime, timedelta
from datetime import time as dtime

from . import ai, config, db, hosting, instagram

log = logging.getLogger("autopost")


def next_free_slot(brand: dict) -> str:
    """brand.json의 게시 시간 중 아직 예약이 없는 가장 가까운 시간."""
    taken = db.taken_slots()
    earliest = datetime.now() + timedelta(minutes=5)
    times = []
    for t in brand.get("posting_times", []):
        try:
            hh, mm = (int(x) for x in t.split(":"))
            times.append(dtime(hh, mm))
        except ValueError:
            continue
    if not times:
        return (earliest + timedelta(hours=1)).strftime(db.TIME_FMT)
    for day in range(90):
        date = earliest.date() + timedelta(days=day)
        for t in sorted(times):
            slot = datetime.combine(date, t)
            key = slot.strftime(db.TIME_FMT)
            if slot > earliest and key not in taken:
                return key
    return (earliest + timedelta(days=90)).strftime(db.TIME_FMT)


def publish(post_id: int) -> None:
    """상태가 'publishing'인 게시물을 인스타에 올린다. 결과는 DB에 기록."""
    post = db.get_post(post_id)
    if not post:
        return
    try:
        client = instagram.get_client()
        caption = ai.compose_caption(post["caption"], post["hashtags"])
        if client.dry_run:
            urls = [f"test://{name}" for name in post["images"]]
        else:
            urls = [hosting.upload_image(config.PROCESSED_DIR / name) for name in post["images"]]
        result = client.publish_post(urls, caption)
        db.update_post(
            post_id, status="published", published_at=db.now_str(),
            ig_media_id=result["id"], permalink=result["permalink"], error=None,
        )
        log.info("게시 완료: post %s -> %s", post_id, result["id"])
    except Exception as e:  # 어떤 오류든 '실패' 탭에서 사람이 볼 수 있게 기록
        log.exception("게시 실패: post %s", post_id)
        db.update_post(post_id, status="failed", error=str(e))


def publish_in_background(post_id: int) -> None:
    threading.Thread(target=publish, args=(post_id,), daemon=True).start()


class Scheduler(threading.Thread):
    """30초마다 예약 시간이 지난 게시물을 올리고, 가끔 인스타 토큰을 갱신한다."""

    def __init__(self, interval: int = 30):
        super().__init__(name="autopost-scheduler", daemon=True)
        self.interval = interval
        self._last_token_check = 0.0

    def run(self) -> None:
        log.info("스케줄러 시작 (%s초 간격)", self.interval)
        while True:
            try:
                for post_id in db.claim_due_posts():
                    publish(post_id)
            except Exception:
                log.exception("예약 게시 확인 중 오류")
            if time.time() - self._last_token_check > 3600:  # 토큰 확인은 1시간에 한 번
                self._last_token_check = time.time()
                try:
                    msg = instagram.maybe_refresh_token()
                    if msg:
                        log.info(msg)
                except Exception:
                    log.exception("인스타 토큰 갱신 실패")
            time.sleep(self.interval)
