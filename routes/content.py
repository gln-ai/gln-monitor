"""
routes/content.py — 콘텐츠 라우트 (이원화 채널 지원)
"""
import importlib.util
import json
import os
import sys
import threading
from datetime import datetime

from flask import Blueprint, jsonify, render_template, request

from config import APPS_ROOT, KST
from db import get_db
from services.pipeline import generate_single, run_content_pipeline
from routes.monitor import COUNTRY_LABEL

content_bp = Blueprint("content", __name__)

# 통합 포맷 레이블 → 내부 format 코드 매핑 (방향 A)
FORMAT_MAP = {
    "blog":      ["blog"],
    "instagram": ["instagram_card", "reels", "cartoon"],
    "youtube":   ["youtube_shorts"],
    "threads":   ["threads"],
}


@content_bp.route("/content")
def content_status():
    conn     = get_db()
    grade     = request.args.get("grade", "")
    channel   = request.args.get("channel", "official")
    fmt       = request.args.get("format", "")
    date_from = request.args.get("date_from", "")
    date_to   = request.args.get("date_to", "")
    page      = int(request.args.get("page", 1))
    per_page  = 20
    offset    = (page - 1) * per_page

    where = "WHERE deleted_at IS NULL"
    args  = []
    if grade:
        where += " AND guard_grade = ?"; args.append(grade)
    if channel:
        where += " AND channel = ?"; args.append(channel)
    if fmt:
        codes = FORMAT_MAP.get(fmt)
        if codes and len(codes) > 1:
            placeholders = ",".join("?" * len(codes))
            where += f" AND format IN ({placeholders})"
            args.extend(codes)
        elif codes:
            where += " AND format = ?"; args.append(codes[0])
        else:
            where += " AND format = ?"; args.append(fmt)
    if date_from:
        where += " AND DATE(created_at) >= ?"; args.append(date_from)
    if date_to:
        where += " AND DATE(created_at) <= ?"; args.append(date_to)

    total       = conn.execute(f"SELECT COUNT(*) FROM content_drafts {where}", args).fetchone()[0]
    total_pages = max(1, (total + per_page - 1) // per_page)

    drafts = conn.execute(
        f"SELECT * FROM content_drafts {where} ORDER BY created_at DESC LIMIT {per_page} OFFSET {offset}",
        args
    ).fetchall()

    grade_counts = {}
    for g in ["green", "yellow", "red", "pending"]:
        grade_counts[g] = conn.execute(
            "SELECT COUNT(*) FROM content_drafts WHERE guard_grade = ? AND deleted_at IS NULL", (g,)
        ).fetchone()[0]

    channel_counts = {}
    for ch in ["official", "gorani"]:
        channel_counts[ch] = conn.execute(
            "SELECT COUNT(*) FROM content_drafts WHERE channel = ? AND deleted_at IS NULL", (ch,)
        ).fetchone()[0]

    published_count = conn.execute(
        "SELECT COUNT(*) FROM content_drafts WHERE approval_status = 'published' AND deleted_at IS NULL"
    ).fetchone()[0]
    trash_count = conn.execute(
        "SELECT COUNT(*) FROM content_drafts WHERE deleted_at IS NOT NULL"
    ).fetchone()[0]
    conn.close()

    return render_template(
        "content_status.html",
        drafts=drafts,
        grade_counts=grade_counts,
        channel_counts=channel_counts,
        published_count=published_count,
        trash_count=trash_count,
        filter_grade=grade,
        filter_channel=channel,
        filter_format=fmt,
        filter_date_from=date_from,
        filter_date_to=date_to,
        page=page,
        total_pages=total_pages,
        total=total,
        country_label=COUNTRY_LABEL,
    )


@content_bp.route("/content/trash")
def content_trash():
    conn     = get_db()
    page     = int(request.args.get("page", 1))
    per_page = 20
    offset   = (page - 1) * per_page
    total    = conn.execute("SELECT COUNT(*) FROM content_drafts WHERE deleted_at IS NOT NULL").fetchone()[0]
    total_pages = max(1, (total + per_page - 1) // per_page)
    drafts   = conn.execute(
        f"SELECT * FROM content_drafts WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC LIMIT {per_page} OFFSET {offset}"
    ).fetchall()
    conn.close()
    return render_template("content_trash.html",
        drafts=drafts, total=total, total_pages=total_pages, page=page, country_label=COUNTRY_LABEL)


@content_bp.route("/content/create")
def content_create():
    """작성 설정 페이지 — 채널/포맷/토픽 선택 후 동기 생성."""
    conn    = get_db()
    briefs  = conn.execute("""
        SELECT p.id, p.title, a.summary, a.importance_score
        FROM posts p
        JOIN ai_analysis a ON p.id = a.post_id
        WHERE a.importance_score >= 5 AND p.is_processed = 1
        ORDER BY a.importance_score DESC
        LIMIT 5
    """).fetchall()
    conn.close()
    return render_template("content_create.html", briefs=briefs)


@content_bp.route("/api/content/generate", methods=["POST"])
def api_content_generate():
    """동기 단건 생성 — /content/create 페이지에서 호출."""
    data      = request.get_json(silent=True) or {}
    channel   = data.get("channel", "official")
    fmt       = data.get("format", "blog")
    topic     = data.get("topic", "")
    country   = data.get("country", "")
    use_auto  = data.get("use_auto", False)
    try:
        result = generate_single(channel, fmt, topic=topic,
                                 country=country, use_auto=use_auto)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@content_bp.route("/api/content/run", methods=["POST"])
def api_content_run():
    data    = request.get_json(silent=True) or {}
    channel = data.get("channel")
    formats = data.get("formats")
    threading.Thread(
        target=run_content_pipeline,
        kwargs={"channel": channel, "formats": formats},
        daemon=True
    ).start()
    ch_label = f"{channel} 채널" if channel else "전체 채널"
    return jsonify({"status": f"{ch_label} 콘텐츠 파이프라인 시작됨"})


@content_bp.route("/api/content/<int:draft_id>")
def api_content_detail(draft_id):
    conn = get_db()
    row  = conn.execute("SELECT * FROM content_drafts WHERE id=?", (draft_id,)).fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "not found"}), 404
    return jsonify(dict(row))


