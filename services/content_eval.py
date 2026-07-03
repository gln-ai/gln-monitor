"""
services/content_eval.py — 서포터즈 콘텐츠 품질 평가 엔진

채점 구조:
  [가이드 준수 40점] 규칙 기반 — 키워드/링크/분량
  [참여도 30점]     규칙 기반 — 플랫폼별 지표
  [콘텐츠 품질 30점 + PASS/FAIL] Claude 정성평가
  total_score = 세 항목 합산 (0~100)
  safety_status FAIL이면 total_score와 무관하게 최종 FAIL
"""
import json

from config import MODEL_ID
from utils import get_claude_client

# ─── 기본 가이드라인 (1차 하드코딩) ─────────────────────────────────────────
DEFAULT_GUIDELINE = {
    "required_keywords": ["GLN", "퍼플GLN"],
    "required_link":     True,
    "min_length": {
        "youtube_sec":        15,
        "naver_blog_chars":  500,
    },
}

_QUALITY_PROMPT = """당신은 GLN 브랜드 콘텐츠 품질 심사 전문가입니다.
아래 텍스트를 분석하고 JSON만 출력하세요. 다른 텍스트 없이 JSON만 출력.

평가 대상 텍스트:
{text}

평가 기준:
- quality_score (0~30): 정보성이 높고 독자에게 실질적 도움이 되면 높은 점수. 단순 홍보 문구 나열·과장·허위 표현이면 낮은 점수.
- safety_status: "PASS" 또는 "FAIL" — 과장/허위 표현, 부적절한 콘텐츠, 경쟁사 비방 등이 있으면 FAIL
- safety_reason: FAIL인 경우 한 줄 이유. PASS면 빈 문자열.

응답 형식 (JSON만):
{{"quality_score": 0~30, "safety_status": "PASS", "safety_reason": ""}}"""


# ─── 가이드 준수 점수 (40점) ─────────────────────────────────────────────────

def _score_guideline(raw: dict, platform: str, guideline: dict) -> tuple[int, dict]:
    """
    raw: 플랫폼 fetch 결과 dict
    반환: (점수, 세부내역)
    """
    score = 0
    detail: dict = {}

    # 분석 대상 텍스트 합산
    if platform == "youtube":
        full_text = (raw.get("title") or "") + " " + (raw.get("description") or "")
    elif platform == "naver_blog":
        full_text = (raw.get("title") or "") + " " + (raw.get("text") or "")
    else:  # instagram
        full_text = raw.get("caption") or ""

    # 1) 필수 키워드 포함 여부 (20점)
    kw_found = [kw for kw in guideline.get("required_keywords", []) if kw in full_text]
    kw_score = 20 if kw_found else 0
    detail["keyword_found"] = kw_found
    detail["keyword_score"] = kw_score
    score += kw_score

    # 2) 필수 링크/해시태그 포함 (10점) — URL 패턴 또는 해시태그 감지
    import re
    has_link = bool(
        re.search(r"https?://", full_text)
        or re.search(r"#\S+", full_text)
        or (platform == "naver_blog" and (raw.get("hashtags") or []))
    )
    link_score = 10 if (has_link or not guideline.get("required_link")) else 0
    detail["has_link_or_tag"] = has_link
    detail["link_score"] = link_score
    score += link_score

    # 3) 분량 기준 충족 (10점)
    min_len = guideline.get("min_length", {})
    if platform == "youtube":
        dur = raw.get("duration_sec", 0) or 0
        min_sec = min_len.get("youtube_sec", 15)
        length_ok = dur >= min_sec
        detail["duration_sec"] = dur
        detail["min_sec"] = min_sec
    elif platform == "naver_blog":
        chars = raw.get("char_count", 0) or 0
        min_chars = min_len.get("naver_blog_chars", 500)
        length_ok = chars >= min_chars
        detail["char_count"] = chars
        detail["min_chars"] = min_chars
    else:  # instagram — 캡션 길이 최소 50자 (가이드라인에 없으므로 만점 처리)
        length_ok = True
        detail["length_note"] = "인스타 분량 기준 없음 — 만점 처리"

    length_score = 10 if length_ok else 0
    detail["length_ok"] = length_ok
    detail["length_score"] = length_score
    score += length_score

    return score, detail


# ─── 참여도 점수 (30점) ──────────────────────────────────────────────────────

