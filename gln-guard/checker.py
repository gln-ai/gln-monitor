"""
gln-guard/checker.py — 채널별 콘텐츠 검수/필터링
채널: 'official' | 'gorani'
등급: green(승인가능) | yellow(검토필요) | red(수정필요)

check()       — 규칙 기반 빠른 검수 (기존)
check_ai()    — Claude 3단계 AI 검수 (팩트→역할혼입→톤앤매너)
check_full()  — 규칙 + AI 통합 (pipeline 권장)
"""
import json
import os
import re
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from dotenv import load_dotenv

_THIS_DIR   = os.path.dirname(os.path.abspath(__file__))
_APPS_ROOT  = os.path.dirname(_THIS_DIR)
# symlink 또는 Railway 번들 시: db.py가 _APPS_ROOT에 있으면 그 자체가 gln-monitor
MONITOR_DIR = _APPS_ROOT if os.path.exists(os.path.join(_APPS_ROOT, "db.py")) \
              else os.path.join(_APPS_ROOT, "gln-monitor")
SHARED      = os.path.join(os.path.dirname(MONITOR_DIR), "shared")

load_dotenv(os.path.join(MONITOR_DIR, ".env"))


def _load_shared(filename):
    with open(os.path.join(SHARED, filename), encoding="utf-8") as f:
        return json.load(f)


# 공식 채널에서 걸러야 할 밈/비격식 표현 패턴
_OFFICIAL_TONE_WARN = [
    r"ㅋㅋ", r"ㅎㅎ", r"헐", r"대박", r"진짜요\?",
    r"실화냐", r"멘붕", r"찌질", r"개좋", r"개꿀",
    r"ㄷㄷ", r"ㅠㅠ", r"레전드", r"넘나", r"겁나",
]

# 고라니 채널에서 걸러야 할 직접 광고 문구
_GORANI_AD_WARN = [
    r"지금 바로 다운로드",
    r"지금 신청하세요",
    r"구매하러 가기",
    r"링크 클릭",
    r"할인 쿠폰",
    r"특별 혜택.*?신청",
]


def _parse_grade(text: str) -> str:
    """응답 텍스트에서 최종 등급(GREEN/YELLOW/RED) 파싱"""
    for line in text.splitlines():
        m = re.search(r'최종\s*등급[:\s]+([A-Z]+)', line, re.IGNORECASE)
        if m:
            g = m.group(1).upper()
            if g in ("RED", "YELLOW", "GREEN"):
                return g.lower()
    # 헤더 없이 나왔을 때 폴백
    upper = text.upper()
    if "RED" in upper:
        return "red"
    if "YELLOW" in upper:
        return "yellow"
    return "green"


