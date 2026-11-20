"""실행: python run.py        → 대시보드 + 예약 게시 스케줄러
       python run.py check  → 연결 상태만 점검
"""
import logging
import os
import sys

from autopost import config, db, instagram
from autopost.publisher import Scheduler
from autopost.web import create_app


def check() -> None:
    db.init_db()
    print("Claude     :", "ANTHROPIC_API_KEY 설정됨" if os.getenv("ANTHROPIC_API_KEY") else "ANTHROPIC_API_KEY 없음")
    if instagram.is_dry_run():
        print("Instagram  : 테스트 모드 (IG_ACCESS_TOKEN 없음 또는 DRY_RUN=1)")
    else:
        try:
            client = instagram.InstagramClient(instagram.get_token())
            print(f"Instagram  : @{client.username} 연결됨")
            limit = client.publishing_limit()
            print(f"             최근 24시간 API 게시 {limit['used']}/{limit['total']}")
        except instagram.InstagramError as e:
            print("Instagram  : 연결 실패 -", e)
    print("Cloudinary :", "설정됨" if config.cloudinary_configured() else "설정 안 됨 (실제 게시에 필요)")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    if len(sys.argv) > 1 and sys.argv[1] == "check":
        check()
        return
    app = create_app()
    db.reset_stuck_publishing()
    Scheduler().start()
    mode = "테스트 모드" if instagram.is_dry_run() else "실제 게시 모드"
    print(f"\n  대시보드: http://{config.HOST}:{config.PORT}  ({mode})\n  종료: Ctrl+C\n")
    app.run(host=config.HOST, port=config.PORT, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
