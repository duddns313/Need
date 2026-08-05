"""폰에서 여는 한 장짜리 화면(아티팩트)을 만든다.

PC 없이 쓰고 싶다는 요구에서 나온 도구다. 뱅샐 엑셀을 넣으면
계산이 끝난 숫자를 HTML 안에 박아 넣은 파일 하나가 나온다.
그 파일만 있으면 서버도, 파이썬도, 인터넷도 없이 화면이 뜬다.

    python budget/tools/build_artifact.py 남편파일.xlsx [아내파일.xlsx] -o out.html

숫자를 다시 뽑을 일이 생겼을 때 손으로 재현하지 않으려고 스크립트로 남긴다.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from budget.app import (  # noqa: E402
    categories as cat, classifier, couple, parser, payexport,
)

TEMPLATE = ROOT / 'design' / 'artifact_template.html'
PLACEHOLDER = '__DATA__'

FOOD = ['식료품', '외식', '배달', '카페·간식']


def load(paths_and_owners, pay_files=()) -> tuple[list, dict]:
    """파일들을 읽어 분류까지 끝낸 거래와 자산을 돌려준다.

    pay_files 를 주면 간편결제 영수증의 상품명을 먼저 붙인다. 분류보다
    먼저 해야 한다 — '네이버페이'라는 이름으로는 어느 항목인지 알 수 없지만
    '핑기 실리카겔 제습제'면 생활용품인 게 보인다.
    """
    txs, files = [], []
    for path, owner in paths_and_owners:
        pf = parser.parse(path, owner)
        files.append(pf)
        txs.extend(pf.transactions)

    # 영수증 파일은 여러 개로 나눠 받게 된다(달마다, 카드/현금 따로). 같은 건이
    # 여러 파일에 겹쳐 들어오므로 한 번에 모아 놓고 중복을 걷어낸 뒤 붙인다.
    # 안 그러면 같은 결제가 두 거래에 각각 붙어 엉뚱한 이름이 생긴다.
    receipts, seen = [], set()
    for path in pay_files or ():
        got = payexport.parse(path)
        fresh = 0
        for r in got.rows:
            key = (r.when, r.amount, r.raw_name)
            if key in seen:
                continue
            seen.add(key)
            receipts.append(r)
            fresh += 1
        dup = len(got.rows) - fresh
        print(f"  영수증 {Path(path).name} — {got.source}, {len(got.rows)}줄"
              + (f" (겹치는 {dup}줄 제외)" if dup else ""))

    if receipts:
        res = payexport.apply_names(txs, receipts)
        print(f"  모두 {len(receipts)}건")
        print(f"      이름 채움      {len(res['filled']):>3}건  (네이버페이 → 상품명)")
        print(f"      메모만 붙임    {len(res['noted']):>3}건  (상호는 이미 있음)")
        print(f"      기간 밖        {len(res['outside']):>3}건")
        print(f"      짝 못 찾음     {len(res['missed']):>3}건")
        for r in res['missed'][:6]:
            print(f"          {r.day}  {r.amount:>9,}  {r.name}")

    engine = classifier.Classifier()
    engine.classify_all(txs)
    classifier.detect_recurring_income(txs)
    # 대출금은 통장에 들어와도 소득이 아니다. 급여 승격 뒤에 내려야
    # 승격된 것까지 다시 걸러진다.
    all_loans = [ln for pf in files for ln in pf.loans]
    classifier.detect_loan_disbursements(txs, all_loans)
    # 카드값이 나갔다 그대로 돌아온 짝은 환불이 아니다. 서로 지운다.
    classifier.detect_payment_reversals(txs)

    owners = [pf.owner for pf in files]
    if len(owners) > 1:
        couple.offset_spouse_transfers(txs, owners)

    wealth = {
        'assets': sum(pf.total_assets for pf in files),
        'liabilities': sum(pf.total_liabilities for pf in files),
        'investments': [
            {'name': a.name, 'group': a.group,
             '원금': a.extra.get('원금', 0), '평가금액': a.amount}
            for pf in files for a in pf.investments
        ],
        'loans': [
            {'name': a.name, 'group': a.group,
             '원금': a.extra.get('원금', 0), '잔액': a.amount}
            for pf in files for a in pf.loans
        ],
    }
    # 자산을 묶음째로 넘긴다. 순자산 총액만으로는 '당장 쓸 수 있는 돈'을
    # 알 수 없다. 4,900만원을 갖고 있어도 그게 전부 묶여 있으면 냉장고가
    # 고장났을 때 카드를 또 긁는 수밖에 없다.
    # 뱅샐 시트는 묶음 이름을 그 묶음의 첫 줄에만 적어 둔다. 나머지 줄은
    # 빈칸이라, 그대로 읽으면 묶음마다 한 줄씩만 남는다. 앞줄 이름을 물려준다.
    lines, group = [], ''
    for pf in files:
        group = ''
        for a in pf.assets:
            group = a.group or group
            lines.append({'group': group, 'name': a.name, 'amount': a.amount})
    wealth['asset_lines'] = lines
    # 즉시 꺼내 쓸 수 있는 묶음. 청약·투자·연금은 비상금이 아니다.
    wealth['liquid_groups'] = ['자유입출금 자산', '현금 자산', '전자금융 자산']

    # 퇴직연금(DC/DB)은 회사가 넣어준 돈이라 세액공제와 상관없다.
    # 그런데 '연금'이라는 이름 때문에 본인 납입분과 섞이기 쉬워서 따로 뽑아 둔다.
    wealth['pension_assets'] = [
        {'name': a.name, 'amount': a.amount}
        for pf in files for a in pf.assets if '연금' in (a.group or '')
    ]
    wealth['net'] = wealth['assets'] - wealth['liabilities']
    return txs, wealth


def rows_of(txs) -> list[dict]:
    """거래 원본. 화면에서 분류를 고칠 수 있으려면 집계된 숫자가 아니라
    거래 자체를 들고 있어야 한다.

    폰으로 받는 파일이라 글자 수가 곧 무게다. 키를 한 글자로 줄인다.
      u 식별자 · d 날짜 · o 사람 · c 내용 · a 금액 · k 카테고리
      t 뱅샐 원본 타입 · m 결제수단 · r 어떤 규칙으로 분류됐는지
    """
    out = []
    for tx in sorted(txs, key=lambda t: (t.date, t.time)):
        out.append({
            'u': tx.uid, 'd': tx.date.isoformat(), 'o': tx.owner,
            'c': tx.content, 'a': tx.amount, 'k': tx.category,
            't': tx.bs_type, 'm': tx.method, 'r': tx.rule,
        })
    return out


def build(txs, wealth, owner_label: str) -> dict:
    dates = [t.date for t in txs]
    return {
        'owner': owner_label,
        'owners': sorted({t.owner for t in txs}),
        'range': [min(dates).isoformat(), max(dates).isoformat()],
        'rows': rows_of(txs),
        # 카테고리 -> 성격. 화면에서 분류를 바꾸면 성격도 따라 바뀌어야 한다.
        'cats': dict(cat.CATEGORIES),
        'order': list(cat.ORDER),
        'food_keys': FOOD,
        **wealth,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('files', nargs='+', help='뱅크샐러드 엑셀 (사람 수만큼)')
    ap.add_argument('--owners', nargs='*', default=None, help='사람 이름 (기본: 남편, 아내)')
    ap.add_argument('-o', '--out', default='budget_artifact.html')
    ap.add_argument('--json', default=None, help='데이터만 따로 저장할 경로')
    ap.add_argument('--pay', nargs='*', default=[],
                    help='네이버페이 카드영수증 · 카카오페이 거래내역서 엑셀')
    args = ap.parse_args()

    owners = args.owners or ['남편', '아내'][:len(args.files)]
    if len(owners) != len(args.files):
        ap.error('파일 수와 이름 수가 다릅니다.')

    txs, wealth = load(list(zip(args.files, owners)), pay_files=args.pay)
    data = build(txs, wealth, ' · '.join(owners))

    payload = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    # </script> 가 데이터 안에 들어가면 스크립트 태그가 거기서 끊긴다.
    payload = payload.replace('</', '<\\/')

    html = TEMPLATE.read_text(encoding='utf-8')
    if PLACEHOLDER not in html:
        raise SystemExit(f'서식 파일에 {PLACEHOLDER} 자리가 없습니다: {TEMPLATE}')
    Path(args.out).write_text(html.replace(PLACEHOLDER, payload), encoding='utf-8')

    if args.json:
        Path(args.json).write_text(
            json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')

    from budget.app import advisor
    b = advisor.baseline(txs)
    unknown = sum(1 for t in txs if t.category == '미분류')
    print(f"거래 {len(txs):,}건 · {data['range'][0]} ~ {data['range'][1]}")
    print(f"보통 달  수입 {b['수입']:,} / 지출 {b['지출']:,} / 저축률 {b['저축률']*100:.0f}%")
    print(f"미분류 {unknown}건 · 화면 데이터 {len(payload)/1024:.0f}KB")
    print(f"→ {args.out}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
