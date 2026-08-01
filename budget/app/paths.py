"""데이터 보관 위치.

가계부 데이터는 금융 정보라 시스템 폴더 여기저기에 흩어놓지 않는다.
실행 파일 옆 `budget_data/` 한 폴더에 모아서, 폴더째 복사하면 백업이 끝나게 한다.
"""
from __future__ import annotations

import sys
from pathlib import Path


def app_root() -> Path:
    """exe로 빌드된 상태면 exe가 있는 폴더, 소스 실행이면 budget/ 폴더."""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def bundle_root() -> Path:
    """PyInstaller가 templates/static을 풀어놓는 임시 폴더."""
    if getattr(sys, 'frozen', False):
        return Path(getattr(sys, '_MEIPASS'))
    return Path(__file__).resolve().parent


DATA = app_root() / 'budget_data'
SETTINGS_FILE = DATA / 'settings.json'
STATE_FILE = DATA / 'state.json'
PLACES_CACHE = DATA / 'places_cache.json'
USER_RULES = DATA / 'user_rules.json'
EXPORTS = DATA / 'exports'


def ensure_dirs() -> None:
    for d in (DATA, EXPORTS):
        d.mkdir(parents=True, exist_ok=True)
