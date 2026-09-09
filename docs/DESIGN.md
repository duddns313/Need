# 철도안전관리체계 요구사항 추적기 (RSMS Requirement Tracker) — 설계 문서

이 문서는 이 저장소의 프로그램을 처음부터 다시 만들거나, 다른 조직/분야용으로
포크해서 개선하려는 개발자를 위한 설계 명세서입니다. "왜 이렇게 만들었는가"와
"무엇을 조심해야 하는가"에 초점을 맞췄습니다 — 코드는 저장소를 보면 되지만,
설계 의도와 실패했던 접근/함정은 코드만 봐서는 알기 어렵기 때문입니다.

---

## 1. 이 프로그램이 푸는 문제

철도 운영기관은 「철도안전법」 등 관련 **법령·행정규칙**을 반영해 **사내
안전관리체계 규정(내규)**을 최신 상태로 유지해야 합니다. 문제는:

- 관리해야 할 법령·행정규칙이 수십~수백 건이고, 각각 담당 부서가 다름
- 법령은 수시로 개정되고, 심지어 **시행 예정**(공포는 됐지만 아직 시행 전) 개정도 있음
- "이 법이 바뀌었는데 우리 내규에 반영했나?"를 사람이 매번 국가법령정보센터에서
  하나씩 검색해서 대조하는 건 현실적으로 지속 불가능
- 회사는 보통 **요구사항 관리카드(엑셀)** 로 전사 목록을 관리하고, 부서별로
  **관리카드(한글/hwp)** 로 상세를 관리함 — 이 두 문서가 이미 존재한다는 전제

그래서 이 프로그램은 "새로 뭔가를 찾아내는" 도구가 아니라, **이미 존재하는
관리 대장(엑셀+한글)에 적힌 항목만** 골라서, 국가법령정보센터 공개 API로
최신 상태와 자동 대조해주는 도구입니다. 이 전제가 전체 설계를 지배합니다 —
문서 전체를 정규식으로 훑어 "법령처럼 보이는 문자열"을 다 긁어모으는 방식은
의도적으로 쓰지 않았습니다(잡음이 너무 많아짐).

### 1.1 운영 환경 제약 (설계에 가장 큰 영향을 준 요소)

- **외부망 차단 PC에서 실행**됨. 즉 Python 설치/패키지 설치가 불가능한 환경이
  기본 전제 → **단일 실행파일(exe)** 로 배포해야 함.
- PC가 꺼지면 초기화되는 환경일 수 있음 → 모든 산출물을 **한 폴더(workspace/)**
  에 모아 zip 하나로 백업/복원 가능해야 함.
- 유일하게 필요한 네트워크는 **국가법령정보센터 Open API** (그리고 선택적으로
  Claude API). 그 외에는 전부 로컬(127.0.0.1)에서만 동작.
- 문서(엑셀/한글) 원본은 로컬을 벗어나지 않음. AI 모드를 켰을 때만 **발췌본**이
  Claude API로 전송됨 — 이 경계를 코드에서도, UI 문구에서도 명확히 밝혀야 함.

---

## 2. 기술 스택 선택 이유

| 구성 요소 | 선택 | 이유 |
|---|---|---|
| 웹 프레임워크 | Flask | 가볍고, 단일 프로세스로 `127.0.0.1`에만 바인딩하기 쉬움. 사용자는 브라우저로만 접근 — 별도 GUI 툴킷 불필요 |
| 프로덕션 서버 | waitress | 순수 Python 서버라 PyInstaller onefile에 문제없이 번들됨. 없으면 `app.run()`으로 폴백 |
| 패키징 | PyInstaller `--onefile` | 외부망 PC에 Python이 없다는 전제이므로 단일 exe가 사실상 유일한 선택지 |
| 암호화 | `cryptography` (PBKDF2HMAC + Fernet) | OC 인증키·Claude API 키를 평문으로 두지 않기 위함. 표준 라이브러리 조합이라 감사(audit)하기 쉬움 |
| 엑셀 파싱 | openpyxl | `data_only=True, read_only=True`로 수식 결과값만, 메모리 적게 읽음 |
| PDF 파싱 | pypdf | 텍스트 레이어만 지원(스캔본 불가 — OCR은 범위 밖으로 명시) |
| HWP 파싱 | olefile + 자체 레코드 파서 | 공식 SDK나 상용 변환기 없이 무료로 HWP 5.0 바이너리를 읽는 유일한 실용적 방법 |
| AI(선택) | Anthropic Claude API | 카드에 이미 적힌 "개정 필요여부"를 신뢰하는 게 기본 모드이고, AI는 내부규정 발췌본까지 직접 읽고 판단하는 **선택적 강화 모드** |

**중요한 설계 원칙**: AI는 선택 사항(옵트인)이고, 기본 동작은 **규칙 기반
(keyword/카드 기준) 판정**입니다. 이유는 (1) 외부망 환경에서 항상 Claude API
키가 있으리라는 보장이 없고, (2) 법령 준수 판정처럼 책임이 따르는 업무는
"카드에 부서가 이미 검토해 적어둔 값 + 기계적으로 확인 가능한 사실(공포번호
불일치 등)"을 기본으로 하고, AI는 보조 신호로만 쓰는 게 안전하기 때문입니다.

---

## 3. 전체 아키텍처

