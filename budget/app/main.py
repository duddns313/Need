"""부부 공동 가계부 — 로컬 웹앱 본체.

브라우저에서 열리지만 서버는 이 PC 안에서만 돕니다(127.0.0.1).
업로드한 엑셀은 이 PC를 떠나지 않습니다.
바깥으로 나가는 건 딱 두 가지, 그것도 켰을 때만입니다.
  - 지도 조회: 가맹점 '상호명'만 (금액·날짜·계좌번호는 나가지 않습니다)
  - Claude 분류: 지도에서 못 찾은 '상호명'만
"""
from __future__ import annotations

import secrets
import threading
import webbrowser
from pathlib import Path

from flask import (
    Flask, flash, redirect, render_template, request, send_file, url_for,
)

from . import ai_classify, aggregate, categories as cat, classifier, couple, parser, places, store
from .paths import EXPORTS, PLACES_CACHE, USER_RULES, bundle_root, ensure_dirs

app = Flask(
    __name__,
    template_folder=str(bundle_root() / 'templates'),
    static_folder=str(bundle_root() / 'static'),
)
app.secret_key = secrets.token_hex(32)
app.config['MAX_CONTENT_LENGTH'] = 52428800   # 50MB — 뱅샐 엑셀은 보통 1MB 미만


# ------------------------------------------------------------------ 도우미
def engine_for(settings) -> classifier.Classifier:
    return classifier.Classifier(user_rules_path=USER_RULES)


def lookup_for(settings) -> places.PlaceLookup:
    cache = {}
    if PLACES_CACHE.exists():
        import json
        try:
            cache = json.loads(PLACES_CACHE.read_text(encoding='utf-8'))
        except json.JSONDecodeError:
            cache = {}
    lookup = places.PlaceLookup(
        kakao_key=settings.get('kakao_key') or None,
        naver_id=settings.get('naver_id') or None,
        naver_secret=settings.get('naver_secret') or None,
        cache=cache,
    )
    lookup.__dict__['_cache_file'] = PLACES_CACHE
    return lookup


def save_lookup(lookup) -> None:
    import json
    ensure_dirs()
    PLACES_CACHE.write_text(
        json.dumps(lookup.cache, ensure_ascii=False, indent=1), encoding='utf-8'
    )


def reclassify(transactions, settings) -> classifier.Classifier:
    """분류를 처음부터 다시 돌린다. 규칙이 바뀔 때마다 호출한다."""
    engine = engine_for(settings)
    engine.classify_all(transactions)
    classifier.detect_recurring_income(transactions)
    names = {settings['husband_name']: settings['husband_name'],
             settings['wife_name']: settings['wife_name']}
    couple.offset_spouse_transfers(transactions, names)
    return engine


def unclassified_count(transactions) -> int:
    return sum(1 for t in transactions if t.category == '미분류')


@app.context_processor
def inject_common():
    settings = store.load_settings()
    state = store.load_state()
    return {
        'settings': settings,
        'owners': store.owner_labels(settings),
        'has_data': bool(state['transactions']),
        'pending': unclassified_count(state['transactions']),
    }


# ------------------------------------------------------------------- 화면
@app.route('/')
def home():
    state = store.load_state()
    if state['transactions']:
        return redirect(url_for('dashboard'))
    return render_template('upload.html')


@app.route('/upload', methods=['GET', 'POST'])
def upload():
    settings = store.load_settings()
    if request.method == 'GET':
        return render_template('upload.html')

    labels = store.owner_labels(settings)
    incoming, assets, problems = [], {}, []

    for slot, label in zip(('husband_file', 'wife_file'), labels):
        uploaded = request.files.get(slot)
        if not uploaded or not uploaded.filename:
            continue
        temp = EXPORTS / f'_upload_{slot}.xlsx'
        ensure_dirs()
        uploaded.save(temp)
        try:
            one = parser.parse(temp, label)
        except parser.ParseError as e:
            problems.append(f'{label}: {e}')
            continue
        finally:
            temp.unlink(missing_ok=True)

        incoming.extend(one.transactions)
        assets[label] = {
            'assets': one.total_assets,
            'liabilities': one.total_liabilities,
            'net': one.net_worth,
            'investments': [{'name': a.name, 'group': a.group, **a.extra}
                            for a in one.investments],
            'loans': [{'name': a.name, 'group': a.group, **a.extra}
                      for a in one.loans],
        }

    for problem in problems:
        flash(problem, 'error')
    if not incoming:
        if not problems:
            flash('파일을 하나 이상 올려주세요.', 'error')
        return render_template('upload.html')

    state = store.load_state()
    merged = store.merge(state['transactions'], incoming)
    reclassify(merged, settings)
    store.save_state(merged, assets={**state.get('assets', {}), **assets})

    flash(f'{len(incoming):,}건을 읽었습니다. 전체 {len(merged):,}건.', 'ok')
    return redirect(url_for('review'))


