"""
routes/keywords.py — 키워드 관리 라우트
"""
from flask import Blueprint, jsonify, render_template, request

from db import get_db
from services.naver import collect_all
import threading

keywords_bp = Blueprint("keywords", __name__)

# 수집 채널 기본값
CHANNELS = ["카페", "블로그", "뉴스"]


@keywords_bp.route("/keywords")
def keywords_page():
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM keywords ORDER BY is_active DESC, created_at DESC"
    ).fetchall()
    conn.close()
    return render_template("keywords.html", keywords=rows, channels=CHANNELS)


@keywords_bp.route("/api/keywords", methods=["GET"])
def api_keywords_list():
    conn = get_db()
    rows = conn.execute("SELECT * FROM keywords ORDER BY is_active DESC, created_at DESC").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@keywords_bp.route("/api/keywords", methods=["POST"])
def api_keywords_add():
    data    = request.get_json(silent=True) or {}
    keyword = (data.get("keyword") or "").strip()
    channel = data.get("channel", "all")
    if not keyword:
        return jsonify({"error": "keyword required"}), 400
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO keywords (keyword, channel) VALUES (?, ?)", (keyword, channel)
        )
        conn.commit()
        row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()
        return jsonify({"id": row_id, "keyword": keyword, "channel": channel, "is_active": 1})
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 409


@keywords_bp.route("/api/keywords/<int:kw_id>", methods=["PATCH"])
def api_keywords_toggle(kw_id):
    data      = request.get_json(silent=True) or {}
    is_active = int(data.get("is_active", 1))
    conn = get_db()
    conn.execute("UPDATE keywords SET is_active=? WHERE id=?", (is_active, kw_id))
    conn.commit()
    conn.close()
    return jsonify({"id": kw_id, "is_active": is_active})


@keywords_bp.route("/api/keywords/<int:kw_id>", methods=["DELETE"])
def api_keywords_delete(kw_id):
    conn = get_db()
    conn.execute("DELETE FROM keywords WHERE id=?", (kw_id,))
    conn.commit()
    conn.close()
    return jsonify({"status": "deleted"})


@keywords_bp.route("/api/keywords/collect", methods=["POST"])
def api_keywords_collect():
    """키워드 목록 기반으로 즉시 수집 트리거."""
    threading.Thread(target=collect_all, daemon=True).start()
    return jsonify({"status": "수집 시작됨"})


@keywords_bp.route("/api/settings", methods=["GET"])
def api_settings_get():
    conn = get_db()
    rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
    conn.close()
    return jsonify({r["key"]: r["value"] for r in rows})


@keywords_bp.route("/api/settings", methods=["POST"])
def api_settings_save():
    data = request.get_json(silent=True) or {}
    if not data:
        return jsonify({"error": "empty body"}), 400
    conn = get_db()
    for key, value in data.items():
        conn.execute(
            """INSERT INTO app_settings (key, value, updated_at)
               VALUES (?, ?, datetime('now','localtime'))
               ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
            (key, str(value))
        )
    conn.commit()
    conn.close()
    return jsonify({"status": "saved", "keys": list(data.keys())})


@keywords_bp.route("/api/settings/schedule", methods=["POST"])
def api_settings_schedule():
    from flask import current_app
    data = request.get_json(silent=True) or {}
    conn = get_db()
    for key in ("report_weekday_hour", "report_weekend_hour", "report_weekly_hour"):
        if key in data:
            conn.execute(
                """INSERT INTO app_settings (key, value, updated_at)
                   VALUES (?, ?, datetime('now','localtime'))
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
                (key, str(data[key]))
            )
    conn.commit()
    conn.close()
    sched = getattr(current_app, "_scheduler", None)
    if sched:
        try:
            if "report_weekday_hour" in data:
                sched.reschedule_job("daily_weekday", trigger="cron",
                    day_of_week="mon-fri", hour=int(data["report_weekday_hour"]), minute=0)
            if "report_weekend_hour" in data:
                sched.reschedule_job("daily_weekend", trigger="cron",
                    day_of_week="sat,sun", hour=int(data["report_weekend_hour"]), minute=0)
            if "report_weekly_hour" in data:
                sched.reschedule_job("weekly_report", trigger="cron",
                    day_of_week="mon", hour=int(data["report_weekly_hour"]), minute=0)
        except Exception as e:
            print(f"[스케줄 변경 오류] {e}")
    return jsonify({"status": "rescheduled", "applied": list(data.keys())})