def _score_engagement(raw: dict, platform: str, manual_stats: dict | None) -> tuple[int, dict]:
    detail: dict = {}

    if platform == "youtube":
        views    = raw.get("view_count", 0) or 0
        likes    = raw.get("like_count", 0) or 0
        comments = raw.get("comment_count", 0) or 0
        if views > 0:
            rate = (likes + comments) / views
        else:
            rate = 0.0
        detail["view_count"] = views
        detail["like_count"] = likes
        detail["comment_count"] = comments
        detail["eng_rate"] = round(rate, 4)

        if rate >= 0.05:
            eng_score = 30
        elif rate >= 0.02:
            eng_score = 20
        elif rate >= 0.005:
            eng_score = 10
        else:
            eng_score = 5 if views > 0 else 0

    elif platform == "naver_blog":
        # 네이버 블로그: 정확한 조회수 수집 어려움 → 이미지 수 + 발행 후 경과일 대체 지표
        # TODO: 실제 조회수 API가 생기면 교체 필요
        img_count    = raw.get("image_count", 0) or 0
        published_at = raw.get("published_at", "") or ""
        days_elapsed = 0
        if published_at:
            from datetime import date
            try:
                pub = date.fromisoformat(published_at)
                days_elapsed = (date.today() - pub).days
            except Exception:
                pass

        # 이미지 5개 이상 + 발행 후 1일 이상이면 만점 처리 (조회수 대체)
        if img_count >= 5 and days_elapsed >= 1:
            eng_score = 30
        elif img_count >= 3:
            eng_score = 20
        elif img_count >= 1:
            eng_score = 10
        else:
            eng_score = 30  # 이미지 없어도 만점 처리 (TODO: 조회수 기반으로 교체)
        detail["image_count"] = img_count
        detail["days_elapsed"] = days_elapsed
        detail["note"] = "조회수 API 미지원 — 이미지·경과일 대체 지표 사용"

    else:  # instagram
        if manual_stats:
            likes    = int(manual_stats.get("like_count", 0) or 0)
            comments = int(manual_stats.get("comment_count", 0) or 0)
            views    = int(manual_stats.get("view_count", 0) or 0)
            total_eng = likes + comments
            detail["manual_stats"] = manual_stats

            if views > 0:
                rate = total_eng / views
                if rate >= 0.05:
                    eng_score = 30
                elif rate >= 0.02:
                    eng_score = 20
                elif total_eng >= 10:
                    eng_score = 10
                else:
                    eng_score = 5
            elif total_eng >= 50:
                eng_score = 20
            elif total_eng >= 10:
                eng_score = 10
            else:
                eng_score = 5
        else:
            eng_score = 0
            detail["note"] = "manual_stats 없음 — 0점 처리"

    detail["engagement_score"] = eng_score
    return eng_score, detail


# ─── Claude 정성평가 (품질 30점 + 안전성 PASS/FAIL) ─────────────────────────

def _score_quality_claude(text: str) -> tuple[int, str, str]:
    """반환: (quality_score, safety_status, safety_reason)"""
    if not text or not text.strip():
        return 0, "FAIL", "평가 텍스트 없음"

    prompt = _QUALITY_PROMPT.format(text=text[:3000])
    try:
        client = get_claude_client()
        msg = client.messages.create(
            model=MODEL_ID,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        # ```json ... ``` 래퍼 제거
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        result = json.loads(raw.strip())
        q = max(0, min(30, int(result.get("quality_score", 0))))
        s = result.get("safety_status", "FAIL")
        r = result.get("safety_reason", "")
        return q, s, r
    except Exception as e:
        print(f"[품질평가 오류] Claude 호출 실패: {e}")
        return 0, "FAIL", f"평가 오류: {e}"


# ─── 공통 진입점 ─────────────────────────────────────────────────────────────

def evaluate(submission: dict, guideline: dict | None = None) -> dict:
    """
    submission: content_submissions 행 (dict)
      - platform, url, manual_stats (JSON str or None)
    guideline: None이면 DEFAULT_GUIDELINE 사용

    반환:
      guideline_score, engagement_score, quality_score,
      total_score, safety_status, safety_reason, detail_json
    """
    from services.platform_detect import normalize_url
    from services.youtube import fetch_video_data
    from services.naver_blog import fetch_blog_content
    from services.instagram import fetch_reel_meta

    if guideline is None:
        guideline = DEFAULT_GUIDELINE

    platform = submission["platform"]
    url      = normalize_url(submission.get("url", ""))

    # manual_stats 파싱
    manual_stats = None
    if submission.get("manual_stats"):
        try:
            manual_stats = json.loads(submission["manual_stats"])
        except Exception:
            pass

    # ── 플랫폼별 데이터 수집 ──────────────────────────────────────────────
    raw: dict = {}
    if platform == "youtube":
        raw = fetch_video_data(url) or {}
    elif platform == "naver_blog":
        raw = fetch_blog_content(url) or {}
    elif platform == "instagram":
        raw = fetch_reel_meta(url) or {}
    else:
        print(f"[평가 오류] 알 수 없는 플랫폼: {platform}")
        return {
            "guideline_score":  0,
            "engagement_score": 0,
            "quality_score":    0,
            "total_score":      0,
            "safety_status":    "FAIL",
            "safety_reason":    f"미지원 플랫폼: {platform}",
            "detail_json":      "{}",
        }

    # ── 채점 ──────────────────────────────────────────────────────────────
    g_score, g_detail = _score_guideline(raw, platform, guideline)
    e_score, e_detail = _score_engagement(raw, platform, manual_stats)

    # Claude 정성평가용 텍스트 추출
    if platform == "youtube":
        eval_text = (raw.get("title") or "") + "\n" + (raw.get("description") or "")
    elif platform == "naver_blog":
        eval_text = (raw.get("title") or "") + "\n" + (raw.get("text") or "")
    else:
        eval_text = raw.get("caption") or ""

    q_score, safety_status, safety_reason = _score_quality_claude(eval_text)

    total = g_score + e_score + q_score

    detail = {
        "raw":        raw,
        "guideline":  g_detail,
        "engagement": e_detail,
    }

    return {
        "guideline_score":  g_score,
        "engagement_score": e_score,
        "quality_score":    q_score,
        "total_score":      total,
        "safety_status":    safety_status,
        "safety_reason":    safety_reason,
        "detail_json":      json.dumps(detail, ensure_ascii=False),
    }
