"""
gln-monitor/db.py — DB 초기화 및 연결
"""
import os
import sqlite3
from config import DB_PATH


def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS posts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            title       TEXT NOT NULL,
            link        TEXT NOT NULL,
            description TEXT,
            cafe_name   TEXT,
            post_date   TEXT,
            created_at  TEXT DEFAULT (datetime('now','localtime')),
            hash        TEXT UNIQUE,
            keyword     TEXT,
            is_processed INTEGER DEFAULT 0,
            is_urgent   INTEGER DEFAULT 0,
            reply_status TEXT DEFAULT '미확인',
            status_updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS ai_analysis (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id          INTEGER UNIQUE,
            summary          TEXT,
            category         TEXT,
            sentiment        TEXT,
            importance_score INTEGER,
            created_at       TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (post_id) REFERENCES posts(id)
        );

        CREATE TABLE IF NOT EXISTS draft_replies (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER,
            type    TEXT,
            content TEXT,
            FOREIGN KEY (post_id) REFERENCES posts(id)
        );

        CREATE TABLE IF NOT EXISTS pr_drafts (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            headline       TEXT,
            subheadline    TEXT,
            body           TEXT,
            key_messages   TEXT,
            verify_list    TEXT,
            approval_status TEXT DEFAULT 'pending',
            created_at     TEXT DEFAULT (datetime('now','localtime')),
            updated_at     TEXT
        );

        CREATE TABLE IF NOT EXISTS keywords (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            keyword    TEXT NOT NULL UNIQUE,
            channel    TEXT DEFAULT 'all',
            is_active  INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS content_drafts (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            source_post_id INTEGER,
            topic          TEXT,
            seo_titles     TEXT,
            body           TEXT,
            shorts_script  TEXT,
            verify_list    TEXT,
            guard_grade    TEXT DEFAULT 'pending',
            guard_issues   TEXT,
            created_at     TEXT DEFAULT (datetime('now','localtime')),
            updated_at     TEXT
        );
    """)
    conn.commit()
    # 마이그레이션 — 기존 DB에 컬럼 없으면 추가
    for alter_sql in [
        "ALTER TABLE posts ADD COLUMN reply_status TEXT DEFAULT '미확인'",
        "ALTER TABLE posts ADD COLUMN status_updated_at TEXT",
        # v2: 이원화 채널 지원
        "ALTER TABLE content_drafts ADD COLUMN channel TEXT DEFAULT 'official'",
        "ALTER TABLE content_drafts ADD COLUMN format TEXT DEFAULT 'blog'",
        "ALTER TABLE content_drafts ADD COLUMN platform TEXT DEFAULT 'naver_blog'",
        "ALTER TABLE content_drafts ADD COLUMN raw_output TEXT",
        # v3: 발행 상태 (approval_status 컬럼을 publish_status 용도로 재활용)
        "ALTER TABLE content_drafts ADD COLUMN approval_status TEXT DEFAULT 'unpublished'",
        # 값: 'unpublished' | 'published'
        "ALTER TABLE content_drafts ADD COLUMN approved_at TEXT",   # published_at
        "ALTER TABLE content_drafts ADD COLUMN approved_by TEXT",   # published_by
        "ALTER TABLE content_drafts ADD COLUMN reject_reason TEXT", # 미사용 (하위 호환)
        # v4: 키워드 관리
        "ALTER TABLE keywords ADD COLUMN is_active INTEGER DEFAULT 1",
        # v5: 국가 + 소재 출처
        "ALTER TABLE content_drafts ADD COLUMN country TEXT",
        "ALTER TABLE content_drafts ADD COLUMN source_type TEXT DEFAULT 'auto'",
        # v6: 생성된 이미지 경로 목록 (JSON 배열)
        "ALTER TABLE content_drafts ADD COLUMN image_paths TEXT",
        # v8: 휴지통 — 소프트 삭제
        "ALTER TABLE content_drafts ADD COLUMN deleted_at TEXT",
    ]:
        try:
            conn.execute(alter_sql)
            conn.commit()
        except Exception:
            pass

    # v7: 알림 설정 테이블
    conn.execute("""
        CREATE TABLE IF NOT EXISTS app_settings (
            key        TEXT PRIMARY KEY,
            value      TEXT NOT NULL,
            updated_at TEXT
        )
    """)
    conn.commit()
    for key, value in [
        ("alert_sla_enabled",    "1"),
        ("alert_spike_enabled",  "1"),
        ("alert_urgent_enabled", "1"),
        ("sla_hours",            "6"),
        ("spike_threshold",      "2.0"),
        ("alert_start_hour",     "8"),
        ("alert_end_hour",       "20"),
        ("report_to_list",       os.getenv("REPORT_TO", "")),
        ("urgent_alert_to_list", os.getenv("URGENT_ALERT_TO", "brad@glninternational.com")),
    ]:
        conn.execute(
            "INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, ?)",
            (key, value)
        )
    conn.commit()
    conn.close()


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_setting(key: str, default: str = "") -> str:
    try:
        conn = get_db()
        row  = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
        conn.close()
        return row["value"] if row else default
    except Exception:
        return default
