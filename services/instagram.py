"""
services/instagram.py — Instagram 릴스 메타데이터 수집

설계 의도:
  - Instagram 공식 API는 타인 게시물 조회수/좋아요 수 제공 안 함.
  - oEmbed API(로그인 불필요)로 캡션·썸네일·작성자 정도만 수집.
  - 조회수/좋아요/댓글수는 CSV 업로드 시 manual_stats 컬럼으로 수동 입력.
  이것은 임시방편이 아닌 최종 설계 의도임.
"""
import re
import time

import requests

_OEMBED_URL = "https://graph.facebook.com/v19.0/instagram_oembed"


def _extract_shortcode(url: str) -> str | None:
    """인스타그램 URL에서 shortcode 추출."""
    m = re.search(r"/(?:p|reel|tv)/([A-Za-z0-9_-]+)", url)
    return m.group(1) if m else None


def fetch_reel_meta(url: str) -> dict | None:
    """
    Instagram oEmbed API로 릴스 기본 정보 수집.
    조회수/좋아요/댓글수는 API 제공 불가 → manual_stats로 수동 입력 필요.
    실패 시 None 반환.
    """
    shortcode = _extract_shortcode(url)
    if not shortcode:
        print(f"[Instagram] shortcode 추출 실패: {url}")
        return None

    # oEmbed는 access_token 없이도 기본 동작 (rate limit 있음)
    params = {"url": url, "omitscript": True}

    for attempt in range(3):
        try:
            res = requests.get(_OEMBED_URL, params=params, timeout=10)
            if res.status_code == 400:
                # oEmbed 미지원 URL 또는 비공개 게시물
                print(f"[Instagram] oEmbed 400 — 비공개 게시물이거나 URL 오류: {url}")
                # 기본 정보만 반환 (shortcode는 확보)
                return {
                    "shortcode":     shortcode,
                    "caption":       "",
                    "author":        "",
                    "thumbnail_url": "",
                    "html":          "",
                }
            res.raise_for_status()
            data = res.json()

            return {
                "shortcode":     shortcode,
                "caption":       data.get("title", ""),
                "author":        data.get("author_name", ""),
                "thumbnail_url": data.get("thumbnail_url", ""),
                "html":          data.get("html", ""),
            }
        except requests.exceptions.HTTPError as e:
            print(f"[Instagram 오류] HTTP {e.response.status_code} — 재시도 안 함")
            return None
        except Exception as e:
            if attempt < 2:
                wait = 2 ** attempt
                print(f"[Instagram 재시도 {attempt + 1}/3] {e} — {wait}s 후 재시도")
                time.sleep(wait)
            else:
                print(f"[Instagram 실패] {e}")
    return None
