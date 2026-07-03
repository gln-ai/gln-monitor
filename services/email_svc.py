"""
services/email_svc.py — 이메일 발송 서비스
"""
import base64
import os
import re
import smtplib
from datetime import datetime, timedelta
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

_PROMPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts")


def _call_claude(prompt_filename: str, user_text: str) -> str:
    """prompts/ 폴더의 시스템 프롬프트로 Claude 호출. 실패 시 빈 문자열 반환."""
    try:
        import anthropic
        prompt_path = os.path.join(_PROMPTS_DIR, prompt_filename)
        with open(prompt_path, encoding="utf-8") as f:
            system_prompt = f.read().strip()
        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=system_prompt,
            messages=[{"role": "user", "content": user_text}],
        )
        return resp.content[0].text
    except Exception as e:
        print(f"[Claude {prompt_filename}] 오류: {e}")
        return ""

_MONITOR_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from config import KST
from db import get_db, get_setting

# ── 국가 감지 상수 ────────────────────────────────────────────────────────────
_COUNTRY_MAP = {
    # 국가명
    "태국": "thailand",    "방콕": "thailand",
    "일본": "japan",       "도쿄": "japan",       "오사카": "japan",
    "대만": "taiwan",      "타이베이": "taiwan",
    "베트남": "vietnam",   "호치민": "vietnam",    "하노이": "vietnam",
    "필리핀": "philippines", "마닐라": "philippines",
    "싱가포르": "singapore",
    "홍콩": "hongkong",
    "마카오": "macau",
    "중국": "china",       "베이징": "china",      "상하이": "china",
    "캄보디아": "cambodia", "프놈펜": "cambodia",
    "몽골": "mongolia",    "울란바토르": "mongolia",
    "라오스": "laos",
    "괌": "guam",
    "사이판": "saipan",
    # 베트남 도시
    "나트랑": "vietnam",   "냐짱": "vietnam",     "다낭": "vietnam",
    "달랏": "vietnam",     "다랏": "vietnam",     "푸꾸옥": "vietnam",
    "할롱": "vietnam",     "호이안": "vietnam",   "무이네": "vietnam",
    "빈펄": "vietnam",     "사파": "vietnam",     "붕따우": "vietnam",
    # 태국 도시
    "치앙마이": "thailand", "파타야": "thailand",  "푸켓": "thailand",
    "사무이": "thailand",   "끄라비": "thailand",  "후아힌": "thailand",
    # 일본 도시
    "교토": "japan",       "후쿠오카": "japan",    "삿포로": "japan",
    "오키나와": "japan",   "나고야": "japan",      "나하": "japan",
    "고베": "japan",       "요코하마": "japan",    "히로시마": "japan",
    # 대만 도시
    "가오슝": "taiwan",    "타이중": "taiwan",     "타이난": "taiwan",
    "화롄": "taiwan",
    # 필리핀 도시
    "세부": "philippines", "보라카이": "philippines", "다바오": "philippines",
    "팔라완": "philippines", "엘니도": "philippines",
    # 중국 도시
    "광저우": "china",     "선전": "china",        "청두": "china",
    "시안": "china",       "항저우": "china",      "구이린": "china",
    # 캄보디아 도시
    "씨엠립": "cambodia",  "앙코르": "cambodia",
    # 라오스 도시
    "비엔티안": "laos",    "루앙프라방": "laos",   "방비엥": "laos",
    # 괌
    "투몬": "guam",
}

_COUNTRY_LABEL = {
    "thailand": "태국", "japan": "일본", "taiwan": "대만",
    "vietnam": "베트남", "philippines": "필리핀", "singapore": "싱가포르",
    "hongkong": "홍콩", "macau": "마카오", "china": "중국",
    "cambodia": "캄보디아", "mongolia": "몽골", "laos": "라오스",
    "guam": "괌사이판", "saipan": "괌사이판",
}

_COUNTRY_EMOJI = {
    "vietnam":     "🇻🇳", "china":       "🇨🇳", "hongkong":    "🇭🇰",
    "macau":       "🇲🇴", "philippines": "🇵🇭", "thailand":    "🇹🇭",
    "laos":        "🇱🇦", "japan":       "🇯🇵", "taiwan":      "🇹🇼",
    "mongolia":    "🇲🇳", "singapore":   "🇸🇬", "cambodia":    "🇰🇭",
    "guam":        "🏝️",  "saipan":      "🏝️",
}

_WEEKDAY_KR = ["월", "화", "수", "목", "금", "토", "일"]


def _detect_country_email(title: str, description: str = "", cafe_name: str = "") -> str:
    text = (title or "") + " " + (description or "") + " " + (cafe_name or "")
    for kor, eng in _COUNTRY_MAP.items():
        if kor in text:
            return eng
    return ""


