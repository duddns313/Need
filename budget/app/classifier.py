"""분류 엔진 — 뱅샐 거래를 우리 카테고리로 옮긴다.

뱅샐 원본은 그대로 쓰면 가계부가 안 된다. 실제 파일을 보면
카드대금·계좌이체·페이충전이 전부 '지출'로 잡혀 있고, 지출의 26%가 '미분류'다.
그 두 가지를 여기서 해결한다.

우선순위 (처음 맞는 규칙 채택):
  1. 사용자가 확정한 규칙 (내용 완전일치)
  2. 제외 키워드 (카드대금·페이충전)
  3. 내용 키워드 규칙
  4. 뱅샐 대/소분류 매핑
  5. 남으면 미분류
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import categories as cat

RULES_DIR = Path(__file__).resolve().parent.parent / 'rules'
DEFAULT_RULES = RULES_DIR / 'default_rules.json'
USER_RULES = RULES_DIR / 'user_rules.json'


class Classifier:
    def __init__(self, rules: dict | None = None, user_rules: dict | None = None):
        self.rules = rules if rules is not None else _load(DEFAULT_RULES)
        self.user_rules = user_rules if user_rules is not None else _load(USER_RULES, default={})
        self._compiled = None

    # ------------------------------------------------------------------ 분류
    def classify(self, tx) -> None:
        """거래 하나에 category/nature/rule을 채운다. 제자리에서 고친다."""
        category, rule = self._decide(tx)
        tx.category = category
        tx.nature = cat.nature_of(category)
        tx.rule = rule

    def classify_all(self, transactions) -> None:
        for tx in transactions:
            self.classify(tx)

    def _decide(self, tx) -> tuple[str, str]:
        content = tx.content or ''

        # 1. 사용자가 손으로 확정한 것 — 무조건 우선
        hit = self.user_rules.get(content)
        if hit:
            return hit, 'user'

        # 2. 방향을 먼저 존중한다.
        #    돈이 들어온 거래에 '교통' 같은 지출 카테고리를 붙이면 안 된다.
        #    (실제로 '공항철도(주)'가 준 급여 3,400만원이 지출 키워드에 걸려
        #     교통비로 잡히는 사고가 났다.)
        if tx.bs_type == '수입':
            return self._decide_income(content)

        # 3. 제외 키워드
        for kw in self.rules.get('exclude_keywords', []):
            if kw in content:
                return '카드대금' if '카드' in kw else '페이충전', f'exclude:{kw}'

        # 4. 내용 키워드 (지출·이체 거래에만)
        for rule in self.rules.get('content_rules', []):
            for kw in rule['keywords']:
                if kw.lower() in content.lower():
                    return rule['category'], f'keyword:{kw}'

        # 5. 업종 접미사 패턴 — '독일빵집', '광주횟집'처럼 상호가 무한한 경우
        for rule in self._patterns():
            if rule['regex'].search(content):
                return rule['category'], f"pattern:{rule['category']}"

        # 6. 뱅샐 대/소분류 — 긴 매치(대+소)부터 본다
        banksalad = self.rules.get('banksalad_rules', [])
        for rule in sorted(banksalad, key=lambda r: -len(r['match'])):
            if self._matches(tx, rule['match']):
                return rule['category'], 'banksalad:' + '/'.join(rule['match'])

        # 7. 남은 것
        if tx.bs_type == '이체':
            return '내계좌이체', 'fallback:transfer'
        return '미분류', 'fallback'

    def _patterns(self):
        """정규식은 한 번만 컴파일해서 재사용한다 (거래 수천 건 × 규칙 수)."""
        if self._compiled is None:
            self._compiled = [
                {'category': r['category'], 'regex': re.compile(r['pattern'])}
                for r in self.rules.get('content_patterns', [])
            ]
        return self._compiled

    def _decide_income(self, content: str) -> tuple[str, str]:
        """수입 거래는 수입 카테고리 3개(급여/부수입/금융소득) 안에서만 결정한다."""
        for kw in self.rules.get('income_finance_keywords', []):
            if kw in content:
                return '금융소득', f'income:{kw}'
        for kw in self.rules.get('income_salary_keywords', []):
            if kw in content:
                return '급여', f'income:{kw}'
        return '부수입', 'income:fallback'

    @staticmethod
    def _matches(tx, match: list[str]) -> bool:
        fields = [tx.bs_type, tx.bs_major, tx.bs_minor]
        return all(m == f for m, f in zip(match, fields))

    # ------------------------------------------------- 사용자 규칙 쌓기
    def learn(self, content: str, category: str) -> None:
        """정리 화면에서 '이건 식비' 하고 누르면 호출된다.

        같은 상호의 과거·미래 거래가 전부 이 규칙을 따르게 된다.
        """
        if category not in cat.CATEGORIES:
            raise ValueError(f'모르는 카테고리입니다: {category}')
        self.user_rules[content] = category

    def save_user_rules(self) -> None:
        USER_RULES.parent.mkdir(parents=True, exist_ok=True)
        USER_RULES.write_text(
            json.dumps(self.user_rules, ensure_ascii=False, indent=2), encoding='utf-8'
        )


SALARY_MIN_AMOUNT = 1_000_000   # 이 금액 이상이
SALARY_MIN_COUNT = 3            # 이 횟수 이상 같은 이름으로 들어오면 급여로 본다


def detect_recurring_income(transactions) -> list[dict]:
    """정기적으로 들어오는 큰 수입을 급여로 승격한다.

    뱅샐은 회사에서 들어온 월급도 '금융수입/미분류'로 던져 놓는다.
    이름·금액·횟수만 보면 급여인 게 명백한데 카테고리만 비어 있다.
    자동으로 '급여'로 올리고, 무엇을 올렸는지 화면에 보여줘 되돌릴 수 있게 한다.
    """
    groups = {}
    for tx in transactions:
        if tx.nature != cat.INCOME or tx.category == '급여':
            continue
        if tx.amount < SALARY_MIN_AMOUNT:
            continue
        groups.setdefault((tx.owner, tx.content), []).append(tx)

    promoted = []
    for (owner, content), items in groups.items():
        if len(items) < SALARY_MIN_COUNT:
            continue
        for tx in items:
            tx.category = '급여'
            tx.nature = cat.INCOME
            tx.rule = 'recurring-income'
        promoted.append({
            'owner': owner, 'content': content,
            'count': len(items), 'amount': sum(t.amount for t in items),
        })
    promoted.sort(key=lambda r: -r['amount'])
    return promoted


TRANSFER_MIN_AMOUNT = 500_000
TRANSFER_MIN_COUNT = 3


def detect_transfer_candidates(transactions) -> list[dict]:
    """상계 못 한 큰 반복 송금을 찾아 '이 사람 배우자인가요?'로 물어볼 목록을 만든다.

    파일을 한 쪽만 올렸을 때는 상계할 짝이 없어서 배우자 송금이 미분류로 남는다.
    금액이 크고 반복되면 소비가 아닐 가능성이 높으니 그냥 지출로 세지 말고 물어본다.
    """
    groups = {}
    for tx in transactions:
        if tx.category != '미분류' or tx.amount < TRANSFER_MIN_AMOUNT:
            continue
        groups.setdefault((tx.owner, _base_name(tx.content)), []).append(tx)

    out = []
    for (owner, name), items in groups.items():
        if len(items) < TRANSFER_MIN_COUNT:
            continue
        out.append({
            'owner': owner, 'content': name, 'count': len(items),
            'amount': sum(t.amount for t in items),
            'uids': [t.uid for t in items],
        })
    out.sort(key=lambda r: -r['amount'])
    return out


def _base_name(content: str) -> str:
    """'윤영운(12월)', '윤영운(월급 잔여)' 를 같은 '윤영운'으로 묶는다."""
    return content.split('(')[0].strip()


def _load(path: Path, default=None):
    if not path.exists():
        return default if default is not None else {}
    return json.loads(path.read_text(encoding='utf-8'))
