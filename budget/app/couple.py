"""부부 합산에서만 생기는 문제를 처리한다.

부부 가계부의 진짜 함정은 여기다.
남편이 아내에게 50만원 보내면 남편 파일엔 지출 50만, 아내 파일엔 수입 50만이 찍힌다.
두 파일을 그냥 합치면 쓰지도 않은 지출 50만과 벌지도 않은 수입 50만이 생긴다.
"""
from __future__ import annotations

from . import categories as cat

# 상계 후보로 볼 카테고리 (이미 소비로 분류된 건은 건드리지 않는다)
_TRANSFERISH = {'내계좌이체', '부부간이체', '미분류', '부수입', '금융소득'}

DEFAULT_DATE_TOLERANCE = 1  # 며칠 차이까지 같은 이체로 볼지


def offset_spouse_transfers(transactions, names, date_tolerance=DEFAULT_DATE_TOLERANCE):
    """부부간 이체를 찾아 양쪽 다 '부부간이체'(제외)로 바꾼다.

    names: {'남편': '이호현', '아내': '김OO'} 처럼 소유자 라벨 -> 실명.
    반환: 상계된 (보낸쪽, 받은쪽) 쌍 목록.
    """
    outgoing = [t for t in transactions if t.bs_type in ('지출', '이체') and t.category in _TRANSFERISH]
    incoming = [t for t in transactions if t.bs_type == '수입' and t.category in _TRANSFERISH]

    by_amount = {}
    for tx in incoming:
        by_amount.setdefault(tx.amount, []).append(tx)

    pairs = []
    used = set()
    for out in outgoing:
        for cand in by_amount.get(out.amount, []):
            if cand.uid in used or cand.owner == out.owner:
                continue
            if abs((cand.date - out.date).days) > date_tolerance:
                continue
            if not _looks_like_spouse_transfer(out, cand, names):
                continue
            _mark(out, cand)
            _mark(cand, out)
            used.add(cand.uid)
            pairs.append((out, cand))
            break
    return pairs


def _aliases(owner, names):
    """그 사람을 가리키는 말들. 실명과 파일에 붙인 이름을 함께 본다.

    부부끼리는 서로를 실명으로 안 적는다. 실제 파일에서 20만원짜리 두 건이
    '호현이월용' → '호현인최고야!' 로 오갔는데, 실명('이호현')만 찾다가
    양쪽 다 못 잡아 한쪽은 지출, 한쪽은 부수입으로 남아 있었다.
    성을 뗀 이름('호현')이면 충분히 특정된다.
    """
    got = []
    real = names.get(owner, '')
    if real:
        got.append(real)
        if len(real) >= 3:
            got.append(real[1:])       # 이호현 -> 호현
    if owner:
        got.append(owner)              # 파일에 붙인 이름 자체
    return [g for g in got if len(g) >= 2]


def _looks_like_spouse_transfer(out, inc, names) -> bool:
    """금액·날짜가 맞아도, 상대 이름이나 이체성 단서가 있어야 상계한다.

    이 조건이 없으면 '같은 날 같은 금액'이라는 이유로 남남인 거래가 묶인다.
    """
    text = f'{out.content} {inc.content}'
    if any(a in out.content for a in _aliases(inc.owner, names)):
        return True
    if any(a in inc.content for a in _aliases(out.owner, names)):
        return True
    return any(k in text for k in ('이체', '송금', '생활비', '용돈', '정산', '보냄', '입금'))


def _mark(tx, partner) -> None:
    tx.category = '부부간이체'
    tx.nature = cat.EXCLUDED
    tx.rule = 'couple-offset'
    tx.offset_with = partner.uid


# --------------------------------------------------------------------- 정산
def settle(transactions, month, names, split='half', incomes=None):
    """공동비용을 누가 얼마 냈는지, 정산 차액이 얼마인지 계산한다.

    split: 'half' (반반) 또는 'income' (소득 비례)
    incomes: 소득 비례일 때 {'남편': 3000000, '아내': 2500000}
    """
    owners = list(names.keys())
    paid = {o: 0 for o in owners}
    for tx in transactions:
        if tx.month != month or not tx.shared:
            continue
        if tx.nature in (cat.FIXED, cat.VARIABLE):
            paid[tx.owner] = paid.get(tx.owner, 0) + tx.amount

    total = sum(paid.values())
    if split == 'income' and incomes and sum(incomes.values()) > 0:
        base = sum(incomes.values())
        share = {o: total * incomes.get(o, 0) / base for o in owners}
    else:
        share = {o: total / len(owners) for o in owners} if owners else {}

    balance = {o: round(paid.get(o, 0) - share.get(o, 0)) for o in owners}
    return {'total': total, 'paid': paid, 'share': {o: round(v) for o, v in share.items()},
            'balance': balance}
