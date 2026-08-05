"""간편결제 영수증 파일 읽기 — 뱅샐이 못 가져오는 '무엇을 샀는가'.

카드 명세서에는 네이버페이 결제가 가맹점 '네이버페이'로만 찍힌다. 무엇을
샀는지는 네이버페이 쪽에만 있다. 다행히 네이버페이는 카드영수증을 엑셀로
내려받게 해 준다. 거기엔 상호보다 나은 것이 들어 있다 — **상품명**이다.

    승인번호 | 카드사 | 카드번호 | 거래종류/할부 | 결제일자 | 취소일자
    | 상품명 | 승인금액 | 취소금액 | 공급가액 | 부가세액 | 봉사료
    | 컵보증금 | 합계

이 파일을 읽어 뱅샐 거래와 금액·날짜로 짝을 맞추고, 이름을 상품명으로
바꿔 놓는다. 짝이 없으면 그대로 알려준다 — 조용히 버리면 다 된 줄 안다.

카카오페이 거래내역서도 열 이름만 다르지 구조가 같아서 같이 읽는다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import openpyxl

# 열 이름이 앱마다 조금씩 다르다. 뜻이 같은 것끼리 묶어 둔다.
COLUMNS = {
    'date': ['결제일자', '거래일시', '거래일자', '이용일자', '승인일자', '일시', '날짜'],
    'name': ['상품명', '가맹점', '가맹점명', '내용', '거래내용', '적요', '이용처'],
    'amount': ['합계', '승인금액', '거래금액', '이용금액', '결제금액', '금액'],
    'cancel': ['취소금액'],
    'cancel_date': ['취소일자'],
    'issuer': ['카드사', '결제수단', '결제수단명'],
}

NAME_MAX = 40          # 상품명이 한 줄을 넘기면 화면에서 읽을 수가 없다
HEADER_SCAN = 12       # 머리글이 몇 줄 아래에 있을 수도 있다


class PayParseError(Exception):
    pass


@dataclass
class Receipt:
    when: datetime
    name: str
    amount: int
    issuer: str = ''
    cancelled: bool = False
    raw_name: str = ''

    @property
    def day(self) -> date:
        return self.when.date()


@dataclass
class PayFile:
    path: str
    rows: list[Receipt] = field(default_factory=list)
    source: str = ''

    @property
    def total(self) -> int:
        return sum(r.amount for r in self.rows if not r.cancelled)


def _find_header(ws) -> tuple[int, dict]:
    """머리글 줄과 '뜻 -> 열번호' 표를 찾는다."""
    for row in range(1, min(HEADER_SCAN, ws.max_row) + 1):
        labels = {}
        for col in range(1, ws.max_column + 1):
            v = ws.cell(row, col).value
            if isinstance(v, str) and v.strip():
                labels[v.strip()] = col
        if not labels:
            continue
        got = {}
        for key, names in COLUMNS.items():
            for n in names:
                if n in labels:
                    got[key] = labels[n]
                    break
        if 'date' in got and 'amount' in got and 'name' in got:
            return row, got
    raise PayParseError(
        '결제일자·상품명·금액 열을 못 찾았습니다. '
        '네이버페이 카드영수증이나 카카오페이 거래내역서 파일이 맞나요?')


def _as_dt(value):
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if not value:
        return None
    text = str(value).strip().replace('.', '-').replace('/', '-')
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d', '%Y%m%d'):
        try:
            return datetime.strptime(text[:len(fmt) + 4].strip(), fmt)
        except ValueError:
            continue
    return None


def _as_int(value) -> int:
    if value is None or value == '':
        return 0
    if isinstance(value, (int, float)):
        return int(round(value))
    text = str(value).replace(',', '').replace('원', '').strip()
    try:
        return int(round(float(text)))
    except ValueError:
        return 0


def _shorten(name: str) -> str:
    """'핑기 실리카겔 재사용 제습제 습기제거제 곰팡이 방지 가정 150g 1개+가정 450g 1개'
    같은 상품명은 그대로 두면 목록이 무너진다. 앞부분만 남긴다."""
    name = ' '.join(str(name).split())
    if len(name) <= NAME_MAX:
        return name
    cut = name[:NAME_MAX]
    space = cut.rfind(' ')
    if space > NAME_MAX * 0.6:      # 낱말 중간에서 끊지 않는다
        cut = cut[:space]
    return cut + '…'


def parse(path) -> PayFile:
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb.active
    header, cols = _find_header(ws)

    out = PayFile(path=str(path), source=ws.title)
    for row in ws.iter_rows(min_row=header + 1, values_only=False):
        get = lambda key: (row[cols[key] - 1].value if key in cols else None)
        when = _as_dt(get('date'))
        amount = _as_int(get('amount'))
        raw = get('name')
        if not when or not amount or not raw:
            continue
        out.rows.append(Receipt(
            when=when, name=_shorten(raw), raw_name=' '.join(str(raw).split()),
            amount=amount, issuer=str(get('issuer') or '').strip(),
            cancelled=bool(_as_int(get('cancel'))) or bool(_as_dt(get('cancel_date'))),
        ))
    wb.close()
    if not out.rows:
        raise PayParseError('읽을 수 있는 거래가 한 줄도 없습니다.')
    return out


DAY_TOLERANCE = 3      # 카드 승인일과 페이 결제일이 하루이틀 어긋난다


def apply_names(transactions, receipts, only_vague=True) -> dict:
    """영수증의 상품명을 뱅샐 거래에 붙인다.

    금액이 같고 날짜가 며칠 안쪽인 것을 짝으로 본다. 한 거래에 두 번 붙지
    않게 쓴 것은 표시해 둔다. 가까운 날짜부터 가져간다.
    """
    pool = [t for t in transactions
            if not only_vague or _is_vague(t)]
    used, filled, missed = set(), [], []

    for r in sorted(receipts, key=lambda x: x.when):
        if r.cancelled:
            continue
        best, gap = None, 99
        for tx in pool:
            if id(tx) in used or tx.amount != r.amount:
                continue
            d = abs((tx.date - r.day).days)
            if d <= DAY_TOLERANCE and d < gap:
                best, gap = tx, d
        if best is None:
            missed.append(r)
            continue
        used.add(id(best))
        best.content = r.name
        best.memo = (best.memo + ' ' if best.memo else '') + r.raw_name
        filled.append((best, r))

    return {'filled': filled, 'missed': missed, 'read': len(receipts)}


VAGUE_NAMES = {'네이버페이', '카카오페이', '토스', '페이코', '삼성페이', '애플페이',
               '스마일캐시', '네이버파이낸셜', '(주)네이버파이낸셜'}


def _is_vague(tx) -> bool:
    """이름만 남고 무엇을 샀는지는 안 남은 거래."""
    return (tx.content or '').strip() in VAGUE_NAMES
