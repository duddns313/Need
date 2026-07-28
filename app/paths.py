"""
워크스페이스 경로 결정.

외부망 PC가 리셋되는 환경을 고려해서, 데이터는 시스템 폴더가 아니라
**실행 파일 바로 옆의 workspace 폴더**에 모읍니다.
그래야 폴더 하나만 통째로 복사하면 백업이 끝납니다.
"""
import os
import sys
from pathlib import Path


def app_root():
    """exe로 빌드된 상태면 exe가 있는 폴더, 소스 실행이면 프로젝트 루트."""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def bundle_root():
    """PyInstaller가 templates/static을 풀어놓는 임시 폴더."""
    if getattr(sys, 'frozen', False):
        return Path(getattr(sys, '_MEIPASS'))
    return Path(__file__).resolve().parent


def running_exe():
    """exe로 실행 중이면 그 exe 파일 경로. 소스(python) 실행 중이면 None."""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable)


WORKSPACE = app_root() / 'workspace'
UPLOADS = WORKSPACE / 'uploads'
SESSIONS = WORKSPACE / 'sessions'
SNAPSHOTS = WORKSPACE / 'law_snapshots'
EXPORTS = app_root() / 'exports'
CRED_FILE = WORKSPACE / 'credentials.enc'
HISTORY_FILE = WORKSPACE / 'history.jsonl'
STATE_FILE = WORKSPACE / 'state.json'


def ensure_dirs():
    for d in (WORKSPACE, UPLOADS, SESSIONS, SNAPSHOTS, EXPORTS):
        d.mkdir(parents=True, exist_ok=True)
