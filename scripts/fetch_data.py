#!/usr/bin/env python3
"""
서울 열린데이터광장 '일반음식점 인허가 정보' Open API를 받아
지도 페이지용 data/restaurants.json 으로 변환한다.

환경변수
  SEOUL_API_KEY   (필수) 서울 열린데이터광장 일반 인증키
  SEOUL_SERVICES  (선택) 쉼표로 구분한 서비스명. 기본: LOCALDATA_072404_GN (강남구)
                  예) LOCALDATA_072404_GN,LOCALDATA_072404_SC  (강남+서초)
  KEEP_CLOSED_DAYS (선택) 최근 N일 안에 폐업한 곳도 남김(폐업 확인용). 기본 365

실행
  pip install pyproj
  SEOUL_API_KEY=xxxx python scripts/fetch_data.py
"""
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from pyproj import Transformer

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "restaurants.json"
PAGE = 1000  # 서울 Open API 1회 최대 요청 건수

# LOCALDATA 좌표는 중부원점(Bessel) EPSG:5174. 국내 실무에서 쓰는 towgs84 보정값 포함.
EPSG5174 = ("+proj=tmerc +lat_0=38 +lon_0=127.0028902777778 +k=1 +x_0=200000 +y_0=500000 "
            "+ellps=bessel +units=m +no_defs "
            "+towgs84=-115.80,474.99,674.11,1.16,-2.31,-1.63,6.43")
TO_WGS84 = Transformer.from_crs(EPSG5174, "EPSG:4326", always_xy=True)

KST = dt.timezone(dt.timedelta(hours=9))

# 출력 컬럼 (페이지와 약속된 순서)
FIELDS = ["id", "name", "type", "status", "detail", "road", "jibun", "tel",
          "permit", "closed", "hygiene", "area", "lat", "lng", "modified"]

# 서울 API(영문 대문자)와 CSV(한글) 컬럼명을 모두 받아들인다.
ALIASES = {
    "id": ["MGTNO", "관리번호"],
    "name": ["BPLCNM", "사업장명"],
    "type": ["UPTAENM", "업태구분명"],
    "status": ["TRDSTATENM", "영업상태명"],
    "status_code": ["TRDSTATEGBN", "영업상태구분코드"],
    "detail": ["DTLSTATENM", "상세영업상태명"],
    "road": ["RDNWHLADDR", "도로명전체주소", "도로명주소"],
    "jibun": ["SITEWHLADDR", "소재지전체주소", "지번주소"],
    "tel": ["SITETEL", "소재지전화", "전화번호"],
    "permit": ["APVPERMYMD", "인허가일자"],
    "closed": ["DCBYMD", "폐업일자"],
    "hygiene": ["SNTUPTAENM", "위생업태명"],
    "area": ["FACILTOTSCP", "시설총규모"],
    "x": ["X", "좌표정보(X)", "좌표정보X(EPSG5174)", "좌표정보(x)"],
    "y": ["Y", "좌표정보(Y)", "좌표정보Y(EPSG5174)", "좌표정보(y)"],
    "modified": ["LASTMODTS", "최종수정시점", "데이터갱신일자", "UPDATEDT"],
}


def pick(row, key):
    for k in ALIASES[key]:
        v = row.get(k)
        if v is None:
            # 대소문자 차이 흡수
            for rk in row:
                if rk.upper() == k.upper():
                    v = row[rk]
                    break
        if v not in (None, ""):
            return str(v).strip()
    return ""


def ymd(s):
    s = (s or "").replace("-", "").replace(".", "").strip()[:8]
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 and s.isdigit() else ""


class ApiError(RuntimeError):
    pass


def call(key, service, start, end, retries=4):
    url = f"http://openapi.seoul.go.kr:8088/{key}/json/{service}/{start}/{end}/"
    last = None
    for i in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                text = r.read().decode("utf-8")
            # 인증키 오류 등은 /json/ 요청이어도 XML <RESULT>로 온다 → 재시도 없이 바로 중단
            if text.lstrip().startswith("<"):
                code = re.search(r"<CODE>(.*?)</CODE>", text, re.S)
                msg = re.search(r"<MESSAGE>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</MESSAGE>", text, re.S)
                raise ApiError(f"{service}: {code and code.group(1)} "
                               f"{msg and ' '.join(msg.group(1).split())}")
            return json.loads(text)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last = e
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"{service} {start}-{end} 요청 실패: {last}")


