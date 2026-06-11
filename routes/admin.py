import json
import os
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, make_response, render_template, request

from config import APPS_ROOT, KST
from db import get_db
from routes.monitor import COUNTRY_EMOJI, COUNTRY_LABEL

admin_bp = Blueprint("admin", __name__)

_FACT_DB_PATH = os.path.join(APPS_ROOT, "shared", "fact_db.json")
# Railway 백업 경로 (shared가 상위에 없으면 로컬 복사본)
_FACT_DB_ALT  = os.path.join(os.path.dirname(APPS_ROOT), "shared", "fact_db.json")


def _load_fact_db() -> dict:
    path = _FACT_DB_PATH if os.path.exists(_FACT_DB_PATH) else _FACT_DB_ALT
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save_fact_db(data: dict):
    path = _FACT_DB_PATH if os.path.exists(_FACT_DB_PATH) else _FACT_DB_ALT
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


@admin_bp.route("/admin/fact-checker")
def fact_checker():
    resp = make_response(render_template("fact_checker.html"))
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@admin_bp.route("/admin/agent-map")
def agent_map():
    return render_template("agent_map.html")


# ── 팩트체커 → fact_db.json 저장 ─────────────────────────────────────

@admin_bp.route("/api/admin/fact-db/update", methods=["POST"])
def api_fact_db_update():
    """팩트체커 UI에서 변경한 국가/앱 상태를 fact_db.json에 반영."""
    data = request.get_json(silent=True) or {}
    countries_payload = data.get("countries", [])
    apps_payload      = data.get("apps", [])

    if not countries_payload and not apps_payload:
        return jsonify({"error": "변경 데이터 없음"}), 400

    try:
        fdb = _load_fact_db()
    except Exception as e:
        return jsonify({"error": f"fact_db.json 로드 실패: {e}"}), 500

    # 한국어 국가명 → 영문 코드 역매핑
    ko_to_code = {v: k for k, v in COUNTRY_LABEL.items()}
    # COUNTRY_LABEL에 없는 이름은 fact_db countries의 name_ko로도 시도
    for code, info in fdb.get("countries", {}).items():
        ko_to_code.setdefault(info.get("name_ko", ""), code)

    changed_countries, changed_apps = [], []

    # 국가 상태 갱신
    for item in countries_payload:
        name   = item.get("name", "")
        active = item.get("active", True)
        code   = ko_to_code.get(name)
        if code and code in fdb.get("countries", {}):
            new_status = "서비스 중" if active else "서비스 중단"
            if fdb["countries"][code].get("launch_status") != new_status:
                fdb["countries"][code]["launch_status"] = new_status
                changed_countries.append(name)

    # 앱별 ATM 지원 국가 갱신
    for item in apps_payload:
        app_name = item.get("name", "")
        atm_list = item.get("atm", [])
        if app_name in fdb.get("app_atm_support", {}):
            if fdb["app_atm_support"][app_name].get("atm") != atm_list:
                fdb["app_atm_support"][app_name]["atm"] = atm_list
                changed_apps.append(app_name)

    fdb["last_updated"] = datetime.now(KST).strftime("%Y-%m-%d")

    try:
        _save_fact_db(fdb)
    except Exception as e:
        return jsonify({"error": f"fact_db.json 저장 실패: {e}"}), 500

    return jsonify({
        "ok": True,
        "changed_countries": changed_countries,
        "changed_apps": changed_apps,
        "saved_at": fdb["last_updated"],
    })


# ── 에이전트맵 통계 API ───────────────────────────────────────────────

