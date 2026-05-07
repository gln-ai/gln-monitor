"""
services/naver.py — 네이버 오픈 API 수집
"""
import hashlib
import os
import sqlite3
import threading

import requests

from db import get_db
from services.analysis import process_unanalyzed

_DEFAULT_KEYWORDS = ["GLN", "퍼플GLN", "GLN ATM", "GLN 해외결제", "GLN 출금"]

CHANNELS = {
    "카페":  "https://openapi.naver.com/v1/search/cafearticle.json",
    "블로그": "https://openapi.naver.com/v1/search/blog.json",
    "뉴스":  "https://openapi.naver.com/v1/search/news.json",
}


def make_hash(title: str, link: str) -> str:
    return hashlib.md5(f"{title}{link}".encode()).hexdigest()


def parse_date(item: dict) -> str:
    """채널별 날짜 파싱 — 카페: postdate(20260324), 뉴스/블로그: pubDate(RFC2822)"""
    raw = item.get("postdate", "") or item.get("pubDate", "")
    if not raw:
        return ""
    if raw.isdigit() and len(raw) == 8:
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(raw)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return ""


def fetch_naver_posts(keyword: str, channel: str = "카페", display: int = 30) -> list:
    url = CHANNELS.get(channel, CHANNELS["카페"])
    headers = {
        "X-Naver-Client-Id":     os.getenv("NAVER_CLIENT_ID"),
        "X-Naver-Client-Secret": os.getenv("NAVER_CLIENT_SECRET"),
    }
    params = {"query": keyword, "display": display, "sort": "date"}
    try:
        res = requests.get(url, headers=headers, params=params, timeout=10)
        res.raise_for_status()
        return res.json().get("items", [])
    except Exception as e:
        print(f"[수집 오류] {channel}/{keyword}: {e}")
        return []


def _get_keywords() -> list[str]:
    """DB keywords 테이블에서 활성 키워드 로드. 없으면 기본값 사용."""
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT keyword FROM keywords WHERE is_active=1 ORDER BY created_at"
        ).fetchall()
        conn.close()
        if rows:
            return [r["keyword"] for r in rows]
    except Exception:
        pass
    return _DEFAULT_KEYWORDS


def collect_all():
    """모든 키워드 × 채널 수집 → DB 저장 → AI 처리 트리거"""
    from datetime import datetime
    from config import KST
    print(f"[{datetime.now(KST).strftime('%H:%M')}] 수집 시작...")
    conn = get_db()
    new_count = 0
    keywords  = _get_keywords()

    for channel in CHANNELS.keys():
        for keyword in keywords:
            items = fetch_naver_posts(keyword, channel)
            for item in items:
                title = item.get("title", "").replace("<b>", "").replace("</b>", "")
                link  = item.get("link", "")
                desc  = item.get("description", "").replace("<b>", "").replace("</b>", "")
                h     = make_hash(title, link)

                if channel == "카페":
                    source = item.get("cafename", "")
                elif channel == "블로그":
                    source = item.get("bloggername", "")
                else:
                    source = item.get("originallink", "")[:30] if item.get("originallink") else ""

                try:
                    conn.execute(
                        """INSERT INTO posts (title, link, description, cafe_name, post_date, hash, keyword)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (title, link, desc, source, parse_date(item), h, f"{channel}/{keyword}")
                    )
                    conn.commit()
                    new_count += 1
                except sqlite3.IntegrityError:
                    pass

    conn.close()
    print(f"[수집 완료] 신규 {new_count}건")

    if new_count > 0:
        threading.Thread(target=process_unanalyzed, daemon=True).start()