```
┌─────────────────────────────────────────────────────────────┐
│  브라우저 (사용자 PC, 127.0.0.1:8733)                         │
└───────────────────────────┬─────────────────────────────────┘
                             │ HTTP (로컬 전용)
┌───────────────────────────▼─────────────────────────────────┐
│  Flask 앱 (app/main.py)                                       │
│  - 세션/로그인 (app/vault.py 로 암호화된 OC키·API키 잠금 해제)   │
│  - 업로드 처리, 워크스페이스 화면                                │
│  - /analyze → 백그라운드 스레드(threading.Thread)로 분석 실행   │
│  - /api/progress/<sid> → 폴링용 진행률 JSON                    │
└───────┬───────────────────────────────────┬─────────────────┘
        │                                   │
┌───────▼───────────┐               ┌───────▼──────────────────┐
│ app/extractors.py  │               │ app/analyzer.py            │
│ 문서 → 텍스트/표    │               │ 카드 파싱 · 이름 매칭 ·      │
│ (xlsx/hwp/pdf/…)   │               │ 판정 로직(evaluate/AI)     │
└────────────────────┘               └───────┬───────────────────┘
                                              │
                                     ┌────────▼──────────────────┐
                                     │ app/lawapi.py               │
                                     │ 국가법령정보센터 Open API 클라이언트│
                                     └─────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ app/storage.py — workspace/ 폴더 하나에 전부 모음:              │
│   credentials.enc(암호화) · uploads/ · sessions/ ·             │
│   law_snapshots/(개정 감지 기준선) · history.jsonl(감사 로그)   │
└─────────────────────────────────────────────────────────────┘
```

### 3.1 요청 흐름 한 줄 요약

1. 최초 실행 → `/login`에서 앱 비밀번호 설정 + OC 인증키(필수) + Claude API 키(선택) 입력
   → `vault.save()`로 암호화 저장.
2. 이후 실행 → 비밀번호만 입력하면 `vault.load()`로 복호화, 메모리(`_UNLOCKED` dict)에만
   올려두고 세션 토큰으로 참조 (파일에 평문으로 남기지 않음).
3. `/workspace`에서 3종 문서 업로드: **①요구사항 관리카드(엑셀, 필수)**,
   **②분야별 관리 문서(한글, 선택)**, **③내부 규정 원문(한글/PDF, 대조용)**.
4. `/analyze` → 새 세션 ID 발급 → 현재 업로드본을 세션 폴더로 스냅샷 →
   백그라운드 스레드에서 `_run_analysis()` 실행 → `/progress/<sid>`로 리다이렉트,
   해당 화면이 `/api/progress/<sid>`를 폴링(진행률 바).
5. 완료되면 `/result/<sid>`에서 항목별 상태(현행일치/확인필요/개정필요/미반영/조회실패)를
   보여주고, `/recommendation/<sid>`는 조치가 필요한 항목만 추려서 보고서 형태로 제공.

---

## 4. 디렉토리 구조

```
Need/
  run.py                 # 진입점 (개발 실행 / exe 실행 겸용)
  requirements.txt
  app/
    __init__.py
    paths.py             # exe/소스 실행 여부에 따른 경로 결정 (가장 먼저 이해해야 할 파일)
    vault.py             # 자격증명 암호화 저장
    storage.py           # workspace/ 폴더 관리, 세션/이력/백업
    extractors.py        # 문서 → 텍스트 추출 (xlsx/csv/pdf/hwpx/hwp/txt)
    lawapi.py            # 국가법령정보센터 Open API 클라이언트
    analyzer.py          # 카드 파싱, 이름 매칭, 판정 로직(evaluate/compare_ai)
    main.py              # Flask 앱, 라우트, 백그라운드 분석 파이프라인
    templates/           # Jinja2 템플릿 (base/login/workspace/progress/result/recommendation/history/backup/settings)
    static/
      style.css
  workspace/              # 런타임에 생성됨 (git에는 커밋하지 않음)
    credentials.enc
    state.json
    history.jsonl
    uploads/<세션>/{cards,domain,internal}/
    sessions/<세션>.json
    law_snapshots/<target>_<seq>.json
  exports/                # 백업 zip 저장 위치
```

---

## 5. 핵심 모듈 상세 설계

### 5.1 `paths.py` — 실행 모드에 따른 경로 결정 (제일 먼저 정확히 이해해야 함)

```python
def app_root():
    """exe로 빌드된 상태면 exe가 있는 폴더, 소스 실행이면 프로젝트 루트."""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent

def bundle_root():
    """PyInstaller가 templates/static을 풀어놓는 임시 폴더."""
    if getattr(sys, 'frozen', False):
        return Path(getattr(sys, '_MEIPASS'))
    return Path(__file__).resolve().parent  # app/ 디렉터리 자체
```

- `WORKSPACE = app_root() / 'workspace'` — **exe 파일이 놓인 위치 기준**. 사용자가
  exe를 어디로 옮기든 그 옆에 workspace가 생김 (레지스트리/AppData 사용 안 함 →
  포터블성, 백업 용이성을 위한 의도적 선택).
- `bundle_root()`는 **읽기 전용 리소스(템플릿/정적 파일)** 용이고, `app_root()`는
  **쓰기 가능한 사용자 데이터**용입니다. 이 둘을 섞으면 안 됩니다 — PyInstaller
  onefile은 실행마다 `_MEIPASS`에 새 임시 폴더를 풀기 때문에 거기다 쓰면
  다음 실행 때 사라집니다.

