"""
services/weekly_report.py — 주간 브랜드 모니터링 리포트
매주 월요일 09:00 KST 자동 발송 (전주 월~일 데이터 집계)
"""
import os
from datetime import datetime, timedelta

from config import KST
from db import get_db, get_setting
from services.email_svc import send_email

_MONITOR_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CAT_COLOR = {
    "정보공유": "#7000FC",
    "후기":     "#059669",
    "문의":     "#D97706",
    "불만":     "#DC2626",
    "기타":     "#9CA3AF",
}
_DOW_LABEL = ["일", "월", "화", "수", "목", "금", "토"]  # strftime %w 0=일
_DOW_ORDER = [1, 2, 3, 4, 5, 6, 0]  # 월~토~일 표시 순서


def build_weekly_report():
    now         = datetime.now(KST)
    last_monday = (now - timedelta(days=now.weekday() + 7)).replace(hour=0, minute=0, second=0, microsecond=0)
    last_sunday = last_monday + timedelta(days=6, hours=23, minutes=59, seconds=59)
    date_from   = last_monday.strftime("%Y-%m-%d")
    date_to     = last_sunday.strftime("%Y-%m-%d")
    week_label  = f"{date_from} ~ {date_to}"

    conn = get_db()

    prev_monday = last_monday - timedelta(days=7)
    prev_sunday = last_sunday - timedelta(days=7)

    def count_period(d_from, d_to, extra=""):
        return conn.execute(
            f"SELECT COUNT(*) FROM posts WHERE DATE(created_at) BETWEEN ? AND ? {extra}",
            (d_from.strftime("%Y-%m-%d"), d_to.strftime("%Y-%m-%d"))
        ).fetchone()[0]

    total_this  = count_period(last_monday, last_sunday)
    total_prev  = count_period(prev_monday, prev_sunday)
    urgent_this = count_period(last_monday, last_sunday, "AND is_urgent=1")
    urgent_prev = count_period(prev_monday, prev_sunday, "AND is_urgent=1")

    def pct_change(curr, prev):
        if prev == 0:
            return "+100%" if curr > 0 else "0%"
        delta = ((curr - prev) / prev) * 100
        return f"{'+'if delta>=0 else ''}{delta:.0f}%"

    def arrow(curr, prev):
        return "▲" if curr > prev else "▼" if curr < prev else "─"

    def arrow_color(curr, prev, invert=False):
        up   = "#DC2626" if invert else "#16A34A"
        down = "#16A34A" if invert else "#DC2626"
        return up if curr > prev else down if curr < prev else "#6B7280"

    channels = ["카페", "블로그", "뉴스"]
    ch_stats = {}
    for ch in channels:
        ch_stats[ch] = conn.execute(
            "SELECT COUNT(*) FROM posts WHERE DATE(created_at) BETWEEN ? AND ? AND keyword LIKE ?",
            (date_from, date_to, f"{ch}/%")
        ).fetchone()[0]

    sentiment_rows = conn.execute("""
        SELECT a.sentiment, COUNT(*) as cnt
        FROM ai_analysis a JOIN posts p ON a.post_id = p.id
        WHERE DATE(p.created_at) BETWEEN ? AND ? AND a.sentiment IS NOT NULL
          AND (a.is_relevant IS NULL OR a.is_relevant = 1)
        GROUP BY a.sentiment
    """, (date_from, date_to)).fetchall()
    sentiment = {r["sentiment"]: r["cnt"] for r in sentiment_rows}
    s_total   = sum(sentiment.values()) or 1
    pos_pct   = round(sentiment.get("positive", 0) / s_total * 100)
    neu_pct   = round(sentiment.get("neutral",  0) / s_total * 100)
    neg_pct   = round(sentiment.get("negative", 0) / s_total * 100)

    health_score = round(pos_pct * 0.6 + (100 - neg_pct) * 0.4)
    health_color = "#16A34A" if health_score >= 70 else "#D97706" if health_score >= 50 else "#DC2626"
    health_label = "양호"    if health_score >= 70 else "주의"    if health_score >= 50 else "위험"

    # 카테고리 분포
    cat_rows = conn.execute("""
        SELECT a.category, COUNT(*) as cnt FROM ai_analysis a
        JOIN posts p ON a.post_id = p.id
        WHERE DATE(p.created_at) BETWEEN ? AND ?
          AND a.category IS NOT NULL AND (a.is_relevant IS NULL OR a.is_relevant = 1)
        GROUP BY a.category ORDER BY cnt DESC
    """, (date_from, date_to)).fetchall()

    # 요일별 분포 (strftime %w: 0=일, 1=월, ..., 6=토)
    day_rows = conn.execute("""
        SELECT CAST(strftime('%w', created_at) AS INTEGER) as dow, COUNT(*) as cnt
        FROM posts WHERE DATE(created_at) BETWEEN ? AND ?
        GROUP BY dow ORDER BY dow
    """, (date_from, date_to)).fetchall()

    kw_rows = conn.execute("""
        SELECT SUBSTR(keyword, INSTR(keyword, '/') + 1) as kw, COUNT(*) as cnt
        FROM posts WHERE DATE(created_at) BETWEEN ? AND ? AND keyword IS NOT NULL
        GROUP BY kw ORDER BY cnt DESC LIMIT 5
    """, (date_from, date_to)).fetchall()
    kw_max = kw_rows[0]["cnt"] if kw_rows else 1

    top_posts = conn.execute("""
        SELECT p.title, p.link, a.summary, a.category, a.sentiment, a.importance_score
        FROM posts p LEFT JOIN ai_analysis a ON p.id = a.post_id
        WHERE DATE(p.created_at) BETWEEN ? AND ? AND a.importance_score IS NOT NULL
          AND (a.is_relevant IS NULL OR a.is_relevant = 1)
        ORDER BY a.importance_score DESC LIMIT 5
    """, (date_from, date_to)).fetchall()

    unprocessed = conn.execute(
        "SELECT COUNT(*) FROM posts WHERE reply_status='미확인' AND DATE(created_at) BETWEEN ? AND ?",
        (date_from, date_to)
    ).fetchone()[0]
    conn.close()

    base_url = os.getenv("BASE_URL", "http://192.168.1.30:5001")

    def sentiment_badge(s):
        colors = {"positive": ("#DCFCE7","#16A34A","긍정"), "neutral": ("#F3F4F6","#6B7280","중립"), "negative": ("#FEE2E2","#DC2626","부정")}
        bg, fg, label = colors.get(s, ("#F3F4F6","#6B7280","-"))
        return f'<span style="background:{bg};color:{fg};padding:2px 8px;border-radius:99px;font-size:11px;font-weight:600">{label}</span>'

    top_rows_html = "".join(f"""
        <tr style="border-bottom:1px solid #F3F4F6">
          <td style="padding:10px 8px;font-size:13px">
            <a href="{p['link']}" style="color:#1D4ED8;text-decoration:none;font-weight:500">{(p['title'] or '')[:45]}</a>
            <div style="font-size:11px;color:#9CA3AF;margin-top:2px">{p['summary'] or ''}</div>
          </td>
          <td style="padding:10px 8px;font-size:12px;white-space:nowrap">{p['category'] or '-'}</td>
          <td style="padding:10px 8px">{sentiment_badge(p['sentiment'])}</td>
          <td style="padding:10px 8px;font-size:13px;font-weight:700;text-align:center;color:#7000FC">{p['importance_score'] or '-'}</td>
        </tr>""" for p in top_posts)

    kw_bars_html = "".join(f"""
        <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">
          <div style="width:80px;font-size:12px;color:#374151;font-weight:500;text-align:right">{r['kw']}</div>
          <div style="flex:1;height:8px;background:#F0EDF7;border-radius:4px;overflow:hidden">
            <div style="width:{round(r['cnt']/kw_max*100)}%;height:100%;background:#7000FC;border-radius:4px"></div>
          </div>
          <div style="width:30px;font-size:12px;font-weight:600;color:#130D2A">{r['cnt']}</div>
        </div>""" for r in kw_rows)

    ch_cells = "".join(f"""
        <div style="text-align:center;flex:1">
          <div style="font-size:22px;font-weight:700;color:#130D2A">{ch_stats.get(ch,0)}</div>
          <div style="font-size:11px;color:#918DA0;margin-top:2px">{ch}</div>
        </div>""" for ch in channels)

    # 카테고리 분포 바
    cat_max = cat_rows[0]["cnt"] if cat_rows else 1
    cat_bars_html = "".join(f"""
        <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px">
          <div style="width:60px;font-size:12px;color:#374151;font-weight:500;text-align:right">{r['category']}</div>
          <div style="flex:1;height:8px;background:#F3F4F6;border-radius:4px;overflow:hidden">
            <div style="width:{round(r['cnt']/cat_max*100)}%;height:100%;background:{_CAT_COLOR.get(r['category'],'#9CA3AF')};border-radius:4px"></div>
          </div>
          <div style="width:30px;font-size:12px;font-weight:600;color:#374151">{r['cnt']}</div>
        </div>""" for r in cat_rows) if cat_rows else "<div style='font-size:12px;color:#9CA3AF'>데이터 없음</div>"

    # 요일별 분포 미니 바
    day_dict = {r["dow"]: r["cnt"] for r in day_rows}
    day_max  = max(day_dict.values()) if day_dict else 1
    bar_h    = 36

    day_cells_html = "".join(f"""
        <div style="text-align:center;flex:1">
          <div style="height:{bar_h}px;display:flex;align-items:flex-end;justify-content:center;margin-bottom:3px">
            <div style="width:14px;background:#7000FC;border-radius:3px 3px 0 0;height:{max(2,round(day_dict.get(dow,0)/day_max*bar_h))}px"></div>
          </div>
          <div style="font-size:10px;color:#9CA3AF">{_DOW_LABEL[dow]}</div>
          <div style="font-size:10px;font-weight:600;color:#374151">{day_dict.get(dow,0)}</div>
        </div>""" for dow in _DOW_ORDER)

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
</head>
<body style="margin:0;padding:20px 12px;background:#F5F3FF;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif">
<div style="max-width:680px;margin:auto;background:#fff;border-radius:16px;overflow:hidden;border:1px solid #DDD6FE;box-shadow:0 2px 12px rgba(112,0,252,0.08)">

  <!-- 라벤더 헤더 -->
  <div style="background:#EDE7FF;padding:0">
    <table width="100%" cellpadding="0" cellspacing="0">
      <tr>
        <td style="padding:24px 16px 24px 24px;vertical-align:middle" width="100">
          <img src="cid:mascot" alt="AI퍼플이" width="84" height="84"
               style="border-radius:50%;border:3px solid #7000FC;display:block;object-fit:cover">
        </td>
        <td style="padding:24px 24px 24px 0;vertical-align:middle">
          <div style="font-size:12px;font-weight:800;color:#7000FC;letter-spacing:0.12em;margin-bottom:8px">[AI퍼플이] 주간 브리핑 📊</div>
          <div style="font-size:22px;font-weight:800;color:#1E0942;line-height:1.2">GLN 주간 모니터링 리포트</div>
          <div style="font-size:15px;color:#6D28D9;margin-top:6px;font-weight:500">{week_label}</div>
        </td>
        <td style="padding:24px 24px 24px 0;vertical-align:middle;text-align:right" width="90">
          <div style="background:#7000FC;border-radius:12px;padding:10px 14px;display:inline-block;text-align:center">
            <div style="font-size:28px;font-weight:800;color:#fff;line-height:1">{health_score}</div>
            <div style="font-size:10px;color:rgba(255,255,255,0.7);margin-top:2px">브랜드 헬스</div>
            <div style="font-size:11px;font-weight:700;color:{health_color};margin-top:2px">{health_label}</div>
          </div>
        </td>
      </tr>
    </table>
  </div>

  <!-- 본문 -->
  <div style="padding:20px 24px">

    <!-- 요약 카드 3개 -->
    <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:16px">
      <tr>
        <td width="33%" style="padding-right:5px">
          <div style="background:#F9FAFB;border:1px solid #E5E7EB;border-radius:12px;padding:16px 18px">
            <div style="font-size:11px;color:#918DA0;margin-bottom:4px">총 언급</div>
            <div style="font-size:28px;font-weight:700;color:#130D2A;line-height:1">{total_this}</div>
            <div style="font-size:12px;color:{arrow_color(total_this,total_prev)};margin-top:6px">{arrow(total_this,total_prev)} 전주 {total_prev}건 ({pct_change(total_this,total_prev)})</div>
          </div>
        </td>
        <td width="33%" style="padding:0 3px">
          <div style="background:#FEF2F2;border:1px solid #FECACA;border-radius:12px;padding:16px 18px">
            <div style="font-size:11px;color:#918DA0;margin-bottom:4px">긴급 알림</div>
            <div style="font-size:28px;font-weight:700;color:#DC2626;line-height:1">{urgent_this}</div>
            <div style="font-size:12px;color:{arrow_color(urgent_this,urgent_prev,invert=True)};margin-top:6px">{arrow(urgent_this,urgent_prev)} 전주 {urgent_prev}건 ({pct_change(urgent_this,urgent_prev)})</div>
          </div>
        </td>
        <td width="33%" style="padding-left:5px">
          <div style="background:#F5F3FF;border:1px solid #DDD6FE;border-radius:12px;padding:16px 18px">
            <div style="font-size:11px;color:#918DA0;margin-bottom:4px">미처리</div>
            <div style="font-size:28px;font-weight:700;color:#7000FC;line-height:1">{unprocessed}</div>
            <div style="font-size:12px;color:#918DA0;margin-top:6px">전주 미확인 누계</div>
          </div>
        </td>
      </tr>
    </table>

    <!-- 채널별 언급량 -->
    <div style="background:#fff;border:1px solid #E5E7EB;border-radius:12px;padding:16px 20px;margin-bottom:16px">
      <div style="font-size:13px;font-weight:600;color:#130D2A;margin-bottom:14px">채널별 언급량</div>
      <div style="display:flex;gap:0">{ch_cells}</div>
    </div>

    <!-- 감성 분포 -->
    <div style="background:#fff;border:1px solid #E5E7EB;border-radius:12px;padding:16px 20px;margin-bottom:16px">
      <div style="font-size:13px;font-weight:600;color:#130D2A;margin-bottom:14px">감성 분포</div>
      <div style="display:flex;height:12px;border-radius:6px;overflow:hidden;margin-bottom:10px">
        <div style="width:{pos_pct}%;background:#16A34A"></div>
        <div style="width:{neu_pct}%;background:#9CA3AF"></div>
        <div style="width:{neg_pct}%;background:#DC2626"></div>
      </div>
      <div style="display:flex;gap:16px;font-size:12px">
        <span style="color:#16A34A;font-weight:600">긍정 {pos_pct}% ({sentiment.get('positive',0)}건)</span>
        <span style="color:#6B7280">중립 {neu_pct}% ({sentiment.get('neutral',0)}건)</span>
        <span style="color:#DC2626;font-weight:600">부정 {neg_pct}% ({sentiment.get('negative',0)}건)</span>
      </div>
    </div>

    <!-- 카테고리 분포 (NEW) -->
    <div style="background:#fff;border:1px solid #E5E7EB;border-radius:12px;padding:16px 20px;margin-bottom:16px">
      <div style="font-size:13px;font-weight:600;color:#130D2A;margin-bottom:14px">카테고리 분포</div>
      {cat_bars_html}
    </div>

    <!-- 요일별 분포 (NEW) -->
    <div style="background:#fff;border:1px solid #E5E7EB;border-radius:12px;padding:16px 20px;margin-bottom:16px">
      <div style="font-size:13px;font-weight:600;color:#130D2A;margin-bottom:14px">요일별 언급량</div>
      <div style="display:flex;gap:4px;align-items:flex-end">
        {day_cells_html}
      </div>
    </div>

    <!-- 키워드별 언급량 TOP 5 -->
    <div style="background:#fff;border:1px solid #E5E7EB;border-radius:12px;padding:16px 20px;margin-bottom:16px">
      <div style="font-size:13px;font-weight:600;color:#130D2A;margin-bottom:14px">키워드별 언급량 TOP 5</div>
      {kw_bars_html}
    </div>

    <!-- 주요 언급 TOP 5 -->
    <div style="background:#fff;border:1px solid #E5E7EB;border-radius:12px;padding:16px 20px;margin-bottom:20px">
      <div style="font-size:13px;font-weight:600;color:#130D2A;margin-bottom:12px">주요 언급 TOP 5</div>
      <table style="width:100%;border-collapse:collapse;font-size:13px">
        <thead><tr style="background:#F9FAFB">
          <th style="padding:7px 8px;text-align:left;font-size:11px;color:#9CA3AF;font-weight:500">제목 / 요약</th>
          <th style="padding:7px 8px;text-align:left;font-size:11px;color:#9CA3AF;font-weight:500">분류</th>
          <th style="padding:7px 8px;text-align:left;font-size:11px;color:#9CA3AF;font-weight:500">감성</th>
          <th style="padding:7px 8px;text-align:center;font-size:11px;color:#9CA3AF;font-weight:500">중요도</th>
        </tr></thead>
        <tbody>{top_rows_html}</tbody>
      </table>
    </div>

    <!-- 푸터 -->
    <div style="text-align:center;border-top:1px solid #F3F4F6;padding-top:16px">
      <a href="{base_url}" style="display:inline-block;padding:10px 24px;background:#7000FC;color:#fff;border-radius:10px;text-decoration:none;font-size:13px;font-weight:600">대시보드 열기</a>
      <p style="font-size:11px;color:#9CA3AF;margin-top:12px">GLN 모니터링 시스템 자동 발송 · 매주 월요일 09:00 KST</p>
    </div>

  </div>
</div>
</body>
</html>"""

    return html, week_label, total_this


def send_weekly_report(to: str = ""):
    if not to:
        to = get_setting("report_to_list") or os.getenv("REPORT_TO", "")
    if not to:
        print("[주간 리포트] 수신자 없음 — 스킵")
        return
    print(f"[주간 리포트] 생성 시작 ({datetime.now(KST).strftime('%Y-%m-%d %H:%M')})", flush=True)
    try:
        html, week_label, total = build_weekly_report()
        mascot_path = os.path.join(_MONITOR_DIR, "static", "img", "mascot_email.jpg")
        img_files   = {"mascot": mascot_path} if os.path.isfile(mascot_path) else {}
        send_email(
            to,
            f"[AI퍼플이] 주간 브리핑 📊 {week_label} — 총 {total}건",
            html,
            report_type="weekly",
            images=img_files or None,
        )
        print(f"[주간 리포트] 발송 완료: {week_label}", flush=True)
    except Exception as e:
        import traceback
        print(f"[주간 리포트 오류] {e}")
        print(traceback.format_exc())