def _country_badge_html(country: str) -> str:
    emoji = _COUNTRY_EMOJI.get(country, "🌐")
    label = _COUNTRY_LABEL.get(country, "공통")
    return (
        f'<span style="background:#F3F4F6;color:#374151;border:0.5px solid #E5E7EB;'
        f'padding:2px 7px;border-radius:99px;font-size:10px;font-weight:500;'
        f'white-space:nowrap;margin-right:4px">{emoji} {label}</span>'
    )


# ── 이메일 로깅 ───────────────────────────────────────────────────────────────
def _log_email(report_type: str, subject: str, recipients: str, status: str, error_msg: str = ""):
    try:
        conn = get_db()
        conn.execute(
            "INSERT INTO email_log (report_type, subject, recipients, status, error_msg) VALUES (?,?,?,?,?)",
            (report_type, subject, recipients, status, error_msg or None)
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


# ── 발송 (Gmail API → SMTP 폴백, CID 인라인 이미지 지원) ────────────────────
def send_email(to: str, subject: str, html_body: str, report_type: str = "",
               images: "dict[str, str] | None" = None):
    """
    images = {"cid명": "/절대/경로/파일.jpg"} 형식으로 전달하면
    HTML 내 <img src="cid:cid명"> 으로 인라인 임베딩됨 (로컬 IP 문제 해결).
    """
    # Railway 환경에서는 이메일 발송 차단 (로컬 맥미니에서만 발송)
    if os.getenv("RAILWAY_ENVIRONMENT"):
        print("⚠️ [이메일 발송 비활성화] Railway 환경에서는 이메일 발송이 차단됩니다. 실제 발송은 로컬 맥미니 서버에서만 실행됩니다.", flush=True)
        return

    from email.header import Header
    from email.utils import formataddr
    _raw = os.getenv("REPORT_FROM", "glninternational.ai@gmail.com")
    from_addr = formataddr((str(Header("AI퍼플이", "utf-8")), _raw))

    client_id     = os.getenv("GMAIL_CLIENT_ID", "")
    client_secret = os.getenv("GMAIL_CLIENT_SECRET", "")
    refresh_token = os.getenv("GMAIL_REFRESH_TOKEN", "")

    recipients = [r.strip() for r in to.split(",") if r.strip()]

    # ── MIME 구조 조립 ────────────────────────────────────────────────────────
    if images:
        outer = MIMEMultipart("related")
        alt   = MIMEMultipart("alternative")
        alt.attach(MIMEText(html_body, "html", "utf-8"))
        outer.attach(alt)
        for cid, fpath in images.items():
            try:
                with open(fpath, "rb") as f:
                    raw_img = f.read()
                ext      = os.path.splitext(fpath)[1].lower().lstrip(".")
                subtype  = "jpeg" if ext in ("jpg", "jpeg") else ext
                img_part = MIMEImage(raw_img, _subtype=subtype)
                img_part.add_header("Content-ID", f"<{cid}>")
                img_part.add_header("Content-Disposition", "inline",
                                    filename=os.path.basename(fpath))
                outer.attach(img_part)
            except Exception as ex:
                print(f"[이메일] 이미지 첨부 실패 ({fpath}): {ex}", flush=True)
        msg = outer
    else:
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText(html_body, "html", "utf-8"))

    msg["Subject"] = subject
    msg["From"]    = from_addr
    msg["To"]      = ", ".join(recipients)

    # ── Gmail API ─────────────────────────────────────────────────────────────
    if client_id and client_secret and refresh_token:
        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
            creds = Credentials(
                token=None,
                refresh_token=refresh_token,
                client_id=client_id,
                client_secret=client_secret,
                token_uri="https://oauth2.googleapis.com/token",
                scopes=["https://www.googleapis.com/auth/gmail.send"],
            )
            service = build("gmail", "v1", credentials=creds, cache_discovery=False)
            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
            service.users().messages().send(userId="me", body={"raw": raw}).execute()
            print(f"[이메일 Gmail] {subject} → {', '.join(recipients)}", flush=True)
            _log_email(report_type, subject, to, "ok")
            return
        except Exception as e:
            import traceback
            print(f"[이메일 Gmail 오류] {e}", flush=True)
            print(traceback.format_exc(), flush=True)
            _log_email(report_type, subject, to, "error", str(e))
            return

    # ── SMTP 폴백 ─────────────────────────────────────────────────────────────
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASS", "")
    if not (smtp_user and smtp_pass):
        print("[이메일] Gmail OAuth2 및 SMTP 설정 없음 — 스킵", flush=True)
        _log_email(report_type, subject, to, "skip", "OAuth2/SMTP 설정 없음")
        return

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(smtp_user, smtp_pass)
            smtp.sendmail(from_addr, recipients, msg.as_bytes())
        print(f"[이메일 SMTP] {subject} → {', '.join(recipients)}", flush=True)
        _log_email(report_type, subject, to, "ok")
    except Exception as e:
        import traceback
        print(f"[이메일 SMTP 오류] {e}", flush=True)
        print(traceback.format_exc(), flush=True)
        _log_email(report_type, subject, to, "error", str(e))