@content_bp.route("/api/content/<int:draft_id>/publish", methods=["POST"])
def api_content_publish(draft_id):
    now  = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    conn = get_db()
    conn.execute(
        "UPDATE content_drafts SET approval_status='published', approved_at=?, updated_at=? WHERE id=?",
        (now, now, draft_id)
    )
    conn.commit()
    conn.close()
    return jsonify({"status": "published", "published_at": now})


@content_bp.route("/api/content/<int:draft_id>/unpublish", methods=["POST"])
def api_content_unpublish(draft_id):
    now  = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    conn = get_db()
    conn.execute(
        "UPDATE content_drafts SET approval_status='unpublished', approved_at=NULL, updated_at=? WHERE id=?",
        (now, draft_id)
    )
    conn.commit()
    conn.close()
    return jsonify({"status": "unpublished"})


@content_bp.route("/api/content/<int:draft_id>/email", methods=["POST"])
def api_content_email(draft_id):
    """발행 패키지 이메일 즉시 발송."""
    conn = get_db()
    row  = conn.execute("SELECT * FROM content_drafts WHERE id=?", (draft_id,)).fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "not found"}), 404

    _checker_path = os.path.join(APPS_ROOT, "gln-guard", "checker.py")
    spec = importlib.util.spec_from_file_location("checker", _checker_path)
    mod  = importlib.util.module_from_spec(spec)
    sys.modules["checker"] = mod
    spec.loader.exec_module(mod)
    ok = mod.send_publish_package_email(dict(row))
    if ok:
        return jsonify({"status": "발송 완료"})
    return jsonify({"status": "SMTP 미설정 — 콘솔 출력"}), 200


@content_bp.route("/api/content/<int:draft_id>", methods=["DELETE"])
def api_content_delete(draft_id):
    now  = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db()
    conn.execute("UPDATE content_drafts SET deleted_at=? WHERE id=?", (now, draft_id))
    conn.commit()
    conn.close()
    return jsonify({"status": "deleted"})


@content_bp.route("/api/content/bulk-delete", methods=["POST"])
def api_content_bulk_delete():
    ids = request.json.get("ids", [])
    if not ids:
        return jsonify({"deleted": 0})
    now          = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    placeholders = ",".join("?" * len(ids))
    conn = get_db()
    conn.execute(f"UPDATE content_drafts SET deleted_at=? WHERE id IN ({placeholders})", [now] + ids)
    conn.commit()
    conn.close()
    return jsonify({"deleted": len(ids)})


