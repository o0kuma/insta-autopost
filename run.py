"""실행: python run.py        → 대시보드 + 예약 게시 스케줄러
       python run.py check  → 연결 상태만 점검
"""
import logging
import os
import sys

from waitress import serve

from autopost import config, db, hosting, instagram
from autopost.publisher import Scheduler
from autopost.web import create_app


def check() -> None:
    db.init_db()
    print("로그인     :", "비밀번호 설정됨" if config.DASHBOARD_PASSWORD else "비밀번호 없음 (이 PC 전용으로만 쓰세요)")
    print("Claude     :", "ANTHROPIC_API_KEY 설정됨" if os.getenv("ANTHROPIC_API_KEY") else "ANTHROPIC_API_KEY 없음")
    print("공개 주소  :", config.PUBLIC_BASE_URL or "없음")
    print("연결 버튼  :", "사용 가능" if instagram.oauth_configured() and config.PUBLIC_BASE_URL
          else "사용 불가 (IG_APP_ID / IG_APP_SECRET / 공개 주소 필요)")
    if instagram.is_dry_run():
        print("Instagram  : 테스트 모드 (연결 안 됨 또는 DRY_RUN=1)")
    else:
        try:
            client = instagram.InstagramClient(instagram.get_token())
            print(f"Instagram  : @{client.username} 연결됨")
            limit = client.publishing_limit()
            print(f"             최근 24시간 API 게시 {limit['used']}/{limit['total']}")
        except instagram.InstagramError as e:
            print("Instagram  : 연결 실패 -", e)
    print("사진 주소  :", {"self": "이 서버가 직접 제공", "cloudinary": "Cloudinary",
                          "none": "없음 (실제 게시에 필요)"}[hosting.mode()])
    print("시간대     :", config.TIMEZONE, db.now_str())


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if len(sys.argv) > 1 and sys.argv[1] == "check":
        check()
        return
    if not config.DASHBOARD_PASSWORD and not config.is_local_host():
        print(f"[중단] HOST={config.HOST} 로 외부에 공개하려면 DASHBOARD_PASSWORD를 꼭 설정하세요.")
        sys.exit(1)
    app = create_app()
    db.reset_stuck_publishing()
    Scheduler().start()
    mode = "테스트 모드" if instagram.is_dry_run() else "실제 게시 모드"
    shown = config.PUBLIC_BASE_URL or f"http://{config.HOST}:{config.PORT}"
    print(f"\n  대시보드: {shown}  ({mode})\n  종료: Ctrl+C\n", flush=True)
    serve(app, host=config.HOST, port=config.PORT, threads=8)


if __name__ == "__main__":
    main()
