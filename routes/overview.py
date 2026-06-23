"""
routes/overview.py — 시스템 통합 현황판
"""
import subprocess
from datetime import datetime

from flask import Blueprint, jsonify, render_template

from config import KST
from db import get_db

overview_bp = Blueprint("overview", __name__)


def _get_stats():
    conn = get_db()
    now  = datetime.now(KST)
    today = now.strftime("%Y-%m-%d")

    # ── 모니터링 ────────────────────────────────────────────────────────
    posts_total = conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
    posts_today = conn.execute(
        "SELECT COUNT(*) FROM posts WHERE DATE(created_at)=DATE('now','localtime')"
    ).fetchone()[0]
    unprocessed = conn.execute(
        "SELECT COUNT(*) FROM posts WHERE is_processed=0"
    ).fetchone()[0]
    urgent_today = conn.execute(
        """SELECT COUNT(*) FROM posts p
           JOIN ai_analysis a ON p.id=a.post_id
           WHERE (a.importance_score>=7 OR a.sentiment='negative')
             AND DATE(p.created_at)=DATE('now','localtime')"""
    ).fetchone()[0]

    # ── 콘텐츠 ─────────────────────────────────────────────────────────
    content_total = conn.execute(
        "SELECT COUNT(*) FROM content_drafts WHERE deleted_at IS NULL"
    ).fetchone()[0]
    green_unpub = conn.execute(
        "SELECT COUNT(*) FROM content_drafts WHERE guard_grade='green' AND approval_status='unpublished' AND deleted_at IS NULL"
    ).fetchone()[0]
    yellow_count = conn.execute(
        "SELECT COUNT(*) FROM content_drafts WHERE guard_grade='yellow' AND deleted_at IS NULL"
    ).fetchone()[0]
    red_count = conn.execute(
        "SELECT COUNT(*) FROM content_drafts WHERE guard_grade='red' AND deleted_at IS NULL"
    ).fetchone()[0]
    published_count = conn.execute(
        "SELECT COUNT(*) FROM content_drafts WHERE approval_status='published' AND deleted_at IS NULL"
    ).fetchone()[0]

    # ── 이메일 ─────────────────────────────────────────────────────────
    email_today = conn.execute(
        "SELECT COUNT(*) FROM email_log WHERE DATE(sent_at)=DATE('now','localtime') AND status='ok'"
    ).fetchone()[0]
    email_week = conn.execute(
        "SELECT COUNT(*) FROM email_log WHERE sent_at >= datetime('now','-7 days','localtime') AND status='ok'"
    ).fetchone()[0]
    email_total = conn.execute("SELECT COUNT(*) FROM email_log WHERE status='ok'").fetchone()[0]

    # ── 채널 성과 (최신 날짜) ───────────────────────────────────────────
    channel_latest = conn.execute(
        "SELECT MAX(metric_date) FROM channel_performance"
    ).fetchone()[0]
    channel_platforms = []
    if channel_latest:
        rows = conn.execute(
            "SELECT platform, subscribers, total_views, followers, sessions, users, synced_at "
            "FROM channel_performance WHERE metric_date=? ORDER BY platform",
            (channel_latest,),
        ).fetchall()
        channel_platforms = [dict(r) for r in rows]

    # ── 기타 DB 통계 ───────────────────────────────────────────────────
    pr_drafts    = conn.execute("SELECT COUNT(*) FROM pr_drafts").fetchone()[0]
    reply_total  = conn.execute("SELECT COUNT(*) FROM draft_replies").fetchone()[0]
    reports_arch = conn.execute("SELECT COUNT(*) FROM reports_archive").fetchone()[0]

    # ── 최근 리포트 ────────────────────────────────────────────────────
    last_report = conn.execute(
        "SELECT created_at FROM reports_archive ORDER BY id DESC LIMIT 1"
    ).fetchone()

    conn.close()

    return {
        "now":          now.strftime("%Y-%m-%d %H:%M"),
        # 모니터링
        "posts_total":   posts_total,
        "posts_today":   posts_today,
        "unprocessed":   unprocessed,
        "urgent_today":  urgent_today,
        # 콘텐츠
        "content_total":   content_total,
        "green_unpub":     green_unpub,
        "yellow_count":    yellow_count,
        "red_count":       red_count,
        "published_count": published_count,
        # 이메일
        "email_today": email_today,
        "email_week":  email_week,
        "email_total": email_total,
        # 채널 성과
        "channel_latest":    channel_latest,
        "channel_platforms": channel_platforms,
        # 기타
        "pr_drafts":    pr_drafts,
        "reply_total":  reply_total,
        "reports_arch": reports_arch,
        "last_report":  last_report["created_at"][:16] if last_report else "없음",
    }


@overview_bp.route("/overview")
def overview():
    stats = _get_stats()
    return render_template("overview.html", s=stats)


@overview_bp.route("/api/overview/stats")
def overview_stats():
    return jsonify(_get_stats())
