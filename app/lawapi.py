"""
국가법령정보 공동활용(open.law.go.kr) Open API 클라이언트.

법령(law)만이 아니라 행정규칙(admrul)까지 함께 조회합니다.
'철도안전관리체계 기술기준' 같은 문서는 법률이 아니라 국토교통부 고시 =
행정규칙으로 등록되어 있어서, admrul을 빼면 아예 검색되지 않습니다.

  목록조회  https://www.law.go.kr/DRF/lawSearch.do
  본문조회  https://www.law.go.kr/DRF/lawService.do

⚠ 응답 JSON의 키 이름은 target별로 다르고 법제처 쪽 변경 가능성도 있습니다.
   _pick() / _norm() 이 여러 후보 키를 순서대로 훑도록 되어 있으니,
   실제 응답을 보고 후보 목록만 보완하면 됩니다.
   (Claude Code에서 `python -m app.lawapi <OC키> 철도안전` 으로 원본 응답을 찍어볼 수 있습니다.)
"""
from __future__ import annotations
import json
import re
import sys
import time
from dataclasses import dataclass, asdict
from typing import Any, Iterable
import requests

SEARCH_URL = 'https://www.law.go.kr/DRF/lawSearch.do'
SERVICE_URL = 'https://www.law.go.kr/DRF/lawService.do'
TARGETS = {
    'law': '법령',
    'admrul': '행정규칙',
    'eflaw': '시행예정법령',
}
_TIMEOUT = 20
_PAUSE = 0.35


class LawApiError(RuntimeError):
    pass


@dataclass
class LawRecord:
    target: str
    target_label: str
    name: str
    seq: str
    promulgated: str
    effective: str
    ministry: str
    revision: str
    detail_url: str
    promulgation_no: str
    history_code: str
    raw: dict

    def to_dict(self):
        d = asdict(self)
        d.pop('raw', None)
        return d

    @property
    def fingerprint(self):
        return f'{self.target}:{self.seq}:{self.promulgated}:{self.effective}'


def _pick(d, *keys, default=''):
    for k in keys:
        v = d.get(k)
        if v not in (None, '', []):
            return str(v).strip()
    return default


def _as_list(v):
    if v is None:
        return []
    if isinstance(v, list):
        return v
    return [v]


def _find_items(payload):
    """
    응답 루트 키가 target마다 다릅니다(LawSearch/AdmRulSearch 등).
    루트를 한 겹 벗기고, 그 안에서 리스트로 보이는 값을 찾아 돌려줍니다.
    """
    if not isinstance(payload, dict):
        return []
    for root in payload.values():
        if not isinstance(root, dict):
            continue
        for key in ('law', 'admrul', 'AdmRul', 'Law', 'list'):
            if key in root:
                return _as_list(root[key])
        for v in root.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
    return []


def _norm(item, target):
    return LawRecord(
        target=target,
        target_label=TARGETS.get(target, target),
        name=_pick(item, '법령명한글', '행정규칙명', '법령명', '제목', '법령약칭명'),
        seq=_pick(item, '법령일련번호', '행정규칙일련번호', '일련번호', 'ID'),
        promulgated=_pick(item, '공포일자', '발령일자'),
        effective=_pick(item, '시행일자'),
        ministry=_pick(item, '소관부처명', '소관부처', '발령기관', '담당부서명'),
        revision=_pick(item, '제개정구분명', '제개정구분', '구분'),
        detail_url=_pick(item, '법령상세링크', '행정규칙상세링크', '상세링크'),
        promulgation_no=_pick(item, '공포번호', '발령번호'),
        history_code=_pick(item, '현행연혁코드', '현행연혁구분'),
        raw=item,
    )


