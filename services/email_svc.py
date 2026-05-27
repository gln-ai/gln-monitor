"""
services/email_svc.py — 이메일 발송 서비스 (Gmail API OAuth2)
"""
import base64
import os
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from config import KST
from db import get_db, get_setting


def _log_email(report_type: str, subject: str, recipients: str, status: str, error_msg: str = ""):
    try:
        from db import get_db
        conn = get_db()
        conn.execute(
            "INSERT INTO email_log (report_type, subject, recipients, status, error_msg) VALUES (?,?,?,?,?)",
            (report_type, subject, recipients, status, error_msg or None)
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def send_email(to: str, subject: str, html_body: str, report_type: str = ""):
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    client_id     = os.getenv("GMAIL_CLIENT_ID", "")
    client_secret = os.getenv("GMAIL_CLIENT_SECRET", "")
    refresh_token = os.getenv("GMAIL_REFRESH_TOKEN", "")
    from_addr     = os.getenv("REPORT_FROM", "glninternational.ai@gmail.com")

    if not (client_id and client_secret and refresh_token):
        print("[이메일] Gmail OAuth2 설정 없음 — 스킵")
        _log_email(report_type, subject, to, "skip", "OAuth2 설정 없음")
        return

    recipients = [r.strip() for r in to.split(",") if r.strip()]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = from_addr
    msg["To"]      = ", ".join(recipients)
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    print(f"[이메일] Gmail API 발송: {subject} → {recipients}", flush=True)
    try:
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
        print(f"[이메일 발송] {subject} → {', '.join(recipients)}", flush=True)
        _log_email(report_type, subject, to, "ok")
    except Exception as e:
        import traceback
        print(f"[이메일 오류] {e}", flush=True)
        print(traceback.format_exc(), flush=True)
        _log_email(report_type, subject, to, "error", str(e))


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

    base_url = os.getenv("BASE_URL", "http://192.168.1.60:5001")
    collected_at = created_at[:16] if created_at else datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    detail_url = f"{base_url}/post/{post_id}" if post_id else base_url
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


def send_daily_report(to: str = ""):
    if not to:
        to = get_setting("daily_report_to_list") or get_setting("report_to_list") or os.getenv("REPORT_TO", "")
    print(f"[일일리포트] 수신자: {to}", flush=True)
    if not to:
        print("[일일리포트] 수신자 없음 — 스킵")
        return
    try:
        from datetime import timedelta
        base_url  = os.getenv("BASE_URL", "http://192.168.1.60:5001")
        now       = datetime.now(KST)
        yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        conn = get_db()
        channels = ["카페", "블로그", "뉴스"]
        cat_posts = {}
        for ch in channels:
            rows = conn.execute("""
                SELECT p.id, p.title, p.link, p.cafe_name, p.created_at, p.keyword,
                       a.summary, a.category, a.sentiment, a.importance_score
                FROM posts p
                LEFT JOIN ai_analysis a ON p.id = a.post_id
                WHERE DATE(p.created_at) = ?
                  AND p.keyword LIKE ?
                ORDER BY a.importance_score DESC NULLS LAST
                LIMIT 10
            """, (yesterday, f"{ch}/%")).fetchall()
            if rows:
                cat_posts[ch] = rows
        total = conn.execute(
            "SELECT COUNT(*) as cnt FROM posts WHERE DATE(created_at)=?", (yesterday,)
        ).fetchone()["cnt"]
        urgent = conn.execute(
            "SELECT COUNT(*) as cnt FROM posts WHERE is_urgent=1 AND DATE(created_at)=?", (yesterday,)
        ).fetchone()["cnt"]
        ch_counts = {}
        for ch in ["카페", "블로그", "뉴스"]:
            cnt = conn.execute(
                "SELECT COUNT(*) FROM posts WHERE DATE(created_at)=? AND keyword LIKE ?",
                (yesterday, f"{ch}/%")
            ).fetchone()[0]
            ch_counts[ch] = cnt
        conn.close()

        def post_row(p):
            sc  = {"positive": "#16A34A", "neutral": "#6B7280", "negative": "#DC2626"}.get(p["sentiment"], "#6B7280")
            sl  = {"positive": "긍정", "neutral": "중립", "negative": "부정"}.get(p["sentiment"], "-")
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
        ch_colors = {"카페": "#1D4ED8", "블로그": "#059669", "뉴스": "#D97706"}
        for ch, posts in cat_posts.items():
            color     = ch_colors.get(ch, "#6B7280")
            rows_html = "".join(post_row(p) for p in posts)
            sections_html += f"""
            <div style="margin-bottom:24px">
              <h2 style="font-size:14px;font-weight:600;color:{color};margin:0 0 8px;padding:0">{ch} ({len(posts)}건)</h2>
              <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;border:1px solid #F3F4F6">
                <thead>
                  <tr style="background:#F9FAFB">
                    <th style="padding:7px 8px;text-align:left;font-size:11px;color:#9CA3AF;font-weight:500">제목 / 요약</th>
                    <th style="padding:7px 8px;text-align:left;font-size:11px;color:#9CA3AF;font-weight:500;white-space:nowrap">분류</th>
                    <th style="padding:7px 8px;text-align:left;font-size:11px;color:#9CA3AF;font-weight:500;white-space:nowrap">감성</th>
                    <th style="padding:7px 8px;text-align:center;font-size:11px;color:#9CA3AF;font-weight:500;white-space:nowrap">중요도</th>
                  </tr>
                </thead>
                <tbody>{rows_html}</tbody>
              </table>
            </div>"""

        urgent_txt = f'<div style="font-size:13px;font-weight:700;color:#F87171;margin-top:6px">⚠ 전일 긴급 {urgent}건 발생</div>' if urgent else ""
        mascot_url = f"{base_url}/static/img/mascot.jpg"
        html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="X-UA-Compatible" content="IE=edge">
</head>
<body style="margin:0;padding:20px 12px;background:#F3F4F6;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif">
<div style="max-width:640px;margin:auto;background:#fff;border-radius:12px;overflow:hidden;border:1px solid #E5E7EB">

  <!-- 헤더: 마스코트 + 브랜딩 -->
  <div style="background:#130D2A;padding:0">
    <table width="100%" cellpadding="0" cellspacing="0">
      <tr>
        <td style="padding:20px 20px 20px 0;vertical-align:middle" align="right" width="110">
          <img src="{mascot_url}" alt="AI퍼플이" width="90" height="90"
               style="border-radius:50%;border:3px solid #7000FC;display:block;object-fit:cover">
        </td>
        <td style="padding:20px 20px 20px 0;vertical-align:middle">
          <div style="font-size:10px;font-weight:700;color:#7000FC;letter-spacing:0.1em;margin-bottom:6px">AI퍼플이의 아침 브리핑 ☕</div>
          <div style="font-size:18px;font-weight:700;color:#fff;line-height:1.3">전일자 GLN<br>카페·블로그·뉴스 모아보기</div>
          <div style="font-size:12px;color:rgba(255,255,255,0.5);margin-top:6px">{yesterday}</div>
          {urgent_txt}
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
            <div style="font-size:26px;font-weight:700;color:#130D2A">{total}</div>
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
            <div style="font-size:12px;font-weight:600;color:#166534">{ch_counts.get("카페",0)} 카페</div>
            <div style="font-size:12px;font-weight:600;color:#1E40AF;margin-top:4px">{ch_counts.get("블로그",0)} 블로그</div>
            <div style="font-size:12px;font-weight:600;color:#92400E;margin-top:4px">{ch_counts.get("뉴스",0)} 뉴스</div>
          </div>
        </td>
      </tr>
    </table>

    <!-- 채널별 섹션 -->
    {sections_html}

    <!-- 푸터 -->
    <div style="border-top:1px solid #F3F4F6;padding-top:14px;text-align:center">
      <p style="font-size:11px;color:#9CA3AF;margin:0">GLN 모니터링 시스템 · 매일 오전 8시 자동 발송</p>
    </div>

  </div>
</div>
</body>
</html>"""

        send_email(to, f"[AI퍼플이의 아침 브리핑] 전일자 GLN 카페/블로그/뉴스 모아보기 ☕", html, report_type="daily")
    except Exception as e:
        import traceback
        print(f"[리포트 오류] {e}")
        print(traceback.format_exc())
