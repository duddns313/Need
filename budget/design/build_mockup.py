"""대시보드 시안(HTML)을 실제 뱅샐 파일로 채워서 만든다.

    python budget/design/build_mockup.py <뱅샐엑셀> [출력.html]

시안이 손으로 그린 그림이 아니라 진짜 파이프라인의 출력이어야
"이 숫자 어디서 나왔냐"에 답할 수 있다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from budget.app import aggregate, categories as cat, classifier, parser  # noqa: E402

HERE = Path(__file__).resolve().parent


def build(xlsx_path: str, owner: str = '남편') -> dict:
    parsed = parser.parse(xlsx_path, owner)
    engine = classifier.Classifier()
    engine.classify_all(parsed.transactions)
    tx = parsed.transactions

    promoted = classifier.detect_recurring_income(tx)
    candidates = classifier.detect_transfer_candidates(tx)

    months = aggregate.months_of(tx)
    month = months[-2] if len(months) > 1 else months[-1]
    prev = months[months.index(month) - 1] if months.index(month) > 0 else month

    return {
        'month': month,
        'months': months,
        'summary': aggregate.summary(tx, month),
        'prev': aggregate.summary(tx, prev),
        'series': aggregate.monthly_series(tx),
        'income_cat': aggregate.totals_by_category(tx, nature=cat.INCOME, month=month),
        'fixed_cat': aggregate.totals_by_category(tx, nature=cat.FIXED, month=month),
        'var_cat': aggregate.totals_by_category(tx, nature=cat.VARIABLE, month=month),
        'save_cat': aggregate.totals_by_category(tx, nature=cat.SAVING, month=month),
        'excluded': aggregate.excluded_total(tx),
        'unclassified': aggregate.unclassified(tx, limit=8),
        'unclassified_n': sum(1 for t in tx if t.category == '미분류'),
        'promoted': promoted,
        'candidates': candidates,
        'assets': parsed.total_assets,
        'liabilities': parsed.total_liabilities,
        'net': parsed.net_worth,
        'investments': [{'name': a.name, 'group': a.group, **a.extra} for a in parsed.investments],
        'loans': [{'name': a.name, 'group': a.group, **a.extra} for a in parsed.loans],
        'n_tx': len(tx),
    }


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(1)
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else HERE / 'dashboard_mockup.html'
    data = build(sys.argv[1])
    template = (HERE / 'mockup.html').read_text(encoding='utf-8')
    out.write_text(
        template.replace('__DATA__', json.dumps(data, ensure_ascii=False)), encoding='utf-8'
    )
    print(f'wrote {out}  ({out.stat().st_size:,} bytes)')


if __name__ == '__main__':
    main()