class LawClient:
    def __init__(self, oc, display=40):
        if not oc:
            raise LawApiError('OC 인증키가 비어 있습니다. 설정 화면에서 먼저 등록해주세요.')
        self.oc = oc.strip()
        self.display = display
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'RSMS-Req-Tracker/0.1'})

    def _get(self, url, params):
        params = {'OC': self.oc, 'type': 'JSON', **params}
        try:
            res = self.session.get(url, params=params, timeout=_TIMEOUT)
            if res.status_code != 200:
                raise LawApiError(f'HTTP {res.status_code} — OC키가 승인되었는지 확인해주세요.')
            body = res.text.strip()
            if body.startswith('<'):
                raise LawApiError('JSON 대신 HTML이 반환되었습니다. OC키 승인 상태 또는 해당 target 서비스 신청 여부를 확인해주세요.')
            return json.loads(body)
        except requests.RequestException as e:
            raise LawApiError(f'법령정보센터에 접속하지 못했습니다: {e}') from e
        except json.JSONDecodeError as e:
            raise LawApiError(f'응답을 해석하지 못했습니다: {body[:200]}') from e

    def search(self, query, target='law'):
        """목록조회. target: law(법령) | admrul(행정규칙)"""
        payload = self._get(SEARCH_URL, {
            'target': target,
            'query': query,
            'display': self.display,
            'page': 1,
        })
        time.sleep(_PAUSE)
        return [_norm(it, target) for it in _find_items(payload)]

    def search_all(self, query, targets=('law', 'admrul')):
        """법령 + 행정규칙을 모두 훑습니다."""
        found = []
        for t in targets:
            found.extend(self.search(query, t))
        return found

    @staticmethod
    def _pick_best(candidates, name):
        if not candidates:
            return None
        cleaned = name.replace(' ', '')
        exact = [c for c in candidates if c.name.replace(' ', '') == cleaned]
        if exact:
            return exact[0]
        starts = [c for c in candidates if c.name.replace(' ', '').startswith(cleaned)]
        if starts:
            return sorted(starts, key=lambda c: len(c.name))[0]
        return sorted(candidates, key=lambda c: len(c.name))[0]

    def best_match(self, name):
        """
        문서에서 뽑아낸 법령명과 가장 잘 맞는 1건.
        곧바로 찾지 못하면, 이름이 쉼표로 여러 개 묶여 있거나(엑셀에서 흔함)
        괄호 설명이 붙어 있는 경우를 가정하고 한 번 더 시도합니다.
        """
        candidates = self.search_all(name)
        hit = self._pick_best(candidates, name)
        if hit:
            return hit
        for part in re.split('[,、]', name):
            part = part.strip()
            if len(part) < 4 or part == name:
                continue
            hit = self._pick_best(self.search_all(part), part)
            if hit:
                return hit
        stripped = re.sub('[\\(（][^)）]*[\\)）]', '', name).strip()
        if stripped and stripped != name and len(stripped) >= 4:
            hit = self._pick_best(self.search_all(stripped), stripped)
            if hit:
                return hit
        return None

    def pending(self, name):
        """
        아직 시행되지 않은 예고된 개정(시행예정법령)을 찾습니다.
        행정규칙(고시 등)에는 이 조회가 없어서 법령만 대상입니다.
        """
        try:
            candidates = self.search(name, 'eflaw')
            cleaned = name.replace(' ', '')
            matched = [c for c in candidates if c.name.replace(' ', '') == cleaned]
            pending = [c for c in matched if c.history_code == '시행예정']
            return sorted(pending, key=lambda c: c.effective)
        except LawApiError:
            return []

    def body(self, rec):
        """본문조회. 조문 비교가 필요할 때 사용합니다."""
        key = 'ID' if rec.target == 'law' else 'LID'
        params = {'target': rec.target, key: rec.seq}
        if rec.target == 'law':
            params = {'target': 'law', 'ID': rec.seq}
        else:
            params = {'target': 'admrul', 'ID': rec.seq}
        payload = self._get(SERVICE_URL, params)
        time.sleep(_PAUSE)
        return payload

    def ping(self):
        """설정 화면의 '연결 확인' 버튼용."""
        try:
            got = self.search('철도안전법', 'law')
            if not got:
                return (False, '연결은 되었으나 결과가 비어 있습니다. 서비스 신청 상태를 확인해주세요.')
            return (True, f"정상 — '{got[0].name}' 조회 성공")
        except LawApiError as e:
            return (False, str(e))


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print('사용법: python -m app.lawapi <OC키> <검색어> [target]')
        raise SystemExit(1)
    oc, q = sys.argv[1], sys.argv[2]
    tg = sys.argv[3] if len(sys.argv) > 3 else 'law'
    client = LawClient(oc)
    raw = client._get(SEARCH_URL, {
        'target': tg,
        'query': q,
        'display': 5,
        'page': 1,
    })
    print(json.dumps(raw, ensure_ascii=False, indent=2)[:4000])
