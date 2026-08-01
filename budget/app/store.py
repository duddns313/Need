"""설정과 거래 데이터 보관.

DB는 쓰지 않는다. 거래 수천 건은 JSON으로 충분히 빠르고, 사용자가 파일을
직접 열어보거나 통째로 백업할 수 있는 편이 낫다.
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict

from .parser import Transaction
from .paths import SETTINGS_FILE, STATE_FILE, ensure_dirs

DEFAULT_SETTINGS = {
    'husband_name': '남편',
    'wife_name': '아내',
    'kakao_key': '',
    'naver_id': '',
    'naver_secret': '',
    'anthropic_key': '',
    'split': 'half',          # half | income
    'lan_enabled': False,     # 폰 등 같은 와이파이 기기에서 접속 허용
    'access_pin': '',         # 위를 켰을 때 요구할 숫자 암호
    'budgets': {},            # 카테고리 -> 월 예산
    'start_balance': 0,
}


def load_settings() -> dict:
    if SETTINGS_FILE.exists():
        try:
            saved = json.loads(SETTINGS_FILE.read_text(encoding='utf-8'))
        except json.JSONDecodeError:
            saved = {}
    else:
        saved = {}
    return {**DEFAULT_SETTINGS, **saved}


def save_settings(settings: dict) -> None:
    ensure_dirs()
    merged = {**load_settings(), **settings}
    SETTINGS_FILE.write_text(
        json.dumps(merged, ensure_ascii=False, indent=1), encoding='utf-8'
    )


def owner_labels(settings: dict) -> list[str]:
    return [settings['husband_name'], settings['wife_name']]


# ------------------------------------------------------------- 거래 저장
def _tx_to_dict(tx: Transaction) -> dict:
    row = asdict(tx)
    row['date'] = tx.date.isoformat()
    return row


def _tx_from_dict(row: dict) -> Transaction:
    row = dict(row)
    row['date'] = dt.date.fromisoformat(row['date'])
    return Transaction(**row)


def save_state(transactions, assets=None, meta=None) -> None:
    ensure_dirs()
    payload = {
        'saved_at': dt.datetime.now().isoformat(timespec='seconds'),
        'meta': meta or {},
        'assets': assets or {},
        'transactions': [_tx_to_dict(t) for t in transactions],
    }
    STATE_FILE.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {'transactions': [], 'assets': {}, 'meta': {}}
    try:
        payload = json.loads(STATE_FILE.read_text(encoding='utf-8'))
    except json.JSONDecodeError:
        return {'transactions': [], 'assets': {}, 'meta': {}}
    payload['transactions'] = [_tx_from_dict(r) for r in payload.get('transactions', [])]
    return payload


def clear_state() -> None:
    if STATE_FILE.exists():
        STATE_FILE.unlink()


#: 사람이 직접 정한 값. 같은 파일을 다시 올려도 이건 지키다.
USER_DECISIONS = ('spike', 'shared')


def merge(existing, incoming) -> list:
    """이미 저장된 거래에 새 거래를 합친다.

    uid가 같으면 새로 읽은 것으로 갈아끼우되, **사람이 직접 정한 값은 옮겨온다.**
    안 그러면 같은 파일을 다시 올릴 때마다 '이건 일회성' 같은 판단이 사라진다.
    (카테고리는 규칙 파일에 남아 있어 다시 분류되므로 옮길 필요가 없다.)
    """
    by_uid = {t.uid: t for t in existing}
    for tx in incoming:
        previous = by_uid.get(tx.uid)
        if previous is not None:
            for field in USER_DECISIONS:
                setattr(tx, field, getattr(previous, field))
        by_uid[tx.uid] = tx
    return sorted(by_uid.values(), key=lambda t: (t.date, t.time))
