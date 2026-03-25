"""
GLN 네이버 카페 모니터링 웹앱
실행: python app.py
대시보드: http://localhost:5001
"""

import os
import hashlib
import json
import sqlite3
import smtplib
import threading
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import pytz
import requests
import anthropic
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, render_template, jsonify, request
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
KST = pytz.timezone("Asia/Seoul")

def get_claude_client():
    import httpx
    return anthropic.Anthropic(
        api_key=os.getenv("ANTHROPIC_API_KEY"),
        http_client=httpx.Client()
    )

# ─── KnuSentiLex 한국어 감성 사전 ─────────────────────────────────────────────

def load_knu_senti_dict():
    senti_dict = {}
    senti_path = os.path.join(os.path.dirname(__file__), "KnuSentiLex", "SentiWord_info.json")
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

# ─── DB 초기화 ──────────────────────────────────────────────────────────────

DB_PATH = os.environ.get("DB_PATH", "/app/data/gln_monitor.db") if os.path.exists("/app/data") else "gln_monitor.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS posts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            title       TEXT NOT NULL,
            link        TEXT NOT NULL,
            description TEXT,
            cafe_name   TEXT,
            post_date   TEXT,
            created_at  TEXT DEFAULT (datetime('now','localtime')),
            hash        TEXT UNIQUE,
            keyword     TEXT,
            is_processed INTEGER DEFAULT 0,
            is_urgent   INTEGER DEFAULT 0,
            reply_status TEXT DEFAULT '미확인',
            status_updated_at TEXT
        );
        -- 기존 테이블에 컬럼이 없으면 추가 (마이그레이션)
        

        CREATE TABLE IF NOT EXISTS ai_analysis (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id          INTEGER UNIQUE,
            summary          TEXT,
            category         TEXT,
            sentiment        TEXT,
            importance_score INTEGER,
            created_at       TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (post_id) REFERENCES posts(id)
        );

        CREATE TABLE IF NOT EXISTS draft_replies (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER,
            type    TEXT,
            content TEXT,
            FOREIGN KEY (post_id) REFERENCES posts(id)
        );
    """)
    conn.commit()
    # 마이그레이션 — 기존 DB에 컬럼 없으면 추가
    try:
        conn.execute("ALTER TABLE posts ADD COLUMN reply_status TEXT DEFAULT '미확인'")
        conn.commit()
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE posts ADD COLUMN status_updated_at TEXT")
        conn.commit()
    except Exception:
        pass
    conn.close()

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# ─── 네이버 수집 ──────────────────────────────────────────────────────────────

KEYWORDS = ["GLN", "퍼플GLN", "GLN ATM", "GLN 해외결제", "GLN 출금"]

# 채널별 API 엔드포인트
CHANNELS = {
    "카페":  "https://openapi.naver.com/v1/search/cafearticle.json",
    "블로그": "https://openapi.naver.com/v1/search/blog.json",
    "뉴스":  "https://openapi.naver.com/v1/search/news.json",
}

def make_hash(title: str, link: str) -> str:
    return hashlib.md5(f"{title}{link}".encode()).hexdigest()

def parse_date(item: dict) -> str:
    """채널별 날짜 파싱 — 카페: postdate(20260324), 뉴스/블로그: pubDate(RFC2822)"""
    raw = item.get("postdate", "") or item.get("pubDate", "")
    if not raw:
        return ""
    # 카페: 20260324 형식
    if raw.isdigit() and len(raw) == 8:
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    # 뉴스/블로그: Thu, 24 Mar 2026 03:58:00 +0900 형식
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(raw)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return ""

def fetch_naver_posts(keyword: str, channel: str = "카페", display: int = 30) -> list[dict]:
    url = CHANNELS.get(channel, CHANNELS["카페"])
    headers = {
        "X-Naver-Client-Id":     os.getenv("NAVER_CLIENT_ID"),
        "X-Naver-Client-Secret": os.getenv("NAVER_CLIENT_SECRET"),
    }
    params = {"query": keyword, "display": display, "sort": "date"}
    try:
        res = requests.get(url, headers=headers, params=params, timeout=10)
        res.raise_for_status()
        return res.json().get("items", [])
    except Exception as e:
        print(f"[수집 오류] {channel}/{keyword}: {e}")
        return []

def collect_all():
    """모든 키워드 × 채널 수집 → DB 저장 → AI 처리 트리거"""
    print(f"[{datetime.now(KST).strftime('%H:%M')}] 수집 시작...")
    conn = get_db()
    new_count = 0

    for channel in CHANNELS.keys():
        for keyword in KEYWORDS:
            items = fetch_naver_posts(keyword, channel)
            for item in items:
                title = item.get("title", "").replace("<b>", "").replace("</b>", "")
                link  = item.get("link", "")
                desc  = item.get("description", "").replace("<b>", "").replace("</b>", "")
                h     = make_hash(title, link)

                # 채널별 출처명 처리
                if channel == "카페":
                    source = item.get("cafename", "")
                elif channel == "블로그":
                    source = item.get("bloggername", "")
                else:
                    source = item.get("originallink", "")[:30] if item.get("originallink") else ""

                try:
                    conn.execute(
                        """INSERT INTO posts (title, link, description, cafe_name, post_date, hash, keyword)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (title, link, desc, source,
                         parse_date(item),
                         h, f"{channel}/{keyword}")
                    )
                    conn.commit()
                    new_count += 1
                except sqlite3.IntegrityError:
                    pass  # 중복

    conn.close()
    print(f"[수집 완료] 신규 {new_count}건")

    if new_count > 0:
        threading.Thread(target=process_unanalyzed, daemon=True).start()

