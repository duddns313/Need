"""가계 진단과 처방 — 보여주는 데서 끝내지 않기 위한 층.

대시보드는 "얼마 썼다"까지만 말한다. 그 다음이 없으면 아무것도 바뀌지 않는다.
이 모듈은 세 가지를 한다.

  진단  지금 상태가 좋은지 나쁜지, 왜 그런지
  목표  저축률 목표를 정하면 매달 얼마가 부족한지
  처방  어느 항목을 얼마까지 줄이면 되는지

처방의 기준은 **남의 평균이 아니라 본인이 이미 해낸 달**이다.
"식비를 30% 줄이세요" 같은 말은 근거가 없고 지켜지지도 않는다.
"지난 1년 중 잘한 석 달의 수준으로" 는 본인이 실제로 해봤으니 가능하다.
"""
from __future__ import annotations

import math
import statistics as st
from collections import defaultdict

from . import aggregate, categories as cat

RECENT = 6               # 최근 몇 달로 '지금 수준'을 볼지
GOOD_MONTHS = 3          # 처방 기준: 잘한 달 몇 개의 중앙값
MIN_MONTHS = 3           # 이만큼은 있어야 말을 할 수 있다

# 처방을 낼 수 있는 항목의 조건.
# 어쩌다 한 번 쓰는 항목은 '적게 쓴 달'이 사실은 '안 산 달'이다. 그걸 목표로
# 내밀면 "생활용품 월 1,360원" 같은 말이 나오고, 그 순간 나머지 조언까지
# 못 믿게 된다. 매달 꾸준히 쓰는 항목만 손댈 수 있는 습관으로 본다.
HABIT_RATIO = 0.67       # 최근 달 중 이 비율 이상에서 지출이 있어야 한다
MAX_CUT = 0.40           # 한 항목에서 요구할 수 있는 최대 절감폭

# 판정 기준. 절대적인 정답은 아니고, 흔히 쓰는 눈금이다.
SAVING_GOOD, SAVING_WATCH = 0.30, 0.15      # 저축률
FIXED_GOOD, FIXED_WATCH = 0.35, 0.50        # 수입 대비 고정비
EATOUT_GOOD, EATOUT_WATCH = 0.50, 0.70      # 먹는 돈 중 사먹는 비중
EMERGENCY_MONTHS = 3                        # 비상금 목표 — 평소 지출 몇 달치


def _r(x) -> int:
    """0.5는 위로 올린다.

    파이썬 기본 round()는 0.5를 짝수 쪽으로 보낸다(303396.5 -> 303396).
    폰 화면은 자바스크립트로 같은 계산을 하는데 거기선 위로 올라간다(303397).
    두 화면이 1원씩 다르면 어느 쪽이 맞는지 설명할 방법이 없다. 위로 맞춘다.
    """
    return int(math.floor(x + 0.5))


def _months(transactions) -> list[str]:
    """거래가 거의 없는 달(내보내기 경계)은 뺀다."""
    out = []
    for m in aggregate.months_of(transactions):
        s = aggregate.summary(transactions, m)
        if s['수입'] + s['지출'] > 100_000:
            out.append(m)
    return out


def baseline(transactions, months=None) -> dict:
    """'평소 한 달' 수준. 평균이 아니라 중앙값을 쓴다.

    평균은 대출 실행이나 이사처럼 한 달짜리 사건에 통째로 끌려간다.
    중앙값은 안 끌려간다. 가계에서 알고 싶은 건 '보통 달'이다.
    """
    months = months or _months(transactions)
    recent = months[-RECENT:] if len(months) > RECENT else months
    if not recent:
        return {}

    rows = [aggregate.summary(transactions, m) for m in recent]
    med = lambda key: st.median(r[key] for r in rows)
    income, spend = med('수입'), med('지출')
    return {
        'months': recent,
        '수입': _r(income), '지출': _r(spend),
        '고정비': _r(med('고정비')), '변동비': _r(med('변동비')),
        '저축투자': _r(med('저축투자')),
        '남는 돈': _r(income - spend),
        '저축률': round((income - spend) / income, 4) if income else 0.0,
    }


def _verdict(value, good, watch, higher_is_better=True):
    if higher_is_better:
        return 'good' if value >= good else ('watch' if value >= watch else 'act')
    return 'good' if value <= good else ('watch' if value <= watch else 'act')


