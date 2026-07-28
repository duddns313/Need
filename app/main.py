"""
철도안전관리체계 요구사항 추적기 — 로컬 웹앱 본체.

브라우저에서 열리지만 서버는 이 PC 안에서만 돕니다(127.0.0.1).
외부에서 접속할 수 없고, 업로드한 문서도 이 PC를 떠나지 않습니다.
단 하나의 예외는 AI 분석 모드로, 이때만 문서 발췌본이 Claude API로 전송됩니다.
"""
from __future__ import annotations
import os
import re
import secrets
import threading
import webbrowser
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import quote
from flask import Flask, abort, flash, jsonify, redirect, render_template, request, send_file, session, url_for
from . import storage, vault
from .analyzer import (
    AiUnavailable, DEFAULT_MODEL, Reference, build_domain_index, compare_ai, enrich_with_domain,
    evaluate, extract_from_cards, find_uncarded_citations, norm_name, relevant_excerpt, strip_parens,
)
from .extractors import SUPPORTED, extract
from .lawapi import LawApiError, LawClient
from .paths import UPLOADS, bundle_root, ensure_dirs

app = Flask(__name__, template_folder=str(bundle_root() / 'templates'), static_folder=str(bundle_root() / 'static'))
app.secret_key = secrets.token_hex(32)
app.config['MAX_CONTENT_LENGTH'] = 209715200

_UNLOCKED: 'dict[str, dict]' = {}
_JOBS: 'dict[str, dict]' = {}
_JOB_LOCK = threading.Lock()

STATUS_META = {
    'outdated': ('개정 반영 필요', 'stop'),
    'missing': ('내부 미반영', 'stop'),
    'review': ('확인 필요', 'caution'),
    'ok': ('현행 일치', 'clear'),
    'notfound': ('법령정보 조회 실패', 'caution'),
}

_UNSAFE = re.compile('[<>:"/\\\\|?*\\x00-\\x1f]')


def safe_name(raw, fallback_idx=0):
    """
    경로 조작만 막고 한글 파일명은 그대로 둡니다.
    werkzeug의 secure_filename은 한글을 통째로 지워버려서 쓸 수 없습니다.
    """
    name = _UNSAFE.sub('_', Path(raw).name).strip(' .')
    if not name or name in frozenset({'.', '..'}):
        name = f'파일-{fallback_idx + 1}'
    return name[:150]


def creds():
    tok = session.get('tok')
    if tok:
        return _UNLOCKED.get(tok)
    return None


def require_unlock():
    if creds() is None:
        return redirect(url_for('login'))


def _latest_session_id():
    sessions = storage.list_sessions()
    if sessions:
        return sessions[0]['session_id']


@app.context_processor
def inject_globals():
    c = creds() or {}
    return {
        'unlocked': bool(c),
        'has_oc': bool(c.get('oc')),
        'has_api': bool(c.get('api_key')),
        'cur_session': session.get('work_session') or _latest_session_id(),
        'categories': storage.CATEGORIES,
    }


