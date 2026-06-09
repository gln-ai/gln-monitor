"""
gln-content/image_generator.py — AI 이미지 생성 모듈 (OpenAI DALL·E 3)

지원:
  - Instagram 카드뉴스: 슬라이드별 1080×1350px 이미지
  - 고라니 웹툰: 컷별 1:1 정사각형 이미지 (페이지별 넘기기 방식)

사용:
  from image_generator import generate_instagram_images, generate_cartoon_images
"""
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

CONTENT_DIR = os.path.dirname(os.path.abspath(__file__))
APPS_ROOT   = os.path.dirname(CONTENT_DIR)
SHARED_DIR  = os.path.join(APPS_ROOT, "shared")
GORANI_REFS = os.path.join(SHARED_DIR, "gorani_refs")

# 이미지 저장 루트 (gln-monitor static 하위)
IMAGE_ROOT = os.path.join(APPS_ROOT, "gln-monitor", "static", "generated", "images")

# 고라니 캐릭터 설명 (DALL·E 프롬프트용 — 레퍼런스 이미지 없을 때 텍스트로 일관성 유지)
GORANI_CHARACTER_DESC = (
    "Gorani: a cute young male Korean deer (고라니) character with big round eyes, "
    "short brown fur, small antler nubs, wearing casual traveler clothes, "
    "clumsy and expressive, webtoon style."
)
BRAD_CHARACTER_DESC = (
    "Brad: a cool confident male deer character, slightly older, "
    "well-dressed, calm expression, Korean webtoon style."
)
QUENTIN_CHARACTER_DESC = (
    "Quentin: a laid-back male deer character, casual clothes, "
    "slightly indifferent expression, Korean webtoon style."
)

# 국가 → 배경 힌트 (DALL·E 프롬프트 품질 향상)
COUNTRY_BG = {
    "thailand":    "Thailand, Bangkok, golden temple, tuk-tuk, tropical",
    "japan":       "Japan, Tokyo, neon signs, cherry blossom, Shibuya",
    "taiwan":      "Taiwan, Taipei night market, lanterns",
    "vietnam":     "Vietnam, Ho Chi Minh City, street food, motorbikes",
    "philippines": "Philippines, Manila, tropical beach",
    "singapore":   "Singapore, Marina Bay, modern skyline",
    "hongkong":    "Hong Kong, night skyline, harbour, neon",
    "macau":       "Macau, casino street, Portuguese tiles",
    "china":       "China, Beijing, Great Wall, red lanterns",
    "cambodia":    "Cambodia, Angkor Wat, temple ruins",
    "mongolia":    "Mongolia, Ulaanbaatar, steppe, ger tent",
    "laos":        "Laos, Vientiane, Mekong river, Buddhist temple",
    "guam":        "Guam, tropical beach, palm trees, clear water",
    "saipan":      "Saipan, Pacific island beach, crystal water",
}


