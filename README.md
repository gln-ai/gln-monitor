# GLN 네이버 카페 모니터링

브라우저에서 `http://localhost:5000` 으로 접속하면 됩니다.

---

## 설치 및 실행 (처음 한 번만)

### 1단계 — Python 설치 확인

터미널(맥: Terminal, 윈도우: PowerShell)을 열고 입력:

```
python --version
```

3.11 이상이면 OK. 없으면 https://www.python.org/downloads/ 에서 설치.

---

### 2단계 — 프로젝트 폴더로 이동

```
cd gln-monitor
```

---

### 3단계 — 패키지 설치 (최초 1회)

```
pip install -r requirements.txt
```

---

### 4단계 — API 키 설정

`.env.example` 파일을 복사해서 `.env` 파일을 만드세요:

```
cp .env.example .env
```

그 다음 `.env` 파일을 메모장(또는 VSCode)으로 열어 키를 입력합니다.

**네이버 API 키 발급:**
1. https://developers.naver.com 접속
2. "애플리케이션 등록" 클릭
3. 사용 API → "검색" 선택
4. Client ID와 Client Secret 복사

**Anthropic API 키 발급:**
1. https://console.anthropic.com 접속
2. API Keys → "Create Key"
3. 키 복사 (sk-ant-... 로 시작)

**Gmail 앱 비밀번호 발급 (이메일 기능 사용 시):**
1. 구글 계정 → 보안 → 2단계 인증 활성화
2. 2단계 인증 페이지 하단 → "앱 비밀번호" 생성
3. 앱: 메일, 기기: Windows/Mac 선택 → 생성된 16자리 비밀번호 사용
   (일반 Gmail 비밀번호가 아닌 앱 비밀번호를 SMTP_PASS에 입력!)

---

### 5단계 — 실행

```
python app.py
```

브라우저에서 http://localhost:5000 접속!

---

## 매일 사용하기

터미널에서:

```
cd gln-monitor
python app.py
```

- 실행하면 즉시 1회 수집 + AI 분석 시작
- 이후 1시간마다 자동 수집
- 매일 오전 8시 이메일 리포트 자동 발송
- 중요도 7 이상 또는 부정 감성 감지 시 즉시 알림 이메일 발송

---

## 파일 구조

```
gln-monitor/
├── app.py              ← 전체 앱 (이 파일 하나로 동작)
├── requirements.txt    ← 필요 패키지 목록
├── .env                ← API 키 (직접 작성, git에 올리지 마세요!)
├── .env.example        ← .env 작성 가이드
├── gln_monitor.db      ← 수집 데이터 자동 생성됨
└── templates/
    ├── dashboard.html  ← 대시보드 화면
    └── post_detail.html← 게시글 상세 화면
```

---

## 문제 해결

**`ModuleNotFoundError` 오류:**
```
pip install -r requirements.txt
```

**수집이 안 될 때:**
- `.env` 파일의 NAVER_CLIENT_ID, NAVER_CLIENT_SECRET 확인
- 네이버 개발자센터에서 "검색" API가 등록됐는지 확인

**AI 분석이 안 될 때:**
- ANTHROPIC_API_KEY 확인
- Anthropic 계정에 크레딧이 있는지 확인

**이메일이 안 올 때:**
- SMTP_PASS는 Gmail 일반 비밀번호가 아닌 "앱 비밀번호" 사용
- Gmail 2단계 인증이 켜져 있어야 앱 비밀번호 생성 가능
