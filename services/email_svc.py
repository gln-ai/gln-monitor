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


def send_email(to: str, subject: str, html_body: str):
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    client_id     = os.getenv("GMAIL_CLIENT_ID", "")
    client_secret = os.getenv("GMAIL_CLIENT_SECRET", "")
    refresh_token = os.getenv("GMAIL_REFRESH_TOKEN", "")
    from_addr     = os.getenv("REPORT_FROM", "glninternational.ai@gmail.com")

    if not (client_id and client_secret and refresh_token):
        print("[이메일] Gmail OAuth2 설정 없음 — 스킵")
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
    except Exception as e:
        import traceback
        print(f"[이메일 오류] {e}", flush=True)
        print(traceback.format_exc(), flush=True)


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
    send_email(to, f"[GLN 긴급] {title[:40]}", html)


def send_daily_report(to: str = ""):
    if not to:
        to = get_setting("report_to_list") or os.getenv("REPORT_TO", "")
    print(f"[리포트] 수신자: {to}", flush=True)
    if not to:
        print("[리포트] 수신자 없음 — 스킵")
        return
    try:
        base_url = os.getenv("BASE_URL", "http://192.168.1.60:5001")
        today    = datetime.now(KST).strftime("%Y-%m-%d")
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
            sc = {"positive": "#16A34A", "neutral": "#6B7280", "negative": "#DC2626"}.get(p["sentiment"], "#6B7280")
            sl = {"positive": "긍정", "neutral": "중립", "negative": "부정"}.get(p["sentiment"], "-")
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
            color    = ch_colors.get(ch, "#6B7280")
            more_url = f"{base_url}/?channel={ch}&date_from={today}&date_to={today}"
            rows_html = "".join(post_row(p) for p in posts)
            sections_html += f"""
            <div style="margin-bottom:28px">
              <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
                <h2 style="font-size:14px;font-weight:600;color:{color};margin:0">{ch} ({len(posts)}건)</h2>
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
                <tbody>{rows_html}</tbody>
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

        send_email(to, f"[GLN 일일 리포트] {today} — {total}건 수집", html)
    except Exception as e:
        import traceback
        print(f"[리포트 오류] {e}")
        print(traceback.format_exc())
