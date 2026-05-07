# gln-monitor — 메인 웹앱

> 전체 시스템 구조는 ~/apps/CLAUDE.md 참고

## 개요
네이버 카페/블로그/뉴스 수집 · AI 분석 · 콘텐츠 제작 · 인사이트를 통합한 Flask 웹앱.
각 기능은 독립적으로 동작하며, 모니터링 없이 콘텐츠 직접 제작도 가능.

## 실행 환경
- **경로**: ~/apps/gln-monitor
- **실행**: pm2 (gln-monitor), 포트 5001
- **접속**: http://192.168.1.60:5001 (내부망)
- **Python**: 3.14 / .venv

## 기술 스택
Flask · SQLite · Anthropic API · 네이버 Search API · APScheduler · KnuSentiLex

## 환경변수 (.env)
```
NAVER_CLIENT_ID, NAVER_CLIENT_SECRET
ANTHROPIC_API_KEY
SMTP_USER=glninternational.ai@gmail.com
SMTP_PASS (앱비밀번호 16자리, 띄어쓰기 없이)
REPORT_TO=brad@,heybk@,chaechae0704@,jane.oh@
URGENT_ALERT_TO=brad@glninternational.com
BASE_URL=http://192.168.1.60:5001
```

---

## UI 페이지 구조
| 경로 | 템플릿 | 설명 |
|------|--------|------|
| `/` | dashboard.html | 모니터링 피드 (수집 게시글 목록) |
| `/insights` | insights.html | 인사이트 (차트·SLA·이슈) |
| `/content` | content_status.html | 콘텐츠 목록 |
| `/content/create` | content_create.html | 콘텐츠 제작 설정 |
| `/pr` | pr_drafts.html | 보도자료 |
| `/reports` | reports.html | 리포트 아카이브 |
| `/settings` | settings.html | 키워드 관리 |
| `/post/<id>` | post_detail.html | 게시글 상세 |

---

## DB 스키마 요약 (gln_monitor.db)

### posts
수집된 게시글. `keyword` 컬럼 형식: `카페/GLN`, `블로그/퍼플GLN` 등 (채널/키워드).
country 컬럼 없음 — 렌더링 시 title+description 텍스트 매칭으로 감지.

### ai_analysis
posts와 1:1. summary / category / sentiment / importance_score (1~10).

### content_drafts
생성된 콘텐츠 초안. 주요 컬럼:
- `channel`: official(메인) | gorani(서브)
- `format`: blog / instagram_card / youtube_shorts / threads / reels / cartoon
- `platform`: naver_blog / instagram_official / youtube / threads 등
- `country`: GLN 지원 국가 코드 (thailand, japan 등)
- `source_type`: auto(모니터링 기반) | manual(직접 입력)
- `guard_grade`: green / yellow / red / pending
- `approval_status`: unpublished | published
- 마이그레이션: v1~v5 완료 (db.py ALTER TABLE 섹션)

### keywords
수집 기준 키워드. 기본값: GLN / 퍼플GLN / GLN ATM / GLN 해외결제 / GLN 출금

---

## 설계 결정사항

### 포맷 레이블 통일 (방향 A)
UI 표시는 플랫폼 기준으로 통일. `routes/content.py`의 FORMAT_MAP:
```python
FORMAT_MAP = {
    "blog":      ["blog"],
    "instagram": ["instagram_card", "reels", "cartoon"],  # IN 절 처리
    "youtube":   ["youtube_shorts"],
    "threads":   ["threads"],
}
```

### 채널 명칭
- official → **메인채널** (UI 표시)
- gorani → **서브채널** (UI 표시)
- "공식 채널", "고라니 채널" 표현은 코드 내부에서도 점진적으로 통일 중

### 콘텐츠 생성 2가지 경로
1. **동기 (UI용)**: `generate_single()` → `/api/content/generate` POST → 완료 후 목록 리다이렉트
2. **비동기 (스케줄러용)**: `run_content_pipeline()` → `/api/content/run` POST → 백그라운드 스레드

### 국가 감지
- 모니터링 피드: 렌더링 시 매번 텍스트 매칭 (`routes/monitor.py`의 `_detect_country()`)
- 콘텐츠: 생성 시 `content_drafts.country`에 저장, 목록에서 직접 표시
- COUNTRY_LABEL 딕셔너리로 코드→한국어 변환

---

## 알려진 이슈
- Railway SMTP 차단 → 맥미니 로컬 실행으로 해결
- 카페 작성일 미제공 → 수집일 표시
- pydantic v1 경고 → 기능 무관

## 배포 및 PM2
```
cd ~/apps/gln-monitor && git add . && git commit -m "msg" && git push
pm2 restart gln-monitor
pm2 logs gln-monitor --lines 50
```
