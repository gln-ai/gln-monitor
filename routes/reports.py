"""
routes/reports.py — 로그 보고서 뷰어
"""
import json
import os
import threading

from flask import Blueprint, jsonify, render_template, request

from db import get_db

MONITOR_DIR  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS_ROOT = os.getenv("REPORTS_DIR", os.path.join(MONITOR_DIR, "reports"))

reports_bp = Blueprint("reports", __name__)


def _row_to_summary(data: dict, fname: str) -> dict:
    sentiment = data.get("sentiment", {})
    st = max(sentiment.get("positive", 0) + sentiment.get("neutral", 0) + sentiment.get("negative", 0), 1)
    return {
        "filename":     fname,
        "type":         data.get("report_type", ""),
        "period_start": data.get("period_start", ""),
        "period_end":   data.get("period_end", ""),
        "generated_at": data.get("generated_at", ""),
        "total":        data.get("total", 0),
        "urgent":       data.get("urgent", 0),
        "health_score": data.get("health_score", 0),
        "sent_pos": round(sentiment.get("positive", 0) / st * 100),
        "sent_neu": round(sentiment.get("neutral",  0) / st * 100),
        "sent_neg": round(sentiment.get("negative", 0) / st * 100),
    }


def _list_reports(subdir: str) -> list[dict]:
    # DB 우선 — Railway 파일시스템 소실 방지
    conn = get_db()
    rows = conn.execute(
        "SELECT filename, data_json FROM reports_archive WHERE report_type=? ORDER BY filename DESC",
        (subdir,)
    ).fetchall()
    conn.close()
    if rows:
        result = []
        for r in rows:
            try:
                result.append(_row_to_summary(json.loads(r["data_json"]), r["filename"]))
            except Exception:
                pass
        return result

    # 로컬 fallback — 파일에서 읽기
    folder = os.path.join(REPORTS_ROOT, subdir)
    if not os.path.isdir(folder):
        return []
    files = []
    for fname in sorted(os.listdir(folder), reverse=True):
        if fname.endswith(".json"):
            fpath = os.path.join(folder, fname)
            try:
                with open(fpath, encoding="utf-8") as f:
                    data = json.load(f)
                files.append(_row_to_summary(data, fname))
            except Exception:
                pass
    return files


@reports_bp.route("/reports")
def reports_index():
    daily   = _list_reports("daily")
    weekly  = _list_reports("weekly")
    monthly = _list_reports("monthly")
    return render_template("reports.html",
                           daily=daily, weekly=weekly, monthly=monthly)


@reports_bp.route("/api/email-log")
def api_email_log():
    conn = get_db()
    rows = conn.execute(
        "SELECT id, report_type, subject, recipients, sent_at, status, error_msg "
        "FROM email_log ORDER BY sent_at DESC LIMIT 50"
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@reports_bp.route("/api/report/send", methods=["POST"])
def api_report_send():
    import os as _os
    if _os.getenv("DISABLE_EMAIL_SEND", "false").lower() == "true":
        return jsonify({"status": "이메일 발송 비활성화됨 (DISABLE_EMAIL_SEND=true)"})
    data        = request.get_json(silent=True) or {}
    report_type = data.get("type", "daily")
    to          = data.get("to", "").strip()
    if report_type == "weekly":
        from services.weekly_report import send_weekly_report
        threading.Thread(target=send_weekly_report, args=(to,), daemon=True).start()
    else:
        from services.email_svc import send_daily_report
        threading.Thread(target=send_daily_report, args=(to,), daemon=True).start()
    return jsonify({"status": "발송 시작됨", "type": report_type})


@reports_bp.route("/api/reports/<subdir>/<filename>")
def api_report_detail(subdir, filename):
    if subdir not in ("daily", "weekly", "monthly"):
        return jsonify({"error": "invalid"}), 400

    # DB 우선
    conn = get_db()
    row = conn.execute(
        "SELECT data_json FROM reports_archive WHERE report_type=? AND filename=?",
        (subdir, filename)
    ).fetchone()
    conn.close()
    if row:
        return jsonify(json.loads(row["data_json"]))

    # 로컬 fallback
    fpath = os.path.join(REPORTS_ROOT, subdir, filename)
    if not os.path.isfile(fpath):
        return jsonify({"error": "not found"}), 404
    with open(fpath, encoding="utf-8") as f:
        data = json.load(f)
    return jsonify(data)
