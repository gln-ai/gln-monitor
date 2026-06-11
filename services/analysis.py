"""
services/analysis.py — AI 분석 및 댓글 초안 생성 (KnuSentiLex + Claude)
"""
import json
import os
import threading

from config import APPS_ROOT
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


_CITY_TO_COUNTRY = {
    "방콕": "thailand",  "치앙마이": "thailand", "파타야": "thailand",
    "푸켓": "thailand",  "끄라비": "thailand",
    "도쿄": "japan",     "오사카": "japan",      "교토": "japan",
    "후쿠오카": "japan", "삿포로": "japan",      "오키나와": "japan",
    "나고야": "japan",   "고베": "japan",        "요코하마": "japan",
    "호치민": "vietnam", "하노이": "vietnam",    "다낭": "vietnam",
    "나트랑": "vietnam", "푸꾸옥": "vietnam",    "호이안": "vietnam",
    "타이베이": "taiwan","가오슝": "taiwan",     "타이중": "taiwan",
    "마닐라": "philippines","세부": "philippines","보라카이": "philippines",
    "싱가포르": "singapore",
    "홍콩": "hongkong",
    "마카오": "macau",
    "베이징": "china",   "상하이": "china",      "광저우": "china",
    "씨엠립": "cambodia","프놈펜": "cambodia",
    "비엔티안": "laos",  "루앙프라방": "laos",
    "울란바토르": "mongolia",
    "투몬": "guam",
}


def _detect_country_from_text(text: str, fdb: dict) -> str:
    for code, info in fdb.get("countries", {}).items():
        name_ko = info.get("name_ko", "")
        if name_ko and name_ko in text:
            return code
    for city, code in _CITY_TO_COUNTRY.items():
        if city in text:
            return code
    return ""


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
    return "\n".join(lines)


# ─── KnuSentiLex ──────────────────────────────────────────────────────────────

def load_knu_senti_dict() -> dict:
    senti_dict = {}
    senti_path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                              "KnuSentiLex", "SentiWord_info.json")
    if not os.path.exists(senti_path):
        print("[KnuSentiLex] 사전 파일 없음 — Claude 단독 분석 사용")
        return senti_dict
    try:
        with open(senti_path, encoding="utf-8") as f:
            data = json.load(f)
        for item in data:
            word = item.get("word", "").strip()
            score = float(item.get("polarity", 0))
            if word:
                senti_dict[word] = score
        print(f"[KnuSentiLex] 감성 사전 로드 완료 — {len(senti_dict)}개 단어")
    except Exception as e:
        print(f"[KnuSentiLex] 로드 오류: {e}")
    return senti_dict


KNU_DICT = load_knu_senti_dict()

GLN_DOMAIN_WORDS = {
    "결제오류": -2, "결제실패": -2, "오류": -1.5, "안됨": -1.5,
    "먹통": -2, "버그": -1.5, "중복청구": -2, "환불": -1,
    "적립안됨": -2, "포인트없어짐": -2, "사기": -2, "불편": -1,
    "편리": 1.5, "편함": 1.5, "좋아요": 2, "추천": 1.5,
    "빠름": 1, "간편": 1.5, "유용": 1.5, "만족": 2,
}


def knu_sentiment_score(text: str) -> float:
    if not text:
        return 0.0
    score = 0.0
    matched = 0
    text_clean = text.replace(" ", "")
    for word, w_score in GLN_DOMAIN_WORDS.items():
        if word in text_clean:
            score += w_score
            matched += 1
    for word, w_score in KNU_DICT.items():
        if word in text:
            score += w_score
            matched += 1
    return score / max(matched, 1) if matched > 0 else 0.0


def knu_to_label(score: float) -> str:
    if score >= 0.5:
        return "positive"
    elif score <= -0.5:
        return "negative"
    return "neutral"


# ─── 프롬프트 ─────────────────────────────────────────────────────────────────

ANALYSIS_PROMPT = """당신은 핀테크 브랜드 'GLN' 소셜미디어 모니터링 전문가입니다.
아래 네이버 카페/블로그/뉴스 게시글을 분석하고 JSON으로만 응답하세요. 다른 텍스트 없이 JSON만 출력하세요.

게시글 제목: {title}
게시글 내용 요약: {description}

응답 형식:
{{
  "summary": "2~3문장 요약",
  "category": "문의|후기|불만|정보공유|기타",
  "sentiment": "positive|neutral|negative",
  "importance_score": 1~10 숫자,
  "is_relevant": true 또는 false,
  "reason": "중요도 판단 근거 한 줄"
}}

중요도 기준: 불만/오류 보고=8~10, 문의=5~7, 후기/정보=3~6, 단순언급=1~3
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


def analyze_post(post_id: int, title: str, description: str):
    prompt = ANALYSIS_PROMPT.format(title=title, description=description or "내용 없음")
    try:
        client = get_claude_client()
        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}]
        )
        result = _parse_json_response(msg.content[0].text)

        full_text = f"{title} {description or ''}"
        knu_score = knu_sentiment_score(full_text)
        knu_label = knu_to_label(knu_score)
        claude_sentiment = result.get("sentiment", "neutral")

        if knu_score != 0.0:
            if claude_sentiment == knu_label:
                final_sentiment = claude_sentiment
            else:
                final_sentiment = knu_label if abs(knu_score) >= 1.0 else claude_sentiment
            result["sentiment"] = final_sentiment
            result["knu_score"] = round(knu_score, 2)
            print(f"[감성] Claude={claude_sentiment} KNU={knu_label}({knu_score:.2f}) -> {final_sentiment}")

        return result
    except Exception as e:
        print(f"[AI 분석 오류] post {post_id}: {e}")
        return None


def generate_replies(post_id: int, title: str, summary: str,
                     sentiment: str, category: str, description: str = ""):
    fdb          = _load_fact_db()
    country_code = _detect_country_from_text(f"{title} {description}", fdb)
    service_ctx  = _build_service_context(country_code, fdb)
    if service_ctx:
        print(f"[답변 생성] #{post_id} 국가 감지: {country_code}")

    prompt = REPLY_PROMPT.format(
        title=title, summary=summary,
        sentiment=sentiment, category=category,
        service_context=f"\n{service_ctx}\n" if service_ctx else "",
    )
    try:
        client = get_claude_client()
        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
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
        post_id  = row["id"]
        analysis = analyze_post(post_id, row["title"], row["description"])
        if not analysis:
            continue

        is_urgent = 1 if (
            analysis.get("importance_score", 0) >= 7 or
            analysis.get("sentiment") == "negative"
        ) else 0

        is_cafe = (str(row["keyword"]).startswith("카페/") or
                   not str(row["keyword"]).startswith(("블로그/", "뉴스/")))
        replies = generate_replies(
            post_id, row["title"],
            analysis.get("summary", ""),
            analysis.get("sentiment", "neutral"),
            analysis.get("category", "기타"),
            description=row["description"] or "",
        ) if is_cafe else None

        conn = get_db()
        is_relevant = 0 if analysis.get("is_relevant") is False else 1
        conn.execute(
            """INSERT OR REPLACE INTO ai_analysis
               (post_id, summary, category, sentiment, importance_score, is_relevant)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (post_id, analysis.get("summary"), analysis.get("category"),
             analysis.get("sentiment"), analysis.get("importance_score"), is_relevant)
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

        print(f"[AI 완료] #{post_id} | {analysis.get('category')} | "
              f"{analysis.get('sentiment')} | 중요도 {analysis.get('importance_score')}")
