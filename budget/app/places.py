"""가맹점명을 지도 API로 조회해서 업종을 알아낸다.

'알레스구떼'가 뭔지는 추측할 게 아니라 조회하면 된다. 실제로 존재하는 가게이고,
카카오맵·네이버지도에 업종이 등록돼 있다. 추측(LLM)보다 조회(지도)가 먼저다.

조회 결과의 카테고리는 계층 문자열로 온다:
    카카오  category_name  = "음식점 > 카페 > 디저트카페"
    네이버  category       = "음식점>한식>육류,고기"
이 문자열의 앞머리만 보고 우리 카테고리로 옮긴다.

  ⚠️ 카카오의 `category_group_code`(FD6/CE7/…) 표는 공식 문서가 막혀 있어
     확인하지 못했다. 그래서 코드 표에 의존하지 않고 위 계층 문자열로만 매핑한다.
     코드는 보조 힌트로만 쓴다.

한 번 조회한 상호는 캐시에 남아 다시 묻지 않는다.
키가 없으면 이 모듈은 통째로 건너뛴다 — 규칙 엔진만으로도 앱은 동작한다.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / 'data'
CACHE_FILE = DATA_DIR / 'places_cache.json'

KAKAO_URL = 'https://dapi.kakao.com/v2/local/search/keyword.json'
NAVER_URL = 'https://openapi.naver.com/v1/search/local.json'

TIMEOUT = 6


class PlacesUnavailable(Exception):
    """키가 없거나 requests가 없는 경우. 조회를 건너뛰고 진행하면 된다."""


# ---------------------------------------------------------------- 상호명 정제
_CORP = re.compile(r'(주식회사|유한회사|합자회사|\(주\)|（주）|㈜|\(유\)|주\)|\bCo\.?,?\s?Ltd\.?\b)', re.I)
_BRANCH = re.compile(r'\s*\S{0,12}(점|지점|본점|직영점)$')
_NOISE = re.compile(r'[_#·]|\d+F|\s{2,}')
_PAREN = re.compile(r'[(（][^)）]*[)）]?')   # '객기(gaekgi)', '곳간 (전세계 F&B)'


def normalize(name: str) -> list[str]:
    """가맹점명 → 조회에 쓸 후보 문자열들 (좋은 것부터).

    카드 명세서 상호는 법인격·지점·결제대행사가 뒤섞여 있다:
        '(주)빅바이트컴퍼니 잠바주스 서울역점'  → '잠바주스'
        '엔제리너스잠실샤롯데1F점/롯데컬처웍스(주' → '엔제리너스잠실샤롯데'
        '피터팬/주식회사피터팬식품'              → '피터팬'
    한 번에 정답을 만들 수 없으니 후보를 여러 개 만들어 순서대로 조회한다.
    """
    raw = (name or '').strip()
    if not raw:
        return []

    # '/'로 붙은 결제대행사·법인명은 법인격이 없는 쪽을 택한다
    parts = [p.strip() for p in raw.split('/') if p.strip()]
    if len(parts) > 1:
        plain = [p for p in parts if not _CORP.search(p)]
        base = plain[0] if plain else parts[0]
    else:
        base = raw

    base = _CORP.sub(' ', base)
    base = _PAREN.sub(' ', base)
    base = _NOISE.sub(' ', base)
    base = re.sub(r'\s+', ' ', base).strip(' -,')
    base = re.sub(r'\s+점$', '', base)   # '1F점'에서 층 표기를 지운 뒤 남는 '점'

    candidates = [base]

    without_branch = _BRANCH.sub('', base).strip()
    if without_branch and without_branch != base:
        candidates.append(without_branch)

    # '(주)씨앤코컴퍼니 블루포트 연세대과학' 처럼 앞에 법인명이 남는 경우가 많다.
    # 진짜 상호가 가운데 있을 수 있으니 앞을 하나씩 떼면서 후보를 만든다.
    tokens = (without_branch or base).split()
    for drop in range(1, len(tokens)):
        candidates.append(' '.join(tokens[drop:]))
    if len(tokens) > 1:
        candidates.append(tokens[-1])
        candidates.append(tokens[0])

    seen, out = set(), []
    for c in candidates:
        c = c.strip()
        if len(c) >= 2 and c not in seen:
            seen.add(c)
            out.append(c)
    return out


# ------------------------------------------------------------ 업종 → 카테고리
# 계층을 **뒤에서부터**(구체적인 것부터) 본다.
#   '서비스,산업 > 자동차 > 주차장' 을 앞머리로만 보면 '서비스,산업'에 걸려
#   엉뚱한 카테고리가 된다. 실제로 그 버그가 났다. 마지막 칸이 진짜 업종이다.
SEGMENT_MAP = [
    ('주차장',     '교통'),
    ('주유',       '자동차'),
    ('충전소',     '자동차'),
    ('세차',       '자동차'),
    ('편의점',     '생활·마트'),
    ('마트',       '생활·마트'),
    ('슈퍼',       '생활·마트'),
    ('미용',       '생활·마트'),
    ('세탁',       '생활·마트'),
    ('약국',       '의료·건강'),
    ('병원',       '의료·건강'),
    ('의원',       '의료·건강'),
    ('한의원',     '의료·건강'),
    ('치과',       '의료·건강'),
    ('영화',       '문화·여가'),
    ('공연',       '문화·여가'),
    ('전시',       '문화·여가'),
    ('서점',       '문화·여가'),
    ('헬스',       '문화·여가'),
    ('호텔',       '여행·숙박'),
    ('모텔',       '여행·숙박'),
    ('펜션',       '여행·숙박'),
    ('게스트하우스', '여행·숙박'),
    ('지하철',     '교통'),
    ('학원',       '교육'),
    ('카페',       '식비·외식'),
    ('백화점',     '쇼핑'),
]

# 최상위 칸으로 판단하는 표 (구체적인 칸에서 못 잡았을 때)
TOP_MAP = [
    ('음식점',     '식비·외식'),
    ('카페',       '식비·외식'),
    ('가정,생활',  '생활·마트'),
    ('생활,편의',  '생활·마트'),
    ('의료,건강',  '의료·건강'),
    ('문화,예술',  '문화·여가'),
    ('문화시설',   '문화·여가'),
    ('스포츠,레저', '문화·여가'),
    ('여가,오락',  '문화·여가'),
    ('숙박',       '여행·숙박'),
    ('여행',       '여행·숙박'),
    ('관광,명소',  '여행·숙박'),
    ('교통,수송',  '교통'),
    ('자동차',     '자동차'),
    ('교육,학문',  '교육'),
    ('학교',       '교육'),
    ('쇼핑,유통',  '쇼핑'),
    ('금융,보험',  '세금·기타'),
    ('부동산',     '주거·관리비'),
]


def map_category(category_text: str) -> str | None:
    """'음식점 > 한식 > 육류,고기' → '식비·외식'. 못 옮기면 None."""
    if not category_text:
        return None
    segments = [s.strip() for s in category_text.split('>') if s.strip()]
    if not segments:
        return None

    # 1) 구체적인 칸부터 (뒤에서 앞으로)
    for segment in reversed(segments):
        for keyword, ours in SEGMENT_MAP:
            if keyword in segment:
                return ours

    # 2) 최상위 칸
    for keyword, ours in TOP_MAP:
        if segments[0].startswith(keyword):
            return ours
    return None


# ------------------------------------------------------------------- 조회
def _get(url, headers, params):
    try:
        import requests
    except ImportError as e:
        raise PlacesUnavailable('requests 패키지가 설치되어 있지 않습니다.') from e
    response = requests.get(url, headers=headers, params=params, timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()


def lookup_kakao(query: str, rest_key: str) -> dict | None:
    data = _get(KAKAO_URL, {'Authorization': f'KakaoAK {rest_key}'},
                {'query': query, 'size': 5})
    for doc in data.get('documents', []):
        text = doc.get('category_name') or ''
        ours = map_category(text)
        if ours:
            return {'category': ours, 'source': 'kakao',
                    'place': doc.get('place_name'), 'raw': text}
    return None


def lookup_naver(query: str, client_id: str, client_secret: str) -> dict | None:
    data = _get(NAVER_URL,
                {'X-Naver-Client-Id': client_id, 'X-Naver-Client-Secret': client_secret},
                {'query': query, 'display': 5})
    for item in data.get('items', []):
        text = item.get('category') or ''
        ours = map_category(text)
        if ours:
            title = re.sub(r'<[^>]+>', '', item.get('title', ''))
            return {'category': ours, 'source': 'naver', 'place': title, 'raw': text}
    return None


class PlaceLookup:
    """상호 → 업종 조회기. 캐시를 들고 있어서 같은 상호는 한 번만 조회한다."""

    def __init__(self, kakao_key=None, naver_id=None, naver_secret=None, cache=None):
        self.kakao_key = kakao_key
        self.naver_id = naver_id
        self.naver_secret = naver_secret
        self.cache = cache if cache is not None else _load_cache()

    @property
    def enabled(self) -> bool:
        return bool(self.kakao_key or (self.naver_id and self.naver_secret))

    def lookup(self, merchant: str) -> dict | None:
        """조회 결과 dict 또는 None. 캐시에 '없음'도 기록해 재조회를 막는다."""
        if merchant in self.cache:
            return self.cache[merchant] or None
        if not self.enabled:
            raise PlacesUnavailable(
                '지도 API 키가 없습니다. 카카오 REST 키 또는 네이버 검색 API 키를 설정하세요.'
            )

        found = None
        for query in normalize(merchant):
            found = self._try(query)
            if found:
                found['query'] = query
                break

        self.cache[merchant] = found or {}
        return found

    def _try(self, query: str) -> dict | None:
        if self.kakao_key:
            try:
                hit = lookup_kakao(query, self.kakao_key)
                if hit:
                    return hit
            except Exception:
                pass   # 한 상호 조회 실패로 전체가 멈추면 안 된다
        if self.naver_id and self.naver_secret:
            try:
                return lookup_naver(query, self.naver_id, self.naver_secret)
            except Exception:
                pass
        return None

    def save(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(
            json.dumps(self.cache, ensure_ascii=False, indent=1), encoding='utf-8'
        )


def _load_cache() -> dict:
    if CACHE_FILE.exists():
        return json.loads(CACHE_FILE.read_text(encoding='utf-8'))
    return {}


def apply(transactions, engine, lookup: PlaceLookup, progress=None) -> dict:
    """미분류 거래를 지도 조회로 분류한다.

    반환: {'applied': [...], 'not_found': [...]}
    지도에서 못 찾은 것만 다음 단계(Claude)로 넘어간다.
    """
    pending = sorted({t.content for t in transactions if t.category == '미분류'})
    applied, missing = [], []

    for i, merchant in enumerate(pending, start=1):
        hit = lookup.lookup(merchant)
        if hit:
            engine.learn(merchant, hit['category'])
            applied.append({'content': merchant, **hit})
        else:
            missing.append(merchant)
        if progress:
            progress(i, len(pending))

    lookup.save()
    if applied:
        engine.save_user_rules()
        engine.classify_all(transactions)
    return {'applied': applied, 'not_found': missing}
