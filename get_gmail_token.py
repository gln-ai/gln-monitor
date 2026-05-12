"""
Gmail OAuth2 리프레시 토큰 발급 스크립트 (1회만 실행)

사용법:
  python get_gmail_token.py --client-id YOUR_CLIENT_ID --client-secret YOUR_CLIENT_SECRET
"""
import argparse
from google_auth_oauthlib.flow import InstalledAppFlow

parser = argparse.ArgumentParser()
parser.add_argument("--client-id",     required=True)
parser.add_argument("--client-secret", required=True)
args = parser.parse_args()

flow = InstalledAppFlow.from_client_config(
    {"installed": {
        "client_id":     args.client_id,
        "client_secret": args.client_secret,
        "redirect_uris": ["http://localhost"],
        "auth_uri":      "https://accounts.google.com/o/oauth2/auth",
        "token_uri":     "https://oauth2.googleapis.com/token",
    }},
    scopes=["https://www.googleapis.com/auth/gmail.send"],
)
creds = flow.run_local_server(port=0)

print("\n=== Railway Variables에 아래 값을 입력하세요 ===")
print(f"GMAIL_CLIENT_ID     = {args.client_id}")
print(f"GMAIL_CLIENT_SECRET = {args.client_secret}")
print(f"GMAIL_REFRESH_TOKEN = {creds.refresh_token}")