# ── 긴급 알림 ─────────────────────────────────────────────────────────────────
def send_urgent_alert(title: str, analysis: dict,
                      cafe_name: str = "", link: str = "",
                      created_at: str = "", post_id: int = 0):
    to = get_setting("urgent_alert_to_list") or os.getenv("URGENT_ALERT_TO", "brad@glninternational.com")
    if not to:
        return

    now_hour = datetime.now(KST).hour
    if not (8 <= now_hour < 18):
        print(f"[긴급 알림] 발송 시간 외 ({now_hour}시) — 스킵")
        return

    base_url     = os.getenv("BASE_URL", "http://192.168.1.60:5001")
    collected_at = created_at[:16] if created_at else datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    detail_url   = f"{base_url}/post/{post_id}" if post_id else base_url

    # ── Claude 긴급알림 에디터 호출 ───────────────────────────────────────────
    channel_label = analysis.get("channel", "카페") or "카페"
    sentiment_map = {"positive": "긍정", "neutral": "중립", "negative": "부정"}
    sentiment_kr  = sentiment_map.get(analysis.get("sentiment", ""), analysis.get("sentiment", ""))
    claude_input  = (
        f"[긴급 알림 요청]\n"
        f"채널: {channel_label}\n"
        f"중요도: {analysis.get('importance_score', '-')}\n"
        f"감성: {sentiment_kr}\n"
        f"게시글 URL: {link or detail_url}\n"
        f"게시글 요약: {analysis.get('summary', title)}\n"
        f"키워드: {analysis.get('category', '-')}"
    )
    claude_text = _call_claude("urgent_alert.txt", claude_input)

    link_btn = (
        f'<a href="{detail_url}" style="display:inline-block;margin-top:16px;'
        f'padding:8px 16px;background:#1D4ED8;color:#fff;text-decoration:none;'
        f'border-radius:6px;font-size:13px;margin-right:8px">상세보기 →</a>'
        + (f'<a href="{link}" style="display:inline-block;margin-top:16px;'
           f'padding:8px 16px;background:#F3F4F6;color:#374151;text-decoration:none;'
           f'border-radius:6px;font-size:13px;border:1px solid #E5E7EB">원문 바로가기 ↗</a>'
           if link else "")
    )

    claude_section = ""
    if claude_text:
        escaped = claude_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        claude_section = (
            f'<div style="background:#FFF7ED;border-left:3px solid #F97316;'
            f'padding:14px 16px;border-radius:4px;margin-bottom:16px">'
            f'<div style="font-size:11px;font-weight:600;color:#9A3412;margin-bottom:8px">AI 분석 요약</div>'
            f'<pre style="font-family:inherit;font-size:13px;color:#1C1917;white-space:pre-wrap;margin:0">'
            f'{escaped}</pre></div>'
        )

    html = f"""
    <div style="font-family:sans-serif;max-width:600px;margin:auto;padding:20px">
      <div style="background:#FEE2E2;border-left:4px solid #EF4444;padding:12px 16px;border-radius:4px;margin-bottom:16px">
        <strong style="color:#B91C1C">긴급 알림 — GLN 모니터링</strong>
      </div>
      <h2 style="font-size:16px;color:#111">{title}</h2>
      {claude_section}
      <table style="width:100%;font-size:14px;border-collapse:collapse">
        <tr><td style="padding:6px 0;color:#666;width:80px">요약</td><td>{analysis.get('summary','')}</td></tr>
        <tr><td style="padding:6px 0;color:#666">분류</td><td>{analysis.get('category','')}</td></tr>
        <tr><td style="padding:6px 0;color:#666">감성</td><td>{sentiment_kr}</td></tr>
        <tr><td style="padding:6px 0;color:#666">중요도</td><td>{analysis.get('importance_score','')}/10</td></tr>
        <tr><td style="padding:6px 0;color:#666">카페</td><td>{cafe_name or '-'}</td></tr>
        <tr><td style="padding:6px 0;color:#666">수집일시</td><td>{collected_at}</td></tr>
      </table>
      {link_btn}
      <p style="font-size:12px;color:#999;margin-top:24px">GLN 모니터링 시스템 자동 발송</p>
    </div>"""
    send_email(to, f"[GLN 긴급] {title[:40]}", html, report_type="urgent")


