"""남은 미분류를 Claude에게 물어보는 모듈 (선택 기능).

규칙 엔진으로 342건 → 132건까지 줄였지만, 남은 건 '알레스구떼', '홍대삭',
'가또블랑코' 같은 개별 상호다. 상호 이름은 무한해서 키워드 목록으로는 못 잡는다.
사람은 보면 아는 것 — 이런 건 LLM이 잘한다.

동작 방식:
  - 미분류 '고유 상호'만 뽑아서 한 번에 넘긴다 (거래 132건 → 상호 103개)
  - 결과를 user_rules.json에 저장 → 다음 달 파일부터는 API 없이 자동 분류
  - 즉, 상호 하나당 평생 한 번만 물어본다

개인정보:
  상호명만 보낸다. 금액·날짜·계좌번호·카드번호는 나가지 않는다.
  API 키가 없으면 이 모듈은 그냥 건너뛴다 (규칙 엔진만으로도 동작한다).
"""
from __future__ import annotations

import json
import os

from . import categories as cat

DEFAULT_MODEL = 'claude-opus-5'
BATCH_SIZE = 60          # 한 요청에 넘길 상호 개수
MIN_CONFIDENCE = 0.7     # 이 아래는 자동 확정하지 않고 사용자에게 물어본다

# 분류 대상은 지출 카테고리만 — 수입/이체로 잘못 옮기는 사고를 구조적으로 막는다
_ALLOWED = [c for c in cat.ORDER
            if cat.CATEGORIES[c] in (cat.FIXED, cat.VARIABLE) and c != '미분류']

_SYSTEM = """당신은 한국 가계부의 카드 결제 내역을 분류합니다.

입력은 카드 명세서에 찍힌 가맹점명입니다. 상호만 보고 어떤 지출인지 판단하세요.
- '독일빵집' → 식비·외식, '연희김밥' → 식비·외식
- '지에스25 연희본점' → 생활·마트
- '웨스턴동물의료센터' → 의료·건강

판단 규칙:
- 상호에 지점명·법인격((주), 주식회사, 유한회사)이 붙어 있으면 떼고 본체로 판단하세요.
- 정말 모르겠으면 confidence를 낮게 주세요. 찍지 마세요 — 낮은 확신은 사용자에게 물어봅니다.
- 사람 이름처럼 보이는 것(송금으로 추정)은 confidence를 0.3 이하로 주세요."""

_SCHEMA = {
    'type': 'object',
    'properties': {
        'results': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'merchant': {'type': 'string', 'description': '입력받은 가맹점명 그대로'},
                    'category': {'type': 'string', 'enum': _ALLOWED},
                    'confidence': {'type': 'number', 'description': '0.0~1.0'},
                },
                'required': ['merchant', 'category', 'confidence'],
                'additionalProperties': False,
            },
        },
    },
    'required': ['results'],
    'additionalProperties': False,
}


class AiUnavailable(Exception):
    """API 키가 없거나 SDK가 설치되지 않은 경우. 규칙 엔진만으로 계속 진행하면 된다."""


def available(api_key: str | None = None) -> bool:
    if not (api_key or os.environ.get('ANTHROPIC_API_KEY')):
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


def classify_merchants(merchants, api_key=None, model=DEFAULT_MODEL, progress=None):
    """가맹점명 목록 -> {상호: (카테고리, 확신도)}.

    progress(done, total)를 넘기면 배치가 끝날 때마다 호출한다.
    """
    key = api_key or os.environ.get('ANTHROPIC_API_KEY')
    if not key:
        raise AiUnavailable(
            'Claude API 키가 없습니다. 설정 화면에 키를 넣거나, AI 분류 없이 진행하세요.'
        )
    try:
        from anthropic import Anthropic
    except ImportError as e:
        raise AiUnavailable('anthropic 패키지가 설치되어 있지 않습니다.') from e

    client = Anthropic(api_key=key)
    unique = sorted({m.strip() for m in merchants if m and m.strip()})
    out: dict[str, tuple[str, float]] = {}

    for start in range(0, len(unique), BATCH_SIZE):
        batch = unique[start:start + BATCH_SIZE]
        out.update(_ask(client, batch, model))
        if progress:
            progress(min(start + BATCH_SIZE, len(unique)), len(unique))
    return out


def _ask(client, batch, model) -> dict[str, tuple[str, float]]:
    listing = '\n'.join(f'{i + 1}. {m}' for i, m in enumerate(batch))
    response = client.messages.create(
        model=model,
        max_tokens=16000,
        system=_SYSTEM,
        output_config={
            'effort': 'low',
            'format': {'type': 'json_schema', 'schema': _SCHEMA},
        },
        messages=[{
            'role': 'user',
            'content': f'다음 가맹점을 각각 분류해 주세요. 빠짐없이 {len(batch)}건 모두 답하세요.\n\n{listing}',
        }],
    )
    if response.stop_reason == 'refusal':
        return {}

    text = next((b.text for b in response.content if b.type == 'text'), '')
    if not text:
        return {}

    parsed = json.loads(text)
    result = {}
    for row in parsed.get('results', []):
        merchant = row.get('merchant', '').strip()
        category = row.get('category')
        if merchant and category in cat.CATEGORIES:
            result[merchant] = (category, float(row.get('confidence', 0)))
    return result


def apply(transactions, engine, api_key=None, model=DEFAULT_MODEL,
          min_confidence=MIN_CONFIDENCE, progress=None) -> dict:
    """미분류 거래를 AI로 분류하고, 확신도가 높은 것만 규칙으로 굳힌다.

    반환: {'applied': [...], 'unsure': [...]} — unsure는 정리 화면에서 사용자가 확정한다.
    """
    pending = sorted({t.content for t in transactions if t.category == '미분류'})
    if not pending:
        return {'applied': [], 'unsure': []}

    guesses = classify_merchants(pending, api_key=api_key, model=model, progress=progress)

    applied, unsure = [], []
    for merchant, (category, confidence) in guesses.items():
        row = {'content': merchant, 'category': category, 'confidence': confidence}
        if confidence >= min_confidence:
            engine.learn(merchant, category)
            applied.append(row)
        else:
            unsure.append(row)

    if applied:
        engine.save_user_rules()
        engine.classify_all(transactions)   # 새 규칙으로 다시 분류
    unsure.sort(key=lambda r: -r['confidence'])
    return {'applied': applied, 'unsure': unsure}