⚠️ **가장 흔히 발생하는 배포 버그**: `main.py`는
`Flask(template_folder=str(bundle_root()/'templates'), static_folder=str(bundle_root()/'static'))`
로 되어 있습니다. 즉 PyInstaller 빌드 시 `--add-data` 옵션의 **대상 경로**가
정확히 `templates`, `static` (번들 루트 바로 아래)이어야 합니다. 소스 폴더
구조가 `app/templates`라고 해서 대상도 `app/templates`로 지정하면
`_MEIPASS/app/templates`에 풀리는데, 코드는 `_MEIPASS/templates`를 찾으므로
**로그인 화면조차 못 띄우고 500 에러**가 납니다 (실제로 이 프로젝트에서 한 번
발생했던 실수). 빌드 명령은 반드시 아래처럼:

```bash
pyinstaller --onefile --console --name RSMS \
  --add-data "app/templates;templates" \
  --add-data "app/static;static" \
  run.py
```
(Windows에서 직접 빌드한다면 세미콜론 구분자 그대로, `app\templates;templates`.)

### 5.2 `vault.py` — 자격증명 보관

- PBKDF2HMAC(SHA256, 390,000 iterations) 로 사용자가 정한 "앱 비밀번호"에서
  Fernet 키를 파생 → OC 인증키/Claude API 키를 JSON으로 암호화해 `credentials.enc`
  하나에 저장.
- 파일 포맷: `salt(16 bytes) + Fernet 토큰`. 비밀번호 자체는 어디에도 저장하지
  않음 — 잊으면 파일을 지우고 처음부터 다시 등록하는 것 외에 복구 방법 없음
  (의도된 설계: 서버도 없고 "비밀번호 찾기" 이메일도 없는 완전 오프라인 앱이므로).
- `mask(value, keep=4)`: 설정 화면에 키 원문을 다시 뿌리지 않고 끝 4자리만
  보여주기 위한 유틸.

### 5.3 `storage.py` — 워크스페이스/이력 관리

- 모든 산출물을 `workspace/` 한 폴더에 모으는 이유는 "외부망 PC가 꺼지면
  초기화될 수 있다"는 전제 때문 — `export_zip()` / `export_full_zip()`(exe
  자신까지 포함) 로 통째로 백업하고 `import_zip()`으로 복원.
- `law_snapshots/<target>_<seq>.json`: 법령별로 마지막에 확인한 상태
  (공포일·시행일·핑거프린트)를 저장해뒀다가, 다음 분석 때 `compare_with_snapshot()`
  으로 "직전 확인 이후 개정이 있었는지"를 판단. 이건 카드에 적힌 정보와는
  **별개의 축**입니다 — 카드는 "부서가 이미 검토했는가", 스냅샷은 "우리 프로그램이
  마지막으로 봤을 때와 지금이 같은가"를 봅니다.
- `history.jsonl`: append-only 감사 로그. 분석 실행/완료/실패, 파일 업로드,
  잠금해제 등을 기록. 문제 발생 시 "언제 무슨 일이 있었는지" 추적용.

### 5.4 `extractors.py` — 문서 → 텍스트

지원 포맷과 각각의 함정:

| 포맷 | 방법 | 함정 |
|---|---|---|
| `.xlsx/.xlsm` | openpyxl, `data_only=True` | 수식이 아니라 마지막 계산값만 읽음 — 파일을 열어서 저장한 적 없으면 값이 비어있을 수 있음 |
| `.csv` | 표준 csv, 인코딩 `utf-8-sig→cp949→utf-8` 순서로 재시도 | 한국 엑셀에서 내보낸 CSV는 보통 cp949 |
| `.pdf` | pypdf `extract_text()` | **텍스트 레이어 없는 스캔본은 빈 결과** → 사용자에게 hwpx/PDF+OCR 안내 |
| `.hwpx` | zip + XML, `<hp:t>` 태그 정규식 추출 | hwpx는 사실 zip이라 가장 안정적. 우선 권장 포맷 |
| `.hwp` | olefile로 OLE 스트림 열고 `BodyText` 섹션을 직접 파싱 | 아래 5.4.1 참고. 암호 걸린 문서는 명시적으로 실패 처리 |

#### 5.4.1 HWP 5.0 바이너리 파싱 (가장 까다로운 부분)

공식 SDK 없이 직접 구현. 구조:

1. `FileHeader` 스트림에서 속성 플래그 읽기 → 압축 여부(`props & 1`), 암호화
   여부(`props & 2`) 판별.
2. 암호화되어 있으면 즉시 실패 처리 (풀 방법 없음, 사용자에게 암호 해제 요청).
3. 압축이면 각 `BodyText/SectionN` 스트림을 `zlib.decompress(data, -15)` (raw
   deflate, 헤더 없음 — 이 `-15`가 핵심, 일반 `zlib.decompress(data)`로는 실패함)
   로 해제.
4. 압축 해제된 바이트열은 **레코드 스트림**: `[4바이트 헤더][가변 길이 데이터]`
   반복. 헤더 하위 10비트가 태그, 그 위 12비트가 길이(4095면 다음 4바이트가
   실제 길이 — 레코드가 4KB 넘을 때의 이스케이프).
