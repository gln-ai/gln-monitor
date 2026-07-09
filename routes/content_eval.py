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


def _build_explanation(s: dict) -> dict:
    """detail_json → 각 항목 한 줄 설명 dict 반환."""
    if s.get("total_score") is None:
        return {}
    try:
        detail = json.loads(s.get("detail_json") or "{}")
    except Exception:
        return {}

    g = detail.get("guideline", {})
    e = detail.get("engagement", {})
    platform = s.get("platform", "")

    # 가이드 준수 설명
    kw = g.get("keyword_found") or []
    kw_str = " · ".join(kw) if kw else "키워드 없음"
    link_ok = "링크✓" if g.get("has_link_or_tag") else "링크✗"
    length_ok = "분량✓" if g.get("length_ok") else "분량✗"
    g_text = f"{kw_str} | {link_ok} | {length_ok}"

    # 참여도 설명
    if platform == "youtube":
        views = e.get("view_count", 0) or 0
        rate  = (e.get("eng_rate") or 0) * 100
        v_str = f"{views:,}" if views >= 1000 else str(views)
        e_text = f"조회 {v_str} · 참여율 {rate:.1f}%"
    elif platform == "naver_blog":
        img = e.get("image_count", 0) or 0
        e_text = f"이미지 {img}장 기준"
    else:  # instagram
        ms = e.get("manual_stats") or {}
        if ms:
            likes = int(ms.get("like_count", 0) or 0)
            cmts  = int(ms.get("comment_count", 0) or 0)
            e_text = f"좋아요 {likes:,} · 댓글 {cmts}"
        else:
            e_text = "수동 입력 없음"

    # 품질 설명
    reason = (s.get("safety_reason") or "").strip()
    if s.get("safety_status") == "PASS":
        q_text = "품질 양호"
    else:
        q_text = reason if reason else "부적절 콘텐츠 감지"

    return {"guideline": g_text, "engagement": e_text, "quality": q_text}


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
    sort    = request.args.get("sort", "submitted_at")
    order   = request.args.get("order", "desc")
    status  = request.args.get("status", "all")      # all/pass/fail/pending
    pf      = request.args.get("platform", "all")    # all/youtube/naver_blog/instagram

    proj    = request.args.get("project", "all")

    _allowed = {"name", "platform", "total_score", "guideline_score",
                "engagement_score", "quality_score", "submitted_at", "star", "project"}
    if sort not in _allowed:
        sort = "submitted_at"
    order_sql = "DESC" if order == "desc" else "ASC"
    order_col = f"s.{sort}" if sort in ("star", "submitted_at", "name", "platform", "project") else f"sc.{sort}"
    project_order_sql = order_sql if sort == "project" else "ASC"

    where_parts, params = [], []
    if status == "pass":
        where_parts.append("sc.safety_status = 'PASS'")
    elif status == "fail":
        where_parts.append("sc.safety_status = 'FAIL'")
    elif status == "pending":
        where_parts.append("sc.total_score IS NULL")
    if pf != "all":
        where_parts.append("s.platform = ?")
        params.append(pf)
    if proj != "all":
        where_parts.append("s.project = ?")
        params.append(proj)
    where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

    conn = get_db()
    rows = conn.execute(f"""
        SELECT s.id, s.name, s.platform, s.url, s.submitted_at,
               s.memo, s.star, s.manual_stats, s.project,
               sc.guideline_score, sc.engagement_score, sc.quality_score,
               sc.total_score, sc.safety_status, sc.safety_reason,
               sc.detail_json, sc.evaluated_at
        FROM content_submissions s
        LEFT JOIN content_scores sc ON sc.submission_id = s.id
        {where_sql}
        ORDER BY s.project {project_order_sql}, {order_col} {order_sql} NULLS LAST
        LIMIT 200
    """, params).fetchall()
    projects = [r[0] for r in conn.execute(
        "SELECT DISTINCT project FROM content_submissions WHERE project != '' ORDER BY project"
    ).fetchall()]
    conn.close()

    submissions = [dict(r) for r in rows]
    for s in submissions:
        s["explanation"] = _build_explanation(s)
        ms = {}
        try:
            ms = json.loads(s.get("manual_stats") or "{}")
        except Exception:
            pass
        s["manual_view"]    = int(ms.get("view_count", 0) or 0)
        s["manual_like"]    = int(ms.get("like_count", 0) or 0)
        s["manual_comment"] = int(ms.get("comment_count", 0) or 0)

    return render_template(
        "content_eval.html",
        submissions=submissions,
        platform_label=_PLATFORM_LABEL,
        sort=sort, order=order, status=status, platform_filter=pf,
        projects=projects, project_filter=proj,
    )


@content_eval_bp.route("/content-eval/submissions/<int:sub_id>/memo", methods=["POST"])
def content_eval_memo(sub_id):
    memo = request.json.get("memo", "")
    conn = get_db()
    conn.execute("UPDATE content_submissions SET memo=? WHERE id=?", (memo, sub_id))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@content_eval_bp.route("/content-eval/submissions/<int:sub_id>/star", methods=["POST"])