@app.route('/review')
def review():
    settings = store.load_settings()
    state = store.load_state()
    transactions = state['transactions']
    if not transactions:
        return redirect(url_for('upload'))

    return render_template(
        'review.html',
        queue=aggregate.unclassified(transactions, limit=60),
        promoted=classifier.detect_recurring_income(list(transactions)),
        candidates=classifier.detect_transfer_candidates(transactions),
        options=[c for c in cat.ORDER if c != '미분류'],
        lookup_ready=bool(settings.get('kakao_key') or
                          (settings.get('naver_id') and settings.get('naver_secret'))),
        ai_ready=bool(settings.get('anthropic_key')),
    )


@app.route('/review/set', methods=['POST'])
def review_set():
    settings = store.load_settings()
    content = request.form.get('content', '').strip()
    category = request.form.get('category', '').strip()
    if not content or category not in cat.CATEGORIES:
        flash('카테고리를 고르지 못했습니다.', 'error')
        return redirect(url_for('review'))

    state = store.load_state()
    engine = engine_for(settings)
    engine.learn(content, category)
    engine.save_user_rules()

    transactions = state['transactions']
    reclassify(transactions, settings)
    store.save_state(transactions, assets=state.get('assets'))
    flash(f"'{content}' → {category}. 같은 상호가 전부 바뀌었습니다.", 'ok')
    return redirect(url_for('review'))


@app.route('/review/lookup', methods=['POST'])
def review_lookup():
    settings = store.load_settings()
    state = store.load_state()
    transactions = state['transactions']

    lookup = lookup_for(settings)
    if not lookup.enabled:
        flash('지도 조회 키가 없습니다. 설정에서 카카오 REST API 키를 넣어주세요.', 'error')
        return redirect(url_for('settings_page'))

    engine = engine_for(settings)
    pending = sorted({t.content for t in transactions if t.category == '미분류'})
    found = 0
    for merchant in pending:
        try:
            hit = lookup.lookup(merchant)
        except places.PlacesUnavailable as e:
            flash(str(e), 'error')
            break
        if hit:
            engine.learn(merchant, hit['category'])
            found += 1

    save_lookup(lookup)
    if found:
        engine.save_user_rules()
    reclassify(transactions, settings)
    store.save_state(transactions, assets=state.get('assets'))

    flash(f'지도에서 {found}개 상호를 찾아 분류했습니다. '
          f'(조회 {len(pending)}개 중)', 'ok' if found else 'error')
    return redirect(url_for('review'))


@app.route('/review/ai', methods=['POST'])
def review_ai():
    settings = store.load_settings()
    state = store.load_state()
    transactions = state['transactions']

    engine = engine_for(settings)
    try:
        result = ai_classify.apply(
            transactions, engine, api_key=settings.get('anthropic_key') or None
        )
    except ai_classify.AiUnavailable as e:
        flash(str(e), 'error')
        return redirect(url_for('settings_page'))

    reclassify(transactions, settings)
    store.save_state(transactions, assets=state.get('assets'))
    flash(f"Claude가 {len(result['applied'])}개를 분류했습니다. "
          f"확신이 낮은 {len(result['unsure'])}개는 아래에서 확인해 주세요.", 'ok')
    return redirect(url_for('review'))


