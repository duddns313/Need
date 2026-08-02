"""집계 — 월 × 성격 × 카테고리 × 소유자.

화면과 엑셀이 같은 숫자를 보게 하려고, 집계는 여기 한 군데서만 한다.
"""
from __future__ import annotations

from collections import defaultdict

from . import categories as cat


def months_of(transactions) -> list[str]:
    return sorted({t.month for t in transactions})


def _counted(tx) -> bool:
    return tx.nature in cat.COUNTED_NATURES


def totals_by_nature(transactions, month=None, owner=None) -> dict:
    """성격별 합계. 제외 거래는 안 잡힌다."""
    out = {n: 0 for n in cat.COUNTED_NATURES}
    for tx in transactions:
        if not _counted(tx):
            continue
        if month and tx.month != month:
            continue
        if owner and tx.owner != owner:
            continue
        out[tx.nature] += tx.amount
    return out


def totals_by_category(transactions, nature=None, month=None, owner=None) -> dict:
    out = defaultdict(int)
    for tx in transactions:
        if not _counted(tx):
            continue
        if nature and tx.nature != nature:
            continue
        if month and tx.month != month:
            continue
        if owner and tx.owner != owner:
            continue
        out[tx.category] += tx.amount
    return dict(out)


def category_by_owner(transactions, nature, month, owners) -> list[dict]:
    """카테고리별 × 소유자별 지출. 누적 막대용. 큰 것부터 정렬."""
    table = defaultdict(lambda: {o: 0 for o in owners})
    for tx in transactions:
        if not _counted(tx) or tx.nature != nature or tx.month != month:
            continue
        if tx.owner not in table[tx.category]:
            table[tx.category][tx.owner] = 0
        table[tx.category][tx.owner] += tx.amount

    rows = []
    for category, per_owner in table.items():
        rows.append({'category': category, 'total': sum(per_owner.values()), **per_owner})
    rows.sort(key=lambda r: -r['total'])
    return rows


def monthly_series(transactions, owner=None) -> list[dict]:
    """월별 수입/지출/저축 추이. 차트 3선용."""
    rows = []
    for month in months_of(transactions):
        t = totals_by_nature(transactions, month=month, owner=owner)
        spend = t[cat.FIXED] + t[cat.VARIABLE]
        rows.append({
            'month': month,
            '수입': t[cat.INCOME],
            '지출': spend,
            '고정비': t[cat.FIXED],
            '변동비': t[cat.VARIABLE],
            '저축투자': t[cat.SAVING],
            '저축률': _rate(t[cat.INCOME] - spend, t[cat.INCOME]),
        })
    return rows


def summary(transactions, month, owner=None) -> dict:
    """대시보드 상단 KPI."""
    t = totals_by_nature(transactions, month=month, owner=owner)
    income = t[cat.INCOME]
    spend = t[cat.FIXED] + t[cat.VARIABLE]
    saving = t[cat.SAVING]
    return {
        'month': month,
        '수입': income,
        '지출': spend,
        '고정비': t[cat.FIXED],
        '변동비': t[cat.VARIABLE],
        '저축투자': saving,
        '잔액': income - spend,
        '저축률': _rate(income - spend, income),
    }


def excluded_total(transactions, month=None) -> dict:
    """제외 처리된 금액. '왜 뱅샐 숫자와 다른가'를 설명하는 데 쓴다."""
    out = defaultdict(int)
    for tx in transactions:
        if tx.nature != cat.EXCLUDED:
            continue
        if month and tx.month != month:
            continue
        # 뱅샐이 '지출'로 세던 것만 대조 대상
        if tx.bs_type == '지출':
            out[tx.category] += tx.amount
    return dict(out)


def excluded_income(transactions, month=None) -> dict:
    """들어왔지만 소득이 아닌 돈.

    대출금·배우자 송금·내 다른 계좌에서 옮겨온 돈이 여기 잡힌다.
    이걸 소득으로 세면 저축률이 실제보다 높게 나와 가계를 잘못 읽는다.
    """
    out = defaultdict(int)
    for tx in transactions:
        if tx.nature != cat.EXCLUDED or tx.bs_type != '수입':
            continue
        if month and tx.month != month:
            continue
        out[tx.category] += tx.amount
    return dict(out)


def unclassified(transactions, limit=None) -> list[dict]:
    """미분류 정리 큐. 같은 상호끼리 묶어서 건수 많은 순으로."""
    groups = defaultdict(lambda: {'count': 0, 'amount': 0, 'owners': set()})
    for tx in transactions:
        if tx.category != '미분류':
            continue
        g = groups[tx.content]
        g['count'] += 1
        g['amount'] += tx.amount
        g['owners'].add(tx.owner)

    rows = [{'content': k, 'count': v['count'], 'amount': v['amount'],
             'owners': sorted(v['owners'])} for k, v in groups.items()]
    rows.sort(key=lambda r: (-r['count'], -r['amount']))
    return rows[:limit] if limit else rows


def _rate(numerator, denominator) -> float:
    if not denominator:
        return 0.0
    return round(numerator / denominator, 4)
