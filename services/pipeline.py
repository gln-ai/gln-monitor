"""
services/pipeline.py — 이원화 콘텐츠 생성 파이프라인
채널(official/gorani) + 포맷 목록을 받아 생성 → 검수 → 이메일 → DB 저장
"""
import importlib.util
import json
import os
import sys
from datetime import datetime

from config import APPS_ROOT, KST
from db import get_db


def _load_module(name: str, file_path: str):
    spec = importlib.util.spec_from_file_location(name, file_path)
    mod  = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _get_modules():
    content_gen = _load_module(
        "content_generator",
        os.path.join(APPS_ROOT, "gln-content", "content_generator.py")
    )
    checker = _load_module(
        "checker",
        os.path.join(APPS_ROOT, "gln-guard", "checker.py")
    )
    return content_gen, checker


_DEFAULT_FORMATS = {
    "official": ["blog", "instagram_card"],
    "gorani":   ["threads", "reels"],
}


def run_content_pipeline(channel: str = None, formats: list = None):
    """
    channel: 'official' | 'gorani' | None(전체 순환)
    formats: 포맷 목록. None이면 채널 기본값 사용.
    """
    try:
        content_gen, checker = _get_modules()
    except Exception as e:
        print(f"[파이프라인] 모듈 로드 오류: {e}")
        return

    channels = [channel] if channel else ["official", "gorani"]
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M")

    for ch in channels:
        ch_formats = formats or _DEFAULT_FORMATS.get(ch, ["blog"])
        print(f"[파이프라인] {ch} 채널 시작 — 포맷: {ch_formats}")

        # 소재: Reactive 우선, 없으면 Proactive
        briefs = content_gen.get_briefs(min_score=7, limit=2)
        if not briefs:
            briefs = content_gen.get_briefs(min_score=5, limit=2)

        if briefs:
            sources = [
                {
                    "topic":          b.get("summary") or b["title"][:60],
                    "country":        content_gen.detect_country(
                                          f"{b['title']} {b.get('description','') or ''}"),
                    "brief_summary":  b.get("summary", ""),
                    "source_post_id": b["id"],
                }
                for b in briefs[:1]
            ]
        else:
            proactive = content_gen.get_proactive_topics(limit=1)
            sources = [
                {"topic": p["title"], "country": p["country"],
                 "brief_summary": "", "source_post_id": None}
                for p in proactive
            ]

        if not sources:
            print(f"[파이프라인] {ch} 채널 소재 없음")
            continue

        for src in sources:
            for fmt in ch_formats:
                try:
                    content = content_gen.generate(
                        ch, fmt,
                        topic=src["topic"],
                        country=src.get("country", ""),
                        brief_summary=src.get("brief_summary", "")
                    )
                    result = checker.check(content)
                    print(f"[파이프라인] {ch}/{fmt} 완료: {src['topic'][:30]} / {result['grade']}")

                    conn = get_db()
                    conn.execute("""
                        INSERT INTO content_drafts
                          (source_post_id, topic, seo_titles, body, shorts_script,
                           verify_list, guard_grade, guard_issues,
                           channel, format, platform, raw_output,
                           country, source_type,
                           created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        src.get("source_post_id"),
                        content.get("topic", ""),
                        content.get("seo_titles", ""),
                        content.get("body", ""),
                        content.get("shorts_script", ""),
                        content.get("verify_list", ""),
                        result.get("grade", "pending"),
                        json.dumps(result.get("issues", []), ensure_ascii=False),
                        ch,
                        fmt,
                        content.get("platform", ""),
                        content.get("raw_output", ""),
                        content.get("country", src.get("country", "")),
                        "auto",
                        now, now,
                    ))
                    conn.commit()
                    conn.close()

                except Exception as e:
                    import traceback
                    print(f"[파이프라인] {ch}/{fmt} 오류: {e}")
                    print(traceback.format_exc())


def generate_single(channel: str, fmt: str,
                    topic: str = "", country: str = "",
                    use_auto: bool = False) -> dict:
    """
    동기 단건 생성. /api/content/generate 엔드포인트에서 호출.
    반환: {"draft_id": int, "grade": str, "topic": str}
    """
    try:
        content_gen, checker = _get_modules()
    except Exception as e:
        raise RuntimeError(f"모듈 로드 오류: {e}")

    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M")

    if use_auto:
        briefs = content_gen.get_briefs(min_score=7, limit=1)
        if not briefs:
            briefs = content_gen.get_briefs(min_score=5, limit=1)
        if briefs:
            b = briefs[0]
            topic   = b.get("summary") or b["title"][:60]
            country = content_gen.detect_country(f"{b['title']} {b.get('description','') or ''}")
            source_post_id = b["id"]
        else:
            proactive = content_gen.get_proactive_topics(limit=1)
            if not proactive:
                raise RuntimeError("소재를 찾을 수 없습니다.")
            p = proactive[0]
            topic   = p["title"]
            country = p["country"]
            source_post_id = None
    else:
        source_post_id = None
        # 직접 입력 모드에서 country 비어있으면 토픽에서 자동 감지
        if not country and topic:
            country = content_gen.detect_country(topic)

    # auto 모드에서 summary를 brief_summary로 전달 (품질 향상)
    brief_text = ""
    if use_auto and briefs:
        brief_text = (briefs[0].get("summary") or briefs[0].get("description") or "")[:300]

    content = content_gen.generate(channel, fmt, topic=topic,
                                   country=country, brief_summary=brief_text)
    result  = checker.check(content)

    conn = get_db()
    cur  = conn.execute("""
        INSERT INTO content_drafts
          (source_post_id, topic, seo_titles, body, shorts_script,
           verify_list, guard_grade, guard_issues,
           channel, format, platform, raw_output,
           country, source_type,
           created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        source_post_id,
        content.get("topic", topic),
        content.get("seo_titles", ""),
        content.get("body", ""),
        content.get("shorts_script", ""),
        content.get("verify_list", ""),
        result.get("grade", "pending"),
        json.dumps(result.get("issues", []), ensure_ascii=False),
        channel, fmt,
        content.get("platform", ""),
        content.get("raw_output", ""),
        content.get("country", country),
        "auto" if use_auto else "manual",
        now, now,
    ))
    draft_id = cur.lastrowid
    conn.commit()
    conn.close()

    return {
        "draft_id": draft_id,
        "grade":    result.get("grade", "pending"),
        "topic":    content.get("topic", topic),
        "channel":  channel,
        "format":   fmt,
    }