@app.route('/', methods=['GET'])
def index():
    if creds() is None:
        return redirect(url_for('login'))
    return redirect(url_for('workspace'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    first_run = not vault.is_initialized()
    if request.method == 'POST':
        pw = request.form.get('password', '')
        if len(pw) < 4:
            flash('비밀번호는 4자 이상으로 정해주세요.', 'error')
            return redirect(url_for('login'))
        if first_run:
            if pw != request.form.get('password2', ''):
                flash('두 번 입력한 비밀번호가 서로 다릅니다.', 'error')
                return redirect(url_for('login'))
            data = {
                'oc': request.form.get('oc', '').strip(),
                'api_key': request.form.get('api_key', '').strip(),
                'model': request.form.get('model', '').strip() or DEFAULT_MODEL,
            }
            if not data['oc']:
                flash('OC 인증키는 반드시 입력해야 합니다.', 'error')
                return redirect(url_for('login'))
            vault.save(pw, data)
            storage.log('vault_created')
        else:
            data = vault.load(pw)
            if data is None:
                flash('비밀번호가 맞지 않습니다.', 'error')
                storage.log('unlock_failed')
                return redirect(url_for('login'))
        tok = secrets.token_hex(16)
        _UNLOCKED[tok] = data
        session['tok'] = tok
        session['password_hint'] = True
        storage.log('unlocked')
        return redirect(url_for('workspace'))
    return render_template('login.html', first_run=first_run, default_model=DEFAULT_MODEL)


@app.route('/logout', methods=['POST'])
def logout():
    _UNLOCKED.pop(session.get('tok', ''), None)
    session.clear()
    return redirect(url_for('login'))


@app.route('/settings', methods=['GET', 'POST'])
def settings():
    r = require_unlock()
    if r:
        return r
    c = creds()
    if request.method == 'POST':
        pw = request.form.get('password', '')
        if vault.load(pw) is None:
            flash('현재 비밀번호가 맞지 않아 변경하지 않았습니다.', 'error')
            return redirect(url_for('settings'))
        new = dict(c)
        for field in ('oc', 'api_key', 'model'):
            val = request.form.get(field, '').strip()
            if val:
                new[field] = val
        if request.form.get('clear_api'):
            new['api_key'] = ''
        vault.save(pw, new)
        _UNLOCKED[session['tok']] = new
        storage.log('credentials_updated')
        flash('설정을 저장했습니다.', 'ok')
        return redirect(url_for('settings'))
    return render_template(
        'settings.html',
        oc_mask=vault.mask(c.get('oc', '')),
        api_mask=vault.mask(c.get('api_key', '')),
        model=c.get('model', DEFAULT_MODEL),
    )


@app.route('/api/ping-law')
def ping_law():
    if creds() is None:
        return jsonify(ok=False, message='잠금 상태입니다.')
    try:
        ok, msg = LawClient(creds().get('oc', '')).ping()
        return jsonify(ok=ok, message=msg)
    except LawApiError as e:
        return jsonify(ok=False, message=str(e))


@app.route('/workspace')
def workspace():
    r = require_unlock()
    if r:
        return r
    files = storage.list_uploads(storage.CURRENT)
    return render_template('workspace.html', files=files, supported=', '.join(SUPPORTED), recent=storage.list_sessions()[:5])


@app.route('/upload/<category>', methods=['POST'])
def upload(category):
    r = require_unlock()
    if r:
        return r
    if category not in storage.CATEGORIES:
        abort(404)
    dest = storage.upload_dir(storage.CURRENT, category)
    saved = 0
    for f in request.files.getlist('files'):
        if f and f.filename:
            f.save(dest / safe_name(f.filename, saved))
            saved += 1
    storage.log('files_uploaded', category=category, count=saved)
    flash(f'{storage.CATEGORIES[category]}: {saved}개 파일을 올렸습니다. 다음에 로그인해도 그대로 남아있습니다.', 'ok')
    return redirect(url_for('workspace'))


@app.route('/upload/<category>/delete', methods=['POST'])
def remove_upload(category):
    r = require_unlock()
    if r:
        return r
    fn = request.form.get('filename', '')
    if storage.delete_upload(storage.CURRENT, category, fn):
        storage.log('file_removed', category=category, filename=fn)
    return redirect(url_for('workspace'))


@app.route('/analyze', methods=['POST'])
def analyze():
    r = require_unlock()
    if r:
        return r
    current = storage.list_uploads(storage.CURRENT)
    if not current.get('cards'):
        flash('요구사항 관리카드(①, 엑셀)를 먼저 올려주세요.', 'error')
        return redirect(url_for('workspace'))
    mode = request.form.get('mode', 'keyword')
    c = creds()
    if mode == 'ai' and not c.get('api_key'):
        flash('AI 모드를 쓰려면 설정 화면에서 Claude API 키를 먼저 등록해주세요.', 'error')
        return redirect(url_for('workspace'))
    sid = storage.new_session_id()
    session['work_session'] = sid
    storage.snapshot_current_uploads(sid)
    with _JOB_LOCK:
        if _JOBS.get(sid, {}).get('running'):
            flash('이미 분석이 진행 중입니다.', 'error')
            return redirect(url_for('progress', session_id=sid))
        _JOBS[sid] = {
            'running': True, 'phase': '준비 중', 'done': 0, 'total': 0,
            'log': [], 'error': None, 'finished': False,
        }
    t = threading.Thread(target=_run_analysis, args=(sid, mode, dict(c)), daemon=True)
    t.start()
    return redirect(url_for('progress', session_id=sid))


def _note(sid, text):
    job = _JOBS.get(sid)
    if job is not None:
        job['log'].append(text)
        job['log'] = job['log'][-60:]


def _uploaded_internal_match(name, internal_filenames):
    """
    카드에 적힌 내규명이 실제로 업로드된 파일 중에 있는지 찾습니다.
    완전 포함 매칭 대신 유사도로 비교합니다 — 엑셀에 "약물처리"처럼 줄여
    적혀 있고 실제 파일명은 "약물검사 처리"인 경우처럼, 중간에 글자가
    끼어들면 부분 문자열 매칭은 실패하기 때문입니다.
    """
    key = norm_name(strip_parens(name))
    if not key:
        return None
    best_fn, best_score = None, 0
    for fn in internal_filenames:
        fkey = norm_name(strip_parens(Path(fn).stem))
        if not fkey:
            continue
        if key in fkey or fkey in key:
            return fn
        score = SequenceMatcher(None, key, fkey).ratio()
        if score > best_score:
            best_score, best_fn = score, fn
    if best_score >= 0.72:
        return best_fn
    return None


def _run_analysis(sid, mode, c):
    job = _JOBS[sid]
    try:
        uploads = storage.list_uploads(sid)
        card_tables, domain_docs, internal_docs, read_issues = [], [], [], []
        job['phase'] = '문서 읽는 중'
        for cat, paths in uploads.items():
            for p in paths:
                got = extract(p)
                _note(sid, f"{p.name} — {'읽음 ' + str(got.char_count) + '자' if got.ok else '실패'}")
                if not got.ok:
                    read_issues.append({'file': p.name, 'category': cat, 'note': got.note})
                    continue
                if cat == 'cards':
                    card_tables.extend(got.tables)
                    continue
                if cat == 'domain':
                    domain_docs.append((p.name, got.text))
                    continue
                internal_docs.append((p.name, got.text))
        if not card_tables:
            raise RuntimeError('요구사항 관리카드(①, 엑셀)에서 표를 읽지 못했습니다. 이 파일이 있어야 어떤 항목을 관리하는지 알 수 있습니다. 파일을 올렸는지 확인해주세요.')
        job['phase'] = '관리카드에서 대상 항목 추출 중'
        refs = extract_from_cards(card_tables)
        if domain_docs:
            enrich_with_domain(refs, build_domain_index(domain_docs))
        if not refs:
            raise RuntimeError('관리카드에서 유효한 항목(요구사항 명칭)을 찾지 못했습니다. 엑셀 서식을 확인해주세요.')
        _note(sid, f'관리 대상 항목 {len(refs)}건')

        internal_text = '\n\n'.join(t for _, t in internal_docs)
        internal_filenames = [fn for fn, _ in internal_docs]
        client = LawClient(c['oc'])
        job['total'] = len(refs)
        job['phase'] = '법령정보센터 최신본 조회 및 대조 중'
        rows = []
        for i, ref in enumerate(refs, 1):
            job['done'] = i
            _note(sid, f'[{i}/{len(refs)}] {ref.name}')
            row = {'reference': ref.to_dict()}
            try:
                rec = client.best_match(ref.name)
                if rec is None:
                    search_url = f'https://www.law.go.kr/LSW/lsSc.do?menuId=1&query={quote(ref.name)}'
                    row.update(
                        status='notfound',
                        headline='법령정보센터에서 찾지 못함',
                        detail=f"'{ref.name}' 명칭으로 법령·행정규칙 모두 조회했으나 결과가 없습니다. 명칭이 바뀌었거나 사내 전용 기준일 수 있습니다.",
                        action='아래 링크로 직접 검색해 명칭이 바뀌었는지 확인해주세요.',
                        record=None, changed=False, internal_matches=[], search_url=search_url,
                    )
                    rows.append(row)
                    continue

                snap = storage.compare_with_snapshot(rec)
                pending = []
                if rec.target == 'law':
                    try:
                        pending = [p.to_dict() for p in client.pending(ref.name)]
                    except LawApiError:
                        pending = []

                if mode == 'ai' and c.get('api_key'):
                    verdict = compare_ai(rec, ref, internal_text, c['api_key'], c.get('model', DEFAULT_MODEL))
                else:
                    verdict = evaluate(rec, ref, pending)
                storage.write_snapshot(rec)

                internal_matches = [
                    {'name': name, 'file': _uploaded_internal_match(name, internal_filenames)}
                    for name in ref.related_internal
                ]
                row.update(
                    record=rec.to_dict(),
                    changed=snap['changed'],
                    previous=snap['previous'],
                    status=verdict.get('status', 'review'),
                    headline=verdict.get('headline', ''),
                    detail=verdict.get('detail', ''),
                    action=verdict.get('action', ''),
                    internal_matches=internal_matches,
                    pending=pending,
                )
                if snap['changed'] and row['status'] == 'ok':
                    row['status'] = 'review'
                    row['headline'] = '직전 확인 이후 개정 발생'
                if row['status'] == 'outdated' and ref.related_internal and not any(m['file'] for m in internal_matches):
                    row['status'] = 'missing'
                    row['headline'] = '관련 내규 파일이 업로드되지 않음'
                    row['detail'] = f"카드에 기재된 관련 내규({', '.join(ref.related_internal)})가 업로드된 파일 중에 없습니다."
                    row['action'] = '해당 내규 파일을 올리고 다시 분석해주세요.'
                if row['status'] in ('review', 'outdated'):
                    matched_file = next((m['file'] for m in internal_matches if m['file']), None)
                    if matched_file:
                        doc_text = next((t for fn, t in internal_docs if fn == matched_file), '')
                        excerpt = relevant_excerpt(doc_text, ref.name, window=900)
                        if excerpt:
                            row['internal_excerpt'] = {'file': matched_file, 'text': excerpt[:2000]}
                rows.append(row)
            except LawApiError as e:
                row.update(
                    status='notfound', headline='조회 실패', detail=str(e),
                    action='OC키 승인 상태를 확인해주세요.',
                    record=None, changed=False, internal_matches=[],
                )
                rows.append(row)
                continue

        counts = {k: sum(1 for r in rows if r.get('status') == k) for k in STATUS_META}
        counts['total'] = len(rows)
        counts['changed'] = sum(1 for r in rows if r.get('changed'))

        uncarded = find_uncarded_citations(internal_docs, [r.name for r in refs])
        if uncarded:
            _note(sid, f'내규가 인용하지만 카드엔 없는 법령 후보 {len(uncarded)}건')

        storage.save_session(sid, {
            'mode': mode,
            'rows': rows,
            'counts': counts,
            'read_issues': read_issues,
            'uncarded': uncarded,
            'uploads': {k: [p.name for p in v] for k, v in uploads.items()},
        })

        attention_items = [
            {
                'name': r['reference']['name'],
                'code': r['reference'].get('code', ''),
                'effective': (r.get('record') or {}).get('effective', ''),
                'recommendation': r['reference'].get('recommendation') or r.get('action', ''),
            }
            for r in rows if r['status'] in ('outdated', 'missing')
        ][:20]
        storage.log(
            'analysis_completed', session_id=sid, mode=mode, **counts,
            attention_items=attention_items, uncarded_count=len(uncarded),
        )
        job['phase'] = '완료'
    except (AiUnavailable, LawApiError, RuntimeError) as e:
        job['error'] = str(e)
        storage.log('analysis_failed', session_id=sid, error=str(e))
    except Exception as e:
        job['error'] = f'예기치 못한 오류: {e}'
        storage.log('analysis_failed', session_id=sid, error=repr(e))
    finally:
        job['running'] = False
        job['finished'] = True


@app.route('/progress/<session_id>')
def progress(session_id):
    r = require_unlock()
    if r:
        return r
    return render_template('progress.html', sid=session_id)


@app.route('/api/progress/<session_id>')
def api_progress(session_id):
    job = _JOBS.get(session_id)
    if job is None:
        return jsonify(finished=True, error=None, phase='대기 중', done=0, total=0, log=[])
    return jsonify(
        finished=job['finished'], error=job['error'], phase=job['phase'],
        done=job['done'], total=job['total'], log=job['log'][-12:],
    )


@app.route('/result/<session_id>')
def result(session_id):
    r = require_unlock()
    if r:
        return r
    data = storage.load_session(session_id)
    if data is None:
        flash('해당 분석 결과를 찾지 못했습니다.', 'error')
        return redirect(url_for('workspace'))
    order = {'outdated': 0, 'missing': 1, 'review': 2, 'notfound': 3, 'ok': 4}
    rows = sorted(data['rows'], key=lambda r: order.get(r['status'], 9))
    return render_template('result.html', d=data, rows=rows, meta=STATUS_META)


@app.route('/recommendation/<session_id>')
def recommendation(session_id):
    r = require_unlock()
    if r:
        return r
    data = storage.load_session(session_id)
    if data is None:
        flash('해당 분석 결과를 찾지 못했습니다.', 'error')
        return redirect(url_for('workspace'))
    order = {'outdated': 0, 'missing': 1, 'review': 2}
    rows = sorted(
        (r for r in data['rows'] if r['status'] in order),
        key=lambda r: order.get(r['status'], 9),
    )
    return render_template('recommendation.html', d=data, rows=rows, meta=STATUS_META)


@app.route('/history')
def history():
    r = require_unlock()
    if r:
        return r
    return render_template('history.html', sessions=storage.list_sessions(), events=storage.read_history(200))


@app.route('/backup')
def backup():
    r = require_unlock()
    if r:
        return r
    from .paths import EXPORTS, running_exe
    files = sorted(EXPORTS.glob('*.zip'), key=lambda p: p.stat().st_mtime, reverse=True)
    return render_template(
        'backup.html',
        is_exe=running_exe() is not None,
        files=[{'name': p.name, 'size': f'{p.stat().st_size / 1e6:.1f} MB'} for p in files],
    )


@app.route('/backup/export', methods=['POST'])
def backup_export():
    r = require_unlock()
    if r:
        return r
    include = bool(request.form.get('include_uploads'))
    p = storage.export_zip(include_uploads=include)
    return send_file(p, as_attachment=True, download_name=p.name)


@app.route('/backup/export-full', methods=['POST'])
def backup_export_full():
    r = require_unlock()
    if r:
        return r
    include = bool(request.form.get('include_uploads'))
    p = storage.export_full_zip(include_uploads=include)
    return send_file(p, as_attachment=True, download_name=p.name)


@app.route('/backup/import', methods=['POST'])
def backup_import():
    r = require_unlock()
    if r:
        return r
    f = request.files.get('zipfile')
    if not (f and f.filename):
        flash('복원할 zip 파일을 선택해주세요.', 'error')
        return redirect(url_for('backup'))
    from .paths import EXPORTS
    tmp = EXPORTS / f'_import-{secrets.token_hex(4)}.zip'
    f.save(tmp)
    try:
        msg = storage.import_zip(tmp, replace=bool(request.form.get('replace')))
        flash(msg + ' — 변경 사항을 반영하려면 프로그램을 다시 실행해주세요.', 'ok')
        return redirect(url_for('backup'))
    except Exception as e:
        flash(f'복원 실패: {e}', 'error')
        return redirect(url_for('backup'))
    finally:
        tmp.unlink(missing_ok=True)


def serve(port=8733, open_browser=True):
    ensure_dirs()
    storage.log('app_started', port=port)
    url = f'http://127.0.0.1:{port}/'
    if open_browser:
        threading.Timer(1, lambda: webbrowser.open(url)).start()
    print(f'\n  철도안전관리체계 요구사항 추적기\n  브라우저에서 열기: {url}\n  종료하려면 이 창에서 Ctrl+C 를 누르세요.\n')
    try:
        from waitress import serve as waitress_serve
        waitress_serve(app, host='127.0.0.1', port=port, threads=8)
    except ImportError:
        app.run(host='127.0.0.1', port=port, debug=False)


if __name__ == '__main__':
    serve(port=int(os.environ.get('RSMS_PORT', 8733)))
