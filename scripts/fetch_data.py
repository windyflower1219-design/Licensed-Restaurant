#!/usr/bin/env python3
"""
서울 열린데이터광장 '일반음식점 인허가 정보'를 받아 지도 페이지용 data/restaurants.json 으로 변환한다.

받는 방법 (위에서부터 시도)
  1. CSV 내려받기: 데이터셋 페이지의 「내려받기(CSV)」 버튼과 같은 요청. 인증키 불필요, 한 번에 전체(약 5초).
  2. Open API: CSV가 실패하고 SEOUL_API_KEY가 있을 때만. 1,000건씩 여러 번 받아 합친다(약 10분).

환경변수
  SEOUL_CSV       (선택) 데이터셋ID:개방자치단체코드, 쉼표로 여러 개. 기본: OA-18674:3220000 (강남구)
  SEOUL_API_KEY   (선택) CSV 실패 시 쓰는 서울 열린데이터광장 일반 인증키
  SEOUL_SERVICES  (선택) API 서비스명. 기본: LOCALDATA_072404_GN (강남구)
  KEEP_CLOSED_DAYS (선택) 최근 N일 안에 폐업한 곳도 남김. 기본 0 (페이지는 영업 중만 보여줌)
  GEOCODE_MINUTES (선택) 좌표 없는 곳을 주소로 찾는 데 쓸 최대 시간(분). 0이면 안 찾음. 기본 40
                  결과는 data/geocode_cache.json 에 쌓여 다음부터는 새 주소만 조회한다.

실행
  pip install pyproj
  python scripts/fetch_data.py                      # CSV 자동 내려받기 (실패 시 API)
  python scripts/fetch_data.py --csv 받은파일.csv   # 직접 내려받은 CSV로 만들기
"""
import csv
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from pyproj import Transformer

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "restaurants.json"
GEO_CACHE = ROOT / "data" / "geocode_cache.json"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
GEO_UA = "restaurant-map (https://github.com/windyflower1219-design/Licensed-Restaurant)"
PAGE = 1000  # 서울 Open API 1회 최대 요청 건수
# 데이터셋 페이지 「내려받기(CSV)」 버튼이 보내는 요청 (공식 API는 아니라 바뀌면 API로 넘어간다)
CSV_URL = "https://datafile.seoul.go.kr/bigfile/iot/sheet/csv/download.do"

# LOCALDATA 좌표는 중부원점(Bessel) EPSG:5174. 국내 실무에서 쓰는 towgs84 보정값 포함.
EPSG5174 = ("+proj=tmerc +lat_0=38 +lon_0=127.0028902777778 +k=1 +x_0=200000 +y_0=500000 "
            "+ellps=bessel +units=m +no_defs "
            "+towgs84=-115.80,474.99,674.11,1.16,-2.31,-1.63,6.43")
TO_WGS84 = Transformer.from_crs(EPSG5174, "EPSG:4326", always_xy=True)

KST = dt.timezone(dt.timedelta(hours=9))

# 출력 컬럼 (페이지와 약속된 순서)
# geo: "" = 원본 좌표, "addr" = 주소로 찾은 좌표(건물 일치), "road" = 도로까지만 일치(대략 위치)
FIELDS = ["id", "name", "type", "status", "detail", "road", "jibun", "tel",
          "permit", "closed", "hygiene", "area", "lat", "lng", "modified", "geo"]