# ─── AI 분석 ──────────────────────────────────────────────────────────────────

ANALYSIS_PROMPT = """당신은 핀테크 브랜드 'GLN' 소셜미디어 모니터링 전문가입니다.
아래 네이버 카페 게시글을 분석하고 JSON으로만 응답하세요. 다른 텍스트 없이 JSON만 출력하세요.

게시글 제목: {title}
게시글 내용 요약: {description}

응답 형식:
{{
  "summary": "2~3문장 요약",
  "category": "문의|후기|불만|정보공유|기타",
  "sentiment": "positive|neutral|negative",
  "importance_score": 1~10 숫자,
  "reason": "중요도 판단 근거 한 줄"
}}

중요도 기준: 불만/오류 보고=8~10, 문의=5~7, 후기/정보=3~6, 단순언급=1~3"""

REPLY_PROMPT = """네이버 카페 게시글에 달 댓글 초안 3가지를 작성하세요.
JSON만 출력하세요. 모든 답변은 GLN 브랜드임을 티 내지 않으면서 자연스러운 카페 회원 말투로 작성합니다.

게시글: {title}
요약: {summary}
감성: {sentiment}
분류: {category}

작성 지침:
- "안녕하세요", "GLN입니다" 같은 공식 인사말 절대 금지
- 카페에서 이미 알고 있는 회원처럼 자연스럽게 공감하며 시작
- GLN을 직접 홍보하지 말고 경험담이나 팁처럼 자연스럽게 녹여내기
- 구어체, 이모지 자연스럽게 사용 가능
- 2~4문장으로 짧고 자연스럽게

응답 형식:
{{
  "basic": "공감형 — 글쓴이 상황에 공감하며 가볍게 정보 제공",
  "polite": "정보제공형 — 유용한 팁과 함께 GLN 장점을 자연스럽게 언급",
  "cs": "응원형 — 따뜻하게 응원하며 GLN을 경험담처럼 추천"
}}"""

def analyze_post(post_id: int, title: str, description: str):
    prompt = ANALYSIS_PROMPT.format(title=title, description=description or "내용 없음")
    try:
        client = get_claude_client()
        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}]
        )
        text = msg.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text)

        # KnuSentiLex 점수로 감성 보정
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

def generate_replies(post_id: int, title: str, summary: str, sentiment: str, category: str):
    prompt = REPLY_PROMPT.format(
        title=title, summary=summary,
        sentiment=sentiment, category=category
    )
    try:
        client = get_claude_client()
        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}]
        )
        text = msg.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text)
    except Exception as e:
        print(f"[답변 생성 오류] post {post_id}: {e}")
        return None