5. 태그 67(`HWPTAG_PARA_TEXT`)만 문단 텍스트 — 나머지 태그(문단 모양, 스타일,
   표 등)는 건너뜀.
6. 문단 텍스트는 UTF-16LE인데, 0~31 코드값은 "인라인 컨트롤 문자"(그림, 표,
   각주 등의 삽입 지점)라 실제 유니코드 문자가 아님. 특정 컨트롤 코드
   (1,2,3,11,12,14,15,16,17,18,21,22,23)는 그 뒤에 **16바이트짜리 확장 데이터**가
   따라오므로 그만큼 건너뛰어야 함 — 이걸 안 하면 이후 텍스트가 전부 깨짐.
   탭/줄바꿈에 해당하는 코드(10, 13)는 `\n`으로 치환.

이 부분은 문서화가 거의 없어서(비공식 리버스엔지니어링 자료에 의존) 실제
파일로 검증하며 만들어야 합니다. 이 저장소에서는 실제 사용자 hwp 파일 4개로
텍스트가 정상 추출되는 것을 확인했습니다.

### 5.5 `lawapi.py` — 국가법령정보센터 Open API 클라이언트

- 베이스: `https://www.law.go.kr/DRF/lawSearch.do` (목록), `.../lawService.do` (본문)
- `target` 파라미터로 `law`(법령) / `admrul`(행정규칙) / `eflaw`(시행예정법령)
  구분 — **철도안전관리체계 기술기준 같은 문서는 법률이 아니라 국토교통부
  고시=행정규칙으로 등록**되어 있어서 `admrul`을 빼면 아예 검색되지 않는다는
  점이 이 도메인에서 중요.
- 응답 JSON의 루트 키/필드 키가 target마다 다르고 문서화가 부실해서,
  `_pick()`(여러 후보 키를 순서대로 시도)와 `_find_items()`(응답 루트를 한 겹
  벗기고 리스트로 보이는 값을 탐색)로 방어적으로 파싱.
- `best_match(name)`: 이름이 정확히 안 맞을 때의 폴백 사다리 —
  1) 전체 이름으로 법령+행정규칙 검색 → 완전일치 → 접두일치 → 최단 이름 순으로 선택
  2) 콤마로 여러 이름이 묶여 있으면 각각 재시도
  3) 괄호 설명을 뗀 이름으로 재시도
- `pending(name)`: `eflaw` target으로 조회해 `history_code == '시행예정'`인 것만
  걸러 시행일 순 정렬. 행정규칙에는 이 조회가 없음(법령에만 존재).
- 모든 호출 뒤에 `time.sleep(_PAUSE)`(0.35초) — 공식 rate limit 문서가 없어서
  방어적으로 넣은 값. **6절에서 설명하는 중복 제거+병렬화 최적화가 나오기 전에는
  이 sleep이 대량 항목 처리 시간의 상당 부분을 차지했음.**

### 5.6 `analyzer.py` — 카드 파싱과 판정 로직 (이 프로그램의 두뇌)

#### 5.6.1 `extract_from_cards(tables)` — 엑셀에서 관리 대상 추출

문서 전체를 훑지 않고, **헤더 행을 찾아 컬럼 위치를 파악한 뒤 그 구조를 그대로
읽는 방식**입니다:

1. "요구사항"과 "명"을 동시에 포함하는 셀이 있는 행을 헤더로 간주.
2. 헤더에서 컬럼을 이름 기반 휴리스틱으로 찾음 (`_find_col`): 요구사항명,
   개정사항, 시행일, 개정필요여부, 관련규정(운영절차 우선, 없으면 "관련"+"규정"),
   관련부서, 담당부서, 개정예정/완료일, 주요개정내용.
3. 헤더 다음 행부터 순회하며 `Reference` 객체 생성. 한 셀에 콤마로 여러 법령이
   묶여 있으면 `split_names()`로 분리해 **각각 별도 Reference로** 만듦(괄호 안의
   콤마는 조문 번호 나열이므로 무시 — 괄호 깊이를 추적하는 파서).
4. "개정 필요여부" 컬럼값이 부정어 집합(`빈칸/-/N/X/무/불필요/해당없음`)에 없으면
   `revision_needed=True`로 간주.

**이 함수를 바꾸려면**: 회사마다 엑셀 헤더 문구가 다를 수 있으므로,
`_find_col`의 조건 람다들을 그 조직의 실제 엑셀 헤더 문구로 교체하는 것이
포크 시 가장 먼저 해야 할 작업입니다.

#### 5.6.2 `split_domain_card` / `build_domain_index` / `enrich_with_domain` — 한글 부서 카드 병합

부서별 한글 카드는 `"NO 요구사항명 최종개정일(공포)"` 같은 고정 헤더로 항목이
반복되는 구조라고 가정하고, 그 헤더 패턴으로 문서를 항목 블록 단위로 자릅니다
(`_ITEM_SPLIT_RE`). 각 블록에서:
- 첫 줄이 `XX-123` 형태 코드 패턴(`_CODE_LINE_RE`)이면 관리번호로 인식
- `□ 요구사항 개정사항`, `□ 관련규정 반영계획` 같은 섹션 마커로 시작해서 다음
  `□`가 나올 때까지를 그 섹션 내용으로 수집 (`_extract_section`)
