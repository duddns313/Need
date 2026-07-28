"""
자격증명 보관소.

사용자가 정한 '앱 비밀번호'로 키를 파생(PBKDF2)해서 OC키/API키를 암호화 저장합니다.
- 최초 실행: 비밀번호를 새로 정하고 키를 입력 -> credentials.enc 생성
- 이후 실행: 비밀번호만 입력하면 잠금 해제

비밀번호 자체는 어디에도 저장하지 않습니다. 잊어버리면 credentials.enc를 지우고
키를 다시 입력하는 수밖에 없습니다(파일 자체는 다시 만들면 되므로 복구 불가 아님).
"""
import base64
import json
import os
from typing import Optional
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from .paths import CRED_FILE, WORKSPACE

_SALT_LEN = 16
_ITERATIONS = 390000


def _derive(password, salt):
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=_ITERATIONS)
    key = base64.urlsafe_b64encode(kdf.derive(password.encode('utf-8')))
    return Fernet(key)


def is_initialized():
    return CRED_FILE.exists()


def save(password, data):
    """자격증명 dict를 암호화해서 저장. 기존 파일은 덮어씁니다."""
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    salt = os.urandom(_SALT_LEN)
    token = _derive(password, salt).encrypt(json.dumps(data, ensure_ascii=False).encode('utf-8'))
    CRED_FILE.write_bytes(salt + token)


def load(password):
    """비밀번호가 맞으면 dict, 틀리면 None."""
    if not CRED_FILE.exists():
        return None
    raw = CRED_FILE.read_bytes()
    salt = raw[:_SALT_LEN]
    token = raw[_SALT_LEN:]
    try:
        return json.loads(_derive(password, salt).decrypt(token).decode('utf-8'))
    except (InvalidToken, ValueError):
        return None


def mask(value, keep=4):
    """화면 표시용 마스킹. 키 원문은 화면에 다시 뿌리지 않습니다."""
    if not value:
        return ''
    if len(value) <= keep:
        return '•' * len(value)
    return '•' * (len(value) - keep) + value[-keep:]
