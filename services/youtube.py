"""
services/youtube.py — YouTube Data API v3 단일 영상 데이터 수집

YOUTUBE_API_KEY 발급 방법:
  1. https://console.cloud.google.com/ → 프로젝트 생성
  2. [API 및 서비스] → [사용 설정된 API] → 'YouTube Data API v3' 검색 후 사용 설정
  3. [사용자 인증 정보] → [사용자 인증 정보 만들기] → [API 키]
  4. .env 파일에 YOUTUBE_API_KEY=발급받은키 추가
"""
import os
import re
import time

import requests


_API_BASE = "https://www.googleapis.com/youtube/v3/videos"


def _extract_video_id(url: str) -> str | None:
    """watch?v=, youtu.be/, /shorts/ 세 패턴에서 video_id 추출."""
    patterns = [
        r"(?:youtube\.com/watch\?(?:.*&)?v=)([A-Za-z0-9_-]{11})",
        r"(?:youtu\.be/)([A-Za-z0-9_-]{11})",
        r"(?:youtube\.com/shorts/)([A-Za-z0-9_-]{11})",
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return None


def _parse_duration(iso: str) -> int:
    """ISO 8601 duration (PT1H2M30S) → 초 단위 정수."""
    if not iso:
        return 0
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso)
    if not m:
        return 0
    h = int(m.group(1) or 0)
    mn = int(m.group(2) or 0)
    s = int(m.group(3) or 0)
    return h * 3600 + mn * 60 + s


def fetch_video_data(url: str) -> dict | None:
    """YouTube 영상 메타데이터 수집. 실패 시 None 반환."""
    api_key = os.getenv("YOUTUBE_API_KEY")
    if not api_key:
        print("[YouTube] YOUTUBE_API_KEY 없음 — .env에 추가 필요")
        return None

    video_id = _extract_video_id(url)
    if not video_id:
        print(f"[YouTube] video_id 추출 실패: {url}")
        return None

    params = {
        "key":  api_key,
        "id":   video_id,
        "part": "snippet,statistics,contentDetails",
    }

    # naver.py 패턴과 동일한 3회 재시도 (1s → 2s → 4s 지수 백오프)
    for attempt in range(3):
        try:
            res = requests.get(_API_BASE, params=params, timeout=10)
            res.raise_for_status()
            data = res.json()

            items = data.get("items", [])
            if not items:
                print(f"[YouTube] 영상 없음 (video_id={video_id})")
                return None

            item   = items[0]
            snip   = item.get("snippet", {})
            stats  = item.get("statistics", {})
            detail = item.get("contentDetails", {})

            return {
                "video_id":      video_id,
                "title":         snip.get("title", ""),
                "description":   snip.get("description", ""),
                "view_count":    int(stats.get("viewCount", 0)),
                "like_count":    int(stats.get("likeCount", 0)),
                "comment_count": int(stats.get("commentCount", 0)),
                "duration_sec":  _parse_duration(detail.get("duration", "")),
                "published_at":  snip.get("publishedAt", "")[:10],
                "thumbnail_url": (snip.get("thumbnails", {}).get("high", {}).get("url", "")
                                  or snip.get("thumbnails", {}).get("default", {}).get("url", "")),
            }
        except requests.exceptions.HTTPError as e:
            print(f"[YouTube 오류] HTTP {e.response.status_code} — 재시도 안 함")
            return None
        except Exception as e:
            if attempt < 2:
                wait = 2 ** attempt
                print(f"[YouTube 재시도 {attempt + 1}/3] {e} — {wait}s 후 재시도")
                time.sleep(wait)
            else:
                print(f"[YouTube 실패] {e}")
    return None
