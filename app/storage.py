"""
작업 기록 보관.

외부망 PC가 껐다 켜면 초기화되는 환경이라, 모든 산출물을 workspace/ 한 폴더에
모아두고 zip 하나로 통째로 내보내고/되돌릴 수 있게 했습니다.

  workspace/
    credentials.enc          암호화된 OC키·API키
    state.json               마지막 세션 포인터 등
    history.jsonl            모든 실행 이력 (append-only, 추적용)
    uploads/<세션>/          업로드 원본 파일
    sessions/<세션>.json     분석 결과 스냅샷
    law_snapshots/<키>.json  법령별 최신 상태 (개정 감지 기준선)
"""
from __future__ import annotations
import json
import shutil
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from .paths import EXPORTS, HISTORY_FILE, SESSIONS, SNAPSHOTS, STATE_FILE, UPLOADS, WORKSPACE, ensure_dirs, running_exe

CATEGORIES = {
    'cards': '요구사항 관리카드 (엑셀)',
    'domain': '분야별 요구사항 관리 문서',
    'internal': '내부 안전관리체계·규정',
}
CURRENT = '_current'


def now_stamp():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def new_session_id():
    return datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:4]


def read_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding='utf-8'))
        except json.JSONDecodeError:
            return {}
    return {}


def write_state(patch):
    ensure_dirs()
    state = read_state()
    state.update(patch)
    state['updated_at'] = now_stamp()
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    return state


def upload_dir(session_id, category):
    d = UPLOADS / session_id / category
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_uploads(session_id):
    out = {}
    for cat in CATEGORIES:
        d = UPLOADS / session_id / cat
        out[cat] = sorted(d.iterdir()) if d.exists() else []
    return out


def snapshot_current_uploads(session_id):
    """
    분석을 실행하는 시점의 영구 보관 문서를 세션 폴더로 복사합니다.
    나중에 '그때 무슨 파일로 이 결과가 나왔는지' 확인할 수 있게 하기 위함이고,
    영구 보관함(CURRENT) 자체는 건드리지 않습니다.
    """
    for cat, paths in list_uploads(CURRENT).items():
        dest = upload_dir(session_id, cat)
        for p in paths:
            shutil.copy2(p, dest / p.name)
    return list_uploads(session_id)


def delete_upload(session_id, category, filename):
    p = UPLOADS / session_id / category / filename
    if p.exists() and p.is_file():
        p.unlink()
        return True
    return False


def save_session(session_id, payload):
    ensure_dirs()
    payload['session_id'] = session_id
    payload['saved_at'] = now_stamp()
    p = SESSIONS / f'{session_id}.json'
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    write_state({'last_session': session_id})
    return p


def load_session(session_id):
    p = SESSIONS / f'{session_id}.json'
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding='utf-8'))


def list_sessions():
    ensure_dirs()
    out = []
    for p in sorted(SESSIONS.glob('*.json'), reverse=True):
        try:
            d = json.loads(p.read_text(encoding='utf-8'))
        except json.JSONDecodeError:
            continue
        counts = d.get('counts', {})
        highlights = []
        for row in d.get('rows', []):
            if row.get('status') not in ('outdated', 'missing', 'review'):
                continue
            ref = row.get('reference', {})
            rec = row.get('record') or {}
            highlights.append({
                'name': ref.get('name', ''),
                'effective': rec.get('effective', ''),
                'recommendation': ref.get('recommendation') or row.get('action', ''),
            })
        out.append({
            'session_id': d.get('session_id', p.stem),
            'saved_at': d.get('saved_at', ''),
            'mode': d.get('mode', ''),
            'total': counts.get('total', 0),
            'attention': counts.get('outdated', 0) + counts.get('missing', 0),
            'highlights': highlights[:5],
            'highlights_more': max(0, len(highlights) - 5),
        })
    return out


def _snap_path(target, seq):
    ensure_dirs()
    return SNAPSHOTS / f"{target}_{seq or 'unknown'}.json"