# 서울 API(영문 대문자)와 CSV(한글) 컬럼명을 모두 받아들인다.
ALIASES = {
    "id": ["MGTNO", "관리번호"],
    "name": ["BPLCNM", "사업장명"],
    "type": ["UPTAENM", "업태구분명"],
    "status": ["TRDSTATENM", "영업상태명"],
    "status_code": ["TRDSTATEGBN", "영업상태구분코드", "영업상태코드"],
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
    "modified": ["LASTMODTS", "최종수정시점", "최종수정일자", "데이터갱신일자", "UPDATEDT"],
    "source_dt": ["UPDATEDT", "데이터갱신일자"],
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


def note(msg):
    """Actions에서는 실행 요약 화면(Annotations)에도 보이게 남긴다."""
    print(f"::notice::{msg}" if os.environ.get("GITHUB_ACTIONS") else msg, flush=True)


def fetch_service(key, service, max_pass=8):
    first = call(key, service, 1, 1)
    body = first.get(service)
    if not body:
        res = first.get("RESULT", {})
        raise RuntimeError(f"{service}: {res.get('CODE')} {res.get('MESSAGE')}")
    total = int(body.get("list_total_count", 0))
    print(f"[{service}] 전체 {total:,}건", flush=True)
    # 페이지 순서가 요청마다 달라 페이지끼리 겹치고 일부가 빠진다(받은 줄 수는 total과 같아도
    # 고유 관리번호는 적음). 그래서 관리번호 기준으로 모으고, 빠진 게 있으면 다시 받아 합친다.
    by_id = {}
    for p in range(1, max_pass + 1):
        got, before = 0, len(by_id)
        for s in range(1, total + 1, PAGE):
            e = min(s + PAGE - 1, total)
            page = call(key, service, s, e).get(service, {}).get("row", [])
            got += len(page)
            for r in page:
                by_id.setdefault(pick(r, "id"), r)
            time.sleep(0.2)
        print(f"  {p}회차: {got:,}건 수신 → 고유 {len(by_id):,}/{total:,}", flush=True)
        if len(by_id) >= total or (p > 1 and len(by_id) == before):
            break  # 다 모았거나, 다시 받아도 새로 나오는 게 없음
    note(f"{service}: 전체 {total:,}건 중 고유 {len(by_id):,}건 확보 ({p}회 수신)")
    if len(by_id) < total * 0.98:
        raise RuntimeError(f"{service}: {total}건 중 고유 {len(by_id)}건만 확보 — 중단")
    return list(by_id.values())


def geo_queries(r):
    """좌표 없는 곳의 주소 검색어: 도로명(건물번호까지) → 지번(번지까지)"""
    road = re.sub(r"\s*\(.*$", "", re.sub(r",.*$", "", pick(r, "road"))).strip()
    m = re.match(r"(서울특별시 \S+구 \S+동 (?:산)?\d+(?:-\d+)?)", pick(r, "jibun"))
    return [q for q in (road, m and m.group(1)) if q]


def nominatim(q):
    """OpenStreetMap 주소 검색. 사용 규칙(초당 1회, 결과 캐시)을 지킨다.
    결과에 건물번호가 그대로 있으면 정확("addr"), 도로만 맞으면 대략("road"), 그 밖(동 단위 등)은 버림."""
    url = NOMINATIM + "?" + urllib.parse.urlencode({"q": q, "format": "json", "limit": 1, "countrycodes": "kr"})
    req = urllib.request.Request(url, headers={"User-Agent": GEO_UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        j = json.loads(r.read())
    time.sleep(1.1)
    if not j:
        return None
    g, num = j[0], q.split()[-1]
    parts = [x.strip() for x in g.get("display_name", "").split(",")]
    if num in parts:
        prec = "addr"
    elif g.get("addresstype") == "road" or g.get("class") == "highway":
        prec = "road"
    else:
        return None
    return [round(float(g["lat"]), 6), round(float(g["lon"]), 6), prec]


def geocode(rows_by_q, budget_min):
    """좌표 없는 곳들을 주소로 찾는다. data/geocode_cache.json 에 남겨 다음부터는 새 주소만 조회."""
    try:
        cache = json.loads(GEO_CACHE.read_text("utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        cache = {}
    todo = [q for q in rows_by_q if q not in cache]
    print(f"[주소→좌표] {len(rows_by_q):,}개 주소 중 새로 조회 {len(todo):,}개", flush=True)
    deadline = time.time() + budget_min * 60
    for n, q in enumerate(todo, 1):
        if time.time() > deadline:
            note(f"주소→좌표: 시간 제한으로 {len(todo) - n + 1:,}개는 다음 실행에서 조회")
            break
        try:
            cache[q] = nominatim(q)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as e:
            print(f"  실패 {q}: {e}", flush=True)
            time.sleep(5)
        if n % 100 == 0:
            print(f"  {n:,}/{len(todo):,}", flush=True)
            GEO_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=0), "utf-8")
    GEO_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=0), "utf-8")
    return cache


def convert(raw_rows, keep_closed_days, geo_budget_min=0):
    cutoff = (dt.datetime.now(KST) - dt.timedelta(days=keep_closed_days)).strftime("%Y-%m-%d")
    out, seen, no_xy, pending = [], set(), 0, []
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
        seen.add(rid)
        try:
            x, y = float(pick(r, "x")), float(pick(r, "y"))
            lng, lat = TO_WGS84.transform(x, y)
        except ValueError:
            pending.append(r)  # 좌표 없음 → 아래에서 주소로 찾기
            continue
        if not (37.3 < lat < 37.8 and 126.6 < lng < 127.4):
            no_xy += 1
            continue
        out.append(make_row(r, rid, status, closed, lat, lng, ""))

    # 좌표 없는 곳: 영업 중인 곳만 주소로 찾는다(폐업한 곳까지 찾을 필요는 없음)
    closed_n = len(pending)
    pending = [r for r in pending if "폐업" not in pick(r, "status")]
    no_xy += closed_n - len(pending)
    if pending and geo_budget_min:
        cache = geocode({q: 1 for r in pending for q in geo_queries(r)}, geo_budget_min)
        found = {"addr": 0, "road": 0}
        for r in pending:
            hit = next((cache[q] for q in geo_queries(r) if cache.get(q)), None)
            if not hit:
                no_xy += 1
                continue
            found[hit[2]] += 1
            out.append(make_row(r, pick(r, "id"), pick(r, "status"), ymd(pick(r, "closed")), hit[0], hit[1], hit[2]))
        note(f"좌표 없던 {len(pending):,}곳 중 주소로 찾음: 정확 {found['addr']:,} · 대략(도로) {found['road']:,}")
    else:
        no_xy += len(pending)
    return out, no_xy


def make_row(r, rid, status, closed, lat, lng, geo):
    try:
        area = round(float(pick(r, "area") or 0), 1)
    except ValueError:
        area = 0
    return [
        rid, pick(r, "name"), pick(r, "type") or "기타", status, pick(r, "detail"),
        pick(r, "road"), pick(r, "jibun"), pick(r, "tel"),
        ymd(pick(r, "permit")), closed, pick(r, "hygiene"), area,
        round(lat, 6), round(lng, 6), pick(r, "modified")[:10], geo,
    ]


def parse_csv(data, name):
    """열린데이터광장 CSV(보통 CP949, 가끔 UTF-8). 깨진 바이트가 한두 개 섞여 있어 replace로 읽는다."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp949", errors="replace")
    rows = list(csv.DictReader(text.splitlines()))
    print(f"[CSV] {name}: {len(rows):,}건", flush=True)
    return rows


def read_csv(path):
    return parse_csv(Path(path).read_bytes(), Path(path).name)


def download_csv(inf_id, org_code, retries=3):
    form = {
        "srvType": "S", "infId": inf_id, "serviceKind": "1", "pageNo": "1", "gridTotalCnt": "",
        "ssUserId": "SAMPLE_VIEW", "strWhere": f" AND OPNSFTEAMCODE = {org_code}", "strOrderby": "",
        "filterCol": "필터선택", "txtFilter": "",
    }
    req = urllib.request.Request(CSV_URL, data=urllib.parse.urlencode(form).encode(), method="POST")
    last = None
    for i in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                data = r.read()
            rows = parse_csv(data, f"{inf_id} ({len(data)/1e6:.1f} MB)")
            head = rows[0].keys() if rows else []
            if not {"관리번호", "사업장명"} <= set(head) or len(rows) < 1000:
                raise ValueError(f"CSV 형식이 예상과 다름 (열 {list(head)[:5]}…, {len(rows)}건)")
            ids = {r["관리번호"] for r in rows}
            note(f"CSV {inf_id}: {len(rows):,}건, 고유 관리번호 {len(ids):,}")
            return rows
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            last = e
            time.sleep(5 * (i + 1))
    raise RuntimeError(f"CSV 내려받기 실패: {last}")


def source_date(raw):
    """원본 데이터 기준일 = 행들의 데이터갱신일자 중 가장 최근"""
    return max((ymd(pick(r, "source_dt")) for r in raw), default="")


def main():
    keep_days = int(os.environ.get("KEEP_CLOSED_DAYS", "0"))
    if len(sys.argv) == 3 and sys.argv[1] == "--csv":
        services = ["CSV(직접 업로드)"]
        raw = read_csv(sys.argv[2])
    else:
        sets = [x.strip().split(":") for x in os.environ.get("SEOUL_CSV", "OA-18674:3220000").split(",") if x.strip()]
        try:
            raw = []
            for inf_id, org in sets:
                raw.extend(download_csv(inf_id, org))
            services = [f"CSV {inf_id}" for inf_id, _ in sets]
        except RuntimeError as e:
            key = os.environ.get("SEOUL_API_KEY", "").strip()
            if not key:
                sys.exit(f"{e} — SEOUL_API_KEY가 없어 API로 대신 받을 수도 없습니다.")
            note(f"{e} → Open API로 대신 받습니다.")
            services = [s.strip() for s in os.environ.get("SEOUL_SERVICES", "LOCALDATA_072404_GN").split(",") if s.strip()]
            raw = []
            for svc in services:
                raw.extend(fetch_service(key, svc))
    rows, no_xy = convert(raw, keep_days, int(os.environ.get("GEOCODE_MINUTES", "40")))
    n_open = sum(1 for r in rows if "폐업" not in r[3])
    note(f"변환 완료: {len(rows):,}곳 (폐업 아닌 곳 {n_open:,}) · 좌표 없음/범위 밖 {no_xy:,}건 제외")

    # 안전장치: API 장애로 결과가 급감하면 기존 파일을 덮어쓰지 않는다.
    if OUT.exists():
        try:
            prev = len(json.loads(OUT.read_text("utf-8")).get("rows", []))
            if prev and len(rows) < prev * 0.7:
                sys.exit(f"이전 {prev:,}곳 → 이번 {len(rows):,}곳으로 30% 이상 줄어 갱신을 건너뜁니다.")
        except json.JSONDecodeError:
            pass

    src = source_date(raw)
    note(f"원본 데이터 기준일: {src or '알 수 없음'}")
    payload = {
        "updated": dt.datetime.now(KST).isoformat(timespec="minutes"),
        "source_updated": src,
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
