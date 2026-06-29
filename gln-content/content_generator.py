"""
gln-content/content_generator.py — 콘텐츠 생성 라우터
채널(official/gorani) + 포맷을 받아 적절한 생성기로 위임한다.
소재 수급: Reactive(모니터링 DB) + Proactive(주제 큐)
"""
import os
import sqlite3
import sys

_THIS_DIR   = os.path.dirname(os.path.abspath(__file__))
_APPS_ROOT  = os.path.dirname(_THIS_DIR)
# symlink 또는 Railway 번들 시: db.py가 _APPS_ROOT에 있으면 그 자체가 gln-monitor
MONITOR_DIR = _APPS_ROOT if os.path.exists(os.path.join(_APPS_ROOT, "db.py")) \
              else os.path.join(_APPS_ROOT, "gln-monitor")
MONITOR_DB  = os.environ.get("DB_PATH") or os.path.join(MONITOR_DIR, "gln_monitor.db")

# .env 로드
from dotenv import load_dotenv
load_dotenv(os.path.join(MONITOR_DIR, ".env"))

# 채널별 포맷 정의
OFFICIAL_FORMATS = ["blog", "instagram_card", "youtube_shorts", "threads"]
GORANI_FORMATS   = ["reels", "threads", "cartoon"]
ALL_FORMATS      = OFFICIAL_FORMATS + GORANI_FORMATS

# 국가 감지 매핑 (확장 가능)
COUNTRY_MAP = {
    "태국": "thailand", "방콕": "thailand",
    "일본": "japan",    "도쿄": "japan",     "오사카": "japan",
    "대만": "taiwan",   "타이베이": "taiwan",
    "베트남": "vietnam", "호치민": "vietnam",  "하노이": "vietnam",
    "필리핀": "philippines", "마닐라": "philippines",
    "싱가포르": "singapore",
    "말레이시아": "malaysia", "쿠알라룸푸르": "malaysia",
    "홍콩": "hongkong",
    "마카오": "macau",
    "중국": "china",        "베이징": "china",      "상하이": "china",
    "캄보디아": "cambodia",  "프놈펜": "cambodia",
    "몽골": "mongolia",     "울란바토르": "mongolia",
    "라오스": "laos",
    "괌": "guam",
    "사이판": "saipan",
}


def detect_country(text: str) -> str:
    for kor, eng in COUNTRY_MAP.items():
        if kor in text:
            return eng
    return ""


# ── 소재 수급 ────────────────────────────────────────────────────────────────

def get_briefs(min_score: int = 7, limit: int = 5) -> list[dict]:
    """모니터링 DB에서 고중요도 포스트를 소재로 추출한다.
    최근 60일 내 이미 콘텐츠 소재로 사용된 포스트는 제외해 중복 생성을 방지한다.
    """
    conn = sqlite3.connect(MONITOR_DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT p.id, p.title, p.description, p.keyword,
               a.summary, a.category, a.sentiment, a.importance_score
        FROM posts p
        JOIN ai_analysis a ON p.id = a.post_id
        WHERE a.importance_score >= ?
          AND p.is_processed = 1
          AND (p.title LIKE '%GLN%' OR p.description LIKE '%GLN%' OR p.keyword LIKE '%GLN%')
          AND p.id NOT IN (
              SELECT source_post_id FROM content_drafts
              WHERE source_post_id IS NOT NULL
                AND deleted_at IS NULL
                AND created_at >= date('now', '-60 days')
          )
        ORDER BY a.importance_score DESC
        LIMIT ?
    """, (min_score, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def brief_to_topic(brief: dict) -> tuple[str, str]:
    """포스트 데이터에서 (topic, country) 추출."""
    full_text = f"{brief.get('title', '')} {brief.get('description', '') or ''}"
    topic     = brief.get("summary") or brief.get("title", "")[:60]
    country   = detect_country(full_text)
    return topic, country


def get_proactive_topics(limit: int = 5) -> list[dict]:
    """
    국가 로테이션 기반 자체 기획 주제 목록 반환.
    모니터링 DB 데이터가 없을 때 대체 소재로 사용.
    """
    countries = [
        ("태국",   "thailand"),
        ("일본",   "japan"),
        ("베트남", "vietnam"),
        ("대만",   "taiwan"),
        ("필리핀", "philippines"),
    ]
    topics = [
        "{country} 여행 전 GLN으로 결제 준비하는 법",
        "{country} 현지 QR결제 사용처 총정리",
        "{country} 여행 초보가 자주 하는 결제 실수",
        "해외 ATM 없이도 OK — {country}에서 GLN 활용법",
        "{country} 여행 예산 절약, 결제 수단부터 챙기자",
    ]
    result = []
    for i in range(min(limit, len(topics))):
        kor, eng = countries[i % len(countries)]
        result.append({
            "id":          None,
            "title":       topics[i].format(country=kor),
            "summary":     topics[i].format(country=kor),
            "description": "",
            "sentiment":   "neutral",
            "category":    "정보공유",
            "country":     eng,
        })
    return result


# ── 생성 라우터 ────────────────────────────────────────────────────────────────

def generate(channel: str, fmt: str, topic: str,
             country: str = "", brief_summary: str = "") -> dict:
    """
    channel: 'official' | 'gorani'
    fmt:     OFFICIAL_FORMATS 또는 GORANI_FORMATS 중 하나
    """
    if channel == "official":
        if fmt not in OFFICIAL_FORMATS:
            raise ValueError(f"공식 채널 지원 포맷: {OFFICIAL_FORMATS}")
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "official_generator",
            os.path.join(_THIS_DIR, "official_generator.py")
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.generate(fmt, topic, country, brief_summary)

    elif channel == "gorani":
        if fmt not in GORANI_FORMATS:
            raise ValueError(f"고라니 채널 지원 포맷: {GORANI_FORMATS}")
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "gorani_generator",
            os.path.join(_THIS_DIR, "gorani_generator.py")
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.generate(fmt, topic, country, brief_summary)

    else:
        raise ValueError(f"알 수 없는 채널: {channel}. 'official' 또는 'gorani' 사용")


# ── 하위 호환 (pipeline.py에서 generate_blog 호출 유지) ──────────────────────

def generate_blog(brief: dict) -> dict:
    """기존 pipeline.py 호환용. 공식 블로그 글 생성."""
    topic, country = brief_to_topic(brief)
    result = generate("official", "blog", topic, country, brief.get("summary", ""))
    result["brief_id"] = brief.get("id")
    return result


if __name__ == "__main__":
    print("=== Reactive 소재 확인 ===")
    briefs = get_briefs(min_score=7, limit=3)
    print(f"DB 소재 {len(briefs)}건")

    print("\n=== Proactive 주제 목록 ===")
    for t in get_proactive_topics(5):
        print(f"  - {t['title']} ({t['country']})")

    print("\n=== 공식 블로그 생성 테스트 ===")
    topics = get_proactive_topics(1)
    r = generate("official", "blog", topics[0]["title"], topics[0]["country"])
    print(f"채널: {r['channel']} / 포맷: {r['format']} / 플랫폼: {r['platform']}")
    print((r.get("seo_titles") or "")[:100])

    print("\n=== 고라니 스레드 생성 테스트 ===")
    r2 = generate("gorani", "threads", "해외에서 현금 없을 때 당황하는 고라니", "태국")
    print(f"채널: {r2['channel']} / 포맷: {r2['format']}")
    print((r2.get("posts") or "")[:200])