@content_bp.route("/api/content/<int:draft_id>/restore", methods=["POST"])
def api_content_restore(draft_id):
    conn = get_db()
    conn.execute("UPDATE content_drafts SET deleted_at=NULL WHERE id=?", (draft_id,))
    conn.commit()
    conn.close()
    return jsonify({"status": "restored"})


@content_bp.route("/api/content/bulk-restore", methods=["POST"])
def api_content_bulk_restore():
    ids = request.json.get("ids", [])
    if not ids:
        return jsonify({"restored": 0})
    placeholders = ",".join("?" * len(ids))
    conn = get_db()
    conn.execute(f"UPDATE content_drafts SET deleted_at=NULL WHERE id IN ({placeholders})", ids)
    conn.commit()
    conn.close()
    return jsonify({"restored": len(ids)})


@content_bp.route("/api/content/<int:draft_id>/permanent", methods=["DELETE"])
def api_content_permanent_delete(draft_id):
    conn = get_db()
    conn.execute("DELETE FROM content_drafts WHERE id=?", (draft_id,))
    conn.commit()
    conn.close()
    return jsonify({"status": "deleted"})


@content_bp.route("/api/content/empty-trash", methods=["POST"])
def api_content_empty_trash():
    conn = get_db()
    n    = conn.execute("SELECT COUNT(*) FROM content_drafts WHERE deleted_at IS NOT NULL").fetchone()[0]
    conn.execute("DELETE FROM content_drafts WHERE deleted_at IS NOT NULL")
    conn.commit()
    conn.close()
    return jsonify({"deleted": n})


@content_bp.route("/api/content/<int:draft_id>/generate-images", methods=["POST"])
def api_content_generate_images(draft_id):
    """
    이미지 생성 API — 해당 콘텐츠의 텍스트를 기반으로 DALL·E 이미지 생성.
    지원 포맷: instagram_card (슬라이드별), cartoon (컷별)
    """
    conn = get_db()
    row  = conn.execute("SELECT * FROM content_drafts WHERE id=?", (draft_id,)).fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "not found"}), 404

    draft  = dict(row)
    fmt    = draft.get("format", "")
    country = draft.get("country", "")
    topic  = draft.get("topic", "")

    if fmt not in ("instagram_card", "cartoon"):
        return jsonify({"error": f"이미지 생성은 instagram_card, cartoon 포맷만 지원합니다. 현재: {fmt}"}), 400

    # image_generator 동적 로드
    img_gen_path = os.path.join(APPS_ROOT, "gln-content", "image_generator.py")
    spec = importlib.util.spec_from_file_location("image_generator", img_gen_path)
    img_gen = importlib.util.module_from_spec(spec)
    sys.modules["image_generator"] = img_gen
    try:
        spec.loader.exec_module(img_gen)
    except Exception as e:
        return jsonify({"error": f"image_generator 로드 실패: {e}"}), 500

    try:
        if fmt == "instagram_card":
            # raw_output에서 <SLIDES> 내용 추출
            raw = draft.get("raw_output", "")
            import re
            m = re.search(r"<SLIDES>(.*?)</SLIDES>", raw, re.DOTALL)
            slides_text = m.group(1).strip() if m else draft.get("body", "")
            paths = img_gen.generate_instagram_images(
                draft_id=draft_id,
                slides_text=slides_text,
                topic=topic,
                country=country,
            )
        else:  # cartoon
            raw = draft.get("raw_output", "")
            import re
            m = re.search(r"<CUTS>(.*?)</CUTS>", raw, re.DOTALL)
            cuts_text = m.group(1).strip() if m else draft.get("body", "")
            paths = img_gen.generate_cartoon_images(
                draft_id=draft_id,
                cuts_text=cuts_text,
                country=country,
            )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    if not paths:
        return jsonify({"error": "이미지 생성 결과 없음 — 로그를 확인하세요."}), 500

    # DB 저장
    paths_json = json.dumps(paths, ensure_ascii=False)
    conn = get_db()
    conn.execute(
        "UPDATE content_drafts SET image_paths=?, updated_at=? WHERE id=?",
        (paths_json, datetime.now(KST).strftime("%Y-%m-%d %H:%M"), draft_id)
    )
    conn.commit()
    conn.close()

    return jsonify({"status": "ok", "paths": paths, "count": len(paths)})
