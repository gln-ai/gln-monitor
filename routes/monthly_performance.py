"""
routes/monthly_performance.py — 월별 실적 대시보드
"""
from flask import Blueprint, render_template, request, jsonify
from db import get_db
import datetime

monthly_perf_bp = Blueprint("monthly_perf", __name__)

SEED_DATA = [
    # (year, month, members, revenue, profit)
    # 2025 baseline + monthly
    (2025, 0,  132, 1.9, -6.4),
    (2025, 1,  None, None, None),
    (2025, 2,  137, 2.3, -6.1),
    (2025, 3,  141, 2.0, -2.7),
    (2025, 4,  145, 2.0, -5.6),
    (2025, 5,  148, 2.0, -5.2),
    # 2026 monthly
    (2026, 0,  132, 1.9, -6.4),  # '25 기준점
    (2026, 1,  142, 3.8, -2.7),
    (2026, 2,  147, 4.6, -1.4),
    (2026, 3,  152, 4.4, -0.8),
    (2026, 4,  156, 4.2, -2.0),
    (2026, 5,  160, 4.2, -2.1),
]


def _seed_if_empty(conn):
    count = conn.execute("SELECT COUNT(*) FROM monthly_performance").fetchone()[0]
    if count == 0:
        conn.executemany(
            "INSERT OR IGNORE INTO monthly_performance (year, month, members, revenue, profit) VALUES (?,?,?,?,?)",
            SEED_DATA,
        )
        conn.commit()


def _get_chart_data(conn, year: int):
    """현재 연도와 전년도 데이터를 차트용으로 반환."""
    prev_year = year - 1

    def fetch_year(y):
        rows = conn.execute(
            "SELECT month, members, revenue, profit FROM monthly_performance WHERE year=? ORDER BY month",
            (y,),
        ).fetchall()
        return {r["month"]: r for r in rows}

    cur  = fetch_year(year)
    prev = fetch_year(prev_year)

    months = list(range(1, 13))

    labels, cur_members, cur_revenue, cur_profit = [], [], [], []
    prev_members, prev_revenue, prev_profit = [], [], []

    for m in months:
        labels.append(f"{m}월")

        def val(d, key):
            r = d.get(m)
            return r[key] if r and r[key] is not None else None

        cur_members.append(val(cur,  "members"))
        cur_revenue.append(val(cur,  "revenue"))
        cur_profit.append(val(cur,   "profit"))
        prev_members.append(val(prev, "members"))
        prev_revenue.append(val(prev, "revenue"))
        prev_profit.append(val(prev,  "profit"))

    return dict(
        labels=labels,
        cur_members=cur_members,   cur_revenue=cur_revenue,   cur_profit=cur_profit,
        prev_members=prev_members, prev_revenue=prev_revenue, prev_profit=prev_profit,
    )


@monthly_perf_bp.route("/monthly-report")
def monthly_report():
    conn = get_db()
    _seed_if_empty(conn)

    now = datetime.date.today()
    year  = int(request.args.get("year",  now.year))
    month = int(request.args.get("month", now.month))

    chart = _get_chart_data(conn, year)

    years = [r[0] for r in conn.execute(
        "SELECT DISTINCT year FROM monthly_performance ORDER BY year DESC"
    ).fetchall()]

    # 선택 월 실적 (요약 카드용)
    row = conn.execute(
        "SELECT * FROM monthly_performance WHERE year=? AND month=?", (year, month)
    ).fetchone()
    prev_row = conn.execute(
        "SELECT * FROM monthly_performance WHERE year=? AND month=?", (year - 1, month)
    ).fetchone()

    # 하단 테이블용 — 2025년 이후 전체
    all_rows = conn.execute(
        "SELECT * FROM monthly_performance WHERE year >= 2025 ORDER BY year, month"
    ).fetchall()
    conn.close()

    return render_template(
        "monthly_performance.html",
        chart=chart,
        year=year, month=month,
        years=years,
        row=row,
        prev_row=prev_row,
        all_rows=all_rows,
    )


@monthly_perf_bp.route("/api/monthly-report/save", methods=["POST"])
def save_monthly():
    data = request.get_json(force=True)
    try:
        year    = int(data["year"])
        month   = int(data["month"])
        members = float(data["members"]) if data.get("members") not in (None, "") else None
        revenue = float(data["revenue"]) if data.get("revenue") not in (None, "") else None
        profit  = float(data["profit"])  if data.get("profit")  not in (None, "") else None
        memo    = data.get("memo", "")
    except (KeyError, ValueError) as e:
        return jsonify(ok=False, error=str(e)), 400

    conn = get_db()
    conn.execute("""
        INSERT INTO monthly_performance (year, month, members, revenue, profit, memo, updated_at)
        VALUES (?,?,?,?,?,?, datetime('now','localtime'))
        ON CONFLICT(year, month) DO UPDATE SET
            members=excluded.members, revenue=excluded.revenue,
            profit=excluded.profit,  memo=excluded.memo,
            updated_at=excluded.updated_at
    """, (year, month, members, revenue, profit, memo))
    conn.commit()
    conn.close()
    return jsonify(ok=True)


@monthly_perf_bp.route("/api/monthly-report/delete", methods=["POST"])
def delete_monthly():
    data = request.get_json(force=True)
    year  = int(data.get("year",  0))
    month = int(data.get("month", 0))
    conn = get_db()
    conn.execute("DELETE FROM monthly_performance WHERE year=? AND month=?", (year, month))
    conn.commit()
    conn.close()
    return jsonify(ok=True)