- `관련사규` 라벨 다음 줄을 관련 내규로 (`_extract_after_label`)

`enrich_with_domain`의 병합 규칙이 핵심: **엑셀 값이 있으면 그대로 두고, 비어
있는 필드만 한글 카드 값으로 채웁니다.** 이유는 여러 부서가 같은 법령을
관리하는 한글 카드가 하나로 통합되어 있을 수 있어서, 무조건 덮어쓰면 부서
구분 없이 관련 내규가 뒤섞이기 때문입니다. 이름이 정확히 안 맞으면
`norm_name()`으로 정규화한 뒤 부분 문자열 포함 관계로 한 번 더 시도합니다.

#### 5.6.3 `evaluate(record, ref, pending)` — 규칙 기반 판정 (기본 모드)

판정 우선순위 (이 순서가 설계의 핵심):

1. **카드 자체가 오래됐는지 먼저 확인**: 한글 카드에서 뽑은 공포·발령번호
   (`ref.card_promulgation`, 예: `"제21188호"`)와 법령정보센터의 현재
   공포번호가 다르면, **`revision_needed` 값과 무관하게** `status='review'`로
   격상. "카드에 적힌 판단 자체가 최신 법령을 보고 내려진 게 아닐 수 있다"는
   것을 부서의 "개정 불필요" 응답보다 우선시함. 이때 `pending`(시행예정 개정)
   정보도 함께 안내.
2. 위에 해당하지 않으면 `ref.revision_needed`(부서가 카드에 이미 기재한 판단)를
   그대로 신뢰 → `outdated`(개정 반영 필요) 또는 `ok`(현행 일치).

**의도적으로 하지 않는 것**: 조문(article) 단위 자동 비교. 카드의 "시행일"
칸은 여러 조문의 서로 다른 시행일이 자유 텍스트로 섞여 있는 경우가 많아서
(`"시행1 '26.12.17. / 시행2 '27.06.17."` 같은 형식), 법령정보센터의 단일
시행일자와 기계적으로 비교하면 **거꾸로 된 판정**이 나올 수 있습니다. 그래서
이 두 값은 절대 직접 비교하지 않고, "직전 실행 대비 바뀜"은 `storage.py`의
스냅샷 비교가 별도로 담당하게 분리했습니다. **이 분리를 없애고 자동으로
합치려 하면 오탐이 늘어날 가능성이 높습니다.**

#### 5.6.4 `compare_ai(record, ref, internal_text, ...)` — AI 강화 모드 (선택)

- `relevant_excerpt()`로 내부 문서에서 법령명 주변만(앞뒤 750자, 최대 5곳) 잘라내
  토큰을 절약(전체 내부 문서를 다 보내지 않음 — 비용·프라이버시 양쪽 이유).
- 고정 프롬프트(`_COMPARE_PROMPT`)로 Claude에게 3가지를 판단하게 함: (1) 카드의
  개정필요 판단이 타당한가, (2) 내부 문서가 인용한 버전이 최신과 일치하는가,
  (3) 언급이 아예 없으면 누락인지 해당없음인지. **반드시 JSON만 출력**하도록
  강제하고, 파싱 실패 시 `status='review'`로 안전하게 폴백(예외를 던지지 않고
  사람이 확인하도록 유도).
- `DEFAULT_MODEL = 'claude-sonnet-5'` — 모델 ID는 설정 화면에서 사용자가 바꿀 수
  있게 노출되어 있음(하드코딩 아님).

---

## 6. 성능: 대량 항목 처리 시 병목과 해결

### 6.1 문제

관리카드가 100건 이상이면 원래 구현은 **행 하나마다** 법령정보센터 API를
독립적으로 호출했습니다:
- `best_match()` 1회당 최소 2회(법령+행정규칙) ~ 많으면 6회(이름 안 맞아
  재시도) API 호출
- `target=='law'`면 `pending()` 1회 추가
- 호출마다 0.35초 강제 슬립

그런데 실제 데이터에서는 **같은 법령이 여러 행에 중복 등장**하는 경우가 많습니다
(같은 법이 여러 부서/요구사항에 걸쳐 인용되므로). 원래 구현은 이 중복을
전혀 걸러내지 않고 매번 새로 조회했습니다 — "법령 전체를 스캔하는 것 같다"는
체감은 사실 이 **중복 재조회 + 순차 처리**가 원인이었습니다.

### 6.2 해결 (`app/main.py::_run_analysis`)

조회 단계와 결과 조립 단계를 분리했습니다:

```python
unique_names = list(dict.fromkeys(ref.name for ref in refs))   # 순서 보존 중복 제거
lookup_cache = {}

def _lookup(name):
    rec = client.best_match(name)
    ...
    snap = storage.compare_with_snapshot(rec)   # 스냅샷 비교/저장도 이름당 1번만
    pending = client.pending(name) if rec.target == 'law' else []
    storage.write_snapshot(rec)
    return name, {'rec': rec, 'snap': snap, 'pending': pending}

with ThreadPoolExecutor(max_workers=4) as pool:      # 서로 다른 이름은 동시에
    futures = [pool.submit(_lookup, name) for name in unique_names]
    for fut in as_completed(futures):
        name, result = fut.result()
        lookup_cache[name] = result
        ...  # 진행률 갱신

# 이후 refs를 순회하며 lookup_cache만 참조 — 네트워크 호출 없음, 순수 CPU 작업
for ref in refs:
    cached = lookup_cache[ref.name]
    ...  # evaluate()/compare_ai() 등 기존 로직 그대로
```