def health(transactions, liquid=None) -> list[dict]:
    """지금 상태 진단. 각 항목은 판정과 근거를 함께 낸다.

    liquid 를 주면 비상금까지 본다 — 당장 꺼내 쓸 수 있는 돈의 합계다.
    투자·연금은 넣으면 안 된다. 급할 때 손해 보고 팔거나 아예 못 뺀다.
    """
    months = _months(transactions)
    if len(months) < MIN_MONTHS:
        return []
    base = baseline(transactions, months)
    checks = []

    rate = base['저축률']
    checks.append({
        'key': 'saving', 'label': '저축률',
        'value': rate, 'display': f'{rate*100:.0f}%',
        'verdict': _verdict(rate, SAVING_GOOD, SAVING_WATCH),
        'note': f"보통 달에 {base['수입']:,}원 벌어 {base['지출']:,}원 씁니다.",
        'target': f'{SAVING_GOOD*100:.0f}% 이상이면 좋습니다.',
    })

    # 저축률보다 먼저 봐야 하는 숫자다. 저축률이 아무리 높아도 당장 쓸 돈이
    # 없으면 사고 한 번에 다시 카드빚으로 돌아간다.
    if liquid is not None and base['지출']:
        cover = liquid / base['지출']
        checks.append({
            'key': 'emergency', 'label': '비상금',
            'value': cover, 'display': f'{cover:.1f}개월치',
            'verdict': _verdict(cover, EMERGENCY_MONTHS, 1),
            'note': (f'급할 때 바로 쓸 수 있는 돈이 {round(liquid):,}원입니다. '
                     f'투자·연금은 뺀 금액입니다.'),
            'target': (f"평소 지출 {EMERGENCY_MONTHS}개월치"
                       f"({base['지출']*EMERGENCY_MONTHS:,}원)는 있어야 합니다."),
        })

    fixed_ratio = base['고정비'] / base['수입'] if base['수입'] else 0
    checks.append({
        'key': 'fixed', 'label': '고정비 비중',
        'value': fixed_ratio, 'display': f'{fixed_ratio*100:.0f}%',
        'verdict': _verdict(fixed_ratio, FIXED_GOOD, FIXED_WATCH, higher_is_better=False),
        'note': f"매달 자동으로 나가는 돈이 {base['고정비']:,}원입니다.",
        'target': f'수입의 {FIXED_GOOD*100:.0f}% 아래면 여유가 있습니다.',
    })

    fd = food(transactions)
    if fd['total']:
        checks.append({
            'key': 'eatout', 'label': '사먹는 비중',
            'value': fd['eatout_ratio'], 'display': f"{fd['eatout_ratio']*100:.0f}%",
            'verdict': _verdict(fd['eatout_ratio'], EATOUT_GOOD, EATOUT_WATCH,
                                higher_is_better=False),
            'note': (f"먹는 데 쓴 돈 중 외식·배달·카페가 {fd['eatout_ratio']*100:.0f}%, "
                     f"장보기가 {(1-fd['eatout_ratio'])*100:.0f}%입니다."),
            'target': '장보기 비중이 높을수록 같은 끼니를 덜 쓰고 먹습니다.',
        })

    spends = [aggregate.summary(transactions, m)['지출'] for m in months]
    if len(spends) >= MIN_MONTHS and st.median(spends):
        swing = (max(spends) - min(spends)) / st.median(spends)
        checks.append({
            'key': 'swing', 'label': '월별 들쭉날쭉',
            'value': swing, 'display': f'{swing:.1f}배',
            'verdict': _verdict(swing, 1.0, 2.0, higher_is_better=False),
            'note': f"가장 많이 쓴 달과 적게 쓴 달이 {max(spends):,}원과 {min(spends):,}원입니다.",
            'target': '차이가 작을수록 예산을 세울 수 있습니다.',
        })

    unknown = sum(t.amount for t in transactions if t.category == '미분류')
    total = sum(t.amount for t in transactions
                if t.nature in (cat.FIXED, cat.VARIABLE))
    if total:
        ratio = unknown / total
        checks.append({
            'key': 'unknown', 'label': '정체 모를 지출',
            'value': ratio, 'display': f'{ratio*100:.0f}%',
            'verdict': _verdict(ratio, 0.03, 0.10, higher_is_better=False),
            'note': f'{unknown:,}원이 어느 항목인지 아직 정해지지 않았습니다.',
            'target': '이게 크면 나머지 숫자도 못 믿습니다.',
        })
    return checks


def food(transactions, month=None) -> dict:
    """먹는 돈의 구조. 장보기와 사먹기를 갈라야 손댈 곳이 보인다."""
    keys = ['식료품', '외식', '배달', '카페·간식']
    got = {k: 0 for k in keys}
    for tx in transactions:
        if tx.category in got and (month is None or tx.month == month):
            got[tx.category] += tx.amount
    total = sum(got.values())
    eatout = total - got['식료품']
    return {
        'parts': got, 'total': total, 'eatout': eatout,
        'eatout_ratio': (eatout / total) if total else 0.0,
    }


