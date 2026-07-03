"""
GLN 네이버 카페 모니터링 웹앱
실행: python app.py  또는  gunicorn app:app
대시보드: http://localhost:5001
"""
import os
import sys

# ── 경로 설정 (가장 먼저 실행) ─────────────────────────────────────────────────
MONITOR_DIR = os.path.dirname(os.path.abspath(__file__))
if MONITOR_DIR not in sys.path:
    sys.path.insert(0, MONITOR_DIR)

# config를 먼저 import → .env 로드 + shared/ 경로를 sys.path에 추가
import config  # noqa: F401 (side-effect: .env load, sys.path update)
from config import APPS_ROOT

from flask import Flask

from db import init_db, get_setting
print("[startup] db imported OK", flush=True)

from routes import monitor_bp, content_bp, pr_bp, reports_bp, keywords_bp, admin_bp, monthly_perf_bp, overview_bp
print("[startup] base routes imported OK", flush=True)

try:
    from routes.content_eval import content_eval_bp
    _content_eval_ok = True
    print("[startup] content_eval_bp imported OK", flush=True)
except Exception as _e:
    content_eval_bp = None
    _content_eval_ok = False
    print(f"[startup] content_eval_bp import FAILED: {_e}", flush=True)

from services.naver import collect_all
from services.email_svc import send_daily_report
from services.pipeline import run_content_pipeline
from services.sla_reminder import send_sla_reminder
from services.spike_detector import send_spike_alert
from services.weekly_report import send_weekly_report
from services.log_reporter import save_daily_report, save_weekly_report as save_weekly_log, save_monthly_report
from services.tourism_stats import update_all as update_tourism
from services.jnto_fetcher import fetch_jnto
from services.kto_fetcher import fetch_kto_total


def _daily_weekday():
    to = (get_setting("report_to_weekday") or os.getenv("REPORT_TO", "")).strip()
    if to:
        send_daily_report(to)
    else:
        print("[일일리포트] 평일 수신자 미설정 — 스킵")


def _daily_weekend():
    to = (get_setting("report_to_weekend") or "").strip()
    if to:
        send_daily_report(to)
    else:
        print("[일일리포트] 주말 수신자 미설정 — 스킵")

app = Flask(__name__)


@app.context_processor
def inject_sidebar_globals():
    from db import get_db
    try:
        conn = get_db()
        row = conn.execute("""
            SELECT COUNT(*) AS cnt FROM posts p
            LEFT JOIN ai_analysis a ON p.id = a.post_id
            WHERE (p.reply_status IS NULL OR p.reply_status = '미확인')
            AND (a.importance_score >= 7 OR a.sentiment = 'negative')
            AND date(p.created_at) = date('now', 'localtime')
        """).fetchone()
        conn.close()
        count = row["cnt"] if row else 0
    except Exception:
        count = 0
    return dict(sidebar_urgent=count)


app.register_blueprint(monitor_bp)
app.register_blueprint(content_bp)
app.register_blueprint(pr_bp)
app.register_blueprint(reports_bp)
app.register_blueprint(keywords_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(monthly_perf_bp)
app.register_blueprint(overview_bp)
if _content_eval_ok and content_eval_bp:
    app.register_blueprint(content_eval_bp)
    print("[startup] content_eval_bp registered OK", flush=True)

# ── DB 초기화 + 스케줄러 (gunicorn/직접 실행 모두 동작) ───────────────────────
from apscheduler.schedulers.background import BackgroundScheduler

init_db()

# 이미지 생성 저장 경로 자동 초기화
import pathlib
pathlib.Path(os.path.join(MONITOR_DIR, "static", "generated", "images")).mkdir(parents=True, exist_ok=True)

def _sync_channel_performance():
    """marketing-dashboard/sync.js 실행 → channel_performance 자동 수집."""
    import subprocess
    sync_path = os.path.join(APPS_ROOT, "marketing-dashboard", "sync.js")
    if not os.path.exists(sync_path):
        print("[채널동기화] sync.js 없음 — 스킵", flush=True)
        return
    try:
        result = subprocess.run(
            ["node", sync_path],
            cwd=os.path.join(APPS_ROOT, "marketing-dashboard"),
            capture_output=True, text=True, timeout=120,
        )
        print(f"[채널동기화] 완료 (returncode={result.returncode})", flush=True)
    except Exception as e:
        print(f"[채널동기화] 오류: {e}", flush=True)


# misfire_grace_time=3600: 맥 절전 후 깨어날 때 최대 1시간 이내 잡 소급 실행
_scheduler = BackgroundScheduler(timezone="Asia/Seoul", misfire_grace_time=3600, coalesce=True)
_scheduler.add_job(collect_all,          "interval", hours=1,  id="collect")
_scheduler.add_job(_daily_weekday,       "cron", day_of_week="mon-fri", hour=8, minute=0, id="daily_weekday")
_scheduler.add_job(_daily_weekend,       "cron", day_of_week="sat,sun",  hour=8, minute=0, id="daily_weekend")
_scheduler.add_job(run_content_pipeline, "cron", hour=9,  minute=0, id="content_pipeline")
# _scheduler.add_job(send_sla_reminder,    "cron", hour=17, minute=0, id="sla_reminder")
# _scheduler.add_job(send_spike_alert,     "interval", hours=1, id="spike_detector")
_scheduler.add_job(send_weekly_report,   "cron", day_of_week="mon", hour=8, minute=0,  id="weekly_report")
_scheduler.add_job(save_daily_report,    "cron", hour=23, minute=55,                   id="log_daily")
_scheduler.add_job(save_weekly_log,      "cron", day_of_week="mon", hour=8, minute=5,  id="log_weekly")
_scheduler.add_job(save_monthly_report,  "cron", day=1,  hour=8, minute=10,            id="log_monthly")
_scheduler.add_job(update_tourism,       "cron", day=1,  hour=9, minute=30,            id="tourism_update")
_scheduler.add_job(fetch_jnto,                "cron", day=15, hour=10, minute=0,   id="jnto_monthly")
_scheduler.add_job(fetch_kto_total,           "cron", day=5,  hour=10, minute=30,  id="kto_monthly")
_scheduler.add_job(_sync_channel_performance, "cron", hour=0, minute=30,           id="channel_sync")
_scheduler.start()
app._scheduler = _scheduler
print("[스케줄러] 수집 1h / 아침브리핑 08:00(평일) / 주간리포트 월08:00 / 콘텐츠 09:00 / 채널동기화 00:30 / 로그저장 23:55")

if __name__ == "__main__":
    print("\n✅ GLN 모니터링 시작!")
    print("📊 대시보드: http://localhost:5001\n")
    collect_all()
    port = int(os.getenv("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=False)
