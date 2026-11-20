"""사장님이 브라우저로 쓰는 대시보드 (Flask)."""
import os
import re
import uuid
from datetime import datetime
from pathlib import Path

from flask import (Flask, abort, flash, jsonify, redirect, render_template,
                   request, send_from_directory, url_for)

from . import ai, config, db, images, instagram, publisher

MAX_PHOTOS = 10
STATUS_LABELS = {
    "draft": "검토 대기",
    "approved": "예약됨",
    "published": "게시됨",
    "failed": "실패",
}
WEEKDAYS = "월화수목금토일"


def pretty_time(value: str | None) -> str:
    if not value:
        return ""
    try:
        dt = datetime.strptime(value[:16], db.TIME_FMT)
    except ValueError:
        return value
    return f"{dt.month}/{dt.day}({WEEKDAYS[dt.weekday()]}) {dt:%H:%M}"


def create_app() -> Flask:
    db.init_db()
    app = Flask(__name__)
    app.secret_key = config.SECRET_KEY
    app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024
    app.jinja_env.filters["pretty_time"] = pretty_time

    @app.context_processor
    def inject_globals():
        counts = db.count_by_status()
        counts["approved"] = counts.get("approved", 0) + counts.get("publishing", 0)
        return {"dry_run": instagram.is_dry_run(), "counts": counts, "STATUS_LABELS": STATUS_LABELS}

    # ------------------------------------------------------------ 게시물 목록
    @app.get("/")
    def index():
        tab = request.args.get("tab", "draft")
        if tab not in STATUS_LABELS:
            tab = "draft"
        posts = db.list_posts(tab)
        if tab == "approved":
            posts = db.list_posts("publishing") + posts
        return render_template("index.html", tab=tab, posts=posts)

    @app.get("/media/<path:filename>")
    def media(filename):
        return send_from_directory(config.PROCESSED_DIR, filename)

    # ------------------------------------------------------------ 새 게시물
    @app.route("/new", methods=["GET", "POST"])
    def new_post():
        if request.method == "GET":
            return render_template("new.html", max_photos=MAX_PHOTOS)

        files = [f for f in request.files.getlist("photos") if f and f.filename]
        memo = request.form.get("memo", "").strip()
        if not files:
            flash("사진을 한 장 이상 올려 주세요.", "error")
            return redirect(url_for("new_post"))
        if len(files) > MAX_PHOTOS:
            flash(f"사진은 한 번에 {MAX_PHOTOS}장까지 올릴 수 있어요.", "error")
            return redirect(url_for("new_post"))

        saved = []
        for f in files:
            ext = Path(f.filename).suffix.lower()
            if ext not in images.ALLOWED_EXT:
                flash(f"지원하지 않는 파일이에요: {f.filename} (jpg, png, webp, heic만 가능)", "error")
                return redirect(url_for("new_post"))
            dest = config.UPLOAD_DIR / f"{uuid.uuid4().hex}{ext}"
            f.save(dest)
            saved.append(dest)

        brand = config.load_brand()
        try:
            names = images.prepare_images(saved, config.PROCESSED_DIR, brand.get("image_fit", "crop"))
        except Exception as e:
            flash(f"사진을 처리하지 못했어요: {e}", "error")
            return redirect(url_for("new_post"))

        post_id = db.create_post(names, memo)
        try:
            caption, tags = ai.generate_post([config.PROCESSED_DIR / n for n in names], memo, brand)
            db.update_post(post_id, caption=caption, hashtags=" ".join(tags))
            flash("AI가 초안을 썼어요. 내용을 확인하고 예약해 주세요.", "ok")
        except ai.AIError as e:
            db.update_post(post_id, caption=memo, hashtags=" ".join(ai.normalize_hashtags([], brand)))
            flash(f"{e} (일단 메모를 본문에 넣어 뒀어요)", "error")
        return redirect(url_for("edit_post", post_id=post_id))

    # ------------------------------------------------------------ 편집 / 승인 / 게시
    @app.get("/post/<int:post_id>")
    def edit_post(post_id):
        post = db.get_post(post_id) or abort(404)
        suggested = post["scheduled_at"] or publisher.next_free_slot(config.load_brand())
        return render_template("post.html", post=post, suggested_time=suggested)

    @app.post("/post/<int:post_id>")
    def update_post(post_id):
        post = db.get_post(post_id) or abort(404)
        action = request.form.get("action", "save")
        caption = request.form.get("caption", post["caption"]).replace("\r\n", "\n")
        hashtags = " ".join(request.form.get("hashtags", post["hashtags"]).split())
        scheduled_at = request.form.get("scheduled_at", "").strip()

        if action == "delete":
            if post["status"] == "publishing":
                flash("게시 중인 글은 지울 수 없어요.", "error")
                return redirect(url_for("edit_post", post_id=post_id))
            db.delete_post(post_id)
            note = " (인스타에 올라간 글은 그대로 있어요)" if post["status"] == "published" else ""
            flash("삭제했어요." + note, "ok")
            return redirect(url_for("index", tab=post["status"] if post["status"] in STATUS_LABELS else "draft"))

        if post["status"] not in ("draft", "approved", "failed"):
            flash("게시 중이거나 이미 게시된 글은 고칠 수 없어요.", "error")
            return redirect(url_for("edit_post", post_id=post_id))

        if action == "save":
            db.update_post(post_id, caption=caption, hashtags=hashtags)
            flash("저장했어요.", "ok")

        elif action == "regenerate":
            instruction = request.form.get("instruction", "").strip()
            try:
                new_caption, tags = ai.generate_post(
                    [config.PROCESSED_DIR / n for n in post["images"]], post["memo"],
                    config.load_brand(), instruction=instruction, previous_caption=caption,
                )
                db.update_post(post_id, caption=new_caption, hashtags=" ".join(tags))
                flash("AI가 다시 썼어요.", "ok")
            except ai.AIError as e:
                db.update_post(post_id, caption=caption, hashtags=hashtags)
                flash(str(e), "error")

        elif action == "approve":
            scheduled_at = scheduled_at or publisher.next_free_slot(config.load_brand())
            try:
                datetime.strptime(scheduled_at, db.TIME_FMT)
            except ValueError:
                flash("예약 시간 형식이 올바르지 않아요.", "error")
                return redirect(url_for("edit_post", post_id=post_id))
            db.update_post(post_id, caption=caption, hashtags=hashtags,
                           status="approved", scheduled_at=scheduled_at, error=None)
            when = "지난 시간이라 1분 안에 바로" if scheduled_at <= db.now_str() else pretty_time(scheduled_at) + "에"
            flash(f"{when} 게시되도록 예약했어요.", "ok")
            return redirect(url_for("index", tab="approved"))

        elif action == "unapprove":
            db.update_post(post_id, caption=caption, hashtags=hashtags, status="draft")
            flash("예약을 취소하고 검토 대기로 돌렸어요.", "ok")

        elif action == "publish_now":
            db.update_post(post_id, caption=caption, hashtags=hashtags, scheduled_at=db.now_str())
            if db.mark_publishing(post_id, ("draft", "approved", "failed")):
                publisher.publish_in_background(post_id)
                flash("게시를 시작했어요. 잠시 후 '게시됨' 탭을 확인하세요.", "ok")
            return redirect(url_for("index", tab="approved"))

        return redirect(url_for("edit_post", post_id=post_id))

    @app.get("/api/stats/<int:post_id>")
    def api_stats(post_id):
        post = db.get_post(post_id)
        if not post or not post["ig_media_id"]:
            return jsonify(error="게시 기록이 없어요"), 404
        try:
            return jsonify(instagram.get_client().get_media_stats(post["ig_media_id"]))
        except instagram.InstagramError as e:
            return jsonify(error=str(e)), 502

    # ------------------------------------------------------------ 댓글 관리
    @app.get("/comments")
    def comments():
        client = instagram.get_client()
        items, username = [], ""
        try:
            username = client.username
            for m in client.list_media(limit=10):
                if not m.get("comments_count"):
                    continue
                cs = client.get_comments(m["id"])
                for c in cs:
                    c["answered"] = c.get("username") == username or any(
                        r.get("username") == username for r in c["replies"]
                    )
                items.append({"media": m, "comments": cs,
                              "unanswered": sum(not c["answered"] for c in cs)})
        except instagram.InstagramError as e:
            flash(f"댓글을 불러오지 못했어요: {e}", "error")
        items.sort(key=lambda it: it["unanswered"], reverse=True)
        return render_template("comments.html", items=items, username=username)

    @app.post("/api/suggest-reply")
    def api_suggest_reply():
        data = request.get_json(silent=True) or {}
        try:
            reply = ai.suggest_reply(data.get("text", ""), data.get("username", ""),
                                     data.get("caption", ""), config.load_brand())
            return jsonify(reply=reply)
        except ai.AIError as e:
            return jsonify(error=str(e)), 502

    @app.post("/comments/<comment_id>/reply")
    def reply_comment(comment_id):
        message = request.form.get("message", "").strip()
        if not message:
            flash("답글 내용을 입력해 주세요.", "error")
        else:
            try:
                instagram.get_client().reply_to_comment(comment_id, message)
                flash("답글을 달았어요.", "ok")
            except instagram.InstagramError as e:
                flash(f"답글을 달지 못했어요: {e}", "error")
        return redirect(url_for("comments"))

    # ------------------------------------------------------------ 설정
    @app.route("/settings", methods=["GET", "POST"])
    def settings():
        brand = config.load_brand()
        if request.method == "POST":
            for key in ("business_name", "industry", "location", "tone", "emoji",
                        "call_to_action", "avoid"):
                brand[key] = request.form.get(key, "").strip()
            brand["fixed_hashtags"] = ai.normalize_hashtags(
                re.split(r"[\s,]+", request.form.get("fixed_hashtags", "")), {"max_hashtags": 30}
            )
            try:
                brand["max_hashtags"] = max(0, min(30, int(request.form.get("max_hashtags", 15))))
            except ValueError:
                pass
            times = [t.strip() for t in request.form.get("posting_times", "").split(",") if t.strip()]
            valid = [t for t in times if re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", t)]
            brand["posting_times"] = [f"{int(t.split(':')[0]):02d}:{t.split(':')[1]}" for t in valid]
            brand["image_fit"] = "pad" if request.form.get("image_fit") == "pad" else "crop"
            config.save_brand(brand)
            flash("가게 정보를 저장했어요. 다음 AI 초안부터 반영돼요.", "ok")
            return redirect(url_for("settings"))

        status = {
            "claude": bool(os.getenv("ANTHROPIC_API_KEY")),
            "cloudinary": config.cloudinary_configured(),
            "ig_dry_run": instagram.is_dry_run(),
            "ig_username": None, "ig_error": None, "ig_limit": None,
        }
        if not status["ig_dry_run"]:
            try:
                client = instagram.get_client()
                status["ig_username"] = client.username
                status["ig_limit"] = client.publishing_limit()
            except instagram.InstagramError as e:
                status["ig_error"] = str(e)
        return render_template("settings.html", brand=brand, status=status,
                               model=config.CLAUDE_MODEL)

    return app
