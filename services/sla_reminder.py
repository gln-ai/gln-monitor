"""
services/sla_reminder.py — 미처리 SLA 리마인더 (gln-monitor 통합 버전)
"""
import os
from datetime import datetime

from config import KST
from db import get_db, get_setting
from services.email_svc import send_email as _send


def _send_email(to, subject, html):
    _send(to, subject, html)


def get_overdue_posts(sla_hours: int):
    conn = get_db()
    rows = conn.execute(f"""
        SELECT p.id, p.title, p.link, p.cafe_name, p.keyword,
               p.created_at, p.reply_status,
               a.sentiment, a.importance_score,
               ROUND((julianday('now','localtime') - julianday(p.created_at)) * 24, 1) as hours_elapsed
        FROM posts p
        LEFT JOIN ai_analysis a ON p.id = a.post_id
        WHERE p.reply_status = '미확인'
          AND (julianday('now','localtime') - julianday(p.created_at)) * 24 >= {sla_hours}
        ORDER BY a.importance_score DESC NULLS LAST, p.created_at ASC
        LIMIT 20
    """).fetchall()
    conn.close()
    return rows


def send_sla_reminder(to: str = ""):
    if get_setting("alert_sla_enabled", "1") != "1":
        print("[SLA 리마인더] 알림 OFF — 스킵")
        return

    SLA_HOURS   = int(get_setting("sla_hours",        "6"))
    ALERT_START = int(get_setting("alert_start_hour",  "8"))
    ALERT_END   = int(get_setting("alert_end_hour",   "20"))

    now_hour = datetime.now(KST).hour
    if not (ALERT_START <= now_hour < ALERT_END):
        print(f"[SLA 리마인더] 발송 시간 외 ({now_hour}시) — 스킵")
        return

    if not to:
        to = os.getenv("URGENT_ALERT_TO", os.getenv("REPORT_TO", ""))
    if not to:
        print("[SLA 리마인더] 수신자 없음 — 스킵")
        return

    posts = get_overdue_posts(SLA_HOURS)
    if not posts:
        print("[SLA 리마인더] 초과 건 없음 — 스킵")
        return

    print(f"[SLA 리마인더] {len(posts)}건 초과 발견")

    base_url = os.getenv("BASE_URL", "http://localhost:5001")

    def sentiment_color(s):
        return {"positive": "#16A34A", "neutral": "#6B7280", "negative": "#DC2626"}.get(s, "#6B7280")

    def sentiment_label(s):
        return {"positive": "긍정", "neutral": "중립", "negative": "부정"}.get(s, "-")

    def hours_badge(h):
        if h >= 24:
            color, label = "#DC2626", f"{h/24:.0f}일 {h%24:.0f}시간"
        elif h >= 12:
            color, label = "#D97706", f"{h:.0f}시간"
        else:
            color, label = "#6B7280", f"{h:.0f}시간"
        return f'<span style="background:#FEF2F2;color:{color};padding:2px 8px;border-radius:99px;font-size:11px;font-weight:700">{label} 경과</span>'

    rows_html = "".join(f"""
        <tr style="border-bottom:1px solid #F3F4F6">
          <td style="padding:10px 8px">
            <a href="{base_url}/post/{p['id']}" style="color:#1D4ED8;text-decoration:none;font-size:13px;font-weight:500">{(p['title'] or '')[:40]}</a>
            <div style="font-size:11px;color:#9CA3AF;margin-top:2px">{p['cafe_name'] or ''} · {(p['created_at'] or '')[:16]}</div>
          </td>
          <td style="padding:10px 8px;white-space:nowrap">{hours_badge(p['hours_elapsed'])}</td>
          <td style="padding:10px 8px;font-size:12px;color:{sentiment_color(p['sentiment'])};font-weight:600;white-space:nowrap">{sentiment_label(p['sentiment'])}</td>
          <td style="padding:10px 8px;font-size:13px;font-weight:700;text-align:center;color:#7000FC">{p['importance_score'] or '-'}</td>
        </tr>
    """ for p in posts)

    critical = [p for p in posts if p["hours_elapsed"] >= 24]
    now_str  = datetime.now(KST).strftime("%Y-%m-%d %H:%M")

    html = f"""
    <div style="font-family:-apple-system,sans-serif;max-width:680px;margin:auto;padding:24px;background:#fff">
      <div style="background:#FEF2F2;border-left:4px solid #DC2626;padding:12px 16px;border-radius:6px;margin-bottom:20px">
        <strong style="color:#B91C1C">미처리 SLA 초과 알림 — GLN 모니터링</strong>
        <div style="font-size:12px;color:#B91C1C;margin-top:2px">{now_str} 기준 · {SLA_HOURS}시간 초과 미확인 건</div>
      </div>
      <div style="display:flex;gap:12px;margin-bottom:20px">
        <div style="flex:1;background:#FEF2F2;border:1px solid #FECACA;border-radius:10px;padding:14px;text-align:center">
          <div style="font-size:32px;font-weight:800;color:#DC2626;line-height:1">{len(posts)}</div>
          <div style="font-size:11px;color:#6B7280;margin-top:4px">총 미처리 ({SLA_HOURS}h+)</div>
        </div>
        <div style="flex:1;background:#FFF7ED;border:1px solid #FED7AA;border-radius:10px;padding:14px;text-align:center">
          <div style="font-size:32px;font-weight:800;color:#D97706;line-height:1">{len(critical)}</div>
          <div style="font-size:11px;color:#6B7280;margin-top:4px">24시간 초과</div>
        </div>
        <div style="flex:1;background:#F9FAFB;border:1px solid #E5E7EB;border-radius:10px;padding:14px;text-align:center">
          <div style="font-size:32px;font-weight:800;color:#374151;line-height:1">{SLA_HOURS}h</div>
          <div style="font-size:11px;color:#6B7280;margin-top:4px">SLA 기준</div>
        </div>
      </div>
      <div style="background:#fff;border:1px solid #E5E7EB;border-radius:12px;overflow:hidden;margin-bottom:20px">
        <table style="width:100%;border-collapse:collapse">
          <thead><tr style="background:#F9FAFB">
            <th style="padding:8px;text-align:left;font-size:11px;color:#9CA3AF;font-weight:500">제목 / 출처</th>
            <th style="padding:8px;text-align:left;font-size:11px;color:#9CA3AF;font-weight:500">경과</th>
            <th style="padding:8px;text-align:left;font-size:11px;color:#9CA3AF;font-weight:500">감성</th>
            <th style="padding:8px;text-align:center;font-size:11px;color:#9CA3AF;font-weight:500">중요도</th>
          </tr></thead>
          <tbody>{rows_html}</tbody>
        </table>
      </div>
      <div style="text-align:center">
        <a href="{base_url}/?status=미확인" style="display:inline-block;padding:10px 24px;background:#DC2626;color:#fff;border-radius:10px;text-decoration:none;font-size:13px;font-weight:600">미처리 건 처리하기</a>
      </div>
      <p style="font-size:11px;color:#9CA3AF;text-align:center;margin-top:16px">GLN 모니터링 시스템 자동 발송 · {now_str}</p>
    </div>"""

    subject = f"[GLN SLA 초과] 미처리 {len(posts)}건 ({len(critical)}건 24h+)"
    _send_email(to, subject, html)
