"""
services/kto_fetcher.py — KTO DataLab에서 한국인 월별 총 해외출국 수집

QID NAT_10_01_004: 한국인 월별 해외 출국자 수 (2022-01 ~ 현재)
  - country 코드: 'kor_outbound' (총 출국, 목적지 미분류)
  - 차트에서 전체 트렌드 기준선으로 활용

KTO DataLab 국가별(베트남·태국 등) 월별 데이터는 공개 API 미제공.
  → 베트남·태국은 DataLab 수동 다운로드 후 /insights 업로드(KTO 모드) 사용.
"""
import json
import urllib.request
import urllib.parse

from db import get_db

_DATALAB_URL = "https://datalab.visitkorea.or.kr/visualize/getTempleteData.do"
_COUNTRY_CODE = "kor_outbound"


def fetch_kto_total() -> int:
    """KTO DataLab → 한국인 월별 총 출국자 수 → tourism_monthly 저장."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Referer":    "https://datalab.visitkorea.or.kr/datalab/portal/nat/getOseaTourForm.do",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    body = urllib.parse.urlencode({
        "qid":      "NAT_10_01_004",
        "BASE_YM1": "201901",
        "BASE_YM2": "202512",
    }).encode("utf-8")

    req = urllib.request.Request(_DATALAB_URL, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
        data = json.loads(raw)
    except Exception as e:
        print(f"[KTO fetcher] 요청 실패: {e}", flush=True)
        return 0

    rows = data.get("list", [])
    if not rows:
        print("[KTO fetcher] 데이터 없음", flush=True)
        return 0

    conn = get_db()
    saved = 0
    for row in rows:
        ym_raw = str(row.get("BASE_YM", "")).strip()
        if len(ym_raw) == 6:
            ym = f"{ym_raw[:4]}-{ym_raw[4:]}"
        else:
            continue
        visitors = row.get("TOU_NUM") or 0
        try:
            visitors = int(visitors)
        except (TypeError, ValueError):
            continue
        if visitors <= 0:
            continue
        conn.execute(
            """INSERT INTO tourism_monthly (year_month, country, visitors, source, fetched_at)
               VALUES (?, ?, ?, 'kto', datetime('now','localtime'))
               ON CONFLICT(year_month, country) DO UPDATE
               SET visitors=excluded.visitors, source=excluded.source,
                   fetched_at=excluded.fetched_at""",
            (ym, _COUNTRY_CODE, visitors)
        )
        saved += 1
    conn.commit()
    conn.close()
    print(f"[KTO fetcher] {saved}개월 저장 (country='{_COUNTRY_CODE}')", flush=True)
    return saved
