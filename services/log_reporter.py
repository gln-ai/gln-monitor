"""
services/log_reporter.py — 기간별 로그 보고서 JSON 저장
일간: 매일 23:55 → REPORTS_DIR/daily/  (30일 보관)
주간: 매주 월요일 09:05 → REPORTS_DIR/weekly/ (12주 보관)
월간: 매월 1일 09:10 → REPORTS_DIR/monthly/ (무기한)

REPORTS_DIR 환경변수로 저장 경로 설정 (Railway: /app/data/reports)
기본값: <app>/reports/
"""
import json
import os
from datetime import datetime, timedelta

from config import KST
from db import get_db

MONITOR_DIR  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS_ROOT = os.getenv("REPORTS_DIR", os.path.join(MONITOR_DIR, "reports"))

_DAILY_KEEP  = 30   # 일
_WEEKLY_KEEP = 84   # 12주


def _collect(since_dt: datetime) -> dict:
    conn = get_db()
    since_str = since_dt.strftime("%Y-%m-%d %H:%M:%S")

    total = conn.execute(
        "SELECT COUNT(*) FROM posts WHERE created_at >= ?", (since_str,)
    ).fetchone()[0]
    urgent = conn.execute(
        "SELECT COUNT(*) FROM posts WHERE created_at >= ? AND is_urgent = 1", (since_str,)
    ).fetchone()[0]
    unprocessed = conn.execute(
        "SELECT COUNT(*) FROM posts WHERE created_at >= ? AND reply_status = '미확인'", (since_str,)
    ).fetchone()[0]

    sentiment_rows = conn.execute("""
        SELECT a.sentiment, COUNT(*) as cnt
        FROM ai_analysis a JOIN posts p ON a.post_id = p.id
        WHERE p.created_at >= ?
        GROUP BY a.sentiment
    """, (since_str,)).fetchall()
    sentiment = {r["sentiment"]: r["cnt"] for r in sentiment_rows}

    category_rows = conn.execute("""
        SELECT a.category, COUNT(*) as cnt
        FROM ai_analysis a JOIN posts p ON a.post_id = p.id
        WHERE p.created_at >= ?
        GROUP BY a.category ORDER BY cnt DESC LIMIT 5
    """, (since_str,)).fetchall()
    top_categories = [{"category": r["category"], "count": r["cnt"]} for r in category_rows]

    keyword_rows = conn.execute("""
        SELECT keyword, COUNT(*) as cnt FROM posts
        WHERE created_at >= ? AND keyword IS NOT NULL AND keyword != ''
        GROUP BY keyword ORDER BY cnt DESC
    """, (since_str,)).fetchall()
    by_keyword = [{"keyword": r["keyword"], "count": r["cnt"]} for r in keyword_rows]

    avg_score = conn.execute("""
        SELECT ROUND(AVG(a.importance_score), 2)
        FROM ai_analysis a JOIN posts p ON a.post_id = p.id
        WHERE p.created_at >= ?
    """, (since_str,)).fetchone()[0] or 0

    important_rows = conn.execute("""
        SELECT p.title, p.cafe_name, p.created_at, a.importance_score, a.sentiment
        FROM posts p JOIN ai_analysis a ON a.post_id = p.id
        WHERE p.created_at >= ? AND a.importance_score >= 7
        ORDER BY a.importance_score DESC LIMIT 10
    """, (since_str,)).fetchall()
    important_posts = [dict(r) for r in important_rows]

    content_rows = conn.execute("""
        SELECT channel, format, guard_grade, COUNT(*) as cnt
        FROM content_drafts WHERE created_at >= ?
        GROUP BY channel, format, guard_grade
    """, (since_str,)).fetchall()
    content_stats = [dict(r) for r in content_rows]

    sla_cutoff = (since_dt + timedelta(hours=6)).strftime("%Y-%m-%d %H:%M:%S")
    sla_overdue = conn.execute("""
        SELECT COUNT(*) FROM posts
        WHERE created_at >= ? AND created_at <= ? AND reply_status = '미확인'
    """, (since_str, sla_cutoff)).fetchone()[0]

    conn.close()

    pos = sentiment.get("positive", 0)
    neg = sentiment.get("negative", 0)
    total_sent = sum(sentiment.values()) or 1
    health_score = round((pos / total_sent) * 60 + ((total_sent - neg) / total_sent) * 40, 1)

    return {
        "total": total, "urgent": urgent, "unprocessed": unprocessed,
        "sla_overdue": sla_overdue, "avg_importance_score": avg_score,
        "health_score": health_score, "sentiment": sentiment,
        "top_categories": top_categories, "by_keyword": by_keyword,
        "important_posts": important_posts, "content_stats": content_stats,
    }