def fetch_service(key, service):
    first = call(key, service, 1, 1)
    body = first.get(service)
    if not body:
        res = first.get("RESULT", {})
        raise RuntimeError(f"{service}: {res.get('CODE')} {res.get('MESSAGE')}")
    total = int(body.get("list_total_count", 0))
    print(f"[{service}] 전체 {total:,}건", flush=True)
    rows = []
    for s in range(1, total + 1, PAGE):
        e = min(s + PAGE - 1, total)
        data = call(key, service, s, e).get(service, {})
        rows.extend(data.get("row", []))
        print(f"  {e:,}/{total:,}", flush=True)
        time.sleep(0.2)
    if len(rows) < total * 0.98:
        raise RuntimeError(f"{service}: {total}건 중 {len(rows)}건만 수신 — 중단")
    return rows


def convert(raw_rows, keep_closed_days):
    cutoff = (dt.datetime.now(KST) - dt.timedelta(days=keep_closed_days)).strftime("%Y-%m-%d")
    out, seen, no_xy = [], set(), 0
    for r in raw_rows:
        rid = pick(r, "id")
        if not rid or rid in seen:
            continue
        status = pick(r, "status")
        closed = ymd(pick(r, "closed"))
        is_closed = "폐업" in status or pick(r, "status_code") == "03"
        if is_closed and (not closed or closed < cutoff):
            continue
        if any(w in status for w in ("취소", "말소", "만료")):
            continue
        try:
            x, y = float(pick(r, "x")), float(pick(r, "y"))
            lng, lat = TO_WGS84.transform(x, y)
        except ValueError:
            no_xy += 1
            continue
        if not (37.3 < lat < 37.8 and 126.6 < lng < 127.4):
            no_xy += 1
            continue
        try:
            area = round(float(pick(r, "area") or 0), 1)
        except ValueError:
            area = 0
        seen.add(rid)
        out.append([
            rid, pick(r, "name"), pick(r, "type") or "기타", status, pick(r, "detail"),
            pick(r, "road"), pick(r, "jibun"), pick(r, "tel"),
            ymd(pick(r, "permit")), closed, pick(r, "hygiene"), area,
            round(lat, 6), round(lng, 6), pick(r, "modified")[:10],
        ])
    return out, no_xy


def main():
    key = os.environ.get("SEOUL_API_KEY", "").strip()
    if not key:
        sys.exit("SEOUL_API_KEY 환경변수가 없습니다.")
    services = [s.strip() for s in os.environ.get("SEOUL_SERVICES", "LOCALDATA_072404_GN").split(",") if s.strip()]
    keep_days = int(os.environ.get("KEEP_CLOSED_DAYS", "365"))

    raw = []
    for svc in services:
        raw.extend(fetch_service(key, svc))
    rows, no_xy = convert(raw, keep_days)
    print(f"변환 완료: {len(rows):,}곳 (좌표 없음/범위 밖 {no_xy:,}건 제외)")

    # 안전장치: API 장애로 결과가 급감하면 기존 파일을 덮어쓰지 않는다.
    if OUT.exists():
        try:
            prev = len(json.loads(OUT.read_text("utf-8")).get("rows", []))
            if prev and len(rows) < prev * 0.7:
                sys.exit(f"이전 {prev:,}곳 → 이번 {len(rows):,}곳으로 30% 이상 줄어 갱신을 건너뜁니다.")
        except json.JSONDecodeError:
            pass

    payload = {
        "updated": dt.datetime.now(KST).isoformat(timespec="minutes"),
        "services": services,
        "source": "서울 열린데이터광장 일반음식점 인허가 정보 (공공누리 제1유형)",
        "fields": FIELDS,
        "rows": sorted(rows, key=lambda r: r[0]),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), "utf-8")
    print(f"저장: {OUT} ({OUT.stat().st_size/1e6:.1f} MB)")


if __name__ == "__main__":
    main()
