"""
gln-monitor/config.py — 앱 전역 설정
"""
import os
import sys
import pytz
from dotenv import load_dotenv

MONITOR_DIR = os.path.dirname(os.path.abspath(__file__))
APPS_ROOT   = os.path.dirname(MONITOR_DIR)

# shared/: 로컬은 ../shared/, 컨테이너는 ./shared/ (gln-monitor 내부에 복사)
_local_shared  = os.path.join(MONITOR_DIR, "shared")
_parent_shared = os.path.join(APPS_ROOT, "shared")
SHARED_DIR = _local_shared if os.path.exists(_local_shared) else _parent_shared

# .env 로드 (없어도 무시 — Railway는 환경변수 직접 설정)
load_dotenv(os.path.join(MONITOR_DIR, ".env"), override=False)

# shared/utils.py 임포트 경로 확보
if SHARED_DIR not in sys.path:
    sys.path.insert(0, SHARED_DIR)

KST = pytz.timezone("Asia/Seoul")

# DB_PATH: 환경변수 우선, 없으면 로컬 파일
DB_PATH = os.environ.get("DB_PATH") or os.path.join(MONITOR_DIR, "gln_monitor.db")

PR_RULES_PATH = os.path.join(SHARED_DIR, "pr_rules.json")
