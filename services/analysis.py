"""
services/analysis.py — AI 분석 및 댓글 초안 생성 (Claude)
"""
import json
import os
import threading

from config import APPS_ROOT, ANALYSIS_MODEL_ID
from db import get_db
from utils import get_claude_client


def _get_alert_setting(key: str, default: str = "1") -> str:
    try:
        conn = get_db()
        row  = conn.execute(
            "SELECT value FROM app_settings WHERE key=?", (key,)
        ).fetchone()
        conn.close()
        return row["value"] if row else default
    except Exception:
        return default

# ─── fact_db 서비스 현황 ────────────────────────────────────────────────────────

def _load_fact_db() -> dict:
    path = os.path.join(APPS_ROOT, "shared", "fact_db.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _build_service_context(country_code: str, fdb: dict) -> str:
    if not country_code:
        return ""
    info = fdb.get("countries", {}).get(country_code)
    if not info:
        return ""

    lines = [f"[GLN 서비스 현황 — {info['name_ko']}]"]
    lines.append(f"- QR 결제: {'지원' if info.get('qr_payment') else '미지원'}")
    lines.append(f"- ATM 출금: {'지원' if info.get('atm') else '미지원'}")
    if info.get("qr_network"):
        lines.append(f"- QR 네트워크: {', '.join(info['qr_network'])}")
    if info.get("atm_network"):
        lines.append(f"- ATM 네트워크: {', '.join(info['atm_network'])}")
    if info.get("travel_tips"):
        lines.append(f"- 현지 팁: {info['travel_tips']}")
    atm_apps = [
        app for app, data in fdb.get("app_atm_support", {}).items()
        if isinstance(data, dict) and info["name_ko"] in data.get("atm", [])
    ]
    if atm_apps:
        lines.append(f"- ATM 지원 앱: {', '.join(atm_apps)}")
    # ATM 미지원 국가의 경우 QR 지원 앱 목록 표시 (supported_apps 필드)
    if not info.get("atm") and info.get("supported_apps"):
        lines.append(f"- QR 결제 지원 앱: {', '.join(info['supported_apps'])}")
    return "\n".join(lines)


def _build_country_reference(fdb: dict) -> str:
    """분석 프롬프트에 매번 넣는 국가별 지원현황 요약표.
    country 판단과 '미지원 기능 문의'(기능요청) 구분에 씀."""
    lines = ["[GLN 지원 국가 및 서비스 현황]"]
    for code, info in fdb.get("countries", {}).items():
        name = info.get("name_ko", code)
        qr = "QR지원" if info.get("qr_payment") else "QR미지원"
        atm = "ATM지원" if info.get("atm") else "ATM미지원"
        cities = ",".join(info.get("major_cities", [])[:4])
        line = f"- {name}({code}): {qr}/{atm}"
        if cities:
            line += f" | 주요도시: {cities}"
        lines.append(line)
    return "\n".join(lines)


_FACT_DB_CACHE = _load_fact_db()
COUNTRY_REFERENCE = _build_country_reference(_FACT_DB_CACHE)
_VALID_COUNTRY_CODES = set(_FACT_DB_CACHE.get("countries", {}).keys())


# ─── 경쟁사 언급 감지 ─────────────────────────────────────────────────────────

COMPETITORS: dict[str, str] = {
    "토스페이":   "toss",
    "토스뱅크":   "toss",
    "토스":       "toss",
    "카카오페이": "kakaopay",
    "카카오 페이": "kakaopay",
    "트래블월렛": "travelwallet",
    "트래블 월렛": "travelwallet",
    "하나머니":   "hanamoney",
    "트래블로그": "travellog",
    "네이버페이": "naverpay",
    "네이버 페이": "naverpay",
    "페이코":     "payco",
    "위비트래블": "wibeetravel",
    "WISE":       "wise",
    "와이즈":     "wise",
}

COMPETITOR_LABEL: dict[str, str] = {
    "toss":         "토스",
    "kakaopay":     "카카오페이",
    "travelwallet": "트래블월렛",
    "hanamoney":    "하나머니",
    "travellog":    "트래블로그",
    "naverpay":     "네이버페이",
    "payco":        "페이코",
    "wibeetravel":  "위비트래블",
    "wise":         "와이즈",
}


def detect_competitors(text: str) -> list[str]:
    """텍스트에서 경쟁사 이름을 감지해 코드 목록 반환 (중복 제거)."""
    found: set[str] = set()
    for keyword, code in COMPETITORS.items():
        if keyword in text:
            found.add(code)
    return sorted(found)


# ─── 프롬프트 ─────────────────────────────────────────────────────────────────

ANALYSIS_PROMPT = """당신은 핀테크 브랜드 'GLN' 소셜미디어 모니터링 전문가입니다.
아래 네이버 카페/블로그/뉴스 게시글을 분석하고 JSON으로만 응답하세요. 다른 텍스트 없이 JSON만 출력하세요.

{country_reference}

게시글 제목: {title}
게시글 내용 요약: {description}
{competitor_note}
응답 형식:
{{
  "summary": "2~3문장 요약",
  "category": "문의|후기|불만|기능요청|정보공유|기타",
  "sentiment": "positive|neutral|negative",
  "importance_score": 1~10 숫자,
  "is_relevant": true 또는 false,
  "country": "위 목록의 code (예: thailand). 국가/도시가 특정되지 않으면 빈 문자열",
  "reason": "중요도 판단 근거 한 줄"
}}

country 판단: 국가명·주요도시·문맥으로 명확히 특정되면 위 목록의 code를 반환. 여러 국가가 언급돼도
글의 실제 주제인 국가 하나만 고르고, 특정 국가가 아니면 빈 문자열.

category 판단:
- 불만: 위 목록에서 "지원"으로 표시된 기능의 오류·장애·불편 신고
- 기능요청: 위 목록에서 "미지원"으로 표시된 기능을 요청하거나, 미지원인 줄 모르고 문의하는 경우
  (예: ATM미지원 국가에서 ATM 문의 — 실제 장애가 아니라 안내/로드맵 이슈)
- 문의/후기/정보공유/기타: 기존과 동일

중요도 기준:
- 실제 지원 기능의 오류·장애 신고 = 8~10
- 경쟁사와 비교되며 GLN이 불리하게 언급됨(아래 감지된 경쟁사 참고) = 최소 6점, 내용에 따라 더 높게
- 일반 문의 = 5~7
- 기능요청(미지원 기능 문의) = 3~5 (실제 장애 아니므로 8점 이상 부여 금지)
- 후기·정보공유 = 3~6
- 단순 언급 = 1~3

is_relevant 기준: GLN 해외결제/ATM/송금 서비스와 명확하게 관련 있으면 true.
  GLN이 우연히 언급되거나 완전히 다른 주제(일반 여행팁, 타사 금융, 광고성 무관 내용 등)이면 false."""

REPLY_PROMPT = """네이버 카페 게시글에 달 댓글 초안 3가지를 작성하세요.
JSON만 출력하세요. 모든 답변은 GLN 브랜드임을 티 내지 않으면서 자연스러운 카페 회원 말투로 작성합니다.

게시글: {title}
요약: {summary}
감성: {sentiment}
분류: {category}
{service_context}
작성 지침:
- "안녕하세요", "GLN입니다" 같은 공식 인사말 절대 금지
- 카페에서 이미 알고 있는 회원처럼 자연스럽게 공감하며 시작
- GLN을 직접 홍보하지 말고 경험담이나 팁처럼 자연스럽게 녹여내기
- 구어체, 이모지 자연스럽게 사용 가능
- 2~4문장으로 짧고 자연스럽게
- 서비스 현황 정보가 있으면 국가명·앱명·네트워크명을 자연스럽게 활용하기
- 미지원 항목은 절대 지원된다고 언급 금지

응답 형식:
{{
  "basic": "공감형 — 글쓴이 상황에 공감하며 가볍게 정보 제공",
  "polite": "정보제공형 — 유용한 팁과 함께 GLN 장점을 자연스럽게 언급",
  "cs": "응원형 — 따뜻하게 응원하며 GLN을 경험담처럼 추천"
}}"""


def _parse_json_response(text: str):
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text)