def check_ai(content_obj: dict) -> dict:
    """
    Claude API로 3단계 AI 검수 (팩트체커 → 역할혼입 → 톤앤매너).
    RED 판정 시 이후 단계 스킵. API 오류 시 grade=green 반환 (비차단).
    """
    try:
        import anthropic
        import httpx
    except ImportError:
        print("[Guard AI] anthropic 패키지 없음 — AI 검수 스킵")
        return {"grade": "green", "issues": [], "ai_results": []}

    PROMPTS_DIR = os.path.join(_THIS_DIR, "prompts")
    channel = content_obj.get("channel", "official")
    fmt     = content_obj.get("format", "blog")
    body    = content_obj.get("body", "") or ""
    country = content_obj.get("country", "") or "공통"

    ch_label  = "메인채널" if channel == "official" else "고라니채널"
    fmt_map   = {
        "blog": "블로그", "instagram_card": "인스타",
        "youtube_shorts": "쇼츠", "threads": "스레드",
        "reels": "릴스", "cartoon": "웹툰",
    }
    fmt_label = fmt_map.get(fmt, fmt)

    content_input = (
        f"[검수 요청]\n채널: {ch_label}\n포맷: {fmt_label}\n대상 국가: {country}\n\n"
        f"--- 콘텐츠 시작 ---\n{body}\n--- 콘텐츠 끝 ---"
    )

    checkers = [
        ("팩트체커",   "fact_checker.txt"),
        ("역할혼입",   "role_checker.txt"),
        ("톤앤매너",   "tone_checker.txt"),
    ]

    client        = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"), http_client=httpx.Client())
    overall_grade = "green"
    issues        = []
    ai_results    = []

    for agent_name, prompt_file in checkers:
        if overall_grade == "red":
            break

        prompt_path = os.path.join(PROMPTS_DIR, prompt_file)
        try:
            with open(prompt_path, encoding="utf-8") as f:
                system_prompt = f.read().strip()
        except FileNotFoundError:
            print(f"[Guard AI] 프롬프트 파일 없음: {prompt_file}")
            continue

        # fact_checker.txt: fact_db.json에서 국가 목록·앱 ATM 표를 동적으로 주입
        if prompt_file == "fact_checker.txt":
            try:
                fdb = _load_shared("fact_db.json")
                c_data = fdb.get("countries", {})
                atm_list     = [v["name_ko"] for v in c_data.values() if v.get("atm")        and v.get("name_ko")]
                qr_only_list = [v["name_ko"] for v in c_data.values() if not v.get("atm")   and v.get("qr_payment") and v.get("name_ko")]
                app_atm      = fdb.get("app_atm_support", {})
                rows = "\n".join(
                    f"| {app} | {', '.join(d.get('atm', [])) or '없음'} |"
                    for app, d in app_atm.items()
                    if isinstance(d, dict)
                )
                app_atm_table = "| 앱 | ATM 지원 국가 |\n|----|-------------|\n" + rows if rows else "| (데이터 없음) | - |"
                system_prompt = (system_prompt
                    .replace("{atm_countries}",   " / ".join(atm_list)     if atm_list     else "없음")
                    .replace("{qr_only_countries}", " / ".join(qr_only_list) if qr_only_list else "없음")
                    .replace("{app_atm_table}",   app_atm_table))
            except Exception as e:
                print(f"[Guard AI] fact_db 동적 주입 실패: {e}")

        try:
            resp  = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1024,
                system=system_prompt,
                messages=[{"role": "user", "content": content_input}],
            )
            result_text = resp.content[0].text
        except Exception as e:
            print(f"[Guard AI] {agent_name} API 오류: {e}")
            continue

        grade = _parse_grade(result_text)
        ai_results.append({"checker": agent_name, "grade": grade, "result": result_text[:600]})

        if grade == "red":
            overall_grade = "red"
            issues.append({"type": f"ai_{agent_name}", "detail": result_text[:400]})
        elif grade == "yellow" and overall_grade == "green":
            overall_grade = "yellow"
            issues.append({"type": f"ai_{agent_name}", "detail": result_text[:400]})

    return {"grade": overall_grade, "issues": issues, "ai_results": ai_results}


def check_full(content_obj: dict) -> dict:
    """
    규칙 기반 검수 → AI 3단계 검수 통합.
    pipeline.py에서 check() 대신 이 함수를 사용하면 됩니다.
    """
    result = check(content_obj)
    if result["grade"] == "red":
        return result  # hard block — AI 호출 불필요

    ai = check_ai(content_obj)
    if ai["grade"] == "red":
        result["grade"] = "red"
    elif ai["grade"] == "yellow" and result["grade"] == "green":
        result["grade"] = "yellow"
    result["issues"].extend(ai.get("issues", []))
    result["ai_results"] = ai.get("ai_results", [])
    return result


def check(content_obj: dict) -> dict:
    """
    content_obj: generate() 반환값. 'channel' 키 필수.
    반환: {grade, issues}
    """
    channel  = content_obj.get("channel", "official")
    body     = content_obj.get("body", "") or ""
    raw      = content_obj.get("raw_output", "") or body
    issues   = []
    grade    = "green"

    forbidden = _load_shared("forbidden_words.json")

    # ── 공통: hard_block — 채널 무관 절대 금지 ─────────────────────────────
    for w in forbidden.get("hard_block", []):
        if w in raw:
            issues.append({"type": "hard_block", "word": w})
            grade = "red"

    if channel == "official":
        # ── 공식: soft_warn ────────────────────────────────────────────────
        for w in forbidden.get("soft_warn", []):
            if w in raw:
                issues.append({"type": "soft_warn", "word": w})
                if grade == "green":
                    grade = "yellow"

        # ── 공식: 검증필요 태그 ────────────────────────────────────────────
        verify_count = len(re.findall(r"\[검증필요[^\]]*\]", raw))
        if verify_count:
            issues.append({"type": "unverified", "count": verify_count})
            if grade == "green":
                grade = "yellow"

        # ── 공식: 밈/비격식 톤 감지 ───────────────────────────────────────
        for pattern in _OFFICIAL_TONE_WARN:
            if re.search(pattern, raw):
                issues.append({"type": "tone_warn", "word": pattern.strip("\\")})
                if grade == "green":
                    grade = "yellow"

    elif channel == "gorani":
        # ── 고라니: 직접 광고 문구 감지 ───────────────────────────────────
        for pattern in _GORANI_AD_WARN:
            if re.search(pattern, raw):
                issues.append({"type": "ad_direct", "word": re.sub(r"[\\.*?]", "", pattern)})
                if grade == "green":
                    grade = "yellow"

    return {"grade": grade, "issues": issues}


