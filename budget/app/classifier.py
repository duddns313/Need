"""분류 엔진 — 뱅샐 거래를 우리 카테고리로 옮긴다.

뱅샐 원본은 그대로 쓰면 가계부가 안 된다. 실제 파일을 보면
카드대금·계좌이체·페이충전이 전부 '지출'로 잡혀 있고, 지출의 26%가 '미분류'다.
그 두 가지를 여기서 해결한다.

우선순위 (처음 맞는 규칙 채택):
  1. 사용자가 화면에서 확정한 규칙 (내용 완전일치)
  2. 확인해서 넣어둔 상호 표 (웹 검색·알려진 체인)
  3. 거래 방향 — 수입 거래엔 수입 카테고리만 붙인다
  4. 제외 키워드 (카드대금·페이충전)
  5. 내용 키워드 규칙
  6. 업종 접미사 정규식
  7. 뱅샐 대/소분류 매핑
  8. 남으면 미분류
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import categories as cat

RULES_DIR = Path(__file__).resolve().parent.parent / 'rules'
DEFAULT_RULES = RULES_DIR / 'default_rules.json'
USER_RULES = RULES_DIR / 'user_rules.json'


# 사람 이름 모양. 카카오페이는 가운데를 가려서 '유*란' 처럼 온다.
# 성 한 자 + 이름 한두 자, 별표 섞임까지만 사람으로 본다. 넉 자를 넘으면
# 가게 이름일 가능성이 커져서 함부로 사람이라고 하지 않는다.
PERSON_NAME = re.compile(r'^[가-힣][가-힣*]{1,3}$')

# 사람 이름 길이인데 사람이 아닌 말들. '자동이체'·'용돈정산'·'정산'이
# 넉 자라서 사람으로 잡혀 800만원이 남에게 부친 돈이 될 뻔했다.
MONEY_WORDS = ('이체', '정산', '입금', '출금', '송금', '환급', '환불', '충전', '결제',
               '취소', '회수', '잔여', '잔액', '카드', '급여', '용돈', '대출', '상환',
               '이자', '예금', '적금', '수수료', '요금', '비용', '통장', '계좌', '페이',
               '보험', '세금', '월세', '관리')


class Classifier:
    def __init__(self, rules: dict | None = None, user_rules: dict | None = None,
                 user_rules_path: Path | None = None):
        self.rules = rules if rules is not None else _load(DEFAULT_RULES)
        self.user_rules_path = Path(user_rules_path) if user_rules_path else USER_RULES
        self.user_rules = (user_rules if user_rules is not None
                           else _load(self.user_rules_path, default={}))
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

        # 1-2. 확인해서 넣어둔 상호 표 (웹 검색·알려진 체인)
        known = self.rules.get('merchants', {}).get(content)
        if known:
            return known, 'merchant'

        # 1-3. 본인·배우자 사이 이체 — 돈이 옮겨간 것이지 쓴 것이 아니다.
        #      방향 판정보다 먼저 해야 한다. 안 그러면 배우자가 보낸 돈이
        #      '정기적으로 들어오는 큰 수입'으로 보여 급여로 승격된다
        #      (실제로 1,260만원이 소득으로 부풀려져 있었다).
        #      다만 이름만 보고 판단하면 안 되는 자리가 있다. 통신비·전기요금은
        #      명의자 이름을 달고 나간다. 실제 파일의 'SKT 이호현', '한전(이호현',
        #      'SKT 윤영운' 15만원이 그래서 이체로 빠져 있었다. 청구서 이름이
        #      보이면 이건 이체가 아니라 요금이니 아래 키워드 규칙으로 넘긴다.
        family = self.rules.get('family_names', {})
        if not self._is_bill(content):
            for name in family.get('spouse', []):
                if name and name in content:
                    return '부부간이체', f'family:{name}'
            for name in family.get('self', []):
                if name and name in content:
                    return '내계좌이체', f'family:{name}'
            # 가족(부모·형제 등)에게 보낸 돈 — 내 계좌 사이를 옮긴 게 아니라
            # 실제로 집 밖으로 나가는 돈이라 지출로 센다. 이 사람들이 보내 준
            # 돈까지 지출로 잡으면 안 되므로 나가는 것만 본다.
            if tx.bs_type == '지출' or (tx.bs_type == '이체' and not tx.inflow):
                for name in family.get('family', []):
                    if name and name in content:
                        return '가족·용돈', f'family:{name}'

        # 1-4. 대출 실행금 — 들어온 돈이지만 소득이 아니다
        for kw in self.rules.get('loan_keywords', []):
            if kw in content:
                return '대출실행', f'loan:{kw}'

        # 2. 방향을 먼저 존중한다.
        #    돈이 들어온 거래에 '교통' 같은 지출 카테고리를 붙이면 안 된다.
        #    (실제로 '공항철도(주)'가 준 급여 3,400만원이 지출 키워드에 걸려
        #     교통비로 잡히는 사고가 났다.)
        if tx.bs_type == '수입':
            return self._decide_income(content)

        # 2-2. '이체'인데 돈이 들어온 줄. 지출 규칙을 태우면 안 된다.
        #      뱅샐의 '이체'는 들어온 것과 나간 것이 한 칸에 섞여 있고,
        #      금액의 부호만이 방향을 알려준다. 그걸 무시했더니 아내 파일에서
        #      '스마트에코'가 '마트'에 걸려 3,632만원의 입금이 식료품으로,
        #      계좌에 들어온 3,000만원이 저축으로 잡혀 있었다.
        if tx.bs_type == '이체' and tx.inflow:
            return self._decide_transfer_in(content)

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
            if not self._matches(tx, rule['match']):
                continue
            # 뱅샐 분류를 그대로 믿으면 안 되는 자리가 하나 있다. 실제 파일에서
            # '보관이사비', '관리비', '산후조리원' 세 건이 뱅샐 쪽에
            # '금융/이자/대출'로 들어와 대출이자로 잡혀 있었다. 대출이자는
            # 고정비라 진단·처방에 그대로 얹히는데, 셋 다 대출과 무관하다.
            # 이름에 금융기관이나 이자라는 말이 없으면 대출이자로 안 본다.
            if rule['category'] == '대출이자':
                words = self.rules.get('loan_interest_keywords', [])
                if not any(w in content for w in words):
                    return '미분류', 'banksalad:이자인데-이름이-금융기관이-아님'
            if rule['category'] == '내계좌이체':
                return self._own_or_person(content,
                                           'banksalad:' + '/'.join(rule['match']))
            return rule['category'], 'banksalad:' + '/'.join(rule['match'])

        # 7. 남은 것
        if tx.bs_type == '이체':
            return self._own_or_person(content, 'fallback:transfer')
        return '미분류', 'fallback'

    def _is_pg(self, content: str) -> bool:
        """결제를 대신 받아 주는 회사(PG)인가 — 가게가 아니라 통로다."""
        name = content.replace('(주)', '').replace('㈜', '').replace('주식회사', '').strip()
        return any(name == p for p in self.rules.get('pg_names', []))

    def _own_or_person(self, content: str, why: str) -> tuple[str, str]:
        """'내계좌이체'라고 부르기 직전에, 정말 내 계좌인지 한 번 본다.

        뱅샐은 '지출/금융/은행'과 '이체/이체' 두 칸에 은행 통로로 나간 돈을
        전부 몰아넣는다. 여기에는 내 계좌 사이를 옮긴 것도 있지만 남에게 부친
        돈도 섞여 있다. 그대로 두면 '안 쓴 돈'이 되어 저축률이 부풀려진다.
        실제 파일에서 그렇게 594만원이 사라져 있었다.

        결제대행사(PG)도 여기로 떨어진다. 네이버파이낸셜로 나간 돈은 은행
        통로를 탔을 뿐 실제로는 무언가를 산 것이다. 이체로 묻으면 안 쓴 돈이
        되고, 그렇다고 가게 이름을 아는 것도 아니니 미분류로 올려 둔다.
        (뱅샐이 '온라인쇼핑'처럼 쓸 만한 분류를 준 건은 여기까지 안 온다.)
        """
        if self._is_pg(content):
            return '미분류', 'pg:가게이름-대신-대행사'
        kind, tag = self._person_transfer(content)
        return (kind, tag) if kind else ('내계좌이체', why)

    def _decide_transfer_in(self, content: str) -> tuple[str, str]:
        """계좌로 들어온 이체. 번 돈도 쓴 돈도 아닌 것이 대부분이다.

        내 계좌 사이를 옮긴 것이 기본이다. 다만 사람 이름으로 들어온 것은
        같이 먹고 나눠 낸 돈(정산)이라 따로 표시한다 — 번 돈으로 세면 소득이,
        쓴 돈으로 세면 지출이 부풀려진다. 어느 쪽도 아니다.
        """
        for kw in self.rules.get('income_finance_keywords', []):
            if kw in content:
                return '금융소득', f'income:{kw}'
        kind, tag = self._person_transfer(content)
        if kind == '개인송금':
            return '정산받음', tag.replace('person:', 'settle-in:')
        return '내계좌이체', 'transfer-in'

    def _is_bill(self, content: str) -> bool:
        """청구서 이름인가 — 통신사·한전처럼 명의자 이름을 달고 나가는 것."""
        return any(kw in content for kw in self.rules.get('bill_keywords', []))

    def _person_transfer(self, content: str) -> tuple[str, str]:
        """받는 사람 이름으로만 남은 출금을 가른다. 못 가르면 빈 값을 준다."""
        name = ' '.join(content.split())

        # 카카오페이는 사업자 이름을 '박소영(소마나스**' 처럼 별 두 개로 가린다.
        # 사람한테 부친 게 아니라 가게에서 산 것인데 무슨 가게인지가 안 남는다.
        # 이체로 묻어 두면 영영 안 보이니 미분류로 올려 눈에 걸리게 한다.
        if '**' in name:
            return '미분류', 'pay:가게이름-가려짐'

        # 받는 사람이 아예 안 남은 송금. 24건 280만원이 이렇게 있었다.
        if name in ('송금 내역', '송금내역'):
            return '개인송금', 'pay:받는사람-없음'
        if name in ('송금 취소 내역', '송금취소 내역'):
            return '환불·취소', 'pay:송금취소'

        # 은행·페이 이름이 앞에 붙는 경우가 있다 ('토스최은유').
        for pre in self.rules.get('bank_prefixes', []):
            if name.startswith(pre) and len(name) > len(pre):
                name = name[len(pre):].strip()
                break

        if not PERSON_NAME.match(name) or any(w in name for w in MONEY_WORDS):
            return '', ''
        family = self.rules.get('family_names', {})
        if any(name == n for group in family.values() for n in group):
            return '', ''
        return '개인송금', 'person:' + name

    def _patterns(self):
        """정규식은 한 번만 컴파일해서 재사용한다 (거래 수천 건 × 규칙 수)."""
        if self._compiled is None:
            self._compiled = [
                {'category': r['category'], 'regex': re.compile(r['pattern'])}
                for r in self.rules.get('content_patterns', [])
            ]
        return self._compiled

    def _decide_income(self, content: str) -> tuple[str, str]:
        """들어온 돈을 가른다. 먼저 '소득이 아닌 입금'부터 걷어낸다.

        통장에 찍힌 입금을 전부 소득으로 세면 저축률이 부풀려진다. 실제 파일에서
        '8월 카드값'·'카드비용'·'호현3월카드'로 들어온 585만원이 부수입으로 잡혀
        있었는데, 이건 카드값을 메우려고 배우자가 보낸 돈이다. 나가는 카드대금은
        이미 지출에서 빼 놓았으니, 들어오는 쪽도 빼야 앞뒤가 맞는다.
        """
        # 환불이 먼저다. '씨티카드환급'은 카드가 아니라 환불로 읽어야 한다.
        for kw in self.rules.get('income_refund_keywords', []):
            if kw in content:
                return '환불·취소', f'refund:{kw}'
        # 급여가 카드보다 먼저다. 카드사에 다니는 사람의 월급을 뺏으면 안 된다.
        for kw in self.rules.get('income_salary_keywords', []):
            if kw in content:
                return '급여', f'income:{kw}'
        for kw in self.rules.get('income_card_keywords', []):
            if kw in content:
                return '카드대금', f'card-in:{kw}'
        for kw in self.rules.get('income_finance_keywords', []):
            if kw in content:
                return '금융소득', f'income:{kw}'
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
        self.user_rules_path.parent.mkdir(parents=True, exist_ok=True)
        self.user_rules_path.write_text(
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


LOAN_MIN_AMOUNT = 1_000_000     # 이보다 작은 대출은 금액 일치가 우연일 수 있다


def detect_loan_disbursements(transactions, loans) -> list[dict]:
    """대출 원금과 금액이 딱 맞는 입금을 찾아 '대출실행'으로 내린다.

    대출금은 통장에 들어오지만 소득이 아니라 빚이다. 이걸 수입으로 세면
    저축률이 실제보다 높게 나와서, 가계가 잘 굴러가는 줄 착각하게 된다.

    문제는 이름으로 못 잡는 경우다. 실제 파일에는 '351-004-618299' 처럼
    계좌번호만 찍힌 입금 1,210만원이 있었는데, 같은 파일의 뱅샐현황 시트에
    원금 1,210만원짜리 대출이 그대로 적혀 있었다. 이름은 못 읽어도
    **금액이 정확히 일치**하면 우연으로 보기 어렵다.

    되돌릴 수 있게, 무엇을 왜 내렸는지 목록으로 돌려준다.
    """
    principals = {}
    for loan in loans or []:
        amount = (loan.extra or {}).get('원금') or loan.amount
        if amount and amount >= LOAN_MIN_AMOUNT:
            principals.setdefault(int(amount), loan.name)

    moved, accounts = [], set()
    for tx in transactions:
        if tx.nature != cat.INCOME or tx.amount not in principals:
            continue
        tx.category = '대출실행'
        tx.nature = cat.EXCLUDED
        tx.rule = 'loan-principal-match'
        accounts.add(_digits(tx.content))
        moved.append({
            'date': tx.date.isoformat(), 'owner': tx.owner, 'content': tx.content,
            'amount': tx.amount, 'loan': principals[tx.amount], 'kind': '실행',
        })

    # 대출금이 들어온 그 계좌로 다시 나가는 돈은 상환이다.
    # 실제 파일에선 분기마다 그 계좌로 나간 378,000원이 뱅샐 대분류만 보고
    # '온라인쇼핑'과 '문화/여가'로 흩어져 있었다. 합쳐서 1,138,000원인데,
    # 같은 파일의 대출 원금(1,210만)에서 잔액(1,096만)을 뺀 값과 정확히 같다.
    accounts.discard('')
    for tx in transactions:
        if tx.bs_type == '수입' or _digits(tx.content) not in accounts:
            continue
        tx.category = '대출원금상환'
        tx.nature = cat.SAVING
        tx.rule = 'loan-account-match'
        moved.append({
            'date': tx.date.isoformat(), 'owner': tx.owner, 'content': tx.content,
            'amount': tx.amount, 'loan': '', 'kind': '상환',
        })

    moved.sort(key=lambda r: -r['amount'])
    return moved


def _digits(content: str) -> str:
    """계좌번호만 남긴다. 'J351004618299'와 '351-004-618299'를 같은 것으로 본다.

    짧은 숫자는 우연히 겹치므로 계좌번호로 치지 않는다.
    """
    only = re.sub(r'\D', '', content or '')
    return only if len(only) >= 10 else ''


REVERSAL_DAYS = 5
REVERSAL_MIN = 10_000       # 작은 금액은 우연히 같을 수 있다


def detect_payment_reversals(transactions) -> list[dict]:
    """카드값이 나갔다 그대로 되돌아온 짝을 찾는다.

    카드사에서 결제가 취소됐다 재승인되면 같은 금액이 하루 뒤에 그대로
    입금된다. 이걸 '환불'로 보면 물건값을 돌려받은 것처럼 읽혀서,
    "그만큼 쓴 돈이 실제보다 높게 잡혀 있다"는 잘못된 안내가 나간다.
    실제로는 짝이 맞아 서로 지워지는 돈이다.

    금액이 같고 며칠 안쪽인 짝만 본다. 한 번 짝지은 거래는 다시 안 쓴다.
    """
    outs = [t for t in transactions
            if t.category == '카드대금' and t.bs_type in ('지출', '이체')
            and t.amount >= REVERSAL_MIN]
    ins = [t for t in transactions
           if t.category == '환불·취소' and t.bs_type == '수입'
           and t.amount >= REVERSAL_MIN]

    used, pairs = set(), []
    for inc in sorted(ins, key=lambda t: -t.amount):
        for out in outs:
            if id(out) in used or out.amount != inc.amount:
                continue
            if abs((out.date - inc.date).days) > REVERSAL_DAYS:
                continue
            used.add(id(out))
            for tx in (out, inc):
                tx.category = '결제취소'
                tx.nature = cat.EXCLUDED
                tx.rule = 'reversal'
            pairs.append({
                'amount': inc.amount, 'paid': out.date.isoformat(),
                'back': inc.date.isoformat(), 'content': inc.content,
            })
            break
    pairs.sort(key=lambda r: -r['amount'])
    return pairs


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


SETTLE_DAYS = 3            # 결제하고 그 자리에서 정산하는 게 보통이다
SETTLE_MIN = 500


def detect_shared_settlements(transactions, names, merchant_words=('쿠팡',)) -> list[dict]:
    """아이디를 같이 쓰는 가족이 되갚아 준 돈과, 그 짝이 되는 결제를 함께 뺀다.

    실제 파일에서 안빈·윤다운이 쿠팡 아이디를 같이 쓰고 있었다. 윤영운이 카드로
    긁고 그 자리에서 정산을 받는데, 결제 48초 뒤에 같은 금액이 들어온다.
    둘 다 남겨 두면 쓴 돈도 번 돈도 실제보다 부풀려진다. 한쪽만 빼면 더 나쁘다.

    금액이 같고 며칠 안쪽인 짝만 본다. 한 번 짝지은 결제는 다시 안 쓴다.
    짝을 못 찾은 입금은 손대지 않는다 — 여러 건을 몰아서 준 것일 수도 있고,
    쿠팡이 아닌 다른 정산일 수도 있어서 함부로 지우면 안 된다.
    """
    def is_settler(tx):
        name = (tx.content or '').split('(')[0].strip()
        return any(name == n for n in names)

    backs = [t for t in transactions if is_settler(t) and t.amount >= SETTLE_MIN]
    buys = [t for t in transactions
            if not t.inflow and t.amount >= SETTLE_MIN
            and any(w in (t.content or '') for w in merchant_words)]

    used, pairs = set(), []
    for back in sorted(backs, key=lambda t: -t.amount):
        best, gap = None, 999
        for buy in buys:
            if id(buy) in used or buy.amount != back.amount:
                continue
            d = abs((buy.date - back.date).days)
            if d <= SETTLE_DAYS and d < gap:
                best, gap = buy, d
        if best is None:
            continue
        used.add(id(best))
        for tx in (best, back):
            tx.category = '대신결제'
            tx.nature = cat.EXCLUDED
            tx.rule = f'settle:{(back.content or "").strip()}'
        pairs.append({
            'who': (back.content or '').strip(), 'amount': back.amount,
            'paid': best.date.isoformat(), 'back': back.date.isoformat(),
            'merchant': best.content,
        })
    pairs.sort(key=lambda r: -r['amount'])
    return pairs
