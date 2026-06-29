"""
gln-content/gorani_generator.py — 고라니 캐릭터 채널 콘텐츠 생성기
지원 포맷: reels | threads | cartoon
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
    "reels":   "instagram_gorani",
    "threads": "threads",
    "cartoon": "instagram_gorani",
}

TEMPLATE_MAP = {
    "reels":   "gorani_reels.txt",
    "threads": "gorani_threads.txt",
    "cartoon": "gorani_cartoon.txt",
}


def _load_template(name: str) -> str:
    path = os.path.join(TMPL_DIR, name)
    with open(path, encoding="utf-8") as f:
        return f.read()


def _build_prompt(fmt: str, topic: str, country: str, brief_summary: str) -> str:
    template = _load_template(TEMPLATE_MAP[fmt])
    fact_db  = load_shared("fact_db.json")

    # 고라니 채널은 서비스 핵심 정보 + 국가별 ATM 지원 여부
    atm_map = {
        v["name_ko"]: v.get("atm", False)
        for v in fact_db.get("countries", {}).values()
        if v.get("name_ko")
    }
    fact_context = json.dumps({
        "service_summary": "퍼플GLN 앱으로 해외에서 QR결제·ATM출금 가능. 현금 없이 여행 가능.",
        "countries": list(fact_db.get("countries", {}).keys()),
        "country_atm_support": atm_map,
    }, ensure_ascii=False, indent=2)

    return template.format(
        topic=topic,
        country=country or "해외 여행지",
        brief_summary=brief_summary or "없음",
        fact_db=fact_context,
    )


def _parse_tag(text: str, tag: str) -> str:
    pattern = rf"<{tag}>(.*?)</{tag}>"
    m = re.search(pattern, text, re.DOTALL)
    return m.group(1).strip() if m else ""


def generate(fmt: str, topic: str, country: str = "", brief_summary: str = "") -> dict:
    """
    고라니 채널 콘텐츠 생성.
    반환: {channel, format, platform, topic, country, <포맷별 필드들>, raw_output}
    """
    if fmt not in TEMPLATE_MAP:
        raise ValueError(f"지원하지 않는 포맷: {fmt}. 가능한 값: {list(TEMPLATE_MAP.keys())}")

    prompt = _build_prompt(fmt, topic, country, brief_summary)
    client = get_claude_client()
    msg    = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2500,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = msg.content[0].text

    result = {
        "channel":    "gorani",
        "format":     fmt,
        "platform":   PLATFORM_MAP[fmt],
        "topic":      topic,
        "country":    country,
        "raw_output": raw,
        "verify_list": "",  # 고라니 채널은 별도 검증 목록 없음
    }

    if fmt == "reels":
        result["concept"]    = _parse_tag(raw, "REELS_CONCEPT")
        result["scenes"]     = _parse_tag(raw, "SCENES")
        result["caption"]    = _parse_tag(raw, "CAPTION")
        result["hashtags"]   = _parse_tag(raw, "HASHTAGS")
        # DB 호환
        result["body"]       = result["scenes"]
        result["seo_titles"] = result["concept"]

    elif fmt == "threads":
        result["posts"]      = _parse_tag(raw, "THREADS_POSTS")
        result["best_pick"]  = _parse_tag(raw, "BEST_PICK")
        # DB 호환
        result["body"]       = result["posts"]
        result["seo_titles"] = result["best_pick"]

    elif fmt == "cartoon":
        result["concept"]    = _parse_tag(raw, "CARTOON_CONCEPT")
        result["cuts"]       = _parse_tag(raw, "CUTS")
        result["caption"]    = _parse_tag(raw, "CAPTION")
        result["hashtags"]   = _parse_tag(raw, "HASHTAGS")
        # DB 호환
        result["body"]       = result["cuts"]
        result["seo_titles"] = result["concept"]

    return result


if __name__ == "__main__":
    # 단독 실행 테스트
    print("=== 고라니 스레드 생성 테스트 ===")
    r = generate("threads", topic="해외에서 현금 없을 때 당황하는 상황", country="태국")
    print("[THREADS_POSTS]")
    print(r.get("posts", ""))
    print("\n[BEST_PICK]")
    print(r.get("best_pick", ""))

    print("\n=== 고라니 릴스 기획 테스트 ===")
    r2 = generate("reels", topic="공항 도착 후 현지 결제 첫 시도", country="일본")
    print("[컨셉]")
    print(r2.get("concept", ""))
    print("\n[장면 앞 300자]")
    print((r2.get("scenes") or "")[:300])
