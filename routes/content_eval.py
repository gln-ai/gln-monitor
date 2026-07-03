"""
routes/content_eval.py — 서포터즈 콘텐츠 품질 평가 라우트
"""
import csv
import io
import json
import threading

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

from db import get_db
from services.content_eval import evaluate, DEFAULT_GUIDELINE
from services.platform_detect import detect_platform, normalize_url

content_eval_bp = Blueprint("content_eval", __name__)

_PLATFORM_LABEL = {
    "youtube":    "유튜브",
    "naver_blog": "네이버 블로그",
    "instagram":  "인스타그램",
    "unknown":    "미확인",
}


def _run_eval_and_save(submission_id: int, submission: dict):
    """별도 스레드에서 평가 실행 후 DB 저장."""
    try:
        result = evaluate(submission)
        conn = get_db()
        conn.execute(
            """INSERT INTO content_scores
               (submission_id, guideline_score, engagement_score, quality_score,
                total_score, safety_status, safety_reason, detail_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                submission_id,
                result["guideline_score"],
                result["engagement_score"],
                result["quality_score"],
                result["total_score"],
                result["safety_status"],
                result["safety_reason"],
                result["detail_json"],
            ),
        )
        conn.commit()
        conn.close()
        print(f"[콘텐츠 평가 완료] #{submission_id} — {result['total_score']}점 {result['safety_status']}")
    except Exception as e:
        print(f"[콘텐츠 평가 오류] #{submission_id}: {e}")


@content_eval_bp.route("/content-eval")
def content_eval_index():
    conn = get_db()
    rows = conn.execute("""
        SELECT s.id, s.name, s.platform, s.url, s.submitted_at,
               sc.guideline_score, sc.engagement_score, sc.quality_score,
               sc.total_score, sc.safety_status, sc.safety_reason, sc.evaluated_at
        FROM content_submissions s
        LEFT JOIN content_scores sc ON sc.submission_id = s.id
        ORDER BY s.submitted_at DESC
        LIMIT 200
    """).fetchall()
    conn.close()
    return render_template(
        "content_eval.html",
        submissions=[dict(r) for r in rows],
        platform_label=_PLATFORM_LABEL,
    )


@content_eval_bp.route("/content-eval/upload", methods=["POST"])
def content_eval_upload():
    f = request.files.get("csv_file")
    if not f or not f.filename:
        flash("CSV 파일을 선택하세요.", "error")
        return redirect(url_for("content_eval.content_eval_index"))

    try:
        content = f.read().decode("utf-8-sig")  # BOM 처리
        reader = csv.DictReader(io.StringIO(content))
        rows_saved = 0
        threads = []

        for row in reader:
            name = (row.get("name") or row.get("이름") or "").strip()
            url  = (row.get("url") or row.get("URL") or "").strip()
            if not name or not url:
                continue

            url      = normalize_url(url)
            platform = detect_platform(url)

            # 수동 입력 stats (인스타용)
            manual_stats = None
            view_c  = (row.get("view_count")  or row.get("조회수")  or "").strip()
            like_c  = (row.get("like_count")  or row.get("좋아요")  or "").strip()
            cmt_c   = (row.get("comment_count") or row.get("댓글수") or "").strip()
            if any([view_c, like_c, cmt_c]):
                manual_stats = json.dumps({
                    "view_count":    int(view_c)  if view_c.isdigit()  else 0,
                    "like_count":    int(like_c)  if like_c.isdigit()  else 0,
                    "comment_count": int(cmt_c)   if cmt_c.isdigit()   else 0,
                }, ensure_ascii=False)

            conn = get_db()
            cur = conn.execute(
                """INSERT INTO content_submissions (name, platform, url, manual_stats)
                   VALUES (?, ?, ?, ?)""",
                (name, platform, url, manual_stats),
            )
            submission_id = cur.lastrowid
            conn.commit()
            conn.close()
            rows_saved += 1

            submission = {
                "platform":     platform,
                "url":          url,
                "manual_stats": manual_stats,
            }
            t = threading.Thread(
                target=_run_eval_and_save,
                args=(submission_id, submission),
                daemon=True,
            )
            t.start()
            threads.append(t)

        flash(f"{rows_saved}건 업로드 완료. 평가가 백그라운드에서 진행 중입니다.", "success")
    except Exception as e:
        flash(f"CSV 파싱 오류: {e}", "error")

    return redirect(url_for("content_eval.content_eval_index"))


@content_eval_bp.route("/content-eval/results")
def content_eval_results():
    sort = request.args.get("sort", "total_score")
    order = request.args.get("order", "desc")
    allowed_sort = {"name", "platform", "total_score", "guideline_score",
                    "engagement_score", "quality_score", "submitted_at"}
    if sort not in allowed_sort:
        sort = "total_score"
    order_sql = "DESC" if order == "desc" else "ASC"

    conn = get_db()
    rows = conn.execute(f"""
        SELECT s.id, s.name, s.platform, s.url, s.submitted_at,
               sc.guideline_score, sc.engagement_score, sc.quality_score,
               sc.total_score, sc.safety_status, sc.safety_reason,
               sc.detail_json, sc.evaluated_at
        FROM content_submissions s
        LEFT JOIN content_scores sc ON sc.submission_id = s.id
        ORDER BY sc.{sort} {order_sql} NULLS LAST
        LIMIT 200
    """).fetchall()
    conn.close()

    submissions = [dict(r) for r in rows]
    top5 = [s for s in submissions
            if s.get("safety_status") == "PASS" and s.get("total_score") is not None]
    top5 = sorted(top5, key=lambda x: x["total_score"], reverse=True)[:5]

    return render_template(
        "content_eval_results.html",
        submissions=submissions,
        top5=top5,
        platform_label=_PLATFORM_LABEL,
        sort=sort,
        order=order,
    )


@content_eval_bp.route("/content-eval/send-report", methods=["POST"])
def content_eval_send_report():
    from services.email_svc import send_content_eval_report
    from db import get_setting
    import os
    to = get_setting("urgent_alert_to_list") or os.getenv("URGENT_ALERT_TO", "")
    ok, msg = send_content_eval_report(to)
    return jsonify({"ok": ok, "message": msg})
