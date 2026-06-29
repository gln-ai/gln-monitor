"""
gln-content/official_generator.py — 공식 채널 콘텐츠 생성기
지원 포맷: blog | instagram_card | youtube_shorts
"""
import json
import os
import re
import sys

CONTENT_DIR = os.path.dirname(os.path.abspath(__file__))
APPS_ROOT   = os.path.dirname(CONTENT_DIR)
SHARED_DIR  = os.path.join(APPS_ROOT, "shared")
TMPL_DIR    = os.path.join(SHARED_DIR, "prompt_templates")

if SHARED_DIR not in sys.path:
    sys.path.insert(0, SHARED_DIR)

from utils import get_claude_client, load_shared

PLATFORM_MAP = {
    "blog":            "naver_blog",
    "instagram_card":  "instagram_official",
    "youtube_shorts":  "youtube",
    "threads":         "threads",
}

TEMPLATE_MAP = {
    "blog":           "official_blog.txt",
    "instagram_card": "official_instagram.txt",
    "youtube_shorts": "official_shorts.txt",
    "threads":        "official_threads.txt",
}


def _load_template(name: str) -> str:
    path = os.path.join(TMPL_DIR, name)
    with open(path, encoding="utf-8") as f:
        return f.read()


def _build_prompt(fmt: str, topic: str, country: str, brief_summary: str) -> str:
    template = _load_template(TEMPLATE_MAP[fmt])
    fact_db  = load_shared("fact_db.json")
    fw       = load_shared("forbidden_words.json")

    # fact_db — 해당 국가 정보만 추출
    country_info = {}
    if country and "countries" in fact_db:
        for k, v in fact_db["countries"].items():
            if country.lower() in k.lower() or (v.get("name_ko") and country in v.get("name_ko", "")):
                country_info = v
                break

    fact_context = json.dumps({
        "company":       fact_db.get("company", {}),
        "service":       fact_db.get("service_categories", {}),
        "country":       country_info or "국가 정보 없음",
        "disclaimer":    fact_db.get("global_disclaimer", ""),
        "app_atm_support": fact_db.get("app_atm_support", {}),
    }, ensure_ascii=False, indent=2)

    forbidden_str = (
        "hard_block(절대금지): " + ", ".join(fw.get("hard_block", [])) + "\n"
        "soft_warn(주의): " + ", ".join(fw.get("soft_warn", []))
    )

    return template.format(
        topic=topic,
        country=country or "국가 미지정",
        brief_summary=brief_summary or "없음",
        fact_db=fact_context,
        forbidden_words=forbidden_str,
    )


def _parse_tag(text: str, tag: str) -> str:
    pattern = rf"<{tag}>(.*?)</{tag}>"
    m = re.search(pattern, text, re.DOTALL)
    return m.group(1).strip() if m else ""


def generate(fmt: str, topic: str, country: str = "", brief_summary: str = "") -> dict:
    """
    공식 채널 콘텐츠 생성.
    반환: {channel, format, platform, topic, country, <포맷별 필드들>, verify_list, raw_output}
    """
    if fmt not in TEMPLATE_MAP:
        raise ValueError(f"지원하지 않는 포맷: {fmt}. 가능한 값: {list(TEMPLATE_MAP.keys())}")

    prompt = _build_prompt(fmt, topic, country, brief_summary)
    client = get_claude_client()
    msg    = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=3000,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = msg.content[0].text

    result = {
        "channel":      "official",
        "format":       fmt,
        "platform":     PLATFORM_MAP[fmt],
        "topic":        topic,
        "country":      country,
        "verify_list":  _parse_tag(raw, "VERIFY_LIST"),
        "raw_output":   raw,
    }

    if fmt == "blog":
        result["seo_titles"]    = _parse_tag(raw, "SEO_TITLES")
        result["body"]          = _parse_tag(raw, "BODY")

    elif fmt == "instagram_card":
        result["slides"]        = _parse_tag(raw, "SLIDES")
        result["caption"]       = _parse_tag(raw, "CAPTION")
        result["hashtags"]      = _parse_tag(raw, "HASHTAGS")
        # body 필드에 slides 저장 (DB 호환)
        result["body"]          = result["slides"]
        result["seo_titles"]    = result["caption"][:100] if result["caption"] else ""

    elif fmt == "youtube_shorts":
        result["shorts_title"]  = _parse_tag(raw, "SHORTS_TITLE")
        result["script"]        = _parse_tag(raw, "SCRIPT")
        result["caption_hooks"] = _parse_tag(raw, "CAPTION_HOOKS")
        result["description"]   = _parse_tag(raw, "DESCRIPTION")
        # DB 호환
        result["body"]          = result["script"]
        result["seo_titles"]    = result["shorts_title"]
        result["shorts_script"] = result["script"]

    elif fmt == "threads":
        result["posts"]      = _parse_tag(raw, "THREADS_POSTS")
        result["best_pick"]  = _parse_tag(raw, "BEST_PICK")
        # DB 호환
        result["body"]       = result["posts"]
        result["seo_titles"] = result["best_pick"][:100] if result["best_pick"] else ""

    return result


if __name__ == "__main__":
    # 단독 실행 테스트
    print("=== 공식 블로그 생성 테스트 ===")
    r = generate("blog", topic="태국 여행 QR결제 준비", country="태국")
    print("[SEO 제목]")
    print(r.get("seo_titles", ""))
    print("\n[본문 앞 200자]")
    print((r.get("body") or "")[:200])
    print("\n[검증 목록]")
    print(r.get("verify_list", ""))

    print("\n=== 고라니 인스타 카드뉴스 생성 테스트 ===")
    r2 = generate("instagram_card", topic="해외 ATM 사용 전 체크리스트", country="일본")
    print("[슬라이드 앞 300자]")
    print((r2.get("slides") or "")[:300])