def analyze_post(post_id: int, title: str, description: str, competitors: list[str] | None = None):
    competitor_note = ""
    if competitors:
        labels = [COMPETITOR_LABEL.get(c, c) for c in competitors]
        competitor_note = f"\n감지된 경쟁사 언급: {', '.join(labels)} (GLN과 비교되는 맥락인지 판단해 중요도에 반영)\n"

    prompt = ANALYSIS_PROMPT.format(
        country_reference=COUNTRY_REFERENCE,
        title=title,
        description=description or "내용 없음",
        competitor_note=competitor_note,
    )
    try:
        client = get_claude_client()
        msg = client.messages.create(
            model=ANALYSIS_MODEL_ID,
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}]
        )
        result = _parse_json_response(msg.content[0].text)

        country = (result.get("country") or "").strip()
        result["country"] = country if country in _VALID_COUNTRY_CODES else ""

        return result
    except Exception as e:
        print(f"[AI 분석 오류] post {post_id}: {e}")
        return None


def generate_replies(post_id: int, title: str, summary: str,
                     sentiment: str, category: str, description: str = "",
                     country_code: str = ""):
    # country_code는 analyze_post가 이미 문맥으로 판단한 값 그대로 사용.
    # 여기서 문자열매칭으로 재추정하면 "홍콩반점"처럼 상호명이 국가명을 우연히
    # 포함하는 경우 무관한 국가 서비스 정보가 답변에 섞여 들어갈 수 있어 금지.
    fdb = _load_fact_db()
    service_ctx  = _build_service_context(country_code, fdb)
    if service_ctx:
        print(f"[답변 생성] #{post_id} 국가: {country_code}")

    prompt = REPLY_PROMPT.format(
        title=title, summary=summary,
        sentiment=sentiment, category=category,
        service_context=f"\n{service_ctx}\n" if service_ctx else "",
    )
    try:
        client = get_claude_client()
        msg = client.messages.create(
            model=ANALYSIS_MODEL_ID,
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}]
        )
        return _parse_json_response(msg.content[0].text)
    except Exception as e:
        print(f"[답변 생성 오류] post {post_id}: {e}")
        return None