# ── 보도자료 발송 ─────────────────────────────────────────────────────────────
def send_pr_draft(draft: dict) -> tuple:
    to_raw = get_setting("urgent_alert_to_list") or os.getenv("URGENT_ALERT_TO", "")
    to_list = [e.strip() for e in to_raw.replace("\n", ",").split(",") if e.strip()]
    if not to_list:
        return False, "수신자 없음 — 긴급 알림 수신자 설정 필요"

    headline   = (draft.get("headline") or "").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
    subheadline = (draft.get("subheadline") or "").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
    body_text  = (draft.get("body") or "").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
    pr_type    = draft.get("pr_type") or "general"
    created_at = (draft.get("created_at") or "")[:16]

    html = f"""
<div style="font-family:sans-serif;max-width:700px;margin:auto;padding:24px">
  <div style="background:#F0EAFF;border-left:4px solid #7000FC;padding:10px 16px;border-radius:4px;margin-bottom:20px">
    <strong style="color:#5B00CC">GLN 보도자료</strong>
    <span style="font-size:11px;color:#7000FC;margin-left:8px">{pr_type} · {created_at}</span>
  </div>
  <h1 style="font-size:20px;font-weight:800;color:#130D2A;line-height:1.4;margin-bottom:8px">{headline}</h1>
  {'<p style="font-size:14px;color:#5E5A71;margin-bottom:20px">' + subheadline + '</p>' if subheadline else ''}
  <hr style="border:none;border-top:1px solid #E4E1EE;margin:20px 0">
  <pre style="font-family:inherit;font-size:13px;line-height:1.85;color:#130D2A;white-space:pre-wrap">{body_text}</pre>
  <p style="font-size:11px;color:#AAA7B8;margin-top:32px">GLN 모니터링 시스템 · 보도자료 발송</p>
</div>"""

    subject = f"[GLN 보도자료] {(draft.get('headline') or '')[:60]}"
    try:
        for addr in to_list:
            send_email(addr, subject, html, report_type="pr")
        return True, f"발송 완료 ({len(to_list)}명)"
    except Exception as e:
        return False, str(e)


