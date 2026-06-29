# gln-guard — 콘텐츠 검수 모듈

> 전체 시스템 구조는 ~/apps/CLAUDE.md 참고

## 개요
생성된 콘텐츠의 품질·규정 준수를 자동 검수.
gln-monitor의 pipeline.py에서 동적 로딩(importlib)으로 호출.

## 상태
운영 중

## 검수 결과 구조
```python
{
  "grade": "green" | "yellow" | "red",  # 종합 등급
  "issues": ["이슈1", "이슈2", ...]     # 문제 항목 목록
}
```
- green: 바로 발행 가능
- yellow: 확인 후 발행
- red: 수정 필요

## 파일
- `checker.py` — 메인 검수 로직 + `send_publish_package_email()` (발행 패키지 이메일 발송)

## 참조 데이터
- ~/apps/shared/forbidden_words.json — hard_block(절대금지) / soft_warn(주의)
- ~/apps/shared/fact_db.json — 사실 정보 교차 검증