def process_unanalyzed():
    """미처리 게시글 AI 분석"""
    from services.email_svc import send_urgent_alert

    conn = get_db()
    rows = conn.execute(
        "SELECT id, title, description, keyword, cafe_name, link, created_at "
        "FROM posts WHERE is_processed = 0 LIMIT 20"
    ).fetchall()
    conn.close()

    for row in rows:
        post_id     = row["id"]
        full_text   = f"{row['title']} {row['description'] or ''}"
        competitors = detect_competitors(full_text)

        analysis = analyze_post(post_id, row["title"], row["description"], competitors=competitors)
        if not analysis:
            continue

        is_urgent = 1 if analysis.get("importance_score", 0) >= 7 else 0

        is_cafe = (str(row["keyword"]).startswith("카페/") or
                   not str(row["keyword"]).startswith(("블로그/", "뉴스/")))
        replies = generate_replies(
            post_id, row["title"],
            analysis.get("summary", ""),
            analysis.get("sentiment", "neutral"),
            analysis.get("category", "기타"),
            description=row["description"] or "",
            country_code=analysis.get("country", ""),
        ) if is_cafe else None

        competitors_json = json.dumps(competitors, ensure_ascii=False) if competitors else ""

        conn = get_db()
        is_relevant = 0 if analysis.get("is_relevant") is False else 1
        conn.execute(
            """INSERT OR REPLACE INTO ai_analysis
               (post_id, summary, category, sentiment, importance_score, is_relevant, competitors, country)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (post_id, analysis.get("summary"), analysis.get("category"),
             analysis.get("sentiment"), analysis.get("importance_score"), is_relevant, competitors_json,
             analysis.get("country", ""))
        )
        conn.execute("UPDATE posts SET is_processed=1, is_urgent=? WHERE id=?",
                     (is_urgent, post_id))

        if replies:
            for rtype, content in [("basic", replies.get("basic")),
                                   ("polite", replies.get("polite")),
                                   ("cs", replies.get("cs"))]:
                if content:
                    conn.execute(
                        "INSERT INTO draft_replies (post_id, type, content) VALUES (?, ?, ?)",
                        (post_id, rtype, content)
                    )
        conn.commit()
        conn.close()

        if is_urgent and _get_alert_setting("alert_urgent_enabled", "1") == "1":
            threading.Thread(
                target=send_urgent_alert,
                args=(row["title"], analysis,
                      row["cafe_name"], row["link"], row["created_at"], post_id),
                daemon=True
            ).start()

        if competitors:
            labels = [COMPETITOR_LABEL.get(c, c) for c in competitors]
            print(f"[경쟁사 감지] #{post_id} — {', '.join(labels)}")

        print(f"[AI 완료] #{post_id} | {analysis.get('category')} | "
              f"{analysis.get('sentiment')} | 중요도 {analysis.get('importance_score')}")