def content_eval_star(sub_id):
    star = max(0, min(5, int(request.json.get("star", 0))))
    conn = get_db()
    conn.execute("UPDATE content_submissions SET star=? WHERE id=?", (star, sub_id))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@content_eval_bp.route("/content-eval/submissions/<int:sub_id>/edit", methods=["POST"])
def content_eval_edit(sub_id):
    data        = request.json or {}
    name        = data.get("name", "").strip()
    url         = normalize_url(data.get("url", "").strip())
    project     = data.get("project", "").strip()
    reevaluate  = bool(data.get("reevaluate", False))
    view_c      = data.get("view_count", 0)
    like_c      = data.get("like_count", 0)
    comment_c   = data.get("comment_count", 0)

    if not name or not url:
        return jsonify({"ok": False, "message": "이름과 URL은 필수입니다"})

    platform = detect_platform(url)
    manual_stats = None
    if any([view_c, like_c, comment_c]):
        manual_stats = json.dumps(
            {"view_count": int(view_c), "like_count": int(like_c), "comment_count": int(comment_c)},
            ensure_ascii=False,
        )

    conn = get_db()
    conn.execute(
        "UPDATE content_submissions SET name=?, url=?, platform=?, manual_stats=?, project=? WHERE id=?",
        (name, url, platform, manual_stats, project, sub_id),
    )
    if reevaluate:
        conn.execute("DELETE FROM content_scores WHERE submission_id=?", (sub_id,))
    conn.commit()
    conn.close()

    if reevaluate:
        t = threading.Thread(
            target=_run_eval_and_save,
            args=(sub_id, {"platform": platform, "url": url, "manual_stats": manual_stats}),
            daemon=True,
        )
        t.start()

    return jsonify({"ok": True})


@content_eval_bp.route("/content-eval/submissions/delete", methods=["POST"])
def content_eval_delete():
    ids = [int(i) for i in (request.json or {}).get("ids", []) if str(i).isdigit()]
    if not ids:
        return jsonify({"ok": False, "message": "선택된 항목이 없습니다"})
    ph = ",".join("?" * len(ids))
    conn = get_db()
    conn.execute(f"DELETE FROM content_scores WHERE submission_id IN ({ph})", ids)
    conn.execute(f"DELETE FROM content_submissions WHERE id IN ({ph})", ids)
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "deleted": len(ids)})


@content_eval_bp.route("/content-eval/submissions/<int:sub_id>")
def content_eval_detail(sub_id):
    conn = get_db()
    row = conn.execute("""
        SELECT s.id, s.name, s.platform, s.url, s.submitted_at,
               s.memo, s.star, s.manual_stats, s.project,
               sc.guideline_score, sc.engagement_score, sc.quality_score,
               sc.total_score, sc.safety_status, sc.safety_reason,
               sc.detail_json, sc.evaluated_at
        FROM content_submissions s
        LEFT JOIN content_scores sc ON sc.submission_id = s.id
        WHERE s.id = ?
    """, (sub_id,)).fetchone()
    conn.close()
    if not row:
        return redirect(url_for("content_eval.content_eval_index"))
    s = dict(row)
    s["explanation"] = _build_explanation(s)
    ms = {}
    try:
        ms = json.loads(s.get("manual_stats") or "{}")
    except Exception:
        pass
    s["manual_view"]    = int(ms.get("view_count", 0) or 0)
    s["manual_like"]    = int(ms.get("like_count", 0) or 0)
    s["manual_comment"] = int(ms.get("comment_count", 0) or 0)
    return render_template("content_eval_detail.html", s=s, platform_label=_PLATFORM_LABEL)


@content_eval_bp.route("/content-eval/export")
def content_eval_export():
    import io as _io
    conn = get_db()
    rows = conn.execute("""
        SELECT s.project, s.name, s.platform, s.url, s.star, s.memo, s.submitted_at,
               sc.guideline_score, sc.engagement_score, sc.quality_score,
               sc.total_score, sc.safety_status, sc.safety_reason
        FROM content_submissions s
        LEFT JOIN content_scores sc ON sc.submission_id = s.id
        ORDER BY s.submitted_at DESC
    """).fetchall()
    conn.close()

    buf = _io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["프로젝트", "이름", "플랫폼", "URL", "별점", "메모", "제출일",
                     "가이드점수", "참여도점수", "품질점수", "총점", "결과", "사유"])
    for r in rows:
        writer.writerow([
            r["project"] or "", r["name"], _PLATFORM_LABEL.get(r["platform"], r["platform"]), r["url"],
            r["star"] or 0, r["memo"] or "", (r["submitted_at"] or "")[:16],
            r["guideline_score"] or "", r["engagement_score"] or "",
            r["quality_score"] or "", r["total_score"] or "",
            r["safety_status"] or "대기", r["safety_reason"] or "",
        ])

    from flask import Response
    return Response(
        "﻿" + buf.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=content_eval_result.csv"},
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

            project = (row.get("project") or row.get("프로젝트") or "").strip() or "중국퍼플크리에이터"
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
                """INSERT INTO content_submissions (name, platform, url, manual_stats, project)
                   VALUES (?, ?, ?, ?, ?)""",
                (name, platform, url, manual_stats, project),
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
