"""
services/spike_detector.py — 언급량 급증 감지 (gln-monitor 통합 버전)
"""
import os
from datetime import datetime, timedelta

from config import KST
from db import get_db, get_setting
from services.email_svc import send_email as _send


MIN_COUNT = 3


def detect_spike(spike_threshold: float = 2.0) -> dict | None:
    now      = datetime.now(KST)
    from_now = (now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    to_now   = now.strftime("%Y-%m-%d %H:%M:%S")
    yesterday      = now - timedelta(days=1)
    from_yesterday = (yesterday - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    to_yesterday   = yesterday.strftime("%Y-%m-%d %H:%M:%S")

    conn    = get_db()
    current = conn.execute("SELECT COUNT(*) FROM posts WHERE created_at BETWEEN ? AND ?", (from_now, to_now)).fetchone()[0]
    prev    = conn.execute("SELECT COUNT(*) FROM posts WHERE created_at BETWEEN ? AND ?", (from_yesterday, to_yesterday)).fetchone()[0]
    kw_rows   = conn.execute("""
        SELECT SUBSTR(keyword, INSTR(keyword, '/') + 1) as kw, COUNT(*) as cnt
        FROM posts WHERE created_at BETWEEN ? AND ? AND keyword IS NOT NULL
        GROUP BY kw ORDER BY cnt DESC LIMIT 5
    """, (from_now, to_now)).fetchall()
    sent_rows = conn.execute("""
        SELECT a.sentiment, COUNT(*) as cnt
        FROM ai_analysis a JOIN posts p ON a.post_id = p.id
        WHERE p.created_at BETWEEN ? AND ? AND a.sentiment IS NOT NULL
        GROUP BY a.sentiment
    """, (from_now, to_now)).fetchall()
    conn.close()

    if current < MIN_COUNT:
        print(f"[스파이크] 현재 {current}건 — 임계 미만, 스킵")
        return None

    ratio = current / max(prev, 1)
    if ratio < spike_threshold:
        print(f"[스파이크] {current}건 / 전일 {prev}건 (×{ratio:.1f}) — 정상")
        return None

    print(f"[스파이크 감지!] {current}건 / 전일 {prev}건 (×{ratio:.1f})")
    return {
        "current":   current,
        "prev":      prev,
        "ratio":     ratio,
        "keywords":  [dict(r) for r in kw_rows],
        "sentiment": {r["sentiment"]: r["cnt"] for r in sent_rows},
        "period":    f"{from_now[11:16]} ~ {to_now[11:16]}",
        "date":      now.strftime("%Y-%m-%d"),
    }


def send_spike_alert(to: str = ""):
    if get_setting("alert_spike_enabled", "1") != "1":
        print("[급증 알림] 알림 OFF — 스킵")
        return

    if not to:
        to = os.getenv("URGENT_ALERT_TO", os.getenv("REPORT_TO", ""))
    if not to:
        print("[스파이크 알림] 수신자 없음 — 스킵")
        return

    result = detect_spike(float(get_setting("spike_threshold", "2.0")))
    if not result:
        return

    c, p, ratio = result["current"], result["prev"], result["ratio"]
    pct     = round((ratio - 1) * 100)
    sent    = result["sentiment"]
    s_total = sum(sent.values()) or 1
    neg_pct = round(sent.get("negative", 0) / s_total * 100)
    kw_rows_html = "".join(f"""
        <tr>
          <td style="padding:5px 8px;font-size:13px;color:#374151">{r['kw']}</td>
          <td style="padding:5px 8px;font-size:13px;font-weight:600;text-align:right;color:#7000FC">{r['cnt']}건</td>
        </tr>
    """ for r in result["keywords"])

    base_url = os.getenv("BASE_URL", "http://localhost:5001")
    html = f"""
    <div style="font-family:-apple-system,sans-serif;max-width:600px;margin:auto;padding:24px;background:#fff">
      <div style="background:#FEF3C7;border-left:4px solid #F59E0B;padding:12px 16px;border-radius:6px;margin-bottom:20px">
        <strong style="color:#92400E">언급량 급증 감지 — GLN 모니터링</strong>
        <div style="font-size:12px;color:#92400E;margin-top:2px">{result['date']} {result['period']}</div>
      </div>
      <div style="display:flex;gap:12px;margin-bottom:20px">
        <div style="flex:1;background:#F9FAFB;border-radius:10px;padding:14px;text-align:center">
          <div style="font-size:32px;font-weight:800;color:#DC2626;line-height:1">{c}</div>
          <div style="font-size:11px;color:#6B7280;margin-top:4px">현재 1시간</div>
        </div>
        <div style="flex:1;background:#F9FAFB;border-radius:10px;padding:14px;text-align:center">
          <div style="font-size:32px;font-weight:800;color:#374151;line-height:1">{p}</div>
          <div style="font-size:11px;color:#6B7280;margin-top:4px">전일 동시간</div>
        </div>
        <div style="flex:1;background:#FEF2F2;border-radius:10px;padding:14px;text-align:center">
          <div style="font-size:32px;font-weight:800;color:#DC2626;line-height:1">+{pct}%</div>
          <div style="font-size:11px;color:#6B7280;margin-top:4px">증가율</div>
        </div>
      </div>
      <div style="background:#F9FAFB;border-radius:10px;padding:14px;margin-bottom:16px">
        <div style="font-size:12px;font-weight:600;color:#374151;margin-bottom:8px">키워드별 분포</div>
        <table style="width:100%;border-collapse:collapse">{kw_rows_html}</table>
      </div>
      <div style="background:#F9FAFB;border-radius:10px;padding:14px;margin-bottom:20px">
        <div style="font-size:12px;font-weight:600;color:#374151;margin-bottom:6px">감성 분포</div>
        <div style="font-size:13px;color:#374151">
          긍정 {sent.get('positive',0)}건 &nbsp;·&nbsp; 중립 {sent.get('neutral',0)}건 &nbsp;·&nbsp;
          <span style="color:#DC2626;font-weight:600">부정 {sent.get('negative',0)}건 ({neg_pct}%)</span>
        </div>
      </div>
      <div style="text-align:center">
        <a href="{base_url}" style="display:inline-block;padding:10px 24px;background:#7000FC;color:#fff;border-radius:10px;text-decoration:none;font-size:13px;font-weight:600">대시보드에서 확인</a>
      </div>
      <p style="font-size:11px;color:#9CA3AF;text-align:center;margin-top:16px">GLN 모니터링 시스템 자동 발송</p>
    </div>"""

    _send(to, f"[GLN 급증 알림] 언급량 +{pct}% 증가 ({c}건 / 1시간)", html)