# ── 일일 리포트 (AI퍼플이의 아침브리핑) ──────────────────────────────────────
def send_daily_report(to: str = ""):
    if not to:
        to = get_setting("report_to_weekday") or os.getenv("REPORT_TO", "")
    recipient_list = [r.strip() for r in to.replace("\n", ",").split(",") if r.strip()]
    print(f"[아침브리핑] 수신자 {len(recipient_list)}명 개별 발송", flush=True)
    if not recipient_list:
        print("[아침브리핑] 수신자 없음 — 스킵")
        return
    try:
        now          = datetime.now(KST)
        yesterday_dt = (now - timedelta(days=1)).date()
        yesterday    = yesterday_dt.strftime("%Y-%m-%d")
        yesterday_kr = (
            f"{yesterday_dt.year}년 {yesterday_dt.month}월 {yesterday_dt.day}일"
            f" ({_WEEKDAY_KR[yesterday_dt.weekday()]})"
        )
        mascot_path = os.path.join(_MONITOR_DIR, "static", "img", "mascot_email.jpg")

        conn = get_db()
        channels = ["카페", "블로그", "뉴스"]

        # 채널별 전체 게시글 (LIMIT 없음, description 포함)
        cat_posts: dict[str, list] = {}
        for ch in channels:
            rows = conn.execute("""
                SELECT p.id, p.title, p.link, p.cafe_name, p.created_at,
                       p.keyword, p.description, p.is_urgent,
                       a.summary, a.category, a.sentiment, a.importance_score
                FROM posts p
                LEFT JOIN ai_analysis a ON p.id = a.post_id
                WHERE DATE(p.created_at) = ?
                  AND p.keyword LIKE ?
                  AND (a.is_relevant IS NULL OR a.is_relevant = 1)
                ORDER BY a.importance_score DESC NULLS LAST
            """, (yesterday, f"{ch}/%")).fetchall()
            if rows:
                cat_posts[ch] = rows

        total  = conn.execute(
            "SELECT COUNT(*) FROM posts WHERE DATE(created_at)=?", (yesterday,)
        ).fetchone()[0]
        urgent = conn.execute(
            "SELECT COUNT(*) FROM posts WHERE is_urgent=1 AND DATE(created_at)=?", (yesterday,)
        ).fetchone()[0]
        ch_counts = {
            ch: conn.execute(
                "SELECT COUNT(*) FROM posts WHERE DATE(created_at)=? AND keyword LIKE ?",
                (yesterday, f"{ch}/%")
            ).fetchone()[0]
            for ch in channels
        }
        conn.close()

        # ── 게시글 행 렌더러 ──────────────────────────────────────────────────
        def post_row(p) -> str:
            sc      = {"positive": "#16A34A", "neutral": "#6B7280", "negative": "#DC2626"}.get(p["sentiment"], "#6B7280")
            sl      = {"positive": "긍정", "neutral": "중립", "negative": "부정"}.get(p["sentiment"], "-")
            cat     = p["category"] or "-"
            country = _detect_country_email(p["title"] or "", p["description"] or "", p["cafe_name"] or "")
            badge   = _country_badge_html(country)
            urgent_mark = (
                '<span style="background:#FEE2E2;color:#DC2626;border:0.5px solid #FECACA;'
                'padding:1px 6px;border-radius:99px;font-size:10px;font-weight:700;'
                'margin-right:4px">긴급</span>'
                if p["is_urgent"] else ""
            )
            return f"""
            <tr style="border-bottom:1px solid #F3F4F6">
              <td style="padding:10px 8px;font-size:13px">
                <div style="margin-bottom:4px">{badge}{urgent_mark}</div>
                <a href="{p['link']}" style="color:#1D4ED8;text-decoration:none;font-weight:500">{(p['title'] or '')[:50]}</a>
                <div style="font-size:12px;color:#6B7280;margin-top:3px">{p['summary'] or '분석 중...'}</div>
                <div style="font-size:11px;color:#9CA3AF;margin-top:3px">{p['cafe_name'] or ''} · {(p['created_at'] or '')[:10]}</div>
              </td>
              <td style="padding:10px 8px;font-size:12px;color:#374151;white-space:nowrap;vertical-align:top">{cat}</td>
              <td style="padding:10px 8px;font-size:12px;color:{sc};white-space:nowrap;font-weight:500;vertical-align:top">{sl}</td>
              <td style="padding:10px 8px;font-size:12px;text-align:center;vertical-align:top">{p['importance_score'] or '-'}</td>
            </tr>"""

        def table_header() -> str:
            return """
            <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;border:1px solid #F3F4F6">
              <thead><tr style="background:#F9FAFB">
                <th style="padding:7px 8px;text-align:left;font-size:11px;color:#9CA3AF;font-weight:500">제목 / 요약</th>
                <th style="padding:7px 8px;text-align:left;font-size:11px;color:#9CA3AF;font-weight:500;white-space:nowrap">분류</th>
                <th style="padding:7px 8px;text-align:left;font-size:11px;color:#9CA3AF;font-weight:500;white-space:nowrap">감성</th>
                <th style="padding:7px 8px;text-align:center;font-size:11px;color:#9CA3AF;font-weight:500;white-space:nowrap">중요도</th>
              </tr></thead><tbody>"""

        # ── Claude 일일 브리핑 생성 ───────────────────────────────────────────
        all_posts_flat = [p for posts in cat_posts.values() for p in posts]
        all_posts_flat.sort(key=lambda p: p["importance_score"] or 0, reverse=True)
        top_posts = all_posts_flat[:10]
        analyzed  = [p for p in all_posts_flat if p["sentiment"]]
        n_total_a = len(analyzed) or 1
        pos_pct   = round(sum(1 for p in analyzed if p["sentiment"]=="positive") / n_total_a * 100)
        neg_pct   = round(sum(1 for p in analyzed if p["sentiment"]=="negative") / n_total_a * 100)
        neu_pct   = 100 - pos_pct - neg_pct
        health    = max(0, 100 - neg_pct * 2)

        posts_list_text = ""
        for i, p in enumerate(top_posts, 1):
            posts_list_text += (
                f"\n{i}. [{p['importance_score'] or '-'}점] "
                f"{p['keyword'].split('/')[0] if p['keyword'] else '-'} — "
                f"{(p['title'] or '')[:50]}\n"
                f"   {p['summary'] or '분석 중'}\n"
                f"   {p['link'] or ''}\n"
            )

        claude_input = (
            f"[일일 리포트 요청]\n"
            f"날짜: {yesterday}\n"
            f"수집 기간: {yesterday} 00:00 ~ 23:59\n"
            f"총 수집 건수: {total}건\n"
            f"채널별: 카페 {ch_counts.get('카페',0)}건 / 블로그 {ch_counts.get('블로그',0)}건 / 뉴스 {ch_counts.get('뉴스',0)}건\n"
            f"감성 분포: 긍정 {pos_pct}% / 중립 {neu_pct}% / 부정 {neg_pct}%\n"
            f"브랜드 헬스 스코어: {health}\n"
            f"긴급 알림 발송 여부: {'있음' if urgent > 0 else '없음'}\n"
            f"주요 게시글 목록:{posts_list_text}"
        )
        claude_daily = _call_claude("daily_report.txt", claude_input)

        claude_briefing_html = ""
        if claude_daily:
            escaped_brief = claude_daily.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            claude_briefing_html = f"""
    <div style="background:#F0EAFF;border-radius:12px;padding:16px 20px;margin-bottom:20px;border:1px solid #DDD6FE">
      <div style="font-size:11px;font-weight:700;color:#7000FC;letter-spacing:0.1em;margin-bottom:10px">AI 일일 브리핑</div>
      <pre style="font-family:inherit;font-size:13px;color:#1E0942;white-space:pre-wrap;margin:0;line-height:1.7">{escaped_brief}</pre>
    </div>"""

        # ── 채널 섹션 ─────────────────────────────────────────────────────────
        ch_colors = {"카페": "#1D4ED8", "블로그": "#059669", "뉴스": "#D97706"}
        sections_html = ""
        for ch, posts in cat_posts.items():
            color    = ch_colors.get(ch, "#6B7280")
            all_rows = "".join(post_row(p) for p in posts)

            sections_html += f"""
            <div style="margin-bottom:28px">
              <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:14px">
                <tr>
                  <td style="font-size:15px;font-weight:700;color:{color};padding:0">{ch}</td>
                  <td style="font-size:12px;color:#9CA3AF;padding:0 0 0 10px;white-space:nowrap">{len(posts)}건</td>
                </tr>
              </table>
              {table_header()}{all_rows}</tbody></table>
            </div>"""


        html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="X-UA-Compatible" content="IE=edge">
