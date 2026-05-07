FROM python:3.11-slim

WORKDIR /app

# 의존성 먼저 (캐시 활용)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 앱 전체 복사 (shared/ 포함)
COPY . .

# Railway 볼륨 마운트 포인트
RUN mkdir -p /data

EXPOSE 8080

# workers=1: APScheduler 중복 실행 방지
CMD ["gunicorn", "app:app", "--workers", "1", "--bind", "0.0.0.0:8080", "--timeout", "120", "--preload"]
