"""
services/tourism_stats.py — GLN 서비스 국가별 외래관광객 연간 통계 (KOSIS 국제통계연감)

API: kosis.kr OpenAPI > DT_2KAAA14 "외래관광객및해외관광객" itmId=T1(외래관광객)
  Base: https://kosis.kr/openapi/Param/statisticsParameterData.do
  Params: method=getList, orgId=101, tblId=DT_2KAAA14, itmId=T1,
          objL1=ALL, prdSe=A, startPrdDe=YYYY, endPrdDe=YYYY, format=json, jsonVD=Y
  Response fields: C1_NM (국가명), DT (값, 1000명 단위), PRD_DE (연도)
  출처: UNWTO → KOSIS (연간, 데이터 기준 전년도까지)

커버리지: 14개국 중 10개 (몽골·라오스·괌·사이판 UNWTO 미집계)

ENV: KOSIS_API_KEY — kosis.kr 발급 인증키
"""
import os
import urllib.request
import urllib.parse
import json
from datetime import date

from db import get_db

_API_BASE = "https://kosis.kr/openapi/Param/statisticsParameterData.do"

# 한국관광공사 API 국가명 → GLN 내부 코드
NATION_MAP = {
    "일본":    "japan",
    "태국":    "thailand",
    "베트남":  "vietnam",
    "대만":    "taiwan",
    "필리핀":  "philippines",
    "싱가포르":"singapore",
    "홍콩":    "hongkong",
    "마카오":  "macau",
    "중국":    "china",
    "캄보디아":"cambodia",
    "몽골":    "mongolia",
    "라오스":  "laos",
    "괌":      "guam",
    "사이판":  "saipan",
}

COUNTRY_LABEL = {
    "japan":"일본", "thailand":"태국", "vietnam":"베트남", "taiwan":"대만",
    "philippines":"필리핀", "singapore":"싱가포르", "hongkong":"홍콩",
    "macau":"마카오", "china":"중국", "cambodia":"캄보디아",
    "mongolia":"몽골", "laos":"라오스", "guam":"괌", "saipan":"사이판",
}


def fetch_and_cache(year: int) -> int:
    """KOSIS API 1회 호출 → tourism_stats upsert. 저장 건수 반환."""
    api_key = os.getenv("KOSIS_API_KEY", "").strip()
    if not api_key:
        print("[관광통계] KOSIS_API_KEY 미설정 — 스킵", flush=True)
        return 0

    params = urllib.parse.urlencode({
        "method":     "getList",
        "apiKey":     api_key,
        "orgId":      "101",
        "tblId":      "DT_2KAAA14",
        "itmId":      "T1",
        "objL1":      "ALL",
        "prdSe":      "A",
        "startPrdDe": str(year),
        "endPrdDe":   str(year),
        "format":     "json",
        "jsonVD":     "Y",
    })
    url = f"{_API_BASE}?{params}"

    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
        data = json.loads(raw)
    except Exception as e:
        print(f"[관광통계] API 오류 {year}: {e}", flush=True)
        return 0

    if isinstance(data, dict) and data.get("err"):
        print(f"[관광통계] API 오류 {year}: {data.get('errMsg')}", flush=True)
        return 0

    if not isinstance(data, list):
        print(f"[관광통계] 예상치 못한 응답 형식 {year}", flush=True)
        return 0

    conn = get_db()
    saved = 0
    for item in data:
        c1_nm   = (item.get("C1_NM") or "").strip()
        dt_val  = item.get("DT") or "0"
        country = NATION_MAP.get(c1_nm)
        if not country:
            continue
        try:
            visitors = int(str(dt_val).replace(",", "")) * 1000  # 1000명 단위 → 실제 인원
        except (ValueError, TypeError):
            visitors = 0
        conn.execute(
            """INSERT INTO tourism_stats (year_month, country, visitors, fetched_at)
               VALUES (?, ?, ?, datetime('now','localtime'))
               ON CONFLICT(year_month, country) DO UPDATE
               SET visitors=excluded.visitors, fetched_at=excluded.fetched_at""",
            (str(year), country, visitors)
        )
        saved += 1
    conn.commit()
    conn.close()
    print(f"[관광통계] {year} — {saved}개국 저장", flush=True)
    return saved


def fetch_recent_months(n: int = 8) -> dict:
    """DB에서 최근 n개년 데이터 반환.
    반환: { months (연도 리스트), countries: {code: [visitors...]}, last_updated }
    """
    conn = get_db()
    rows = conn.execute(
        "SELECT year_month, country, visitors, fetched_at FROM tourism_stats ORDER BY year_month"
    ).fetchall()
    conn.close()

    if not rows:
        return {"months": [], "countries": {}, "last_updated": ""}

    all_years = sorted({r["year_month"] for r in rows})
    months = all_years[-n:]

    countries: dict[str, list] = {}
    year_idx = {y: i for i, y in enumerate(months)}
    for r in rows:
        ym = r["year_month"]
        if ym not in year_idx:
            continue
        code = r["country"]
        if code not in countries:
            countries[code] = [0] * len(months)
        countries[code][year_idx[ym]] = r["visitors"]

    last_updated = max((r["fetched_at"] or "") for r in rows)
    return {"months": months, "countries": countries, "last_updated": last_updated[:10]}


def update_all(years_back: int = 7):
    """최근 years_back개년 일괄 갱신 — 스케줄러 진입점 & 초기 로드."""
    current_year = date.today().year
    for year in range(current_year - years_back, current_year + 1):
        fetch_and_cache(year)