def send_approval_email(content_obj: dict, guard_result: dict) -> bool:
    grade = guard_result["grade"]
    if grade == "red":
        print(f"[Guard] red 등급 — 수정 필요: {guard_result['issues']}")
        return False

    channel = content_obj.get("channel", "official")
    fmt     = content_obj.get("format", "blog")

    grade_label = {"green": "승인 가능", "yellow": "검토 후 승인"}.get(grade, "")
    ch_label    = {"official": "공식 채널", "gorani": "고라니 채널"}.get(channel, channel)
    ch_color    = "#7000FC" if channel == "official" else "#F97316"

    issues_html = "".join(
        f'<li>[{i["type"]}] {i.get("word") or str(i.get("count","?")) + "개 항목"}</li>'
        for i in guard_result["issues"]
    ) or "<li>이슈 없음</li>"

    # 포맷별 미리보기 구성
    preview_sections = ""
    if fmt == "blog":
        preview_sections = f"""
        <h3>SEO 제목</h3>
        <pre style="background:#f8f9fa;padding:12px;border-radius:6px">{content_obj.get("seo_titles","")}</pre>
        <h3>본문 미리보기</h3>
        <pre style="background:#f8f9fa;padding:12px;border-radius:6px">{(content_obj.get("body","") or "")[:500]}...</pre>
        <h3>검증 필요 항목</h3>
        <pre style="background:#fff3cd;padding:12px;border-radius:6px">{content_obj.get("verify_list","없음")}</pre>"""
    elif fmt == "instagram_card":
        preview_sections = f"""
        <h3>슬라이드 구성</h3>
        <pre style="background:#f8f9fa;padding:12px;border-radius:6px">{(content_obj.get("slides","") or "")[:500]}</pre>
        <h3>캡션</h3>
        <pre style="background:#f8f9fa;padding:12px;border-radius:6px">{content_obj.get("caption","")}</pre>"""
    elif fmt == "youtube_shorts":
        preview_sections = f"""
        <h3>쇼츠 스크립트</h3>
        <pre style="background:#f8f9fa;padding:12px;border-radius:6px">{(content_obj.get("script","") or "")[:500]}</pre>"""
    elif fmt == "reels":
        preview_sections = f"""
        <h3>릴스 컨셉</h3>
        <pre style="background:#f8f9fa;padding:12px;border-radius:6px">{content_obj.get("concept","")}</pre>
        <h3>장면 구성</h3>
        <pre style="background:#f8f9fa;padding:12px;border-radius:6px">{(content_obj.get("scenes","") or "")[:500]}</pre>"""
    elif fmt == "threads":
        preview_sections = f"""
        <h3>스레드 포스트</h3>
        <pre style="background:#f8f9fa;padding:12px;border-radius:6px">{content_obj.get("posts","")}</pre>
        <h3>추천 버전</h3>
        <pre style="background:#f8f9fa;padding:12px;border-radius:6px">{content_obj.get("best_pick","")}</pre>"""
    elif fmt == "cartoon":
        preview_sections = f"""
        <h3>툰 컨셉</h3>
        <pre style="background:#f8f9fa;padding:12px;border-radius:6px">{content_obj.get("concept","")}</pre>
        <h3>컷 구성</h3>
        <pre style="background:#f8f9fa;padding:12px;border-radius:6px">{(content_obj.get("cuts","") or "")[:500]}</pre>"""

    html = f"""
    <div style="font-family:-apple-system,sans-serif;max-width:680px;margin:auto;padding:24px">
      <div style="background:{ch_color};border-radius:10px;padding:14px 20px;margin-bottom:20px;display:flex;justify-content:space-between;align-items:center">
        <div>
          <div style="font-size:11px;color:rgba(255,255,255,0.7);margin-bottom:4px">GLN 콘텐츠 승인 요청</div>
          <div style="font-size:16px;font-weight:700;color:#fff">{content_obj.get("topic","")[:50]}</div>
        </div>
        <div style="background:rgba(255,255,255,0.2);border-radius:8px;padding:6px 14px;text-align:center">
          <div style="font-size:12px;color:#fff;font-weight:600">{ch_label}</div>
          <div style="font-size:11px;color:rgba(255,255,255,0.8)">{fmt}</div>
        </div>
      </div>
      <table style="width:100%;border-collapse:collapse;margin-bottom:16px">
        <tr><td style="padding:6px 0;color:#6B7280;font-size:13px;width:80px">검수 결과</td>
            <td style="font-size:13px;font-weight:600;color:{"#16A34A" if grade=="green" else "#D97706"}">{grade_label}</td></tr>
        <tr><td style="padding:6px 0;color:#6B7280;font-size:13px">국가</td>
            <td style="font-size:13px">{content_obj.get("country","-") or "-"}</td></tr>
        <tr><td style="padding:6px 0;color:#6B7280;font-size:13px">플랫폼</td>
            <td style="font-size:13px">{content_obj.get("platform","-")}</td></tr>
      </table>
      <h3 style="font-size:13px;color:#374151">검수 이슈</h3>
      <ul style="font-size:13px;padding-left:20px">{issues_html}</ul>
      {preview_sections}
      <p style="font-size:11px;color:#9CA3AF;margin-top:24px;text-align:center">GLN 콘텐츠 파이프라인 자동 발송</p>
    </div>"""

    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASS")
    to_addr   = os.getenv("REPORT_TO") or os.getenv("URGENT_ALERT_TO")

    if not smtp_user or not smtp_pass or not to_addr:
        print(f"[Guard] SMTP 미설정 — 콘솔 출력\n등급: {grade_label} / 이슈: {guard_result['issues']}")
        return True

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[GLN {ch_label}] {grade_label} — {content_obj.get('topic','')[:35]} ({fmt})"
    msg["From"]    = smtp_user
    msg["To"]      = to_addr
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        with smtplib.SMTP(os.getenv("SMTP_HOST", "smtp.gmail.com"),
                          int(os.getenv("SMTP_PORT", "587")), timeout=30) as s:
            s.ehlo(); s.starttls(); s.ehlo()
            s.login(smtp_user, smtp_pass)
            s.sendmail(smtp_user, to_addr, msg.as_string())
        print(f"[Guard] 승인 요청 메일 발송 → {to_addr}")
        return True
    except Exception as e:
        print(f"[Guard] 메일 오류: {e}")
        return False