@admin_bp.route("/api/admin/agent-map/stats")
def api_agent_map_stats():
    """에이전트맵 동적 데이터: 국가 목록 + 파이프라인 통계."""
    # ── 국가 목록 (fact_db 기반) ──
    try:
        fdb = _load_fact_db()
    except Exception:
        fdb = {}

    countries = []
    for code, info in fdb.get("countries", {}).items():
        countries.append({
            "code":   code,
            "name":   info.get("name_ko", code),
            "flag":   COUNTRY_EMOJI.get(code, "🌐"),
            "active": info.get("launch_status", "서비스 중") == "서비스 중",
            "atm":    bool(info.get("atm")),
            "qr":     bool(info.get("qr_payment")),
        })

    conn = get_db()

    # ── 수집 통계 ──
    posts_total = conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
    posts_today = conn.execute(
        "SELECT COUNT(*) FROM posts WHERE DATE(created_at)=DATE('now','localtime')"
    ).fetchone()[0]
    last_row = conn.execute(
        "SELECT created_at FROM posts ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    last_at = ""
    if last_row and last_row[0]:
        try:
            dt = datetime.strptime(last_row[0][:16], "%Y-%m-%d %H:%M")
            last_at = dt.strftime("%m/%d %H:%M")
        except Exception:
            last_at = last_row[0][:16]

    # ── 분석 통계 ──
    analysis_total = conn.execute("SELECT COUNT(*) FROM ai_analysis").fetchone()[0]
    analysis_pending = max(0, posts_total - analysis_total)
    high_imp_today = conn.execute(
        "SELECT COUNT(*) FROM ai_analysis a JOIN posts p ON a.post_id=p.id "
        "WHERE a.importance_score >= 7 AND DATE(p.created_at)=DATE('now','localtime')"
    ).fetchone()[0]

    # ── 콘텐츠 생성 통계 (최근 7일) ──
    fmt_rows = conn.execute(
        "SELECT channel, format, COUNT(*) as cnt FROM content_drafts "
        "WHERE deleted_at IS NULL AND DATE(created_at) >= DATE('now','-7 days','localtime') "
        "GROUP BY channel, format"
    ).fetchall()
    fmt_map = {(r["channel"], r["format"]): r["cnt"] for r in fmt_rows}

    # 검수 등급 전체
    grade_rows = conn.execute(
        "SELECT guard_grade, COUNT(*) as cnt FROM content_drafts "
        "WHERE deleted_at IS NULL GROUP BY guard_grade"
    ).fetchall()
    grade_map = {r["guard_grade"]: r["cnt"] for r in grade_rows}

    published = conn.execute(
        "SELECT COUNT(*) FROM content_drafts "
        "WHERE approval_status='published' AND deleted_at IS NULL"
    ).fetchone()[0]

    # ── 키워드 ──
    kw_rows = conn.execute(
        "SELECT keyword FROM keywords WHERE is_active=1 ORDER BY id"
    ).fetchall()
    keywords = [r["keyword"] for r in kw_rows]

    conn.close()

    return jsonify({
        "countries": countries,
        "posts": {
            "total":   posts_total,
            "today":   posts_today,
            "last_at": last_at,
        },
        "analysis": {
            "total":               analysis_total,
            "pending":             analysis_pending,
            "high_importance_today": high_imp_today,
        },
        "content": {
            "blog":             fmt_map.get(("official", "blog"), 0),
            "instagram_card":   fmt_map.get(("official", "instagram_card"), 0),
            "youtube_shorts":   fmt_map.get(("official", "youtube_shorts"), 0),
            "threads_official": fmt_map.get(("official", "threads"), 0),
            "reels":            fmt_map.get(("gorani", "reels"), 0),
            "threads_gorani":   fmt_map.get(("gorani", "threads"), 0),
            "cartoon":          fmt_map.get(("gorani", "cartoon"), 0),
            "green":   grade_map.get("green",   0),
            "yellow":  grade_map.get("yellow",  0),
            "red":     grade_map.get("red",     0),
            "pending": grade_map.get("pending", 0),
            "published": published,
        },
        "keywords": keywords,
    })


# ── 채널 성과 ingest (marketing-dashboard → gln-monitor) ────────────────

@admin_bp.route("/api/performance/ingest", methods=["POST"])
def performance_ingest():
    """marketing-dashboard sync.js 실행 후 채널 성과 데이터를 저장."""
    data      = request.get_json(silent=True) or {}
    date      = data.get("date") or datetime.now(KST).strftime("%Y-%m-%d")
    platforms = data.get("platforms", {})

    if not platforms:
        return jsonify({"error": "platforms 데이터 없음"}), 400

    PLATFORM_COLS = {
        "youtube":   ("subscribers", "total_views", "video_count", "avg_eng_rate"),
        "ga4":       ("sessions", "users", "conv_rate", "bounce_rate", "avg_duration"),
        "instagram": ("followers", "media_count", "reach", "impressions", "engagement_rate"),
        "blog":      ("total_posts", "total_views_blog", "avg_comments"),
    }

    conn    = get_db()
    saved   = []
    for platform, metrics in platforms.items():
        if platform not in PLATFORM_COLS:
            continue
        cols = PLATFORM_COLS[platform]
        vals = {c: metrics.get(c) for c in cols}
        conn.execute(
            f"""INSERT INTO channel_performance
                    (platform, metric_date,
                     subscribers, total_views, video_count, avg_eng_rate,
                     sessions, users, conv_rate, bounce_rate, avg_duration,
                     followers, media_count, reach, impressions, engagement_rate,
                     total_posts, total_views_blog, avg_comments,
                     raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, metric_date) DO UPDATE SET
                    subscribers     = excluded.subscribers,
                    total_views     = excluded.total_views,
                    video_count     = excluded.video_count,
                    avg_eng_rate    = excluded.avg_eng_rate,
                    sessions        = excluded.sessions,
                    users           = excluded.users,
                    conv_rate       = excluded.conv_rate,
                    bounce_rate     = excluded.bounce_rate,
                    avg_duration    = excluded.avg_duration,
                    followers       = excluded.followers,
                    media_count     = excluded.media_count,
                    reach           = excluded.reach,
                    impressions     = excluded.impressions,
                    engagement_rate = excluded.engagement_rate,
                    total_posts     = excluded.total_posts,
                    total_views_blog= excluded.total_views_blog,
                    avg_comments    = excluded.avg_comments,
                    raw_json        = excluded.raw_json,
                    synced_at       = datetime('now','localtime')""",
            (
                platform, date,
                vals.get("subscribers"),    vals.get("total_views"),
                vals.get("video_count"),    vals.get("avg_eng_rate"),
                vals.get("sessions"),       vals.get("users"),
                vals.get("conv_rate"),      vals.get("bounce_rate"),
                vals.get("avg_duration"),
                vals.get("followers"),      vals.get("media_count"),
                vals.get("reach"),          vals.get("impressions"),
                vals.get("engagement_rate"),
                vals.get("total_posts"),    vals.get("total_views_blog"),
                vals.get("avg_comments"),
                json.dumps(metrics, ensure_ascii=False),
            ),
        )
        saved.append(platform)
    conn.commit()
    conn.close()

    print(f"[성과 ingest] {date} — {', '.join(saved)}", flush=True)
    return jsonify({"ok": True, "date": date, "saved": saved})


@admin_bp.route("/api/performance", methods=["GET"])
def performance_get():
    """채널 성과 데이터 조회. ?days=30&platform=youtube"""
    days     = min(int(request.args.get("days", 30)), 365)
    platform = request.args.get("platform")
    since    = (datetime.now(KST) - timedelta(days=days)).strftime("%Y-%m-%d")

    conn = get_db()
    if platform:
        rows = conn.execute(
            "SELECT * FROM channel_performance WHERE platform=? AND metric_date>=? ORDER BY metric_date",
            (platform, since),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM channel_performance WHERE metric_date>=? ORDER BY platform, metric_date",
            (since,),
        ).fetchall()
    conn.close()

    return jsonify({"data": [dict(r) for r in rows], "days": days, "platform": platform})
