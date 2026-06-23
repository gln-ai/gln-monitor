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

        -- 연간 외래관광객 통계 (KOSIS, year_month='2023' 형식, 연도 단위)
        CREATE TABLE IF NOT EXISTS tourism_stats (
            year_month  TEXT NOT NULL,
            country     TEXT NOT NULL,
            visitors    INTEGER NOT NULL,
            fetched_at  TEXT DEFAULT (datetime('now','localtime')),
            PRIMARY KEY (year_month, country)
        );

        -- 월별 입국자 통계 (JNTO/KTO/수동업로드, year_month='2024-01' 형식, 월 단위)
        CREATE TABLE IF NOT EXISTS tourism_monthly (
            year_month  TEXT NOT NULL,
            country     TEXT NOT NULL,
            visitors    INTEGER NOT NULL,
            source      TEXT DEFAULT 'upload',
            fetched_at  TEXT DEFAULT (datetime('now','localtime')),
            PRIMARY KEY (year_month, country)
        );

        CREATE TABLE IF NOT EXISTS reports_archive (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            report_type  TEXT NOT NULL,
            filename     TEXT NOT NULL,
            period_start TEXT,
            period_end   TEXT,
            generated_at TEXT,
            data_json    TEXT NOT NULL,
            created_at   TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(report_type, filename)
        );

        CREATE TABLE IF NOT EXISTS email_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            report_type TEXT,
            subject     TEXT,
            recipients  TEXT,
            sent_at     TEXT DEFAULT (datetime('now','localtime')),
            status      TEXT DEFAULT 'ok',
            error_msg   TEXT
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
        # v9: GLN 관련성 (0=무관, 1=관련, NULL=미분석)
        "ALTER TABLE ai_analysis ADD COLUMN is_relevant INTEGER DEFAULT 1",
        # v10: 원소스 멀티유즈 — 배치 ID + 추가 요구사항
        "ALTER TABLE content_drafts ADD COLUMN batch_id TEXT DEFAULT NULL",
        "ALTER TABLE content_drafts ADD COLUMN requirements TEXT DEFAULT ''",
        # v11: 보도자료 소재 연동 + 유형
        "ALTER TABLE pr_drafts ADD COLUMN source_post_id INTEGER",
        "ALTER TABLE pr_drafts ADD COLUMN pr_type TEXT DEFAULT 'general'",
        # v12: 채널 성과 테이블 (marketing-dashboard → ingest)
        # 테이블 자체는 아래 CREATE TABLE IF NOT EXISTS로 생성
        # content_drafts에 발행 URL 추가
        "ALTER TABLE content_drafts ADD COLUMN published_url TEXT",
        # v14: 보도자료 확장
        "ALTER TABLE pr_drafts ADD COLUMN country TEXT",
        "ALTER TABLE pr_drafts ADD COLUMN tags TEXT",
        "ALTER TABLE pr_drafts ADD COLUMN sent_at TEXT",
        "ALTER TABLE pr_drafts ADD COLUMN sent_to TEXT",
        # v15: 경쟁사 언급 감지 (JSON 배열: ["toss","kakaopay"] 형태)
        "ALTER TABLE ai_analysis ADD COLUMN competitors TEXT DEFAULT ''",
    ]:
        try:
            conn.execute(alter_sql)
            conn.commit()
        except Exception:
            pass

    # v13: 월별 실적
    conn.execute("""
        CREATE TABLE IF NOT EXISTS monthly_performance (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            year       INTEGER NOT NULL,
            month      INTEGER NOT NULL,
            members    REAL,
            revenue    REAL,
            profit     REAL,
            memo       TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT,
            UNIQUE(year, month)
        )
    """)
    conn.commit()

    # v12: 채널 성과 테이블
    conn.execute("""
        CREATE TABLE IF NOT EXISTS channel_performance (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            platform         TEXT NOT NULL,
            metric_date      TEXT NOT NULL,
            subscribers      INTEGER,
            total_views      INTEGER,
            video_count      INTEGER,
            avg_eng_rate     REAL,
            sessions         INTEGER,
            users            INTEGER,
            conv_rate        REAL,
            bounce_rate      REAL,
            avg_duration     REAL,
            followers        INTEGER,
            media_count      INTEGER,
            reach            INTEGER,
            impressions      INTEGER,
            engagement_rate  REAL,
            total_posts      INTEGER,
            total_views_blog INTEGER,
            avg_comments     REAL,
            raw_json         TEXT,
            synced_at        TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(platform, metric_date)
        )
    """)
    conn.commit()

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
        ("daily_report_to_list", os.getenv("REPORT_TO", "brad@glninternational.com,steinlee21@glninternational.com,heybk@glninternational.com,blu.kim@glninternational.com,gyl@glninternational.com,jay79@glninternational.com,mantto@glninternational.com,shpark@glninternational.com,seankim@glninternational.com,sherman.kim@glninternational.com,yb.cho@glninternational.com,parkkh@glninternational.com,bigmt@glninternational.com,android_yn@glninternational.com,chy@glninternational.com,aimeetran@glninternational.com,aitch.lee@glninternational.com,ysb@glninternational.com,arthur.park@glninternational.com,hankyullee@glninternational.com,soojungoh@glninternational.com,elena.song@glninternational.com,ella@glninternational.com,ellie.jung@glninternational.com,grace@glninternational.com,jysong@glninternational.com,parkhw@glninternational.com,tg.jeong@glninternational.com,jen.jung@glninternational.com,joy.kim@glninternational.com,jude.han@glninternational.com,katie.l@glninternational.com,lana.kim@glninternational.com,leanna@glninternational.com,pji@glninternational.com,liam.kim@glninternational.com,baji1106@glninternational.com,logan.yoo@glninternational.com,luke@glninternational.com,today.as.fresh@glninternational.com,kj.lee@glninternational.com,sungyoon.jung@glninternational.com,may.maeng@glninternational.com,mila23@glninternational.com,monk.oh@glninternational.com,neo.kang@glninternational.com,chaechae0704@glninternational.com,paul@glninternational.com,peter.kim@glninternational.com,quentin@glninternational.com,gyeong@glninternational.com,hykim@glninternational.com,robin.park@glninternational.com,yumsksk@glninternational.com,scarlett@glninternational.com,sophia.lee@glninternational.com,tom.in@glninternational.com,hchoi@glninternational.com,van.dhkim@glninternational.com,yunie.heo@glninternational.com,chloe.jang@glninternational.com,elio.shin@glninternational.com,hailey.choi@glninternational.com,jaena.kim@glninternational.com,jane.oh@glninternational.com,jeff.son@glninternational.com,west.seo@glninternational.com")),
        ("report_to_list",       os.getenv("REPORT_TO", "brad@glninternational.com,steinlee21@glninternational.com,heybk@glninternational.com,blu.kim@glninternational.com,gyl@glninternational.com,jay79@glninternational.com,mantto@glninternational.com,shpark@glninternational.com,seankim@glninternational.com,sherman.kim@glninternational.com,yb.cho@glninternational.com,parkkh@glninternational.com,bigmt@glninternational.com,android_yn@glninternational.com,chy@glninternational.com,aimeetran@glninternational.com,aitch.lee@glninternational.com,ysb@glninternational.com,arthur.park@glninternational.com,hankyullee@glninternational.com,soojungoh@glninternational.com,elena.song@glninternational.com,ella@glninternational.com,ellie.jung@glninternational.com,grace@glninternational.com,jysong@glninternational.com,parkhw@glninternational.com,tg.jeong@glninternational.com,jen.jung@glninternational.com,joy.kim@glninternational.com,jude.han@glninternational.com,katie.l@glninternational.com,lana.kim@glninternational.com,leanna@glninternational.com,pji@glninternational.com,liam.kim@glninternational.com,baji1106@glninternational.com,logan.yoo@glninternational.com,luke@glninternational.com,today.as.fresh@glninternational.com,kj.lee@glninternational.com,sungyoon.jung@glninternational.com,may.maeng@glninternational.com,mila23@glninternational.com,monk.oh@glninternational.com,neo.kang@glninternational.com,chaechae0704@glninternational.com,paul@glninternational.com,peter.kim@glninternational.com,quentin@glninternational.com,gyeong@glninternational.com,hykim@glninternational.com,robin.park@glninternational.com,yumsksk@glninternational.com,scarlett@glninternational.com,sophia.lee@glninternational.com,tom.in@glninternational.com,hchoi@glninternational.com,van.dhkim@glninternational.com,yunie.heo@glninternational.com,chloe.jang@glninternational.com,elio.shin@glninternational.com,hailey.choi@glninternational.com,jaena.kim@glninternational.com,jane.oh@glninternational.com,jeff.son@glninternational.com,west.seo@glninternational.com")),
        ("report_to_weekday",    os.getenv("REPORT_TO", "brad@glninternational.com,steinlee21@glninternational.com,heybk@glninternational.com,blu.kim@glninternational.com,gyl@glninternational.com,jay79@glninternational.com,mantto@glninternational.com,shpark@glninternational.com,seankim@glninternational.com,sherman.kim@glninternational.com,yb.cho@glninternational.com,parkkh@glninternational.com,bigmt@glninternational.com,android_yn@glninternational.com,chy@glninternational.com,aimeetran@glninternational.com,aitch.lee@glninternational.com,ysb@glninternational.com,arthur.park@glninternational.com,hankyullee@glninternational.com,soojungoh@glninternational.com,elena.song@glninternational.com,ella@glninternational.com,ellie.jung@glninternational.com,grace@glninternational.com,jysong@glninternational.com,parkhw@glninternational.com,tg.jeong@glninternational.com,jen.jung@glninternational.com,joy.kim@glninternational.com,jude.han@glninternational.com,katie.l@glninternational.com,lana.kim@glninternational.com,leanna@glninternational.com,pji@glninternational.com,liam.kim@glninternational.com,baji1106@glninternational.com,logan.yoo@glninternational.com,luke@glninternational.com,today.as.fresh@glninternational.com,kj.lee@glninternational.com,sungyoon.jung@glninternational.com,may.maeng@glninternational.com,mila23@glninternational.com,monk.oh@glninternational.com,neo.kang@glninternational.com,chaechae0704@glninternational.com,paul@glninternational.com,peter.kim@glninternational.com,quentin@glninternational.com,gyeong@glninternational.com,hykim@glninternational.com,robin.park@glninternational.com,yumsksk@glninternational.com,scarlett@glninternational.com,sophia.lee@glninternational.com,tom.in@glninternational.com,hchoi@glninternational.com,van.dhkim@glninternational.com,yunie.heo@glninternational.com,chloe.jang@glninternational.com,elio.shin@glninternational.com,hailey.choi@glninternational.com,jaena.kim@glninternational.com,jane.oh@glninternational.com,jeff.son@glninternational.com,west.seo@glninternational.com")),
        ("report_to_weekend",    ""),
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
