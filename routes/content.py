"""
routes/content.py — 콘텐츠 라우트 (이원화 채널 지원)
"""
import importlib.util
import json
import os
import sys
import threading
import uuid
from datetime import datetime

from flask import Blueprint, jsonify, render_template, request

from config import KST
from db import get_db, get_setting

# gln-content 경로: Railway(/app/gln-content/) 또는 로컬(../gln-content/)
_ROUTES_DIR   = os.path.dirname(os.path.abspath(__file__))
_MONITOR_DIR  = os.path.dirname(_ROUTES_DIR)
_APPS_ROOT    = os.path.dirname(_MONITOR_DIR)
_GLN_CONTENT  = os.path.join(_MONITOR_DIR, "gln-content") \
                if os.path.isdir(os.path.join(_MONITOR_DIR, "gln-content")) \
                else os.path.join(_APPS_ROOT, "gln-content")
from services.pipeline import generate_single, generate_multi, run_content_pipeline
from routes.monitor import COUNTRY_LABEL, COUNTRY_EMOJI

content_bp = Blueprint("content", __name__)

# 통합 포맷 레이블 → 내부 format 코드 매핑 (방향 A)
FORMAT_MAP = {
    "blog":      ["blog"],
    "instagram": ["instagram_card", "reels", "cartoon"],
    "youtube":   ["youtube_shorts"],
    "threads":   ["threads"],
}

FORMAT_LABEL = {
    "blog":           "블로그",
    "instagram_card": "인스타그램",
    "youtube_shorts": "쇼츠",
    "threads":        "스레드",
    "reels":          "릴스",
    "cartoon":        "웹툰",
}

FORMAT_ICON = {
    "blog":           "📝",
    "instagram_card": "📷",
    "youtube_shorts": "▶",
    "threads":        "@",
    "reels":          "🎬",
    "cartoon":        "🦌",
}

def _worst_grade(grades_csv: str) -> str:
    grades = set((grades_csv or "").split(","))
    for g in ["red", "yellow", "pending", "green"]:
        if g in grades:
            return g
    return "pending"