@app.route('/dashboard')
def dashboard():
    settings = store.load_settings()
    state = store.load_state()
    transactions = state['transactions']
    if not transactions:
        return redirect(url_for('upload'))

    months = aggregate.months_of(transactions)
    month = request.args.get('month') or (months[-2] if len(months) > 1 else months[-1])
    if month not in months:
        month = months[-1]
    owner = request.args.get('owner') or ''
    owner = owner if owner in store.owner_labels(settings) else None

    idx = months.index(month)
    prev = months[idx - 1] if idx > 0 else month

    assets = state.get('assets', {})
    data = {
        'month': month,
        'months': months,
        'owner': owner or '',
        'summary': aggregate.summary(transactions, month, owner=owner),
        'prev': aggregate.summary(transactions, prev, owner=owner),
        'series': aggregate.monthly_series(transactions, owner=owner),
        'income_cat': aggregate.totals_by_category(transactions, cat.INCOME, month, owner),
        'fixed_cat': aggregate.totals_by_category(transactions, cat.FIXED, month, owner),
        'var_cat': aggregate.totals_by_category(transactions, cat.VARIABLE, month, owner),
        'save_cat': aggregate.totals_by_category(transactions, cat.SAVING, month, owner),
        'by_owner': aggregate.category_by_owner(
            transactions, cat.VARIABLE, month, store.owner_labels(settings)),
        'excluded': aggregate.excluded_total(transactions),
        'unclassified': aggregate.unclassified(transactions, limit=8),
        'unclassified_n': unclassified_count(transactions),
        'settlement': couple.settle(
            transactions, month,
            {o: o for o in store.owner_labels(settings)},
            split=settings.get('split', 'half')),
        'assets': sum(a.get('assets', 0) for a in assets.values()),
        'liabilities': sum(a.get('liabilities', 0) for a in assets.values()),
        'net': sum(a.get('net', 0) for a in assets.values()),
        'investments': [i for a in assets.values() for i in a.get('investments', [])],
        'loans': [l for a in assets.values() for l in a.get('loans', [])],
        'n_tx': len(transactions),
        'owners': store.owner_labels(settings),
        'uploaded': sorted(assets.keys()),
    }
    return render_template('dashboard.html', data=data)


@app.route('/settings', methods=['GET', 'POST'])
def settings_page():
    if request.method == 'POST':
        form = request.form
        before = store.load_settings()
        updates = {
            'husband_name': form.get('husband_name', '남편').strip() or '남편',
            'wife_name': form.get('wife_name', '아내').strip() or '아내',
            'split': form.get('split', 'half'),
        }

        # 이름을 바꾸면 이미 올린 거래의 소유자도 같이 바꿔야 한다.
        # 안 그러면 거래는 '남편' 소유인데 화면은 '호현'을 찾으면서
        # 업로드한 사람이 '미업로드'로 표시되고 소유자별 집계가 전부 빈다.
        renames = {
            before['husband_name']: updates['husband_name'],
            before['wife_name']: updates['wife_name'],
        }
        renames = {old: new for old, new in renames.items() if old != new}
        if renames:
            state = store.load_state()
            for tx in state['transactions']:
                if tx.owner in renames:
                    tx.owner = renames[tx.owner]
            assets = {renames.get(k, k): v for k, v in state.get('assets', {}).items()}
            if state['transactions'] or assets:
                store.save_state(state['transactions'], assets=assets)
        # 키는 빈 값으로 덮어쓰지 않는다 (수정 화면에서 지우고 저장하는 사고 방지)
        for field in ('kakao_key', 'naver_id', 'naver_secret', 'anthropic_key'):
            value = form.get(field, '').strip()
            if value:
                updates[field] = value
            elif form.get(f'clear_{field}'):
                updates[field] = ''
        store.save_settings(updates)
        flash('저장했습니다.', 'ok')
        return redirect(url_for('settings_page'))

    return render_template('settings.html')


@app.route('/reset', methods=['POST'])
def reset():
    store.clear_state()
    flash('업로드한 데이터를 지웠습니다. 분류 규칙과 설정은 남아 있습니다.', 'ok')
    return redirect(url_for('upload'))


@app.route('/export.xlsx')
def export_xlsx():
    from .excel import build_workbook
    settings = store.load_settings()
    state = store.load_state()
    if not state['transactions']:
        flash('먼저 파일을 올려주세요.', 'error')
        return redirect(url_for('upload'))

    ensure_dirs()
    path = EXPORTS / '부부가계부_올인원.xlsx'
    build_workbook(state['transactions'], state.get('assets', {}), settings, path)
    return send_file(path, as_attachment=True, download_name=path.name)


# ------------------------------------------------------------------ 실행
def serve(port: int = 8734, open_browser: bool = True) -> None:
    ensure_dirs()
    url = f'http://127.0.0.1:{port}/'
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    print(f'가계부가 열렸습니다: {url}\n종료하려면 이 창에서 Ctrl+C 를 누르세요.')
    try:
        from waitress import serve as waitress_serve
        waitress_serve(app, host='127.0.0.1', port=port, threads=4)
    except ImportError:
        app.run(host='127.0.0.1', port=port)
