"""
services/platform_detect.py — URL로 플랫폼 판별 + 정규화
"""
from urllib.parse import urlparse


def detect_platform(url: str) -> str:
    """도메인 기반 플랫폼 판별. 미인식 시 'unknown' 반환."""
    try:
        host = urlparse(url.strip()).netloc.lower()
        # www. 제거
        if host.startswith("www."):
            host = host[4:]

        if "youtube.com" in host or "youtu.be" in host:
            return "youtube"
        if "blog.naver.com" in host or "m.blog.naver.com" in host:
            return "naver_blog"
        if "instagram.com" in host:
            return "instagram"
    except Exception:
        pass
    return "unknown"


def normalize_url(url: str) -> str:
    """URL 정규화 — 네이버 모바일을 PC URL로 통일."""
    url = url.strip()
    if "m.blog.naver.com" in url:
        url = url.replace("m.blog.naver.com", "blog.naver.com")
    return url
