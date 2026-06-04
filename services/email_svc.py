"""
services/email_svc.py — 이메일 발송 서비스
"""
import base64
import os
import smtplib
from datetime import datetime, timedelta
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

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

    base_url     = os.getenv("BASE_URL", "http://192.168.1.30:5001")
    collected_at = created_at[:16] if created_at else datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    detail_url   = f"{base_url}/post/{post_id}" if post_id else base_url
    link_btn = (
        f'<a href="{detail_url}" style="display:inline-block;margin-top:16px;'
        f'padding:8px 16px;background:#1D4ED8;color:#fff;text-decoration:none;'
        f'border-radius:6px;font-size:13px;margin-right:8px">상세보기 →</a>'
        + (f'<a href="{link}" style="display:inline-block;margin-top:16px;'
           f'padding:8px 16px;background:#F3F4F6;color:#374151;text-decoration:none;'
           f'border-radius:6px;font-size:13px;border:1px solid #E5E7EB">원문 바로가기 ↗</a>'
           if link else "")
    )
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
    send_email(to, f"[GLN 긴급] {title[:40]}", html, report_type="urgent")


# ── 일일 리포트 (AI퍼플이의 아침브리핑) ──────────────────────────────────────
def send_daily_report(to: str = ""):
    if not to:
        to = get_setting("report_to_weekday") or get_setting("daily_report_to_list") or get_setting("report_to_list") or os.getenv("REPORT_TO", "")
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

        # ── 채널 섹션 ─────────────────────────────────────────────────────────
        ch_colors = {"카페": "#1D4ED8", "블로그": "#059669", "뉴스": "#D97706"}
        sections_html = ""
        for ch, posts in cat_posts.items():
            color    = ch_colors.get(ch, "#6B7280")
            all_rows = "".join(post_row(p) for p in posts)

            sections_html += f"""
            <div style="margin-bottom:24px">
              <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
                <span style="font-size:14px;font-weight:700;color:{color}">{ch}</span>
                <span style="font-size:12px;color:#9CA3AF">{len(posts)}건</span>
              </div>
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
