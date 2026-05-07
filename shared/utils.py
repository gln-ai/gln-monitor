"""
shared/utils.py — 공통 유틸리티
모든 앱에서 import해서 사용하는 공통 함수 모음
"""
import os
import json

APPS_ROOT  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHARED_DIR = os.path.dirname(os.path.abspath(__file__))


def load_shared(filename: str):
    """shared/ 디렉터리의 JSON 파일을 로드합니다."""
    with open(os.path.join(SHARED_DIR, filename), encoding="utf-8") as f:
        return json.load(f)


def get_claude_client():
    """Anthropic 클라이언트를 생성합니다."""
    import httpx
    import anthropic
    return anthropic.Anthropic(
        api_key=os.getenv("ANTHROPIC_API_KEY"),
        http_client=httpx.Client()
    )
