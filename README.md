# 과제비 식당 지도

회사 근처 식당이 **일반음식점 인허가**를 받은 곳인지 지도에서 바로 확인하는 페이지입니다.
서울 열린데이터광장 「일반음식점 인허가 정보」를 매일 자동으로 받아 갱신합니다.

## 구성

| 파일 | 역할 |
|---|---|
| `index.html` | 지도 페이지 (Leaflet + OpenStreetMap 배경지도, API 키 불필요) |
| `scripts/fetch_data.py` | 서울 Open API 호출 → 좌표 변환(EPSG:5174 → WGS84) → `data/restaurants.json` 저장 |
| `.github/workflows/update-data.yml` | 매일 06:30(KST)에 위 스크립트를 실행하고 바뀐 데이터를 커밋 |

```
서울 Open API ──(매일 06:30, GitHub Actions)──▶ data/restaurants.json ──▶ index.html (GitHub Pages)
```

## 설정 (처음 한 번, 약 10분)

1. **인증키 발급**: 서울 열린데이터광장 로그인 → 마이페이지 → 인증키 신청(일반 인증키).
2. **저장소 만들기**: GitHub에 새 저장소(Private 가능)를 만들고 이 폴더 전체를 올립니다.
3. **키 등록**: 저장소 Settings → Secrets and variables → Actions
   - *Secrets* 에 `SEOUL_API_KEY` = 발급받은 키
   - (선택) *Variables* 에 `SEOUL_SERVICES` = 조회할 구. 기본은 강남구 `LOCALDATA_072404_GN`.
     서초구까지 보려면 `LOCALDATA_072404_GN,LOCALDATA_072404_SC`
4. **첫 실행**: Actions 탭 → 「일반음식점 데이터 갱신」 → Run workflow. 2~3분 뒤 `data/restaurants.json`이 생깁니다.
5. **페이지 공개**: Settings → Pages → Branch `main` / `/ (root)` 저장. 표시되는 주소로 접속합니다.
   - Private 저장소의 Pages는 유료 플랜(Team 이상)에서만 됩니다. 무료 계정이면 저장소를 Public으로 두세요(데이터 자체가 공공데이터라 문제없음, 인증키는 Secret이라 노출되지 않음).

## 회사 위치

`index.html` 상단의 `OFFICE_DEFAULT` 좌표가 기본값입니다. 현재 값은 대화빌딩(강남구 영동대로96길 20) 좌표입니다.
페이지에서 「회사 위치 바꾸기」 → 지도 클릭으로도 바꿀 수 있고, 이 값은 각자 브라우저에 저장됩니다.

## 페이지 기능

- 회사 기준 반경(300m~2km) 안의 일반음식점, 가까운 순 목록
- 업태 필터(한식·분식·중국식·일식·호프/통닭 등, 여러 개 동시 선택) — 칩 옆 숫자는 반경 2km 안 개수
- 영업상태(영업 중 / 휴업 / 최근 1년 폐업), 상호·주소 검색
- 상세 조건: 위생업태, 시설규모(㎡, 단체 회식 장소 가늠용), 최근 신규 인허가, 전화번호 등록 여부
- 팝업의 **증빙 문구 복사**: 상호·업태·영업상태·주소·인허가일자·관리번호·기준일을 한 번에 복사 (지출결의 첨부용)
- 카카오맵·네이버지도·구글지도 바로가기 (목록과 팝업 양쪽에서, 상호+도로명 앞부분으로 검색해 다른 지점과 헷갈리지 않게 함)
- 자동 데이터가 없을 때는 열린데이터광장에서 받은 CSV를 끌어다 놓아도 바로 동작 (EUC-KR/UTF-8 자동 인식)

## 알아둘 점

- 지도에 없는 가게 = 해당 구 일반음식점 인허가 목록에 없음(휴게음식점·제과점 등일 수 있음). 반대로 **같은 건물에 상호가 비슷한 곳**이 있을 수 있으니 주소까지 확인하세요.
- 데이터 제공 기관 사정으로 갱신이 하루 이틀 늦을 수 있습니다. 페이지 상단의 「기준」 날짜를 확인하세요.
- 한 번에 받은 건수가 이전보다 30% 이상 줄면(API 장애 추정) 덮어쓰지 않고 이전 데이터를 유지합니다.
- 열린데이터광장 원본시스템이 공공데이터포털(지방행정 인허가정보)로 표기되어 있습니다. 서울 Open API 서비스명이 바뀌거나 종료되면 Actions 실행 로그에 오류 코드가 찍히니, 그때 `SEOUL_SERVICES`나 스크립트의 요청 주소만 바꾸면 됩니다.
- GitHub Actions의 예약 실행은 몇 분~수십 분 늦게 시작될 수 있습니다.
- GitHub 서버(해외)에서 서울 API 접속이 막히는 경우, 회사 PC에서 아래처럼 실행해 커밋하는 방식으로 대체할 수 있습니다(Windows 작업 스케줄러에 등록).

```bat
set SEOUL_API_KEY=발급받은키
python scripts\fetch_data.py && git add data\restaurants.json && git commit -m "데이터 갱신" && git push
```

## 로컬에서 보기

```bash
pip install pyproj
SEOUL_API_KEY=발급받은키 python scripts/fetch_data.py
python -m http.server 8000   # http://localhost:8000 접속
```

데이터 출처: 서울 열린데이터광장 일반음식점 인허가 정보 (공공누리 제1유형) · 지도: © OpenStreetMap contributors