</head>
<body style="margin:0;padding:20px 12px;background:#F5F3FF;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif">
<div style="max-width:640px;margin:auto;background:#fff;border-radius:16px;overflow:hidden;border:1px solid #DDD6FE;box-shadow:0 2px 12px rgba(112,0,252,0.08)">

  <!-- 라벤더 헤더 -->
  <div style="background:#EDE7FF;padding:0">
    <table width="100%" cellpadding="0" cellspacing="0">
      <tr>
        <td style="padding:24px 16px 24px 24px;vertical-align:middle" width="100">
          <img src="cid:mascot" alt="AI퍼플이" width="84" height="84"
               style="border-radius:50%;border:3px solid #7000FC;display:block;object-fit:cover">
        </td>
        <td style="padding:24px 24px 24px 0;vertical-align:middle">
          <div style="font-size:12px;font-weight:800;color:#7000FC;letter-spacing:0.12em;margin-bottom:8px">[AI퍼플이] 아침 브리핑 ☕</div>
          <div style="font-size:22px;font-weight:800;color:#1E0942;line-height:1.2">GLN 카페·블로그·뉴스 모아보기</div>
          <div style="font-size:15px;color:#6D28D9;margin-top:6px;font-weight:500">
            {yesterday_kr}
          </div>
        </td>
      </tr>
    </table>
  </div>

  <!-- 본문 -->
  <div style="padding:20px 24px">

    <!-- 안내 문구 -->
    <div style="background:#F9F7FF;border-left:3px solid #7000FC;border-radius:0 8px 8px 0;padding:12px 14px;margin-bottom:20px">
      <p style="margin:0;font-size:12px;color:#4B4B6B;line-height:1.7">
        본 메일은 GLN 관련 온라인 언급 내용을 공유드리는 일일 모니터링 리포트입니다.<br>
        고객 반응과 문의 흐름을 함께 확인하기 위한 목적이며, 모든 항목이 즉시 조치 요청 사항은 아닙니다.<br>
        필요 시 대응이 필요한 건은 별도로 확인 후 협의드리겠습니다.
      </p>
    </div>

    <!-- 요약 카드 -->
    <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:20px">
      <tr>
        <td width="33%" style="padding-right:5px">
          <div style="background:#F9FAFB;border:1px solid #E5E7EB;border-radius:10px;padding:14px;text-align:center">
            <div style="font-size:26px;font-weight:700;color:#1E0942">{total}</div>
            <div style="font-size:11px;color:#6B7280;margin-top:2px">전일 수집</div>
          </div>
        </td>
        <td width="33%" style="padding:0 3px">
          <div style="background:#FEF2F2;border:1px solid #FECACA;border-radius:10px;padding:14px;text-align:center">
            <div style="font-size:26px;font-weight:700;color:#DC2626">{urgent}</div>
            <div style="font-size:11px;color:#6B7280;margin-top:2px">긴급 알림</div>
          </div>
        </td>
        <td width="33%" style="padding-left:5px">
          <div style="background:#F5F3FF;border:1px solid #DDD6FE;border-radius:10px;padding:14px;text-align:center">
            <div style="font-size:12px;font-weight:600;color:#166534">{ch_counts.get('카페',0)} 카페</div>
            <div style="font-size:12px;font-weight:600;color:#1E40AF;margin-top:4px">{ch_counts.get('블로그',0)} 블로그</div>
            <div style="font-size:12px;font-weight:600;color:#92400E;margin-top:4px">{ch_counts.get('뉴스',0)} 뉴스</div>
          </div>
        </td>
      </tr>
    </table>



    <!-- AI 브리핑 -->
    {claude_briefing_html}

    <!-- 채널별 섹션 -->
    {sections_html if sections_html else '<p style="color:#9CA3AF;font-size:13px;text-align:center;padding:20px 0">전일 수집된 게시글이 없습니다.</p>'}

    <!-- 푸터 -->
    <div style="border-top:1px solid #F3F4F6;padding-top:14px;text-align:center">
      <p style="font-size:11px;color:#9CA3AF;margin:0">GLN 모니터링 시스템 · 매일 오전 8시 자동 발송</p>
    </div>

  </div>
