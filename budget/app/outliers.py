"""평소와 다른 큰 거래를 찾아낸다.

왜 필요한가:
  자동차를 사거나 전세금을 넣은 달은 지출이 평소의 몇 배가 된다. 그대로 두면
  '이번 달 지출 2,400만원'이 되어 평소 씀씀이를 알 수 없고, 추이 그래프도
  그 한 달 때문에 나머지 열한 달이 바닥에 깔려 안 보인다.
  그렇다고 자동으로 빼면 안 된다 — 진짜 쓴 돈이기 때문이다.
  그래서 **찾아서 보여주고, 일회성인지 평소인지는 사람이 정한다.**

판정 방법 (설명 가능해야 한다):
  카테고리마다 '평소 금액'(중앙값)을 구하고, 그보다 몇 배나 큰지를 본다.
  중앙값을 쓰는 이유는 평균이 큰 거래 하나에 끌려가기 때문이다.
  화면에는 "식비·외식 평소 12,000원인데 이번엔 180,000원 (15배)"처럼 보여준다.
"""
from __future__ import annotations

from collections import defaultdict
from statistics import median

from . import categories as cat

# 미확인 / 일회성(평소 계산에서 제외) / 평소(그대로 둠)
UNSET, ONCE, NORMAL = '', 'once', 'normal'

MIN_SAMPLES = 5          # 이만큼은 있어야 '평소'를 말할 수 있다
MULTIPLE = 4             # 평소의 몇 배부터 의심할지
MIN_AMOUNT = 100_000     # 배수가 커도 이 금액 미만이면 굳이 묻지 않는다
UNKNOWN_ASK = 1_000_000  # '평소'를 모르는 카테고리에서 이 금액을 넘으면 물어본다


MAX_ROWS = 40            # 목록이 끝없이 길면 아무도 안 본다

# 미분류는 '아직 뭔지 모르는 것들'의 잡동사니라 중앙값이 아무 뜻이 없다.
# (실제로 배우자 송금 90여 건이 여기 몰려 목록을 가득 채웠다.)
# 그리고 이미 분류 큐에서 다루고 있으니 여기서 또 물으면 중복이다.
SKIP_CATEGORIES = {'미분류'}


def _counted(tx) -> bool:
    return tx.nature in cat.COUNTED_NATURES and tx.category not in SKIP_CATEGORIES


def baselines(transactions) -> dict:
    """카테고리별 '평소 금액'(중앙값). 표본이 적으면 넣지 않는다."""
    buckets = defaultdict(list)
    for tx in transactions:
        if _counted(tx) and tx.spike != ONCE:
            buckets[tx.category].append(tx.amount)

    out = {}
    for category, amounts in buckets.items():
        if len(amounts) >= MIN_SAMPLES:
            out[category] = median(amounts)
    return out


def find(transactions, include_decided: bool = False) -> list[dict]:
    """평소와 다른 큰 거래 목록. 큰 금액 순.

    include_decided=True 면 사용자가 이미 정한 것도 함께 돌려준다
    (되돌리기 화면용).
    """
    base = baselines(transactions)
    rows = []

    for tx in transactions:
        if not _counted(tx):
            continue
        if not include_decided and tx.spike != UNSET:
            continue

        usual = base.get(tx.category)
        ratio = (tx.amount / usual) if usual else None

        if usual is not None:
            # 평소를 아는 카테고리 — 금액이 커도 평소 수준이면 묻지 않는다.
            # (급여 346만원은 큰 돈이지만 그 사람에겐 평소다. 이걸 물어보면
            #  목록이 정상 거래로 가득 차서 진짜 특이한 건이 묻힌다.)
            unusual = tx.amount >= MIN_AMOUNT and ratio >= MULTIPLE
        else:
            # 평소를 모르는 카테고리 — 비교할 게 없으니 금액으로만 판단한다
            unusual = tx.amount >= UNKNOWN_ASK

        if not unusual:
            continue

        rows.append({
            'uid': tx.uid,
            'date': tx.date.isoformat(),
            'owner': tx.owner,
            'content': tx.content,
            'category': tx.category,
            'nature': tx.nature,
            'amount': tx.amount,
            'usual': round(usual) if usual else None,
            'ratio': round(ratio, 1) if ratio else None,
            'spike': tx.spike,
            'reason': _reason(tx, usual, ratio),
        })

    rows.sort(key=lambda r: -r['amount'])
    return rows if include_decided else rows[:MAX_ROWS]


def _reason(tx, usual, ratio) -> str:
    if usual is not None:
        return f'{tx.category} 평소 {round(usual):,}원인데 {ratio:.0f}배'
    return f'{tx.category}는 비교할 기록이 적은데 금액이 큽니다'


def mark(transactions, uid: str, decision: str) -> bool:
    """한 건을 일회성/평소로 정한다. 정한 게 있으면 True."""
    if decision not in (UNSET, ONCE, NORMAL):
        return False
    for tx in transactions:
        if tx.uid == uid:
            tx.spike = decision
            return True
    return False


def mark_all(transactions, uids, decision: str) -> int:
    wanted = set(uids)
    changed = 0
    for tx in transactions:
        if tx.uid in wanted and decision in (UNSET, ONCE, NORMAL):
            tx.spike = decision
            changed += 1
    return changed


def without_once(transactions) -> list:
    """'일회성'으로 표시한 거래를 뺀 목록. 평소 씀씀이를 볼 때 쓴다."""
    return [t for t in transactions if t.spike != ONCE]


def once_total(transactions, month=None, owner=None) -> int:
    total = 0
    for tx in transactions:
        if tx.spike != ONCE or not _counted(tx):
            continue
        if month and tx.month != month:
            continue
        if owner and tx.owner != owner:
            continue
        if tx.nature in (cat.FIXED, cat.VARIABLE):
            total += tx.amount
    return total
