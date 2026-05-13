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

from flask import Flask

from db import init_db
from routes import monitor_bp, content_bp, pr_bp, reports_bp, keywords_bp
from services.naver import collect_all
from services.email_svc import send_daily_report
from services.pipeline import run_content_pipeline
from services.sla_reminder import send_sla_reminder
from services.spike_detector import send_spike_alert
from services.weekly_report import send_weekly_report
from services.log_reporter import save_daily_report, save_weekly_report as save_weekly_log, save_monthly_report

app = Flask(__name__)
app.register_blueprint(monitor_bp)
app.register_blueprint(content_bp)
app.register_blueprint(pr_bp)
app.register_blueprint(reports_bp)
app.register_blueprint(keywords_bp)

# ── DB 초기화 + 스케줄러 (gunicorn/직접 실행 모두 동작) ───────────────────────
from apscheduler.schedulers.background import BackgroundScheduler

init_db()

_scheduler = BackgroundScheduler(timezone="Asia/Seoul")
_scheduler.add_job(collect_all,          "interval", hours=1,  id="collect")
_scheduler.add_job(send_daily_report,    "cron", hour=8,  minute=0, id="daily_report")
_scheduler.add_job(run_content_pipeline, "cron", hour=9,  minute=0, id="content_pipeline")
_scheduler.add_job(send_sla_reminder,    "cron", hour=17, minute=0, id="sla_reminder")
_scheduler.add_job(send_spike_alert,     "interval", hours=1, id="spike_detector")
_scheduler.add_job(send_weekly_report,   "cron", day_of_week="mon", hour=9, minute=0,  id="weekly_report")
_scheduler.add_job(save_daily_report,    "cron", hour=23, minute=55,                   id="log_daily")
_scheduler.add_job(save_weekly_log,      "cron", day_of_week="mon", hour=9, minute=5,  id="log_weekly")
_scheduler.add_job(save_monthly_report,  "cron", day=1,  hour=9, minute=10,            id="log_monthly")
_scheduler.start()
print("[스케줄러] 수집 1h / 일일리포트 08:00 / 주간리포트 월09:00 / 콘텐츠 09:00 / SLA 17:00 / 스파이크 1h / 로그저장 23:55")

if __name__ == "__main__":
    print("\n✅ GLN 모니터링 시작!")
    print("📊 대시보드: http://localhost:5001\n")
    collect_all()
    port = int(os.getenv("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=False)