@content_bp.route("/content")
def content_status():
    conn        = get_db()
    grade       = request.args.get("grade", "")
    channel     = request.args.get("channel", "")
    status      = request.args.get("status", "")      # published / unpublished
    country     = request.args.get("country", "")
    fmt_filter  = request.args.get("format", "")
    date_from   = request.args.get("date_from", "")
    date_to     = request.args.get("date_to", "")
    page        = int(request.args.get("page", 1))
    per_page    = 15

    where = "WHERE deleted_at IS NULL"
    args  = []
    if channel:
        where += " AND channel = ?"; args.append(channel)
    if country:
        where += " AND country = ?"; args.append(country)
    if date_from:
        where += " AND DATE(created_at) >= ?"; args.append(date_from)
    if date_to:
        where += " AND DATE(created_at) <= ?"; args.append(date_to)

    having_parts = []
    having_args  = []
    if grade:
        having_parts.append("SUM(CASE WHEN guard_grade=? THEN 1 ELSE 0 END) > 0")
        having_args.append(grade)
    if fmt_filter:
        having_parts.append("SUM(CASE WHEN format=? THEN 1 ELSE 0 END) > 0")
        having_args.append(fmt_filter)
    if status == "published":
        having_parts.append("SUM(CASE WHEN approval_status='published' THEN 1 ELSE 0 END) > 0")
    elif status == "unpublished":
        having_parts.append("SUM(CASE WHEN approval_status='unpublished' THEN 1 ELSE 0 END) > 0")
    having = ("HAVING " + " AND ".join(having_parts)) if having_parts else ""

    base_sql = f"""
        SELECT
          COALESCE(batch_id, 'solo_' || CAST(id AS TEXT)) AS group_key,
          topic, country, source_type,
          MAX(created_at) AS latest_at,
          COUNT(*) AS total,
          SUM(CASE WHEN guard_grade='green'   THEN 1 ELSE 0 END) AS green_cnt,
          SUM(CASE WHEN guard_grade='yellow'  THEN 1 ELSE 0 END) AS yellow_cnt,
          SUM(CASE WHEN guard_grade='red'     THEN 1 ELSE 0 END) AS red_cnt,
          SUM(CASE WHEN guard_grade='pending' THEN 1 ELSE 0 END) AS pending_cnt,
          SUM(CASE WHEN approval_status='published' THEN 1 ELSE 0 END) AS published_cnt,
          GROUP_CONCAT(format)     AS formats_csv,
          GROUP_CONCAT(guard_grade) AS grades_csv
        FROM content_drafts
        {where}
        GROUP BY group_key
        {having}
        ORDER BY latest_at DESC
    """
    all_args    = args + having_args
    total_groups = conn.execute(f"SELECT COUNT(*) FROM ({base_sql})", all_args).fetchone()[0]
    total_pages  = max(1, (total_groups + per_page - 1) // per_page)
    offset       = (page - 1) * per_page
    groups_raw   = conn.execute(f"{base_sql} LIMIT {per_page} OFFSET {offset}", all_args).fetchall()

    groups = []
    for g in groups_raw:
        row = dict(g)
        row["worst_grade"] = _worst_grade(row.get("grades_csv", ""))
        row["formats_list"] = [
            {"code": f, "label": FORMAT_LABEL.get(f, f), "icon": FORMAT_ICON.get(f, "📄")}
            for f in (row.get("formats_csv") or "").split(",") if f
        ]
        groups.append(row)

    # 전체 통계 (필터 없음)
    stats = dict(conn.execute("""
        SELECT
          COUNT(DISTINCT COALESCE(batch_id, 'solo_' || CAST(id AS TEXT))) AS topic_cnt,
          COUNT(*) AS draft_cnt,
          SUM(CASE WHEN guard_grade='green' THEN 1 ELSE 0 END) AS green_cnt,
          SUM(CASE WHEN approval_status='published' THEN 1 ELSE 0 END) AS published_cnt,
          SUM(CASE WHEN channel='official' THEN 1 ELSE 0 END) AS official_cnt,
          SUM(CASE WHEN channel='gorani'   THEN 1 ELSE 0 END) AS gorani_cnt
        FROM content_drafts WHERE deleted_at IS NULL
    """).fetchone())

    trash_count = conn.execute(
        "SELECT COUNT(*) FROM content_drafts WHERE deleted_at IS NOT NULL"
    ).fetchone()[0]

    # 실제 콘텐츠가 있는 국가 목록 (필터 칩용)
    country_rows = conn.execute(
        "SELECT DISTINCT country FROM content_drafts WHERE deleted_at IS NULL AND country IS NOT NULL ORDER BY country"
    ).fetchall()
    content_countries = [r["country"] for r in country_rows if r["country"]]

    conn.close()

    return render_template(
        "content_status.html",
        groups=groups,
        stats=stats,
        total_groups=total_groups,
        trash_count=trash_count,
        filter_grade=grade,
        filter_channel=channel,
        filter_status=status,
        filter_country=country,
        filter_format=fmt_filter,
        filter_date_from=date_from,
        filter_date_to=date_to,
        page=page,
        total_pages=total_pages,
        country_label=COUNTRY_LABEL,
        country_emoji=COUNTRY_EMOJI,
        content_countries=content_countries,
        format_label=FORMAT_LABEL,
        format_icon=FORMAT_ICON,
        auto_generate_enabled=get_setting("content_auto_generate_enabled", "0") == "1",
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


@content_bp.route("/api/content/group/<path:group_key>")
def api_content_group(group_key):
    """주제 그룹 lazy-load — accordion 클릭 시 해당 그룹의 드래프트 반환."""
    conn = get_db()
    if group_key.startswith("solo_"):
        try:
            draft_id = int(group_key[5:])
            rows = conn.execute(
                "SELECT * FROM content_drafts WHERE id=? AND deleted_at IS NULL",
                (draft_id,)
            ).fetchall()
        except ValueError:
            rows = []
    else:
        rows = conn.execute(
            "SELECT * FROM content_drafts WHERE batch_id=? AND deleted_at IS NULL ORDER BY created_at",
            (group_key,)
        ).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d["format_label"] = FORMAT_LABEL.get(d.get("format", ""), d.get("format", ""))
        d["format_icon"]  = FORMAT_ICON.get(d.get("format", ""), "📄")
        result.append(d)
    return jsonify(result)


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
        bid    = str(uuid.uuid4())[:8]
        result = generate_single(channel, fmt, topic=topic,
                                 country=country, use_auto=use_auto,
                                 batch_id=bid)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@content_bp.route("/api/content/generate-multi", methods=["POST"])
def api_content_generate_multi():
    """원소스 멀티유즈 — 여러 포맷을 한 번에 병렬 생성."""
    data         = request.get_json(silent=True) or {}
    formats      = data.get("formats", [])
    topic        = data.get("topic", "")
    country      = data.get("country", "")
    use_auto     = data.get("use_auto", False)
    requirements = data.get("requirements", "")
    if not formats:
        return jsonify({"error": "formats 필수"}), 400
    try:
        result = generate_multi(formats, topic=topic, country=country,
                                use_auto=use_auto, requirements=requirements)
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


@content_bp.route("/api/content/<int:draft_id>", methods=["PATCH"])
def api_content_update(draft_id):
    """인라인 편집 저장 — 허용 필드: topic, seo_titles, body, shorts_script, verify_list."""
    data    = request.get_json(silent=True) or {}
    allowed = {"topic", "seo_titles", "body", "shorts_script", "verify_list"}
    updates = {k: v for k, v in data.items() if k in allowed and isinstance(v, str)}
    if not updates:
        return jsonify({"error": "변경 없음"}), 400
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    updates["updated_at"] = now
    set_clause = ", ".join(f"{k}=?" for k in updates)
    values     = list(updates.values()) + [draft_id]
    conn = get_db()
    conn.execute(
        f"UPDATE content_drafts SET {set_clause} WHERE id=? AND deleted_at IS NULL",
        values
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


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

    _gln_guard    = os.path.join(_MONITOR_DIR, "gln-guard") if os.path.isdir(os.path.join(_MONITOR_DIR, "gln-guard")) else os.path.join(_APPS_ROOT, "gln-guard")
    _checker_path = os.path.join(_gln_guard, "checker.py")
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
    img_gen_path = os.path.join(_GLN_CONTENT, "image_generator.py")
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
