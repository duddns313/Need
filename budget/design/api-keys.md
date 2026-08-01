# API 키 발급 가이드

가계부의 미분류 정리에 쓰는 키 3종. **셋 다 없어도 앱은 동작한다** (미분류가 남을 뿐).

| 키 | 쓰는 곳 | 비용 | 필요도 |
|---|---|---|---|
| **카카오 REST API** | 상호 → 업종 조회 | 무료 | **이거 하나면 충분** |
| 네이버 검색 API | 위와 동일 (대체제) | 무료 | 카카오 안 되면 |
| Claude API | 지도에 없는 상호 | 유료(소액) | 선택 |

발급 후 반드시 점검 스크립트로 확인:

```bash
python budget/tools/check_keys.py
```

---

## 1. 카카오 REST API 키 (권장)

소요 5분. 카카오계정만 있으면 된다.

### 단계

1. **[developers.kakao.com](https://developers.kakao.com)** 접속 → 우측 상단 **로그인** (카카오계정)
2. 상단 **내 애플리케이션** → **애플리케이션 추가하기**
3. 입력하고 **저장**
   - **앱 이름**: `부부가계부` (아무거나)
   - **사업자명**: 개인이면 본인 이름
   - 약관 동의 체크
4. 생성된 앱을 클릭 → 좌측 **앱 키** → **REST API 키** 복사
   - ⚠️ **REST API 키**를 복사해야 한다. JavaScript 키·네이티브 앱 키는 안 된다 (401 남)
5. 좌측 **앱 설정 > 플랫폼** → **Web 플랫폼 등록**
   - 사이트 도메인: `http://localhost:8734`
   - 앱이 내 PC에서 도니까 localhost로 충분하다
6. 좌측 **제품 설정 > 카카오맵** → **사용 설정 ON**
   - **이 단계를 빼먹으면 403이 난다.** 2024년 12월 1일부터 신규 앱은 이 설정이 필수다.
   - 2026년 7월 21일부터는 **심사 없이 켜는 즉시** 사용 가능하다 (예전엔 심사 대기가 있었다).

### 확인

```bash
export KAKAO_REST_KEY="복사한_REST_API_키"
python budget/tools/check_keys.py
```

성공하면 이렇게 나온다:

```
[카카오 로컬 API]
  통과 조회 성공 — 5건
       스타벅스 연희점  |  음식점 > 카페 > 커피전문점 > 스타벅스  →  식비·외식
```

### 안 될 때

| 증상 | 원인 | 조치 |
|---|---|---|
| `401` | 키를 잘못 복사 | **REST API 키**인지 확인 (JavaScript 키 아님) |
| `403` | 카카오맵 사용 설정 꺼짐 | 위 6단계 |
| 결과 0건 | 검색어 문제 | 다른 상호로 재시도 |

---

## 2. 네이버 검색 API (카카오 대신 쓸 때)

소요 5분. 네이버 계정 필요. 일 25,000회 무료.

### 단계

1. **[developers.naver.com](https://developers.naver.com)** 접속 → **로그인** (네이버 계정)
2. 상단 **Application > 애플리케이션 등록**
3. 입력하고 **등록하기**
   - **애플리케이션 이름**: `부부가계부`
   - **사용 API**: **검색** 선택 ← 이게 핵심. 안 고르면 403
   - **비로그인 오픈 API 서비스 환경**: **WEB 설정** 선택
   - **웹 서비스 URL**: `http://localhost:8734`
4. 등록 완료 화면에서 **Client ID** 와 **Client Secret** 복사
   - Secret은 **보기** 버튼을 눌러야 보인다

### 확인

```bash
export NAVER_CLIENT_ID="발급받은_ID"
export NAVER_CLIENT_SECRET="발급받은_Secret"
python budget/tools/check_keys.py
```

### 안 될 때

| 증상 | 원인 | 조치 |
|---|---|---|
| `401` | ID 또는 Secret 오타 | 다시 복사 |
| `403` | 애플리케이션에 검색 API 미추가 | 내 애플리케이션 > API 설정 > **검색** 추가 |

---

## 3. Claude API 키 (선택)

지도에 없는 상호(온라인 결제, 폐업, 무인점포)까지 처리하고 싶을 때만.
**선불 크레딧이 필요하다** — 무료 체험은 없다.

### 단계

1. **[console.anthropic.com](https://console.anthropic.com)** 접속 → 가입/로그인
2. 좌측 **Billing** → **Add credits** → 카드 등록 후 충전
   - 최소 금액이면 충분하다. 상호 107개 분류에 드는 비용은 몇십 원 수준이다.
3. 좌측 **API keys** → **Create Key** → 이름 입력 → 생성
4. `sk-ant-...` 로 시작하는 키 복사
   - ⚠️ **생성 직후 한 번만 보인다.** 창을 닫으면 다시 못 본다. 바로 저장할 것.

### 확인

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
python budget/tools/check_keys.py
```

---

## 키를 어디에 넣나

### Windows (PowerShell)

한 번만 설정하면 계속 유지된다:

```powershell
setx KAKAO_REST_KEY "복사한_키"
```

설정 후 **PowerShell 창을 새로 열어야** 반영된다. 확인:

```powershell
echo $env:KAKAO_REST_KEY
```

### macOS / Linux

`~/.zshrc` (또는 `~/.bashrc`) 끝에 추가:

```bash
export KAKAO_REST_KEY="복사한_키"
```

저장 후:

```bash
source ~/.zshrc
```

### 앱 설정 화면 (구현 예정)

환경변수가 번거로우면 앱의 설정 화면에 붙여넣는 방식도 만든다.
키는 `budget/data/` 아래에 저장하고, **git에는 올라가지 않는다** (`.gitignore` 처리됨).

---

## 키 관리 주의

- **키를 남에게 보여주지 말 것.** 특히 화면 캡처를 공유할 때 주의.
- 실수로 공개된 것 같으면 각 콘솔에서 **재발급**하면 이전 키는 즉시 무효가 된다.
- 이 저장소는 `budget/data/`와 `budget/rules/user_rules.json`을 git에서 제외한다.

---

## 확인하지 못한 것 (직접 봐야 하는 부분)

이 가이드는 검색 결과를 근거로 썼다. **카카오·네이버 공식 개발자 문서가 자동 접근을 차단(403)해서
화면을 직접 보고 쓰지 못했다.** 그래서 다음은 실제 화면에서 확인이 필요하다:

- 메뉴 이름이 조금 다를 수 있다 (`앱 설정` vs `일반`, `제품 설정` vs `카카오맵` 위치)
- 카카오 `category_group_code` 표(FD6=음식점 등)는 확인하지 못했다
  → 코드에서는 이 표를 **쓰지 않는다.** 대신 `category_name` 계층 문자열로 매핑한다
- 실제 응답 필드명은 첫 호출 때 `check_keys.py`가 출력하니 그때 확인하면 된다

메뉴가 다르면 알려주면 이 문서를 고친다.

## 참고

- [카카오 로컬 API 문서](https://developers.kakao.com/docs/ko/local/dev-guide)
- [카카오맵 신규 API 및 이용 절차 개선 공지](https://devtalk.kakao.com/t/api-4/150764)
- [네이버 검색 API 문서](https://developers.naver.com/docs/serviceapi/search/local/local.md)
- [Claude Console](https://console.anthropic.com)
