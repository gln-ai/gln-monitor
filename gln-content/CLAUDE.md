# gln-content — 콘텐츠 생성기

> 전체 시스템 구조는 ~/apps/CLAUDE.md 참고

## 개요
채널(메인/서브) + 포맷을 입력받아 Claude AI로 콘텐츠를 생성하는 모듈.
gln-monitor에서 동적 로딩(importlib)으로 호출되며, 독립 실행도 가능.

## 상태
운영 중 — 이원화 채널 구조 (메인 4포맷 + 서브 3포맷)

## 채널 및 포맷
| 채널 | UI명 | 내부 코드 | 지원 포맷 |
|------|------|-----------|----------|
| official | 메인채널 | official | blog / instagram_card / youtube_shorts / threads |
| gorani | 서브채널 | gorani | reels / threads / cartoon |

## 파일 구조
- `content_generator.py` — 라우터 진입점. 채널/포맷에 따라 서브 생성기 위임. COUNTRY_MAP 정의.
- `official_generator.py` — 메인채널 전용 생성기
- `gorani_generator.py` — 서브채널 전용 생성기

## 프롬프트 템플릿 (~/apps/shared/prompt_templates/)
| 파일 | 용도 |
|------|------|
| official_blog.txt | 메인 블로그 |
| official_instagram.txt | 메인 인스타 카드뉴스 |
| official_shorts.txt | 메인 유튜브 쇼츠 |
| official_threads.txt | 메인 스레드 |
| gorani_reels.txt | 서브 릴스 |
| gorani_threads.txt | 서브 스레드 |
| gorani_cartoon.txt | 서브 툰 |

## COUNTRY_MAP 지원 국가 (14개)
```python
태국/방콕 → thailand
일본/도쿄/오사카 → japan
대만/타이베이 → taiwan
베트남/호치민/하노이 → vietnam
필리핀/마닐라 → philippines
싱가포르 → singapore
홍콩 → hongkong
마카오 → macau
중국/베이징/상하이 → china
캄보디아/프놈펜 → cambodia
몽골/울란바토르 → mongolia
라오스 → laos
괌 → guam
사이판 → saipan
```

## 소재 수급 (content_generator.py)
- **Reactive**: `get_briefs()` — gln-monitor DB에서 importance_score 기준 추출
- **Proactive**: `get_proactive_topics()` — 국가 로테이션 기반 기획 주제 자동 생성
- 두 방식 모두 독립 사용 가능, 혼합도 가능

## 공통 환경
- gln-monitor/.env 공유 사용 (ANTHROPIC_API_KEY)
- ~/apps/shared/forbidden_words.json — 금칙어 (hard_block / soft_warn)
- ~/apps/shared/fact_db.json — GLN 서비스·국가 정보 (수동 관리 필수)