def compare_with_snapshot(record):
    """
    직전 실행 때 본 것과 달라졌는지 비교합니다.
    반환: {"changed": bool, "previous": {...} | None}
    """
    p = _snap_path(record.target, record.seq)
    prev = None
    if p.exists():
        try:
            prev = json.loads(p.read_text(encoding='utf-8'))
        except json.JSONDecodeError:
            prev = None
    changed = bool(prev) and prev.get('fingerprint') != record.fingerprint
    return {'changed': changed, 'previous': prev}


def write_snapshot(record):
    p = _snap_path(record.target, record.seq)
    data = record.to_dict()
    data['fingerprint'] = record.fingerprint
    data['seen_at'] = now_stamp()
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def log(event, **fields):
    ensure_dirs()
    entry = {'at': now_stamp(), 'event': event, **fields}
    with open(HISTORY_FILE, 'a', encoding='utf-8') as f:
        f.write(json.dumps(entry, ensure_ascii=False) + '\n')


def read_history(limit=300):
    if not HISTORY_FILE.exists():
        return []
    lines = HISTORY_FILE.read_text(encoding='utf-8').strip().splitlines()
    out = []
    for line in lines[-limit:]:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return list(reversed(out))


def export_zip(include_uploads=True):
    """workspace 전체를 zip 한 개로. USB나 네트워크 드라이브에 복사해두면 됩니다."""
    ensure_dirs()
    name = datetime.now().strftime('rsms-backup-%Y%m%d-%H%M%S.zip')
    out = EXPORTS / name
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for p in WORKSPACE.rglob('*'):
            if not p.is_file():
                continue
            rel = p.relative_to(WORKSPACE)
            if include_uploads and rel.parts and rel.parts[0] == 'uploads':
                continue
            z.write(p, arcname=str(Path('workspace') / rel))
    log('backup_exported', file=name, include_uploads=include_uploads)
    return out


def export_full_zip(include_uploads=True):
    """
    workspace뿐 아니라 실행 중인 exe 파일까지 함께 담습니다.
    이 zip 하나만 있으면 어느 PC에서든 풀어서 바로 더블클릭할 수 있습니다.
    exe로 실행 중일 때만 exe가 포함됩니다(개발용 python 실행 중에는 없음).
    """
    ensure_dirs()
    exe_path = running_exe()
    name = datetime.now().strftime('rsms-full-backup-%Y%m%d-%H%M%S.zip')
    out = EXPORTS / name
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        if exe_path and exe_path.exists():
            z.write(exe_path, arcname=exe_path.name)
        for p in WORKSPACE.rglob('*'):
            if not p.is_file():
                continue
            rel = p.relative_to(WORKSPACE)
            if include_uploads and rel.parts and rel.parts[0] == 'uploads':
                continue
            z.write(p, arcname=str(Path('workspace') / rel))
    log('full_backup_exported', file=name, include_uploads=include_uploads, included_exe=bool(exe_path))
    return out


def import_zip(zip_path, replace=False):
    """내보낸 zip을 되돌립니다. replace=True면 기존 workspace를 비우고 덮어씁니다."""
    ensure_dirs()
    if replace and WORKSPACE.exists():
        backup = WORKSPACE.parent / f'workspace.before-import-{datetime.now():%Y%m%d%H%M%S}'
        shutil.move(str(WORKSPACE), str(backup))
        ensure_dirs()
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        root_prefixed = all(n.startswith('workspace/') for n in names if n.strip())
        for n in names:
            if n.endswith('/'):
                continue
            rel = n[len('workspace/'):] if root_prefixed else n
            if not rel:
                continue
            dest = WORKSPACE / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            with z.open(n) as src, open(dest, 'wb') as dst:
                shutil.copyfileobj(src, dst)
    log('backup_imported', file=zip_path.name, replace=replace)
    return f'{zip_path.name} 복원 완료'
