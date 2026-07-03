"""
services/naver_blog.py — 네이버 블로그 특정 URL 직접 파싱
(기존 naver.py의 검색 API와 별개 — 서포터즈 URL 수동 수집용)

실제 구조: m.blog.naver.com/{blogId}/{logNo} 형태의 모바일 URL이 직접 파싱됨.
PC URL (blog.naver.com)은 redirect 없이 iframe 구조로 차단되므로 모바일 URL 기준 파싱.
"""
import re
import time
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup

# lxml이 없는 환경(Railway slim 이미지 등)에서도 동작하도록 파서 자동 선택
try:
    import lxml  # noqa: F401
    _BS_PARSER = "lxml"
except ImportError:
    _BS_PARSER = "html.parser"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9",
}


def _to_mobile_url(url: str) -> str:
    """PC URL → 모바일 URL 변환 (직접 파싱 가능한 구조)."""
    # blog.naver.com/blogId/logNo → m.blog.naver.com/blogId/logNo
    url = url.replace("blog.naver.com", "m.blog.naver.com")
    # PostView.naver?blogId=X&logNo=Y → m.blog.naver.com/X/Y
    pv = re.search(r"[?&]blogId=(\w+)&logNo=(\d+)", url)
    if pv and "PostView" in url:
        parsed = urlparse(url)
        url = f"https://m.blog.naver.com/{pv.group(1)}/{pv.group(2)}"
    return url


def _extract_iframe_src(soup: BeautifulSoup, base_url: str) -> str | None:
    """네이버 블로그 구버전 iframe(mainFrame) 내 실제 콘텐츠 URL 추출 (구버전 대응)."""
    iframe = soup.find("iframe", id="mainFrame")
    if iframe and iframe.get("src"):
        src = iframe["src"]
        if src.startswith("http"):
            return src
        return urljoin(base_url, src)
    return None


def _parse_naver_date(raw: str) -> str:
    """네이버 날짜 형식 파싱. '2026. 1. 7. 20:39' → '2026-01-07'."""
    # ISO 형식
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if m:
        return m.group(0)
    # 네이버 한국식 형식: 2026. 1. 7. 또는 2026.01.07
    m = re.search(r"(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})", raw)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return ""


def _extract_from_mobile(soup: BeautifulSoup) -> dict:
    """모바일 페이지 본문 파싱."""
    # 제목 — 여러 선택자 순서대로 시도
    title = ""
    for selector in [
        {"class": "se-title-text"},
        {"class": "tit_h3"},
        {"class": "se_textarea"},
    ]:
        tag = soup.find(attrs=selector)
        if tag:
            title = tag.get_text(strip=True)
            break
    if not title:
        t = soup.find("title")
        title = re.sub(r"\s*:\s*네이버 블로그$", "", t.get_text(strip=True)) if t else ""

    # 본문 영역
    body_div = (
        soup.find("div", class_="se-main-container")
        or soup.find("div", id="postViewArea")
        or soup.find("div", class_="post_ct")
    )
    text = body_div.get_text("\n", strip=True) if body_div else soup.get_text("\n", strip=True)

    # 이미지 수 (data-src 포함 — lazy load 대응)
    if body_div:
        img_count = len([
            img for img in body_div.find_all("img")
            if img.get("src") or img.get("data-src")
        ])
    else:
        img_count = 0

    # 해시태그 — span.post_tag 또는 텍스트 #태그
    hashtags: list[str] = []
    tag_area = soup.find("div", class_="post_tag") or soup.find("div", class_="se-tags")
    if tag_area:
        hashtags = [a.get_text(strip=True).lstrip("#") for a in tag_area.find_all("a") if a.get_text(strip=True)]
    if not hashtags:
        hashtags = re.findall(r"#([^\s#]+)", text)

    # 발행일
    published_at = ""
    date_tag = (
        soup.find(class_="blog_date")
        or soup.find(class_="se_publishDate")
        or soup.find(class_="date")
    )
    if date_tag:
        published_at = _parse_naver_date(date_tag.get_text(strip=True))

    return {
        "title":        title,
        "text":         text[:5000],
        "char_count":   len(text),
        "image_count":  img_count,
        "hashtags":     hashtags,
        "published_at": published_at,
    }


def fetch_blog_content(url: str) -> dict | None:
    """네이버 블로그 URL에서 콘텐츠 파싱. 실패 시 None 반환."""
    mobile_url = _to_mobile_url(url)

    # naver.py 패턴과 동일한 3회 재시도
    for attempt in range(3):
        try:
            res = requests.get(mobile_url, headers=_HEADERS, timeout=15)
            res.raise_for_status()
            res.encoding = "utf-8"
            soup = BeautifulSoup(res.text, _BS_PARSER)

            # 구버전 블로그: mainFrame iframe이 있는 경우 한 번 더 요청
            iframe_src = _extract_iframe_src(soup, mobile_url)
            if iframe_src:
                inner_res = requests.get(iframe_src, headers=_HEADERS, timeout=15)
                inner_res.raise_for_status()
                inner_res.encoding = "utf-8"
                soup = BeautifulSoup(inner_res.text, _BS_PARSER)

            return _extract_from_mobile(soup)

        except requests.exceptions.HTTPError as e:
            print(f"[NaverBlog 오류] HTTP {e.response.status_code} — 재시도 안 함: {mobile_url}")
            return None
        except Exception as e:
            if attempt < 2:
                wait = 2 ** attempt
                print(f"[NaverBlog 재시도 {attempt + 1}/3] {e} — {wait}s 후 재시도")
                time.sleep(wait)
            else:
                print(f"[NaverBlog 실패] {e}")
    return None