</div>
</body>
</html>"""

        subject = f"[AI퍼플이] 아침 브리핑 ☕ GLN 카페·블로그·뉴스 모아보기 ({yesterday_kr})"
        img_files = {"mascot": mascot_path} if os.path.isfile(mascot_path) else {}
        for addr in recipient_list:
            send_email(addr, subject, html, report_type="daily", images=img_files or None)
    except Exception as e:
        import traceback
        print(f"[아침브리핑 오류] {e}")
        print(traceback.format_exc())


# ── 서포터즈 콘텐츠 평가 리포트 ──────────────────────────────────────────────
def send_content_eval_report(to: str = "") -> tuple[bool, str]:
    """서포터즈 콘텐츠 평가 결과를 이메일로 발송."""
    if not to:
        to = get_setting("urgent_alert_to_list") or os.getenv("URGENT_ALERT_TO", "")
    if not to:
        return False, "수신자 없음"

    try:
        conn = get_db()
        rows = conn.execute("""
            SELECT s.name, s.platform, s.url,
                   sc.guideline_score, sc.engagement_score, sc.quality_score,
                   sc.total_score, sc.safety_status, sc.safety_reason
            FROM content_submissions s
            LEFT JOIN content_scores sc ON sc.submission_id = s.id
            WHERE sc.total_score IS NOT NULL
            ORDER BY sc.total_score DESC
            LIMIT 100
        """).fetchall()
        conn.close()

        submissions = [dict(r) for r in rows]
        if not submissions:
            return False, "평가 완료된 콘텐츠 없음"

        total_cnt = len(submissions)
        pass_cnt  = sum(1 for s in submissions if s.get("safety_status") == "PASS")
        fail_cnt  = total_cnt - pass_cnt
        avg_score = round(sum(s.get("total_score", 0) for s in submissions) / max(total_cnt, 1), 1)

        _PLATFORM_LABEL = {"youtube": "유튜브", "naver_blog": "네이버 블로그", "instagram": "인스타그램"}

        def _score_color(v, max_v):
            ratio = v / max_v if max_v > 0 else 0
            if ratio >= 0.75:
                return "#059669"
            elif ratio >= 0.5:
                return "#D97706"
            return "#DC2626"

        rows_html = ""
        for s in submissions:
            status_style = (
                "background:#F0FDF4;color:#059669;border:0.5px solid #BBF7D0"
                if s.get("safety_status") == "PASS"
                else "background:#FEF2F2;color:#DC2626;border:0.5px solid #FECACA"
            )
            pct = s.get("total_score", 0)
            rows_html += f"""
<tr style="border-bottom:1px solid #F3F4F6">
  <td style="padding:8px 10px;font-size:13px;font-weight:600;color:#130D2A">{s['name']}</td>
  <td style="padding:8px 10px;font-size:11px;color:#5E5A71">{_PLATFORM_LABEL.get(s['platform'], s['platform'])}</td>
  <td style="padding:8px 10px;text-align:center;font-size:13px;font-weight:600;color:{_score_color(s.get('guideline_score',0),40)}">{s.get('guideline_score','—')}</td>
  <td style="padding:8px 10px;text-align:center;font-size:13px;font-weight:600;color:{_score_color(s.get('engagement_score',0),30)}">{s.get('engagement_score','—')}</td>
  <td style="padding:8px 10px;text-align:center;font-size:13px;font-weight:600;color:{_score_color(s.get('quality_score',0),30)}">{s.get('quality_score','—')}</td>
  <td style="padding:8px 10px">
    <div style="display:flex;align-items:center;gap:6px">
      <div style="flex:1;background:#F0EAFF;border-radius:99px;height:6px;overflow:hidden">
        <div style="width:{pct}%;background:#7000FC;height:100%;border-radius:99px"></div>
      </div>
      <span style="font-size:13px;font-weight:700;color:#130D2A;white-space:nowrap">{s.get('total_score','—')}</span>
    </div>
  </td>
  <td style="padding:8px 10px;text-align:center">
    <span style="padding:2px 8px;border-radius:99px;font-size:10px;font-weight:600;{status_style}">{s.get('safety_status','—')}</span>
  </td>
  <td style="padding:8px 10px;font-size:11px;color:#DC2626;max-width:160px">{s.get('safety_reason','') or ''}</td>