def monthly_by_category(transactions, category) -> dict:
    out = defaultdict(int)
    for tx in transactions:
        if tx.category == category:
            out[tx.month] += tx.amount
    return dict(out)


def actions(transactions, top=5) -> list[dict]:
    """줄일 곳 처방. 목표치는 '본인이 이미 해낸 달'에서 가져온다."""
    months = _months(transactions)
    if len(months) < MIN_MONTHS:
        return []
    recent = months[-RECENT:] if len(months) > RECENT else months

    rows = []
    for category in cat.categories_of(cat.VARIABLE):
        if category == '미분류':
            continue
        per_month = monthly_by_category(transactions, category)
        values = [per_month.get(m, 0) for m in recent]
        active = sum(1 for v in values if v > 0)
        if active < max(2, _r(len(recent) * HABIT_RATIO)):
            continue                      # 습관이 아니라 어쩌다 쓴 항목

        now = st.median(values)
        if not now:
            continue

        # 잘한 달 = 적게 쓴 달. 그 중앙값이 목표.
        best = sorted(values)[:min(GOOD_MONTHS, len(values))]
        goal = st.median(best)

        # 다만 아무리 잘한 달이라도 절반 넘게 깎으라는 건 계획이 아니다.
        floor = now * (1 - MAX_CUT)
        capped = goal < floor
        goal = max(goal, floor)

        saving = now - goal
        if saving < 10_000:      # 아껴봐야 티도 안 나는 건 말하지 않는다
            continue

        why = (f'최근 {len(recent)}달 중 적게 쓴 달은 {_r(min(best)):,}원이었습니다. '
               f'해본 수준입니다.')
        if capped:
            why = (f'적게 쓴 달은 더 낮았지만, 한 번에 {MAX_CUT*100:.0f}% 넘게 줄이는 건 '
                   f'오래 못 갑니다. 여기까지만 잡았습니다.')

        rows.append({
            'category': category,
            'now': _r(now), 'goal': _r(goal),
            'save_month': _r(saving), 'save_year': _r(saving * 12),
            'cut': round(saving / now, 3) if now else 0,
            'capped': capped,
            'why': why,
        })

    rows.sort(key=lambda r: -r['save_month'])
    return rows[:top]


def goal_plan(transactions, target_rate: float) -> dict:
    """목표 저축률을 넣으면 매달 얼마가 부족한지, 무엇으로 메울지."""
    base = baseline(transactions)
    if not base:
        return {}
    income = base['수입']
    need_spend = _r(income * (1 - target_rate))
    gap = base['지출'] - need_spend

    plan, running = [], 0
    if gap > 0:
        for row in actions(transactions, top=8):
            if running >= gap:
                break
            take = min(row['save_month'], gap - running)
            running += take
            plan.append({**row, 'take': _r(take)})

    return {
        'target_rate': target_rate,
        'now_rate': base['저축률'],
        'income': income,
        'spend_now': base['지출'],
        'spend_target': need_spend,
        'gap': _r(max(gap, 0)),
        'covered': _r(running),
        'short': _r(max(gap - running, 0)),
        'reachable': gap <= 0 or running >= gap,
        'plan': plan,
    }


def leaks(transactions, months_seen=3) -> list[dict]:
    """새는 돈 후보 — 매달 조용히 빠져나가는 작은 결제.

    구독료는 한 번 끊으면 매달 효과가 나므로 가장 먼저 볼 곳이다.
    """
    groups = defaultdict(lambda: {'months': set(), 'amount': 0, 'count': 0,
                                  'category': ''})
    for tx in transactions:
        if tx.nature not in (cat.FIXED, cat.VARIABLE):
            continue
        if tx.category not in ('구독료', '통신', '보험'):
            continue
        g = groups[tx.content]
        g['months'].add(tx.month)
        g['amount'] += tx.amount
        g['count'] += 1
        g['category'] = tx.category

    out = []
    for content, g in groups.items():
        if len(g['months']) < months_seen:
            continue
        out.append({
            'content': content, 'category': g['category'],
            'months': len(g['months']), 'count': g['count'],
            'amount': g['amount'],
            'per_month': _r(g['amount'] / len(g['months'])),
            'per_year': _r(g['amount'] / len(g['months']) * 12),
        })
    out.sort(key=lambda r: -r['per_year'])
    return out