def process_unanalyzed():
    """미처리 게시글 AI 분석"""
    conn = get_db()
    rows = conn.execute(
        "SELECT id, title, description, keyword, cafe_name, link, created_at FROM posts WHERE is_processed = 0 LIMIT 20"
    ).fetchall()
    conn.close()

    for row in rows:
        post_id = row["id"]
        analysis = analyze_post(post_id, row["title"], row["description"])
        if not analysis:
            continue

        is_urgent = 1 if (
            analysis.get("importance_score", 0) >= 7 or
            analysis.get("sentiment") == "negative"
        ) else 0

        # 답변 초안은 카페 게시글만 생성
        is_cafe = str(row["keyword"]).startswith("카페/") or not str(row["keyword"]).startswith(("블로그/", "뉴스/"))
        replies = generate_replies(
            post_id, row["title"],
            analysis.get("summary", ""),
            analysis.get("sentiment", "neutral"),
            analysis.get("category", "기타")
        ) if is_cafe else None

        conn = get_db()
        conn.execute(
            """INSERT OR REPLACE INTO ai_analysis
               (post_id, summary, category, sentiment, importance_score)
               VALUES (?, ?, ?, ?, ?)""",
            (post_id, analysis.get("summary"), analysis.get("category"),
             analysis.get("sentiment"), analysis.get("importance_score"))
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

        if is_urgent:
            threading.Thread(
                target=send_urgent_alert,
                args=(row["title"], analysis,
                      row["cafe_name"], row["link"], row["created_at"], post_id),
                daemon=True
            ).start()

        print(f"[AI 완료] #{post_id} | {analysis.get('category')} | {analysis.get('sentiment')} | 중요도 {analysis.get('importance_score')}")

# ─── 이메일 ───────────────────────────────────────────────────────────────────

def send_email(to: str, subject: str, html_body: str):
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASS")
    from_addr = os.getenv("REPORT_FROM", smtp_user)

    if not smtp_user or not smtp_pass:
        print("[이메일] SMTP 설정 없음 — 스킵")
        return

    # 쉼표로 구분된 여러 수신자 지원
    recipients = [r.strip() for r in to.split(",") if r.strip()]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = from_addr
    msg["To"]      = ", ".join(recipients)
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(from_addr, recipients, msg.as_string())
        print(f"[이메일 발송] {subject} → {', '.join(recipients)}")
    except Exception as e:
        print(f"[이메일 오류] {e}")

def send_urgent_alert(title: str, analysis: dict, cafe_name: str = "", link: str = "", created_at: str = "", post_id: int = 0):
    to = os.getenv("URGENT_ALERT_TO", "brad@glninternational.com")
    if not to:
        return

    # 발송 시간 제한: 08:00 ~ 18:00 (KST)
    now_hour = datetime.now(KST).hour
    if not (8 <= now_hour < 18):
        print(f"[긴급 알림] 발송 시간 외 ({now_hour}시) — 스킵")
        return

    base_url = os.getenv("BASE_URL", "https://gln-monitor-production.up.railway.app")
    collected_at = created_at[:16] if created_at else datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    detail_url = f"{base_url}/post/{post_id}" if post_id else base_url
    link_btn = f'<a href="{detail_url}" style="display:inline-block;margin-top:16px;padding:8px 16px;background:#1D4ED8;color:#fff;text-decoration:none;border-radius:6px;font-size:13px">상세보기 →</a>'
    html = f"""
    <div style="font-family:sans-serif;max-width:600px;margin:auto;padding:20px">
      <div style="background:#FEE2E2;border-left:4px solid #EF4444;padding:12px 16px;border-radius:4px;margin-bottom:16px">
        <strong style="color:#B91C1C">긴급 알림 — GLN 모니터링</strong>
      </div>
      <h2 style="font-size:16px;color:#111">{title}</h2>
      <table style="width:100%;font-size:14px;border-collapse:collapse">
        <tr><td style="padding:6px 0;color:#666;width:80px">요약</td><td>{analysis.get('summary','')}</td></tr>
        <tr><td style="padding:6px 0;color:#666">분류</td><td>{analysis.get('category','')}</td></tr>
        <tr><td style="padding:6px 0;color:#666">감성</td><td>{analysis.get('sentiment','')}</td></tr>
        <tr><td style="padding:6px 0;color:#666">중요도</td><td>{analysis.get('importance_score','')}/10</td></tr>
        <tr><td style="padding:6px 0;color:#666">카페</td><td>{cafe_name or '-'}</td></tr>
        <tr><td style="padding:6px 0;color:#666">수집일시</td><td>{collected_at}</td></tr>
      </table>
      {link_btn}
      <p style="font-size:12px;color:#999;margin-top:24px">GLN 모니터링 시스템 자동 발송</p>
    </div>"""
    send_email(to, f"[GLN 긴급] {title[:40]}", html)

def send_daily_report(to: str = ""):
    if not to:
        to = os.getenv("REPORT_TO", "")
    print(f"[리포트] 수신자: {to}", flush=True)
    print(f"[리포트] SMTP_USER: {os.getenv('SMTP_USER')}", flush=True)
    print(f"[리포트] SMTP_PASS: {'있음' if os.getenv('SMTP_PASS') else '없음'}", flush=True)
    if not to:
        print("[리포트] 수신자 없음 — 스킵")
        return
    try:
        base_url = os.getenv("BASE_URL", "https://gln-monitor-production.up.railway.app")
        today = datetime.now(KST).strftime("%Y-%m-%d")
        print(f"[리포트] 날짜: {today}")
        conn = get_db()
        channels = ["카페", "블로그", "뉴스"]
        cat_posts = {}
        for ch in channels:
            rows = conn.execute("""
                SELECT p.id, p.title, p.link, p.cafe_name, p.created_at, p.keyword,
                       a.summary, a.category, a.sentiment, a.importance_score
                FROM posts p
                LEFT JOIN ai_analysis a ON p.id = a.post_id
                WHERE DATE(p.created_at) = DATE('now','localtime')
                  AND p.keyword LIKE ?
                ORDER BY a.importance_score DESC NULLS LAST
                LIMIT 10
            """, (f"{ch}/%",)).fetchall()
            if rows:
                cat_posts[ch] = rows
        total = conn.execute(
            "SELECT COUNT(*) as cnt FROM posts WHERE DATE(created_at)=DATE('now','localtime')"
        ).fetchone()["cnt"]
        urgent = conn.execute(
            "SELECT COUNT(*) as cnt FROM posts WHERE is_urgent=1 AND DATE(created_at)=DATE('now','localtime')"
        ).fetchone()["cnt"]
        ch_counts = {}
        for ch in ["카페", "블로그", "뉴스"]:
            cnt = conn.execute(
                "SELECT COUNT(*) FROM posts WHERE DATE(created_at)=DATE('now','localtime') AND keyword LIKE ?",
                (f"{ch}/%",)
            ).fetchone()[0]
            ch_counts[ch] = cnt
        conn.close()

        def post_row(p):
            sc = {"positive":"#16A34A","neutral":"#6B7280","negative":"#DC2626"}.get(p["sentiment"],"#6B7280")
            sl = {"positive":"긍정","neutral":"중립","negative":"부정"}.get(p["sentiment"], "-")
            cat = p["category"] or "-"
            return f"""
            <tr style="border-bottom:1px solid #F3F4F6">
              <td style="padding:10px 8px;font-size:13px">
                <a href="{p['link']}" style="color:#1D4ED8;text-decoration:none;font-weight:500">{(p['title'] or '')[:50]}</a>
                <div style="font-size:12px;color:#6B7280;margin-top:3px">{p['summary'] or '분석 중...'}</div>
                <div style="font-size:11px;color:#9CA3AF;margin-top:3px">{p['cafe_name'] or ''} · {(p['created_at'] or '')[:10]}</div>
              </td>
              <td style="padding:10px 8px;font-size:12px;color:#374151;white-space:nowrap">{cat}</td>
              <td style="padding:10px 8px;font-size:12px;color:{sc};white-space:nowrap;font-weight:500">{sl}</td>
              <td style="padding:10px 8px;font-size:12px;text-align:center">{p['importance_score'] or '-'}</td>
            </tr>"""

        sections_html = ""
        ch_colors = {"카페":"#1D4ED8","블로그":"#059669","뉴스":"#D97706"}
        for ch, posts in cat_posts.items():
            color = ch_colors.get(ch, "#6B7280")
            more_url = f"{base_url}/?channel={ch}&date_from={today}&date_to={today}"
            rows = "".join(post_row(p) for p in posts)
            sections_html += f"""
            <div style="margin-bottom:28px">
              <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
                <h2 style="font-size:14px;font-weight:600;color:{color};margin:0">
                  {ch} ({len(posts)}건)
                </h2>
                <a href="{more_url}" style="font-size:11px;color:#6B7280;text-decoration:none">대시보드에서 더보기 →</a>
              </div>
              <table style="width:100%;border-collapse:collapse;font-size:14px;border:1px solid #F3F4F6;border-radius:8px;overflow:hidden">
                <thead>
                  <tr style="background:#F9FAFB">
                    <th style="padding:7px 8px;text-align:left;font-size:11px;color:#9CA3AF">제목 / 요약</th>
                    <th style="padding:7px 8px;text-align:left;font-size:11px;color:#9CA3AF">분류</th>
                    <th style="padding:7px 8px;text-align:left;font-size:11px;color:#9CA3AF">감성</th>
                    <th style="padding:7px 8px;text-align:center;font-size:11px;color:#9CA3AF">중요도</th>
                  </tr>
                </thead>
                <tbody>{rows}</tbody>
              </table>
            </div>"""

        html = f"""
        <div style="font-family:-apple-system,sans-serif;max-width:680px;margin:auto;padding:24px;background:#fff">
          <h1 style="font-size:20px;color:#111;margin:0 0 4px">GLN 일일 모니터링 리포트</h1>
          <p style="font-size:13px;color:#6B7280;margin:0 0 20px">{today}</p>
          <div style="display:flex;gap:12px;margin-bottom:24px">
            <div style="background:#F9FAFB;border:1px solid #E5E7EB;border-radius:10px;padding:14px 20px;flex:1;text-align:center">
              <div style="font-size:26px;font-weight:700;color:#111">{total}</div>
              <div style="font-size:12px;color:#6B7280;margin-top:2px">오늘 수집</div>
            </div>
            <div style="background:#FEF2F2;border:1px solid #FECACA;border-radius:10px;padding:14px 20px;flex:1;text-align:center">
              <div style="font-size:26px;font-weight:700;color:#DC2626">{urgent}</div>
              <div style="font-size:12px;color:#6B7280;margin-top:2px">긴급 알림</div>
            </div>
            <div style="background:#F0FDF4;border:1px solid #BBF7D0;border-radius:10px;padding:14px 20px;flex:1;text-align:center">
              <div style="font-size:13px;font-weight:600;color:#166534">{ch_counts.get("카페",0)}건 카페</div>
              <div style="font-size:13px;font-weight:600;color:#1E40AF;margin-top:4px">{ch_counts.get("블로그",0)}건 블로그</div>
              <div style="font-size:13px;font-weight:600;color:#92400E;margin-top:4px">{ch_counts.get("뉴스",0)}건 뉴스</div>
            </div>
          </div>
          {sections_html}
          <div style="border-top:1px solid #F3F4F6;padding-top:16px;text-align:center">
            <a href="{base_url}" style="display:inline-block;padding:8px 20px;background:#111;color:#fff;border-radius:8px;text-decoration:none;font-size:13px">대시보드 열기</a>
            <p style="font-size:11px;color:#9CA3AF;margin-top:12px">GLN 모니터링 시스템 자동 발송</p>
          </div>
        </div>"""

        print(f"[리포트] HTML 생성 완료, 발송 시작")
        send_email(to, f"[GLN 일일 리포트] {today} — {total}건 수집", html)
    except Exception as e:
        import traceback
        print(f"[리포트 오류] {e}")
        print(traceback.format_exc())

@app.route("/")
def dashboard():
    conn = get_db()

    # 필터 파라미터
    sentiment  = request.args.get("sentiment", "")
    category   = request.args.get("category", "")
    urgent     = request.args.get("urgent", "")
    today_str  = datetime.now(KST).strftime("%Y-%m-%d")
    date_from  = request.args.get("date_from", today_str)
    date_to    = request.args.get("date_to", today_str)
    channel    = request.args.get("channel", "")

    reply_status = request.args.get("reply_status", "")

    query = """
        SELECT p.id, p.title, p.link, p.cafe_name, p.post_date, p.is_urgent,
               p.keyword, p.created_at, p.reply_status, p.status_updated_at,
               a.summary, a.category, a.sentiment, a.importance_score
        FROM posts p
        LEFT JOIN ai_analysis a ON p.id = a.post_id
        WHERE 1=1
    """
    args = []
    if sentiment:
        query += " AND a.sentiment = ?"; args.append(sentiment)
    if category:
        query += " AND a.category = ?";  args.append(category)
    if urgent == "1":
        query += " AND p.is_urgent = 1"
    if channel:
        query += " AND p.keyword LIKE ?"; args.append(f"{channel}/%")
    if reply_status:
        query += " AND p.reply_status = ?"; args.append(reply_status)
    if date_from:
        query += " AND DATE(p.created_at) >= ?"; args.append(date_from)
    if date_to:
        query += " AND DATE(p.created_at) <= ?"; args.append(date_to)

    # 페이지네이션
    page = int(request.args.get("page", 1))
    per_page = 50
    offset = (page - 1) * per_page

    count_query = """
        SELECT COUNT(*) FROM posts p
        LEFT JOIN ai_analysis a ON p.id = a.post_id
        WHERE 1=1
    """
    count_args = []
    if sentiment:
        count_query += " AND a.sentiment = ?"; count_args.append(sentiment)
    if category:
        count_query += " AND a.category = ?"; count_args.append(category)
    if urgent == "1":
        count_query += " AND p.is_urgent = 1"
    if channel:
        count_query += " AND p.keyword LIKE ?"; count_args.append(f"{channel}/%")
    if reply_status:
        count_query += " AND p.reply_status = ?"; count_args.append(reply_status)
    if date_from:
        count_query += " AND DATE(p.created_at) >= ?"; count_args.append(date_from)
    if date_to:
        count_query += " AND DATE(p.created_at) <= ?"; count_args.append(date_to)

    total = conn.execute(count_query, count_args).fetchone()[0]
    total_pages = max(1, (total + per_page - 1) // per_page)

    query += f" ORDER BY p.created_at DESC LIMIT {per_page} OFFSET {offset}"
    posts = conn.execute(query, args).fetchall()

    # 통계 (날짜 필터 적용)
    stats_where = "WHERE 1=1"
    stats_args = []
    if date_from:
        stats_where += " AND DATE(created_at) >= ?"; stats_args.append(date_from)
    if date_to:
        stats_where += " AND DATE(created_at) <= ?"; stats_args.append(date_to)

    # 채널별 카운트
    channel_counts = {}
    for ch in ["카페", "블로그", "뉴스"]:
        cnt = conn.execute(
            f"SELECT COUNT(*) FROM posts {stats_where} AND keyword LIKE ?",
            stats_args + [f"{ch}/%"]
        ).fetchone()[0]
        channel_counts[ch] = cnt

    stats = {
        "today":    conn.execute(f"SELECT COUNT(*) FROM posts {stats_where}", stats_args).fetchone()[0],
        "urgent":   conn.execute(f"SELECT COUNT(*) FROM posts {stats_where} AND is_urgent=1", stats_args).fetchone()[0],
        "negative": conn.execute("SELECT COUNT(*) FROM ai_analysis WHERE sentiment='negative'").fetchone()[0],
        "total":    conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0],
    }
    conn.close()
    report_to = os.getenv("REPORT_TO", "")
    return render_template("dashboard.html", posts=posts, stats=stats,
                           channel_counts=channel_counts,
                           report_to=report_to,
                           today_str=today_str,
                           filters={"sentiment": sentiment, "category": category,
                                    "urgent": urgent, "date_from": date_from, "date_to": date_to,
                                    "channel": channel, "reply_status": reply_status},
                           page=page, total_pages=total_pages, total=total)

@app.route("/post/<int:post_id>")
def post_detail(post_id):
    conn = get_db()
    post = conn.execute(
        """SELECT p.*, a.summary, a.category, a.sentiment, a.importance_score
           FROM posts p LEFT JOIN ai_analysis a ON p.id=a.post_id
           WHERE p.id=?""", (post_id,)
    ).fetchone()
    replies = conn.execute(
        "SELECT type, content FROM draft_replies WHERE post_id=?", (post_id,)
    ).fetchall()
    conn.close()
    return render_template("post_detail.html", post=post, replies=replies)

@app.route("/api/status/<int:post_id>", methods=["POST"])
def api_update_status(post_id):
    data = request.get_json(silent=True) or {}
    status = data.get("status", "")
    if status not in ["미확인", "확인완료", "답변완료"]:
        return jsonify({"error": "잘못된 상태값"}), 400
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    conn = get_db()
    conn.execute(
        "UPDATE posts SET reply_status=?, status_updated_at=? WHERE id=?",
        (status, now, post_id)
    )
    conn.commit()
    conn.close()
    return jsonify({"status": status, "updated_at": now})

@app.route("/api/collect", methods=["POST"])
def api_collect():
    threading.Thread(target=collect_all, daemon=True).start()
    return jsonify({"status": "수집 시작됨"})

@app.route("/api/process", methods=["POST"])
def api_process():
    threading.Thread(target=process_unanalyzed, daemon=True).start()
    return jsonify({"status": "AI 분석 시작됨"})

@app.route("/api/report", methods=["POST"])
def api_report():
    data = request.get_json(silent=True) or {}
    to = data.get("to", "").strip()
    if not to:
        to = os.getenv("REPORT_TO", "")
    print(f"[API] 리포트 발송 요청 수신 — 수신자: {to}", flush=True)
    threading.Thread(target=send_daily_report, args=(to,), daemon=True).start()
    return jsonify({"status": "리포트 발송 시작됨"})

@app.route("/insights")
def insights():
    return render_template("insights.html")

@app.route("/api/insights")
def api_insights():
    conn = get_db()
    days = int(request.args.get("days", 7))

    # 일별 채널별 수집량
    daily = conn.execute("""
        SELECT DATE(created_at) as day,
               SUM(CASE WHEN keyword LIKE '카페/%' THEN 1 ELSE 0 END) as cafe,
               SUM(CASE WHEN keyword LIKE '블로그/%' THEN 1 ELSE 0 END) as blog,
               SUM(CASE WHEN keyword LIKE '뉴스/%' THEN 1 ELSE 0 END) as news,
               COUNT(*) as total
        FROM posts
        WHERE created_at >= DATE('now', ?, 'localtime')
        GROUP BY DATE(created_at)
        ORDER BY day ASC
    """, (f'-{days} days',)).fetchall()

    # 감성 분포
    sentiment = conn.execute("""
        SELECT a.sentiment, COUNT(*) as cnt
        FROM ai_analysis a
        JOIN posts p ON a.post_id = p.id
        WHERE p.created_at >= DATE('now', ?, 'localtime')
          AND a.sentiment IS NOT NULL
        GROUP BY a.sentiment
    """, (f'-{days} days',)).fetchall()

    # 키워드별 수집량
    keywords = conn.execute("""
        SELECT SUBSTR(keyword, INSTR(keyword, '/') + 1) as kw,
               COUNT(*) as cnt
        FROM posts
        WHERE created_at >= DATE('now', ?, 'localtime')
          AND keyword IS NOT NULL
        GROUP BY kw
        ORDER BY cnt DESC
        LIMIT 10
    """, (f'-{days} days',)).fetchall()

    # 전체 통계
    total = conn.execute("SELECT COUNT(*) FROM posts WHERE created_at >= DATE('now', ?, 'localtime')", (f'-{days} days',)).fetchone()[0]
    urgent = conn.execute("SELECT COUNT(*) FROM posts WHERE is_urgent=1 AND created_at >= DATE('now', ?, 'localtime')", (f'-{days} days',)).fetchone()[0]
    negative = conn.execute("""
        SELECT COUNT(*) FROM ai_analysis a JOIN posts p ON a.post_id=p.id
        WHERE a.sentiment='negative' AND p.created_at >= DATE('now', ?, 'localtime')
    """, (f'-{days} days',)).fetchone()[0]
    conn.close()

    return jsonify({
        "daily": [dict(r) for r in daily],
        "sentiment": [dict(r) for r in sentiment],
        "keywords": [dict(r) for r in keywords],
        "summary": {"total": total, "urgent": urgent, "negative": negative}
    })

@app.route("/api/stats")
def api_stats():
    conn = get_db()
    stats = {
        "today":    conn.execute("SELECT COUNT(*) FROM posts WHERE DATE(created_at)=DATE('now','localtime')").fetchone()[0],
        "urgent":   conn.execute("SELECT COUNT(*) FROM posts WHERE is_urgent=1 AND DATE(created_at)=DATE('now','localtime')").fetchone()[0],
        "negative": conn.execute("SELECT COUNT(*) FROM ai_analysis WHERE sentiment='negative'").fetchone()[0],
        "total":    conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0],
    }
    conn.close()
    return jsonify(stats)

# ─── 스케줄러 ─────────────────────────────────────────────────────────────────

def start_scheduler():
    scheduler = BackgroundScheduler(timezone="Asia/Seoul")
    scheduler.add_job(collect_all,       "interval", hours=1,   id="collect")
    scheduler.add_job(send_daily_report, "cron",     hour=8, minute=0, id="daily_report")
    scheduler.start()
    print("[스케줄러] 1시간마다 수집 / 매일 오전 8시 리포트 발송 설정 완료")

# ─── 실행 ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    start_scheduler()
    print("\n✅ GLN 모니터링 시작!")
    print("📊 대시보드: http://localhost:5001\n")
    collect_all()  # 시작 시 즉시 1회 수집
    port = int(os.getenv("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=False)
