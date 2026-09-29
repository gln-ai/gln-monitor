"""
routes/sync.py — 로컬에서 AI 분석이 끝난 게시물을 받아 DB에 반영 (대시보드 표시용, 재분석 없음).
Railway 쪽에서 대시보드에 최신 피드가 보이도록 하는 수신 엔드포인트.
"""
import os

from flask import Blueprint, jsonify, request

from db import get_db

sync_bp = Blueprint("sync", __name__)

SYNC_SECRET = os.getenv("SYNC_SECRET", "")


@sync_bp.route("/api/sync/ingest", methods=["POST"])
def ingest():
    if not SYNC_SECRET or request.headers.get("X-Sync-Secret") != SYNC_SECRET:
        return jsonify({"error": "unauthorized"}), 403

    data = request.get_json(silent=True) or {}
    posts = data.get("posts", [])

    conn = get_db()
    inserted_posts = 0
    inserted_analysis = 0

    for p in posts:
        h = p.get("hash")
        if not h:
            continue

        cur = conn.execute(
            """INSERT OR IGNORE INTO posts
               (title, link, description, cafe_name, post_date, created_at, hash, keyword,
                is_processed, is_urgent, reply_status, status_updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (p.get("title"), p.get("link"), p.get("description"), p.get("cafe_name"),
             p.get("post_date"), p.get("created_at"), h, p.get("keyword"),
             p.get("is_processed"), p.get("is_urgent"), p.get("reply_status"),
             p.get("status_updated_at"))
        )
        if cur.rowcount:
            inserted_posts += 1

        row = conn.execute("SELECT id FROM posts WHERE hash=?", (h,)).fetchone()
        if not row:
            continue
        post_id = row["id"]

        a = p.get("analysis") or {}
        cur2 = conn.execute(
            """INSERT OR IGNORE INTO ai_analysis
               (post_id, summary, category, sentiment, importance_score, created_at,
                is_relevant, competitors, country)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (post_id, a.get("summary"), a.get("category"), a.get("sentiment"),
             a.get("importance_score"), a.get("created_at"), a.get("is_relevant", 1),
             a.get("competitors", ""), a.get("country", ""))
        )
        if cur2.rowcount:
            inserted_analysis += 1

    conn.commit()
    conn.close()
    return jsonify({
        "received": len(posts),
        "inserted_posts": inserted_posts,
        "inserted_analysis": inserted_analysis,
    })
