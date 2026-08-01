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
    Flask, flash, redirect, render_template, request, send_file, session, url_for,
)

from . import (
    ai_classify, aggregate, categories as cat, classifier, couple, outliers,
    parser, places, store,
)
from .paths import EXPORTS, PLACES_CACHE, USER_RULES, bundle_root, ensure_dirs

app = Flask(
    __name__,
    template_folder=str(bundle_root() / 'templates'),
    static_folder=str(bundle_root() / 'static'),
)
app.secret_key = secrets.token_hex(32)
app.config['MAX_CONTENT_LENGTH'] = 52428800   # 50MB — 뱅샐 엑셀은 보통 1MB 미만

LOOPBACK = ('127.0.0.1', '::1', 'localhost')


def local_ip() -> str:
    """같은 와이파이에서 접속할 때 쓰는 이 PC의 주소."""
    import socket
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(('8.8.8.8', 80))    # 실제로 보내지 않는다. 경로만 물어본다
        return probe.getsockname()[0]
    except OSError:
        return '127.0.0.1'
    finally:
        probe.close()


@app.before_request
def guard_remote():
    """폰 등 다른 기기에서 들어오면 접속 암호를 받는다.

    이 PC(127.0.0.1)에서 여는 경우는 묻지 않는다. 같은 와이파이의 다른 기기가
    들어올 때만 막는다 — 통장 내역이 들어 있는 화면이라 그냥 열어둘 수 없다.
    """
    if request.remote_addr in LOOPBACK:
        return None
    if request.endpoint in ('static', 'unlock'):
        return None
    settings = store.load_settings()
    pin = settings.get('access_pin', '')
    if not pin:
        return ('<meta charset="utf-8"><p style="font:16px system-ui;padding:24px">'
                '이 PC의 가계부 <b>설정</b> 화면에서 "폰에서 접속 허용"을 켜고 '
                '접속 암호를 정해 주세요.</p>', 403)
    if session.get('unlocked') == pin:
        return None
    return redirect(url_for('unlock'))


@app.route('/unlock', methods=['GET', 'POST'])
def unlock():
    settings = store.load_settings()
    pin = settings.get('access_pin', '')
    if request.remote_addr in LOOPBACK:
        return redirect(url_for('home'))
    if request.method == 'POST':
        if pin and request.form.get('pin', '').strip() == pin:
            session['unlocked'] = pin
            session.permanent = True
            return redirect(url_for('home'))
        flash('암호가 맞지 않습니다.', 'error')
    return render_template('unlock.html')


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

    # 폰에서 끝없이 스크롤하지 않도록 조금씩 보여준다.
    # (안 자르면 정리 화면이 37,000px 넘게 늘어난다 — 실제로 그랬다.)
    # 이름은 page_no — 템플릿의 `page`는 네비게이션 강조용으로 이미 쓰고 있다
    page_no = max(1, min(int(request.args.get('page', 1) or 1), 40))
    per_page = 12

    all_queue = aggregate.unclassified(transactions)
    all_spikes = outliers.find(transactions)

    return render_template(
        'review.html',
        queue=all_queue[:page_no * per_page],
        queue_total=len(all_queue),
        spikes=all_spikes[:page_no * per_page],
        spike_total=len(all_spikes),
        page_no=page_no,
        per_page=per_page,
        promoted=classifier.detect_recurring_income(list(transactions)),
        candidates=classifier.detect_transfer_candidates(transactions),
        decided=[r for r in outliers.find(transactions, include_decided=True)
                 if r['spike']],
        options=[c for c in cat.ORDER if c != '미분류'],
        lookup_ready=bool(settings.get('kakao_key') or
                          (settings.get('naver_id') and settings.get('naver_secret'))),
        ai_ready=bool(settings.get('anthropic_key')),
    )


@app.route('/review/spike', methods=['POST'])
def review_spike():
    """평소와 다른 큰 거래를 '일회성'인지 '평소'인지 사람이 정한다."""
    state = store.load_state()
    transactions = state['transactions']
    decision = request.form.get('decision', '')
    uid = request.form.get('uid', '')

    if request.form.get('all_once'):
        changed = outliers.mark_all(
            transactions, [r['uid'] for r in outliers.find(transactions)], outliers.ONCE)
        flash(f'{changed}건을 일회성으로 표시했습니다.', 'ok')
    elif outliers.mark(transactions, uid, decision):
        label = {'once': '일회성', 'normal': '평소 지출', '': '미확인'}[decision]
        flash(f'{label}으로 표시했습니다.', 'ok')
    else:
        flash('표시하지 못했습니다.', 'error')
        return redirect(url_for('review'))

    store.save_state(transactions, assets=state.get('assets'))
    return redirect(url_for('review'))


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

    # '일회성' 거래를 빼고 평소 씀씀이만 보는 모드
    plain = bool(request.args.get('plain'))
    once_amount = outliers.once_total(transactions, month, owner)
    if plain:
        transactions = outliers.without_once(transactions)
        months = aggregate.months_of(transactions) or months
        if month not in months:
            month = months[-1]

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
        'plain': plain,
        'once_amount': once_amount,
        'spike_pending': len(outliers.find(state['transactions'])),
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
            'lan_enabled': bool(form.get('lan_enabled')),
            'access_pin': ''.join(ch for ch in form.get('access_pin', '') if ch.isdigit()),
        }
        if updates['lan_enabled'] and len(updates['access_pin']) < 4:
            flash('폰에서 접속하려면 숫자 4자리 이상의 암호가 필요합니다.', 'error')
            updates['lan_enabled'] = False

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

    return render_template('settings.html', lan_url=f'http://{local_ip()}:8734')


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
    settings = store.load_settings()
    lan = bool(settings.get('lan_enabled')) and len(settings.get('access_pin', '')) >= 4
    host = '0.0.0.0' if lan else '127.0.0.1'

    url = f'http://127.0.0.1:{port}/'
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    print(f'가계부가 열렸습니다: {url}')
    if lan:
        print(f'폰에서는 같은 와이파이로 접속: http://{local_ip()}:{port}')
        print('  (접속하면 설정해 둔 숫자 암호를 물어봅니다)')
    else:
        print('폰에서 쓰려면 설정 화면에서 "폰에서 접속 허용"을 켜세요.')
    print('종료하려면 이 창에서 Ctrl+C 를 누르세요.')

    try:
        from waitress import serve as waitress_serve
        waitress_serve(app, host=host, port=port, threads=4)
    except ImportError:
        app.run(host=host, port=port)