설계 포인트:
- **동시성 단위는 "고유 법령명"이지 "카드 행"이 아닙니다.** 같은 이름을 두
  스레드가 동시에 조회하는 경쟁 상태가 애초에 생기지 않도록, 중복 제거를
  먼저 하고 나서 유니크한 이름들만 스레드 풀에 넣습니다. 그래서 스냅샷
  파일 쓰기(`storage.write_snapshot`)에 락이 필요 없습니다(이름당 정확히
  스레드 하나만 그 파일을 건드림).
- `max_workers=4`로 제한한 이유는 국가법령정보센터 API의 공식 rate limit
  문서가 없어서 방어적으로 잡은 값입니다. 차단되더라도 실패한 항목만
  `status='notfound'`로 표시되고 나머지는 정상 처리되므로, 전체가 막히는
  구조는 아닙니다 — 필요하면 재분석을 다시 돌리면 됩니다. 이 값을 올릴 때는
  실제 API 반응(429나 IP 차단 여부)을 보면서 조정하세요.
- AI 모드(`compare_ai`)는 이 최적화 대상에서 **의도적으로 제외**했습니다 —
  법령 조회와 달리 AI 판정은 카드 행마다 서로 다른 `ref` 필드(개정사항,
  관련 내규 등)를 프롬프트에 넣으므로 이름이 같아도 결과가 다를 수 있어서,
  섣불리 캐싱하면 잘못된 결과를 재사용하게 됩니다.

### 6.3 검증 방법 (재현 가능한 회귀 테스트 패턴)

실제 법령정보센터에 부하를 주지 않고 검증하려면, `LawClient`를 가짜 클라이언트로
치환해 지연시간만 흉내 내는 것으로 충분합니다:

```python
class FakeClient:
    def best_match(self, name):
        time.sleep(0.3)          # 네트워크 지연 흉내
        return LawRecord(...)    # 고정된 가짜 결과
    def pending(self, name):
        time.sleep(0.1)
        return []

app.main.LawClient = FakeClient
# 이름 5종을 20건에 중복 배치한 refs로 _run_analysis() 실행
# → best_match 호출 횟수가 20이 아니라 5인지, 소요 시간이 직렬(20*0.4s)이 아니라
#   병렬(약 2배치*0.4s)에 가까운지 확인
```
이 저장소를 만들 때 이 방식으로 "20건 중 중복 제외 5건만 실제 호출, 0.81초
(직렬이면 약 2초)"를 확인했습니다.

---

## 7. Flask 라우트 전체 목록

| 경로 | 메서드 | 설명 |
|---|---|---|
| `/` | GET | 잠금 여부에 따라 `/login` 또는 `/workspace`로 리다이렉트 |
| `/login` | GET/POST | 최초 실행 시 비밀번호+OC키(필수)+API키(선택) 등록, 이후엔 비밀번호만 |
| `/logout` | POST | 메모리상 잠금 해제 정보 폐기 |
| `/settings` | GET/POST | OC키/API키/모델 변경 (현재 비밀번호 재확인 필요) |
| `/api/ping-law` | GET | 설정 화면 "연결 확인" 버튼 — `철도안전법` 검색 시도 |
| `/workspace` | GET | 업로드 화면, 최근 분석 5건 표시 |
| `/upload/<category>` | POST | category ∈ {cards, domain, internal} |
| `/upload/<category>/delete` | POST | 업로드 파일 삭제 |
| `/analyze` | POST | mode ∈ {keyword, ai} — 백그라운드 스레드 시작, 진행 화면으로 리다이렉트 |
| `/progress/<sid>` | GET | 진행률 화면(폴링 JS 포함) |
| `/api/progress/<sid>` | GET | 진행률 JSON (phase/done/total/log/error/finished) |
| `/result/<sid>` | GET | 상태별 정렬된 전체 결과 |
| `/recommendation/<sid>` | GET | 조치 필요 항목만 추린 보고서 |
| `/history` | GET | 세션 이력 + 감사 로그 |
| `/backup`, `/backup/export`, `/backup/export-full`, `/backup/import` | GET/POST | workspace zip 백업/복원 |

인증은 세션 쿠키의 랜덤 토큰 → 프로세스 메모리(`_UNLOCKED` dict) 매핑 방식입니다
(별도 사용자 DB 없음 — 단일 사용자 로컬 앱이므로).

---

## 8. UI/템플릿 구조

`app/templates/`: `base.html`(공통 레이아웃) + `login/workspace/progress/result/
recommendation/history/backup/settings.html`. 상태 배지는 `main.py`의
`STATUS_META` 딕셔너리 하나로 한글 라벨과 색상 톤(`stop/caution/clear`)을 정의해
템플릿에 전달합니다 — 상태 종류를 늘리려면 이 딕셔너리와 `evaluate()`/
`compare_ai()`가 반환하는 `status` 값만 맞추면 됩니다.

---

## 9. 배포/패키징 실전 가이드

### 9.1 정상적인 경로: Windows PC에서 직접 빌드