def _get_openai_client():
    """OpenAI 클라이언트 반환 (gln-monitor .env의 OPENAI_API_KEY 사용)"""
    try:
        from openai import OpenAI
    except ImportError:
        raise RuntimeError("openai 패키지가 설치되지 않았습니다. pip install openai 실행 후 재시도하세요.")

    # gln-monitor/.env 로드
    env_path = os.path.join(APPS_ROOT, "gln-monitor", ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    if k.strip() not in os.environ:
                        os.environ[k.strip()] = v.strip()

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY가 .env에 없습니다.")
    return OpenAI(api_key=api_key)


def _save_image(url: str, save_path: str) -> str:
    """URL에서 이미지 다운로드 후 저장. 저장된 절대 경로 반환."""
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    urllib.request.urlretrieve(url, save_path)
    return save_path


def _static_rel_path(abs_path: str) -> str:
    """절대 경로 → Flask static 기준 상대 경로 (generated/images/...)"""
    monitor_static = os.path.join(APPS_ROOT, "gln-monitor", "static")
    return os.path.relpath(abs_path, monitor_static).replace(os.sep, "/")


# ---------------------------------------------------------------------------
# Instagram 카드뉴스 이미지 생성
# ---------------------------------------------------------------------------

def _parse_instagram_slides(slides_text: str) -> list[dict]:
    """
    official_generator가 출력한 <SLIDES> 내용을 슬라이드별로 파싱.
    반환: [{"label": "슬라이드1", "title": ..., "body": ...}, ...]
    """
    slides = []
    current = {}
    for line in slides_text.splitlines():
        line = line.strip()
        m_slide = re.match(r"슬라이드(\d+)", line)
        if m_slide:
            if current:
                slides.append(current)
            current = {"label": f"슬라이드{m_slide.group(1)}"}
        elif line.startswith("제목:"):
            current["title"] = line[3:].strip()
        elif line.startswith("서브:"):
            current.setdefault("sub", line[3:].strip())
        elif line.startswith("소제목:"):
            current["title"] = line[4:].strip()
        elif line.startswith("본문:"):
            current["body"] = line[3:].strip()
        elif line.startswith("CTA:"):
            current["title"] = line[4:].strip()
    if current:
        slides.append(current)
    return slides


def _build_instagram_prompt(slide: dict, topic: str, country: str, slide_num: int, total: int) -> str:
    bg_hint = COUNTRY_BG.get(country, "Asian travel destination")
    title   = slide.get("title", "")
    body    = slide.get("body", slide.get("sub", ""))

    if slide_num == 1:
        return (
            f"Create a clean, modern Instagram card image for a Korean fintech travel app called GLN. "
            f"Portrait format 1080x1350px. "
            f"Background: beautiful travel photography of {bg_hint}. "
            f"Overlay: minimal purple gradient overlay (brand color #7C3AED). "
            f"Bold white text at center: '{title}'. "
            f"If subtitle provided, smaller white text below: '{body}'. "
            f"Elegant, trustworthy, premium feel. No logos. No clutter. Photorealistic."
        )
    elif slide_num == total:
        return (
            f"Create a clean call-to-action Instagram card for GLN fintech app. "
            f"Portrait format 1080x1350px. "
            f"Soft purple gradient background (#7C3AED to #A78BFA). "
            f"Bold white CTA text: '{title}'. "
            f"Subtitle text: '{body}'. "
            f"Modern, minimal, professional. Download app button visual implied. No logos."
        )
    else:
        return (
            f"Create an informational Instagram card for a fintech travel app. "
            f"Portrait format 1080x1350px. "
            f"Background: subtle travel imagery of {bg_hint}, light purple tint overlay. "
            f"Section heading: '{title}'. "
            f"Body text area with 2-3 lines: '{body}'. "
            f"Clean, readable, card-style layout. White text on semi-transparent background panel. "
            f"Modern Korean infographic style. No logos."
        )


def generate_instagram_images(
    draft_id: int,
    slides_text: str,
    topic: str,
    country: str = "",
) -> list[str]:
    """
    Instagram 카드뉴스 이미지 생성.

    Args:
        draft_id:    content_drafts.id
        slides_text: gorani_generator 출력의 <SLIDES> 내용
        topic:       콘텐츠 주제
        country:     국가 코드 (thailand, japan 등)

    Returns:
        static 기준 상대 경로 목록 ["generated/images/123/slide_1.png", ...]
    """
    client = _get_openai_client()
    slides = _parse_instagram_slides(slides_text)
    if not slides:
        # 파싱 실패 시 topic 기반 단일 커버 이미지 생성
        slides = [{"label": "슬라이드1", "title": topic}]

    save_dir = os.path.join(IMAGE_ROOT, str(draft_id))
    paths = []

    for i, slide in enumerate(slides, start=1):
        prompt = _build_instagram_prompt(slide, topic, country, i, len(slides))
        try:
            response = client.images.generate(
                model="dall-e-3",
                prompt=prompt,
                size="1024x1792",   # DALL·E 3 지원 최근접 세로 비율 (1080×1350 유사)
                quality="standard",
                n=1,
            )
            img_url   = response.data[0].url
            save_path = os.path.join(save_dir, f"slide_{i}.png")
            _save_image(img_url, save_path)
            paths.append(_static_rel_path(save_path))
            time.sleep(1)  # API rate limit 여유
        except Exception as e:
            print(f"[image_generator] 슬라이드{i} 생성 실패: {e}")

    return paths


# ---------------------------------------------------------------------------
# 고라니 웹툰 이미지 생성
# ---------------------------------------------------------------------------

def _parse_cartoon_cuts(cuts_text: str) -> list[dict]:
    """
    gorani_generator가 출력한 <CUTS> 내용을 컷별로 파싱.
    반환: [{"cut_num": 1, "bg": ..., "characters": ..., "dialogue": ...}, ...]
    """
    cuts = []
    current = {}
    for line in cuts_text.splitlines():
        line = line.strip()
        m_cut = re.match(r"\[컷\s*(\d+)", line)
        if m_cut:
            if current:
                cuts.append(current)
            current = {"cut_num": int(m_cut.group(1))}
        elif line.startswith("배경/상황:"):
            current["bg"] = line[6:].strip()
        elif line.startswith("등장인물:"):
            current["characters"] = line[5:].strip()
        elif "대사/표정:" in line:
            current["dialogue"] = ""
        elif current.get("dialogue") is not None and ":" in line:
            current["dialogue"] = (current.get("dialogue", "") + " " + line).strip()
    if current:
        cuts.append(current)
    return cuts


def _build_cartoon_prompt(cut: dict, country: str) -> str:
    bg_hint    = COUNTRY_BG.get(country, "Asian travel scene")
    bg         = cut.get("bg", bg_hint)
    characters = cut.get("characters", "고라니")
    dialogue   = cut.get("dialogue", "")

    # 등장인물 설명 조합
    char_descs = []
    if "고라니" in characters:
        char_descs.append(GORANI_CHARACTER_DESC)
    if "부래드" in characters or "Brad" in characters.lower():
        char_descs.append(BRAD_CHARACTER_DESC)
    if "쿠앤틴" in characters or "Quentin" in characters.lower():
        char_descs.append(QUENTIN_CHARACTER_DESC)
    char_text = " ".join(char_descs) if char_descs else GORANI_CHARACTER_DESC

    dialogue_hint = f" Speech bubble text: '{dialogue[:60]}'" if dialogue else ""

    return (
        f"Korean webtoon comic panel, single page, square format 1:1. "
        f"Scene: {bg}. Background reference: {bg_hint}. "
        f"Characters: {char_text} "
        f"Flat color illustration style, clean black outlines, soft pastel fill colors, "
        f"expressive cartoon faces, Korean manhwa / webtoon aesthetic. "
        f"No real text except speech bubbles.{dialogue_hint} "
        f"High quality, print-ready, cute and humorous tone."
    )


def generate_cartoon_images(
    draft_id: int,
    cuts_text: str,
    country: str = "",
) -> list[str]:
    """
    고라니 웹툰 컷별 이미지 생성 (페이지별 넘기기 방식).

    Args:
        draft_id:  content_drafts.id
        cuts_text: gorani_generator 출력의 <CUTS> 내용
        country:   국가 코드

    Returns:
        static 기준 상대 경로 목록 ["generated/images/123/cut_1.png", ...]
    """
    client = _get_openai_client()
    cuts   = _parse_cartoon_cuts(cuts_text)
    if not cuts:
        print("[image_generator] 컷 파싱 실패 — cuts_text를 확인하세요.")
        return []

    save_dir = os.path.join(IMAGE_ROOT, str(draft_id))
    paths    = []

    for cut in cuts:
        prompt = _build_cartoon_prompt(cut, country)
        try:
            response = client.images.generate(
                model="dall-e-3",
                prompt=prompt,
                size="1024x1024",  # 정사각형 (웹툰 컷)
                quality="standard",
                n=1,
            )
            img_url   = response.data[0].url
            save_path = os.path.join(save_dir, f"cut_{cut['cut_num']}.png")
            _save_image(img_url, save_path)
            paths.append(_static_rel_path(save_path))
            time.sleep(1)
        except Exception as e:
            print(f"[image_generator] 컷{cut['cut_num']} 생성 실패: {e}")

    return paths


# ---------------------------------------------------------------------------
# 단독 실행 테스트
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=== image_generator 테스트 ===")
    print("주의: 실제 OpenAI API 호출이 발생합니다.")
    sample_cuts = """
[컷 1]
배경/상황: 태국 공항 도착 로비
등장인물: 고라니
대사/표정:
  고라니: "드디어 방콕이다!!!"

[컷 2]
배경/상황: 편의점 앞, 고라니가 지갑을 뒤지고 있음
등장인물: 고라니
대사/표정:
  고라니: "...현금이 없어"

[컷 3]
배경/상황: 스마트폰 화면 클로즈업, 퍼플GLN 앱 QR화면
등장인물: 고라니
대사/표정:
  고라니: "앗, 퍼플GLN이면 되잖아?"

[컷 4 — 마무리/반전]
배경/상황: 편의점에서 QR로 결제 성공, 환한 표정
등장인물: 고라니
대사/표정:
  고라니: "고라니는 준비된 여행자"
"""
    result = generate_cartoon_images(draft_id=9999, cuts_text=sample_cuts, country="thailand")
    print("생성된 경로:", result)