</tr>"""

        html = f"""<!DOCTYPE html>
<html lang="ko">
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:20px 12px;background:#F6F5FF;font-family:-apple-system,sans-serif">
<div style="max-width:720px;margin:auto;background:#fff;border-radius:16px;overflow:hidden;border:1px solid #DDD6FE">
  <div style="background:#EDE7FF;padding:20px 24px">
    <div style="font-size:12px;font-weight:800;color:#7000FC;letter-spacing:0.1em;margin-bottom:6px">GLN 서포터즈 콘텐츠 평가 리포트</div>
    <div style="font-size:20px;font-weight:800;color:#1E0942">스코어카드 요약</div>
    <div style="font-size:12px;color:#6D28D9;margin-top:4px">{datetime.now(KST).strftime('%Y년 %m월 %d일')}</div>
  </div>
  <div style="padding:20px 24px">
    <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:20px">
      <tr>
        <td width="25%" style="padding-right:6px">
          <div style="background:#F9FAFB;border:1px solid #E5E7EB;border-radius:10px;padding:12px;text-align:center">
            <div style="font-size:22px;font-weight:700;color:#1E0942">{total_cnt}</div>
            <div style="font-size:11px;color:#6B7280">총 평가</div>
          </div>
        </td>
        <td width="25%" style="padding:0 3px">
          <div style="background:#F0FDF4;border:1px solid #BBF7D0;border-radius:10px;padding:12px;text-align:center">
            <div style="font-size:22px;font-weight:700;color:#059669">{pass_cnt}</div>
            <div style="font-size:11px;color:#6B7280">PASS</div>
          </div>
        </td>
        <td width="25%" style="padding:0 3px">
          <div style="background:#FEF2F2;border:1px solid #FECACA;border-radius:10px;padding:12px;text-align:center">
            <div style="font-size:22px;font-weight:700;color:#DC2626">{fail_cnt}</div>
            <div style="font-size:11px;color:#6B7280">FAIL</div>
          </div>
        </td>
        <td width="25%" style="padding-left:6px">
          <div style="background:#F0EAFF;border:1px solid #DDD6FE;border-radius:10px;padding:12px;text-align:center">
            <div style="font-size:22px;font-weight:700;color:#7000FC">{avg_score}</div>
            <div style="font-size:11px;color:#6B7280">평균점수</div>
          </div>
        </td>
      </tr>
    </table>

    <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;border:1px solid #F0EDF7">
      <thead>
        <tr style="background:#F9FAFB">
          <th style="padding:7px 10px;text-align:left;font-size:10px;color:#918DA0;font-weight:500">이름</th>
          <th style="padding:7px 10px;text-align:left;font-size:10px;color:#918DA0;font-weight:500">플랫폼</th>
          <th style="padding:7px 10px;text-align:center;font-size:10px;color:#918DA0;font-weight:500">가이드/40</th>
          <th style="padding:7px 10px;text-align:center;font-size:10px;color:#918DA0;font-weight:500">참여도/30</th>
          <th style="padding:7px 10px;text-align:center;font-size:10px;color:#918DA0;font-weight:500">품질/30</th>
          <th style="padding:7px 10px;text-align:center;font-size:10px;color:#918DA0;font-weight:500">총점</th>
          <th style="padding:7px 10px;text-align:center;font-size:10px;color:#918DA0;font-weight:500">결과</th>
          <th style="padding:7px 10px;text-align:left;font-size:10px;color:#918DA0;font-weight:500">사유</th>
        </tr>
      </thead>
      <tbody>{rows_html}</tbody>
    </table>

    <p style="font-size:11px;color:#9CA3AF;margin-top:20px;text-align:center">GLN 모니터링 시스템 · 서포터즈 콘텐츠 평가 자동 발송</p>
  </div>
</div>
</body></html>"""

        subject = f"[GLN 서포터즈] 콘텐츠 평가 결과 — {total_cnt}건 (PASS {pass_cnt} / FAIL {fail_cnt})"
        send_email(to, subject, html, report_type="content_eval")
        return True, f"발송 완료 ({to})"
    except Exception as e:
        import traceback
        print(f"[서포터즈 리포트 오류] {e}")
        print(traceback.format_exc())
        return False, str(e)