```bash
pip install -r requirements.txt pyinstaller
pyinstaller --onefile --console --name RSMS_요구사항추적기 ^
  --add-data "app\templates;templates" ^
  --add-data "app\static;static" ^
  run.py
```
결과물은 `dist/RSMS_요구사항추적기.exe` 단 하나 — 이걸 외부망 PC로 옮기면 됩니다.

### 9.2 소스가 없을 때 exe만으로 이 프로젝트를 복구한 방법 (참고)

이 저장소는 실제로 **원본 소스를 분실한 뒤 배포된 exe 하나만 갖고 전체 코드를
복원**해서 만들어졌습니다. 같은 상황(소스 유실, exe만 존재)에 처했을 때 참고할
수 있도록 절차를 남깁니다:

1. `pyinstxtractor-ng`로 exe에서 PyInstaller 번들을 풀어냄 → `.pyc` 파일들과
   (컴파일되지 않는) `templates/`, `static/` 원본이 그대로 나옴.
2. `pyc` → 소스 복원은 기성 디컴파일러(`decompyle3`, `uncompyle6`)가 최신 Python
   버전(3.11+)의 새 바이트코드(NULL-or-self 호출 규약, `CALL_KW`, superinstruction
   등)를 지원하지 않아서 실패 — 이 경우 `pycdc`(zrax/pycdc, C++ AST 기반
   디컴파일러)를 소스에서 빌드해 필요한 opcode를 직접 패치해가며 썼음.
3. 그래도 안 풀리는 구문(주로 `or`/`and` 단축평가, `with` 문 보일러플레이트)은
   `pycdas`(같은 프로젝트의 디스어셈블러)로 원본 바이트코드를 직접 읽어
   손으로 재구성 — 특히 `a or b` 형태의 기본값 대입 패턴이 흔히 깨졌음.