def send_publish_package_email(content_obj: dict) -> bool:
    """발행 패키지 이메일 — 담당자가 바로 복붙할 수 있도록 전체 콘텐츠 포함."""
    channel = content_obj.get("channel", "official")
    fmt     = content_obj.get("format", "blog")
    topic   = content_obj.get("topic", "") or ""

    ch_label  = {"official": "공식 채널", "gorani": "고라니 채널"}.get(channel, channel)
    ch_color  = "#7000FC" if channel == "official" else "#F97316"
    fmt_label = {
        "blog": "네이버 블로그", "instagram_card": "인스타 카드뉴스",
        "youtube_shorts": "유튜브 쇼츠", "reels": "릴스",
        "threads": "스레드", "cartoon": "카툰",
    }.get(fmt, fmt)

    def _sec(title, text, bg="#f8f9fa"):
        if not text or str(text).strip() in ("", "None", "없음"):
            return ""
        return (
            f'<div style="margin-bottom:18px">'
            f'<div style="font-size:11px;font-weight:600;color:#6B7280;letter-spacing:0.08em;'
            f'text-transform:uppercase;margin-bottom:6px">{title}</div>'
            f'<pre style="background:{bg};border-radius:8px;padding:12px 14px;font-size:13px;'
            f'color:#130D2A;line-height:1.7;white-space:pre-wrap;word-break:break-all;'
            f'font-family:-apple-system,sans-serif;border:0.5px solid #E4E1EE;margin:0">'
            f'{text}</pre></div>'
        )

    checklist_map = {
        "blog":            ["검증 항목 확인", "대표 이미지 준비", "카테고리 선택", "예약 발행 설정"],
        "instagram_card":  ["카드 이미지 제작 (Canva 등)", "캡션 붙여넣기", "해시태그 확인", "위치 태그"],
        "youtube_shorts":  ["영상 촬영/편집", "자막 추가", "썸네일 제작", "제목·설명 복사 후 입력"],
        "reels":           ["영상 촬영 (장면 구성 참고)", "자막 삽입", "캡션·해시태그 입력"],
        "threads":         ["추천 버전 복사", "링크 첨부 여부 확인", "발행 시간대 확인"],
        "cartoon":         ["일러스트 제작 (컷 구성 참고)", "대사 검토", "캡션 작성"],
    }
    checklist_html = "".join(
        f'<li style="padding:4px 0;font-size:13px">☐ {item}</li>'
        for item in checklist_map.get(fmt, ["콘텐츠 검토", "플랫폼에 업로드"])
    )

    if fmt == "blog":
        body_html = (
            _sec("SEO 제목", content_obj.get("seo_titles") or "")
            + _sec("본문 전체", content_obj.get("body") or "")
            + _sec("검증 필요 항목", content_obj.get("verify_list") or "", "#FFFBEB")
        )
    elif fmt == "instagram_card":
        body_html = (
            _sec("슬라이드 구성", content_obj.get("body") or "")
            + _sec("캡션 / 해시태그", content_obj.get("seo_titles") or "")
        )
    elif fmt == "youtube_shorts":
        body_html = (
            _sec("쇼츠 스크립트", content_obj.get("body") or "")
            + _sec("제목", content_obj.get("seo_titles") or "")
        )
    elif fmt == "reels":
        body_html = (
            _sec("릴스 컨셉", content_obj.get("seo_titles") or "")
            + _sec("장면 구성", content_obj.get("body") or "")
        )
    elif fmt == "threads":
        body_html = (
            _sec("스레드 포스트 (3종)", content_obj.get("body") or "")
            + _sec("추천 버전", content_obj.get("seo_titles") or "")
        )
    elif fmt == "cartoon":
        body_html = (
            _sec("카툰 컨셉", content_obj.get("seo_titles") or "")
            + _sec("컷 구성", content_obj.get("body") or "")
        )
    else:
        body_html = _sec("콘텐츠", content_obj.get("body") or "")

    raw = content_obj.get("raw_output") or ""
    if raw:
        body_html += _sec("원본 AI 출력 (전체)", raw, "#F0F4FF")

    html = f"""
    <div style="font-family:-apple-system,sans-serif;max-width:680px;margin:auto;padding:24px">
      <div style="background:{ch_color};border-radius:10px;padding:16px 20px;margin-bottom:24px">
        <div style="font-size:11px;color:rgba(255,255,255,0.7);margin-bottom:4px">GLN 발행 패키지</div>
        <div style="font-size:18px;font-weight:700;color:#fff;margin-bottom:6px">{topic}</div>
        <div style="display:flex;gap:8px">
          <span style="background:rgba(255,255,255,0.2);color:#fff;padding:2px 10px;border-radius:6px;font-size:11px;font-weight:600">{ch_label}</span>
          <span style="background:rgba(255,255,255,0.15);color:#fff;padding:2px 10px;border-radius:6px;font-size:11px">{fmt_label}</span>
        </div>
      </div>
      {body_html}
      <div style="background:#F6F5FF;border-radius:10px;padding:16px 20px;margin-top:8px">
        <div style="font-size:12px;font-weight:700;color:#130D2A;margin-bottom:8px">📋 업로드 체크리스트</div>
        <ul style="margin:0;padding-left:4px;list-style:none">{checklist_html}</ul>
      </div>
      <p style="font-size:11px;color:#9CA3AF;margin-top:20px;text-align:center">GLN 콘텐츠 파이프라인 자동 발송</p>
    </div>"""

    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASS")
    to_addr   = os.getenv("REPORT_TO") or os.getenv("URGENT_ALERT_TO")

    if not smtp_user or not smtp_pass or not to_addr:
        print(f"[Guard] SMTP 미설정 — 콘솔 출력\n[발행패키지] {ch_label} / {fmt_label} / {topic}")
        return True

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[GLN {ch_label}] 발행 패키지 — {topic[:40]} ({fmt_label})"
    msg["From"]    = smtp_user
    msg["To"]      = to_addr
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        with smtplib.SMTP(os.getenv("SMTP_HOST", "smtp.gmail.com"),
                          int(os.getenv("SMTP_PORT", "587")), timeout=30) as s:
            s.ehlo(); s.starttls(); s.ehlo()
            s.login(smtp_user, smtp_pass)
            s.sendmail(smtp_user, to_addr, msg.as_string())
        print(f"[Guard] 발행 패키지 메일 발송 → {to_addr}")
        return True
    except Exception as e:
        print(f"[Guard] 메일 오류: {e}")
        return False