def _save(subdir: str, filename: str, data: dict):
    path = os.path.join(REPORTS_ROOT, subdir, filename)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    print(f"[로그 저장] {path}", flush=True)


def _save_to_db(report_type: str, filename: str, data: dict):
    conn = get_db()
    conn.execute(
        """INSERT OR REPLACE INTO reports_archive
           (report_type, filename, period_start, period_end, generated_at, data_json)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (report_type, filename,
         data.get("period_start"), data.get("period_end"), data.get("generated_at"),
         json.dumps(data, ensure_ascii=False, default=str))
    )
    conn.commit()
    conn.close()
    print(f"[DB 저장] reports_archive: {report_type}/{filename}", flush=True)


def _cleanup(subdir: str, keep_days: int):
    folder = os.path.join(REPORTS_ROOT, subdir)
    if not os.path.isdir(folder):
        return
    cutoff = datetime.now() - timedelta(days=keep_days)
    for fname in os.listdir(folder):
        fpath = os.path.join(folder, fname)
        if os.path.isfile(fpath) and datetime.fromtimestamp(os.path.getmtime(fpath)) < cutoff:
            os.remove(fpath)
            print(f"[로그 정리] 삭제: {fpath}", flush=True)


def _cleanup_db(report_type: str, keep_days: int):
    cutoff = (datetime.now() - timedelta(days=keep_days)).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db()
    conn.execute(
        "DELETE FROM reports_archive WHERE report_type=? AND created_at < ?",
        (report_type, cutoff)
    )
    conn.commit()
    conn.close()


def save_daily_report():
    now   = datetime.now(KST)
    since = now.replace(hour=0, minute=0, second=0, microsecond=0)
    fname = f"daily_{now.strftime('%Y%m%d')}.json"
    data  = {"report_type": "daily",
             "period_start": since.strftime("%Y-%m-%d 00:00"),
             "period_end":   now.strftime("%Y-%m-%d %H:%M"),
             "generated_at": now.isoformat(), **_collect(since)}
    _save("daily", fname, data)
    _save_to_db("daily", fname, data)
    _cleanup("daily", _DAILY_KEEP)
    _cleanup_db("daily", _DAILY_KEEP)


def save_weekly_report():
    now   = datetime.now(KST)
    since = now - timedelta(days=7)
    fname = f"weekly_{now.strftime('%Y-W%W')}.json"
    data  = {"report_type": "weekly",
             "period_start": since.strftime("%Y-%m-%d 00:00"),
             "period_end":   now.strftime("%Y-%m-%d %H:%M"),
             "generated_at": now.isoformat(), **_collect(since)}
    _save("weekly", fname, data)
    _save_to_db("weekly", fname, data)
    _cleanup("weekly", _WEEKLY_KEEP)
    _cleanup_db("weekly", _WEEKLY_KEEP)


def save_monthly_report():
    now              = datetime.now(KST)
    first_this_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_month_end   = first_this_month - timedelta(seconds=1)
    last_month_start = last_month_end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    fname = f"monthly_{last_month_start.strftime('%Y%m')}.json"
    data = {"report_type": "monthly",
            "period_start": last_month_start.strftime("%Y-%m-%d 00:00"),
            "period_end":   last_month_end.strftime("%Y-%m-%d 23:59"),
            "generated_at": now.isoformat(), **_collect(last_month_start)}
    _save("monthly", fname, data)
    _save_to_db("monthly", fname, data)