4. 복원한 코드는 반드시 **실제로 실행해서 검증**했음 — 문법적으로 그럴듯해
   보여도 로직이 반대로 뒤집힌 경우(`enrich_with_domain`의 "비어있을 때만
   채운다" 조건 등)가 있었기 때문. 실제 사용자 표본 파일로 파이프라인을
   끝까지 돌려 출력이 말이 되는지 확인하는 과정이 디컴파일 자체보다 더
   중요했습니다.

### 9.3 외부망 개발 환경(Linux, python.org 접근 불가)에서 Windows exe를 만드는 방법

python.org가 방화벽에 막혀 있고 PyPI(`files.pythonhosted.org`)만 열려 있는
환경에서도, GitHub 릴리스에서 받을 수 있는 **portable Windows Python**
(`astral-sh/python-build-standalone` 프로젝트의 `*-x86_64-pc-windows-msvc-
install_only.tar.gz`)과 **Wine**을 조합하면 리눅스에서도 진짜 PE32+ Windows
exe를 만들 수 있습니다:

```bash
apt-get install -y wine64 wine
export WINEPREFIX=~/.wine-build WINEARCH=win64
wineboot --init   # "rundll32.exe c0000135" 경고가 나와도 64비트 앱 실행에는 무해함
                  # (32비트 WOW64 호환 레이어 wine32 미설치로 인한 경고일 뿐)

# 포터블 Windows Python 받아서 프리픽스에 배치
curl -LO https://github.com/astral-sh/python-build-standalone/releases/download/<TAG>/cpython-<VER>+<TAG>-x86_64-pc-windows-msvc-install_only.tar.gz
tar xzf cpython-*.tar.gz
cp -r python/* "$WINEPREFIX/drive_c/pywin/"

# Wine 콘솔 stdio 초기화가 간헐적으로 "OSError: WinError 6 Invalid handle"을 내므로
# script(1)로 pty를 할당해서 실행 (이게 없으면 pip/python이 랜덤하게 죽음)
script -qc 'wine C:\\pywin\\python.exe -m pip install -r requirements.txt pyinstaller' /dev/null

script -qc 'wine C:\\pywin\\python.exe -m PyInstaller --onefile --console --name RSMS \
  --add-data "app\\templates;templates" --add-data "app\\static;static" run.py' /dev/null
```

주의할 점:
- `--name`에 한글을 쓰면 Wine의 커맨드라인 인코딩 문제로 exe 이름이 깨질 수
  있으므로, 빌드는 영문 이름으로 하고 이후 파일 시스템에서 원하는 한글
  이름으로 복사하세요.
- 빌드 후 반드시 Wine 위에서 실제로 실행해 `curl http://127.0.0.1:<port>/`로
  200/302 응답이 오는지 확인하세요 — `file` 명령으로 "PE32+ executable"인 것만
  확인하고 넘어가면 9.1에서 언급한 템플릿 경로 버그 같은 걸 놓칩니다.

---

## 10. 보안 설계 메모

- 자격증명은 항상 Fernet 암호문으로만 디스크에 존재. 복호화된 값은 프로세스
  메모리에만 상주(`_UNLOCKED` dict), 재시작하면 사라짐.
- 업로드 파일명은 `werkzeug.secure_filename`을 쓰지 않음 — 한글 파일명을
  통째로 지워버리기 때문. 대신 자체 정규식(`_UNSAFE`)으로 경로 조작 문자만
  제거하고 한글은 보존.
- Flask `secret_key`는 프로세스 시작 시 `secrets.token_hex(32)`로 매번 새로
  생성 — 세션은 이 프로세스가 살아있는 동안만 유효(재시작하면 다시 로그인).
- AI 모드에서 전송되는 것은 "법령명 주변 발췌본"뿐이며, 원본 파일 전체나
  다른 무관한 내규 내용은 전송되지 않음(`relevant_excerpt`의 윈도우 제한).
- 서버는 `127.0.0.1`에만 바인딩 — 같은 네트워크의 다른 PC에서 접근 불가.

---

## 11. 알려진 한계 및 개선 로드맵

이 프로젝트를 더 발전시키거나 다른 도메인에 이식할 때 우선순위를 정하는 데
참고하세요.

1. **조문(article) 단위 자동 비교 없음** — 현재는 "법령 자체가 개정됐는가"까지만
   자동 확인하고, "그 개정이 실제로 우리 내규 몇 조에 영향을 주는가"는 사람이
   판단(AI 모드에서 발췌본을 보여주는 정도까지만 지원). 조문별 diff까지
   자동화하려면 `lawapi.py::body()`(본문조회 API)로 조문 텍스트를 받아와
   내규 발췌본과 구조화 비교하는 로직이 추가로 필요합니다.
2. **AI 모드 호출도 순차 처리** — 6절에서 법령 API 호출은 병렬화했지만, AI
   모드의 `compare_ai()`는 여전히 카드 행마다 순차로 Anthropic API를 호출합니다.
   대량 항목에서 AI 모드를 쓸 경우 이 부분이 다음 병목이 될 수 있습니다
   (마찬가지로 `ThreadPoolExecutor`로 병렬화 가능하되, Anthropic API 자체의
   rate limit/비용을 고려해서 동시성 수를 정해야 합니다).
3. **행정규칙 pending(시행예정) 조회 미지원** — `lawapi.pending()`은 법령
   (`target=='law'`)에만 적용됩니다. 행정규칙(고시 등)의 예고 개정을 확인하려면
   법제처 API에 해당 target이 있는지 별도 확인이 필요합니다.
4. **엑셀/한글 서식 의존도가 높음** — `_find_col`의 컬럼 탐지 조건과
   `split_domain_card`의 헤더 패턴(`_ITEM_SPLIT_RE`)이 특정 조직의 실제 서식에
   맞춰져 있습니다. 다른 조직에 이식할 때는 이 두 곳을 그 조직의 실제 문서
   샘플로 다시 맞춰야 합니다 — 나머지 파이프라인(법령 조회, 판정, UI)은
   그대로 재사용 가능합니다.
5. **단일 사용자 전제** — 동시 다중 사용자, 권한 분리, 서버 배포 같은 시나리오는
   설계 범위 밖입니다(의도적으로 "1인이 로컬에서 쓰는 오프라인 도구"로
   한정). 여러 담당자가 함께 쓰려면 workspace 폴더를 네트워크 드라이브에
   두거나, 백업 zip을 공유하는 정도가 현실적인 절충안입니다.
6. **국가법령정보센터 API의 공식 rate limit 문서 부재** — 현재 `_PAUSE=0.35초`,
   병렬 `max_workers=4`는 모두 방어적 추정값입니다. 실사용 중 429/차단이
   확인되면 낮추고, 여유가 있으면 안전하게 올려도 됩니다.

---

## 12. 다른 곳에서 "동일하거나 더 낫게" 만들 때 체크리스트

- [ ] 관리 대상 목록의 **원천 문서 서식**(엑셀 헤더, 한글 카드 섹션 마커)을
  먼저 실제 샘플로 확보하고 `extract_from_cards`/`split_domain_card`의
  패턴 매칭을 그것에 맞게 재작성한다 (이 저장소의 하드코딩된 문구는 참고용).
- [ ] 대상 법령·행정규칙이 실제로 어떤 `target`(law/admrul/eflaw)으로
  등록되는지 `python -m app.lawapi <OC키> <검색어> <target>`으로 먼저 원본
  응답을 찍어보고 확인한다(법제처 응답 키가 문서와 다를 수 있음).
- [ ] "카드에 이미 적힌 판단을 신뢰하되 기계적으로 확인 가능한 사실(공포번호
  불일치)만 우선 적용"이라는 판정 우선순위를 그대로 가져가거나, 의도적으로
  바꾼다면 그 이유를 문서화한다 — 이 순서를 바꾸면 오탐/누락 패턴이 크게
  달라짐.
- [ ] 외부망 배포가 전제라면 처음부터 PyInstaller onefile + `bundle_root()`/
  `app_root()` 분리 패턴을 적용해서, 나중에 "템플릿을 못 찾는다" 류의
  배포 버그를 원천 차단한다.
- [ ] 항목 수가 많아질 걸 예상한다면 처음부터 "고유 키 기준 중복 제거 후
  조회, 조회와 결과 조립을 분리"하는 구조(6절)로 설계한다 — 나중에 리팩터링
  하는 것보다 처음부터 이렇게 짜는 게 쉽습니다.
- [ ] AI 모드는 옵트인으로 유지하고, 어떤 데이터가 언제 외부로 전송되는지
  UI 문구로 명시한다(이 저장소의 "발췌본만, 원본은 로컬을 벗어나지 않음"
  원칙).
