"""뱅크샐러드 엑셀을 분류해서 결과를 보여준다. 앱 없이 터미널에서 바로.

    python budget/tools/classify_run.py 남편파일.xlsx
    python budget/tools/classify_run.py 남편파일.xlsx 아내파일.xlsx

키가 있으면 지도 조회까지 한다 (없으면 규칙 엔진까지만):
    KAKAO_REST_KEY / NAVER_CLIENT_ID+NAVER_CLIENT_SECRET   지도 조회
    ANTHROPIC_API_KEY                                       지도에 없는 상호

결과는 budget/rules/user_rules.json 과 budget/data/places_cache.json 에 남는다.
다음에 같은 상호가 나오면 조회 없이 바로 분류된다.

옵션:
    --no-lookup     지도 조회 건너뛰기 (규칙만)
    --no-ai         Claude 건너뛰기
    --limit N       조회를 N개 상호까지만 (비용/시간 확인용)
"""
from __future__ import annotations

import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from budget.app import (  # noqa: E402
    ai_classify, aggregate, categories as cat, classifier, couple, parser, places,
)

OWNERS = ['남편', '아내']


def won(n) -> str:
    return f'{round(n):,}원'


def snapshot(transactions) -> tuple[int, int]:
    rows = [t for t in transactions if t.category == '미분류']
    return len(rows), sum(t.amount for t in rows)


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    flags = {a for a in sys.argv[1:] if a.startswith('--')}
    if not args:
        print(__doc__)
        raise SystemExit(1)

    limit = None
    for flag in flags:
        if flag.startswith('--limit'):
            limit = int(flag.split('=')[1]) if '=' in flag else None

    # ---------------------------------------------------------------- 읽기
    parsed, transactions = [], []
    for path, owner in zip(args, OWNERS):
        try:
            one = parser.parse(path, owner)
        except parser.ParseError as e:
            print(f'[{owner}] {path}\n  읽기 실패: {e}')
            raise SystemExit(1)
        parsed.append(one)
        transactions.extend(one.transactions)
        print(f'[{owner}] {Path(path).name} — 거래 {len(one.transactions):,}건, '
              f'순자산 {won(one.net_worth)}')

    # 같은 파일을 두 번 넣어도 두 배가 되지 않도록
    seen, deduped = set(), []
    for tx in transactions:
        if tx.uid not in seen:
            seen.add(tx.uid)
            deduped.append(tx)
    if len(deduped) != len(transactions):
        print(f'  중복 {len(transactions) - len(deduped)}건 제거')
    transactions = deduped

    # ------------------------------------------------------------- 분류
    engine = classifier.Classifier()
    engine.classify_all(transactions)

    promoted = classifier.detect_recurring_income(transactions)
    for row in promoted:
        print(f"  급여로 승격: '{row['content']}' {row['count']}건 / {won(row['amount'])}")

    if len(parsed) > 1:
        names = {p.owner: p.owner for p in parsed}
        pairs = couple.offset_spouse_transfers(transactions, names)
        if pairs:
            print(f'  부부간 이체 상계: {len(pairs)}쌍')
    else:
        for row in classifier.detect_transfer_candidates(transactions)[:3]:
            print(f"  ? '{row['content']}' {row['count']}건 / {won(row['amount'])} "
                  f'— 배우자 송금이면 아내 파일을 같이 넣으세요')

    n0, a0 = snapshot(transactions)
    print(f'\n규칙 엔진까지: 미분류 {n0}건 / {won(a0)}')

    # ---------------------------------------------------------- 지도 조회
    if '--no-lookup' not in flags:
        lookup = places.PlaceLookup(
            kakao_key=os.environ.get('KAKAO_REST_KEY'),
            naver_id=os.environ.get('NAVER_CLIENT_ID'),
            naver_secret=os.environ.get('NAVER_CLIENT_SECRET'),
        )
        if not lookup.enabled:
            print('\n지도 조회 건너뜀 — KAKAO_REST_KEY 등이 설정되지 않았습니다.')
        else:
            pending = sorted({t.content for t in transactions if t.category == '미분류'})
            if limit:
                pending = pending[:limit]
            print(f'\n지도 조회 시작 — 상호 {len(pending)}개')

            hits, misses = [], []
            for i, merchant in enumerate(pending, start=1):
                try:
                    hit = lookup.lookup(merchant)
                except places.PlacesUnavailable as e:
                    print(f'  중단: {e}')
                    break
                if hit:
                    engine.learn(merchant, hit['category'])
                    hits.append((merchant, hit))
                else:
                    misses.append(merchant)
                print(f'\r  {i}/{len(pending)}  찾음 {len(hits)}  못찾음 {len(misses)}',
                      end='', flush=True)
            print()
            lookup.save()
            if hits:
                engine.save_user_rules()
                engine.classify_all(transactions)

            print(f'\n  찾음 {len(hits)}개 / 못 찾음 {len(misses)}개')
            for merchant, hit in hits[:20]:
                print(f"    {merchant[:28]:<30} → {hit['category']:<8} "
                      f"({hit.get('place', '')[:18]} · {hit.get('raw', '')[:26]})")
            if len(hits) > 20:
                print(f'    … 외 {len(hits) - 20}개')
            if misses:
                print(f'  지도에 없는 상호: {", ".join(misses[:12])}'
                      + (f' … 외 {len(misses) - 12}개' if len(misses) > 12 else ''))

    n1, a1 = snapshot(transactions)
    if n1 != n0:
        print(f'\n지도 조회 후: 미분류 {n1}건 / {won(a1)}  '
              f'({n0 - n1}건 감소)')

    # -------------------------------------------------------------- Claude
    if '--no-ai' not in flags and ai_classify.available():
        print('\nClaude로 나머지 분류 중…')
        try:
            result = ai_classify.apply(transactions, engine)
        except ai_classify.AiUnavailable as e:
            print(f'  건너뜀: {e}')
        else:
            print(f"  확정 {len(result['applied'])}개 / 확인필요 {len(result['unsure'])}개")
            for row in result['applied'][:15]:
                print(f"    {row['content'][:28]:<30} → {row['category']:<8} "
                      f"(확신 {row['confidence']:.0%})")
            for row in result['unsure'][:8]:
                print(f"    ? {row['content'][:26]:<28} → {row['category']} "
                      f"(확신 {row['confidence']:.0%}) — 직접 확인 필요")

    # -------------------------------------------------------------- 결과
    n2, a2 = snapshot(transactions)
    print('\n' + '=' * 58)
    print(f'미분류  {n0}건 / {won(a0)}   →   {n2}건 / {won(a2)}')
    print('=' * 58)

    months = aggregate.months_of(transactions)
    recent = months[-2] if len(months) > 1 else months[-1]
    summary = aggregate.summary(transactions, recent)
    print(f'\n[{recent}] 수입 {won(summary["수입"])} / 지출 {won(summary["지출"])} '
          f'(고정 {won(summary["고정비"])} + 변동 {won(summary["변동비"])}) / '
          f'저축·투자 {won(summary["저축투자"])} / 저축률 {summary["저축률"]:.0%}')

    print('\n[제외한 금액 — 뱅샐이 지출로 세던 것]')
    for k, v in sorted(aggregate.excluded_total(transactions).items(), key=lambda x: -x[1]):
        print(f'  {won(v):>16}  {k}')

    print('\n[카테고리별 지출 — 최근 12개월]')
    for nature in (cat.FIXED, cat.VARIABLE):
        rows = aggregate.totals_by_category(transactions, nature=nature)
        for k, v in sorted(rows.items(), key=lambda x: -x[1]):
            print(f'  {won(v):>16}  {nature}  {k}')

    left = aggregate.unclassified(transactions, limit=15)
    if left:
        print(f'\n[아직 미분류 — 직접 정해야 하는 것 상위 {len(left)}개]')
        for row in left:
            print(f"  {row['count']:>3}건  {won(row['amount']):>14}  {row['content'][:36]}")

    print('\n규칙이 budget/rules/user_rules.json 에 저장됐습니다. '
          '다음 달 파일부터는 조회 없이 자동 분류됩니다.')


if __name__ == '__main__':
    main()
