"""
services/railway_sync.py — AI 분석이 끝난 게시물만 Railway 대시보드용 DB로 push.
Railway는 자체 수집/분석을 하지 않으므로(크레딧 중복 방지) 분석 완료된 결과만 전달.
"""
import os

import requests

from db import get_db

SYNC_URL = os.getenv("RAILWAY_SYNC_URL", "").rstrip("/")
SYNC_SECRET = os.getenv("SYNC_SECRET", "")
BATCH_SIZE = 200
MAX_BATCHES_PER_RUN = 20  # 한 번에 최대 4000건까지 밀린 분량 처리
SETTING_KEY = "railway_sync_last_post_id"


def _get_last_synced_id() -> int:
    conn = get_db()
    row = conn.execute("SELECT value FROM app_settings WHERE key=?", (SETTING_KEY,)).fetchone()
    conn.close()
    return int(row["value"]) if row and row["value"] else 0


def _set_last_synced_id(post_id: int):
    conn = get_db()
    conn.execute(
        """INSERT INTO app_settings (key, value, updated_at)
           VALUES (?, ?, datetime('now','localtime'))
           ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
        (SETTING_KEY, str(post_id))
    )
    conn.commit()
    conn.close()


def _fetch_batch(last_id: int):
    conn = get_db()
    rows = conn.execute(
        """SELECT p.id, p.title, p.link, p.description, p.cafe_name, p.post_date, p.created_at,
                  p.hash, p.keyword, p.is_processed, p.is_urgent, p.reply_status, p.status_updated_at,
                  a.summary, a.category, a.sentiment, a.importance_score,
                  a.created_at AS analysis_created_at, a.is_relevant, a.competitors, a.country
           FROM posts p
           JOIN ai_analysis a ON a.post_id = p.id
           WHERE p.id > ?
           ORDER BY p.id
           LIMIT ?""",
        (last_id, BATCH_SIZE)
    ).fetchall()
    conn.close()
    return rows


def _to_payload(rows):
    return [
        {
            "title": r["title"], "link": r["link"], "description": r["description"],
            "cafe_name": r["cafe_name"], "post_date": r["post_date"], "created_at": r["created_at"],
            "hash": r["hash"], "keyword": r["keyword"], "is_processed": r["is_processed"],
            "is_urgent": r["is_urgent"], "reply_status": r["reply_status"],
            "status_updated_at": r["status_updated_at"],
            "analysis": {
                "summary": r["summary"], "category": r["category"], "sentiment": r["sentiment"],
                "importance_score": r["importance_score"], "created_at": r["analysis_created_at"],
                "is_relevant": r["is_relevant"], "competitors": r["competitors"], "country": r["country"],
            },
        }
        for r in rows
    ]


def sync_to_railway():
    if not SYNC_URL or not SYNC_SECRET:
        print("[Railway동기화] RAILWAY_SYNC_URL/SYNC_SECRET 미설정 — 스킵", flush=True)
        return

    last_id = _get_last_synced_id()
    total_sent = 0

    for _ in range(MAX_BATCHES_PER_RUN):
        rows = _fetch_batch(last_id)
        if not rows:
            break

        payload = _to_payload(rows)
        try:
            resp = requests.post(
                f"{SYNC_URL}/api/sync/ingest",
                json={"posts": payload},
                headers={"X-Sync-Secret": SYNC_SECRET},
                timeout=30,
            )
            resp.raise_for_status()
            result = resp.json()
        except Exception as e:
            print(f"[Railway동기화] 실패: {e} — 다음 주기에 재시도 (last_id={last_id})", flush=True)
            return

        last_id = rows[-1]["id"]
        _set_last_synced_id(last_id)
        total_sent += len(payload)
        print(f"[Railway동기화] {len(payload)}건 전송 (post_id ≤ {last_id}), 응답: {result}", flush=True)

    if total_sent:
        print(f"[Railway동기화] 완료 — 총 {total_sent}건", flush=True)
