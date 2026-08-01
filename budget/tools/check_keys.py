"""발급받은 키가 실제로 동작하는지 확인한다.

    python budget/tools/check_keys.py

키는 환경변수에서 읽는다:
    KAKAO_REST_KEY          카카오 REST API 키
    NAVER_CLIENT_ID         네이버 애플리케이션 Client ID
    NAVER_CLIENT_SECRET     네이버 애플리케이션 Client Secret
    ANTHROPIC_API_KEY       Claude API 키

키를 다 넣을 필요는 없다. 넣은 것만 검사한다.
실패하면 '왜 실패했는지'와 '어디를 고쳐야 하는지'를 알려준다.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from budget.app import places  # noqa: E402

# 실제로 존재하는 가게 — 조회가 되면 키와 권한이 모두 정상이라는 뜻
PROBE = '스타벅스 연희점'

OK = '  \033[32m통과\033[0m'
NG = '  \033[31m실패\033[0m'
SKIP = '  \033[90m건너뜀\033[0m'


def check_kakao() -> bool:
    key = os.environ.get('KAKAO_REST_KEY')
    print('[카카오 로컬 API]')
    if not key:
        print(f'{SKIP} KAKAO_REST_KEY 환경변수가 없습니다.')
        return False
    try:
        import requests
    except ImportError:
        print(f'{NG} requests 패키지가 없습니다:  pip install requests')
        return False

    try:
        response = requests.get(
            places.KAKAO_URL,
            headers={'Authorization': f'KakaoAK {key}'},
            params={'query': PROBE, 'size': 5},
            timeout=places.TIMEOUT,
        )
    except Exception as e:
        print(f'{NG} 네트워크 오류: {e}')
        return False

    if response.status_code == 401:
        print(f'{NG} 401 — 키가 틀렸습니다. REST API 키가 맞는지 확인하세요')
        print('       (JavaScript 키·네이티브 앱 키를 잘못 넣는 실수가 흔합니다)')
        return False
    if response.status_code == 403:
        print(f'{NG} 403 — 키는 맞지만 권한이 없습니다.')
        print('       카카오디벨로퍼스 > 내 애플리케이션 > 제품 설정 > 카카오맵 > 사용 설정을 켜세요.')
        print(f'       응답: {response.text[:200]}')
        return False
    if response.status_code != 200:
        print(f'{NG} HTTP {response.status_code}: {response.text[:200]}')
        return False

    docs = response.json().get('documents', [])
    if not docs:
        print(f'{NG} 호출은 됐는데 결과가 0건입니다. 검색어를 바꿔 다시 확인해 보세요.')
        return False

    print(f'{OK} 조회 성공 — {len(docs)}건')
    for doc in docs[:3]:
        raw = doc.get('category_name', '')
        print(f'       {doc.get("place_name")}  |  {raw}  →  {places.map_category(raw)}')
    return True


def check_naver() -> bool:
    client_id = os.environ.get('NAVER_CLIENT_ID')
    secret = os.environ.get('NAVER_CLIENT_SECRET')
    print('\n[네이버 지역검색 API]')
    if not (client_id and secret):
        print(f'{SKIP} NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 환경변수가 없습니다.')
        return False
    try:
        import requests
    except ImportError:
        print(f'{NG} requests 패키지가 없습니다:  pip install requests')
        return False

    try:
        response = requests.get(
            places.NAVER_URL,
            headers={'X-Naver-Client-Id': client_id, 'X-Naver-Client-Secret': secret},
            params={'query': PROBE, 'display': 5},
            timeout=places.TIMEOUT,
        )
    except Exception as e:
        print(f'{NG} 네트워크 오류: {e}')
        return False

    if response.status_code == 401:
        print(f'{NG} 401 — Client ID 또는 Secret이 틀렸습니다.')
        return False
    if response.status_code == 403:
        print(f'{NG} 403 — 애플리케이션에 "검색" API가 추가되지 않았습니다.')
        print('       네이버 개발자센터 > 내 애플리케이션 > API 설정에서 검색을 추가하세요.')
        return False
    if response.status_code != 200:
        print(f'{NG} HTTP {response.status_code}: {response.text[:200]}')
        return False

    items = response.json().get('items', [])
    if not items:
        print(f'{NG} 호출은 됐는데 결과가 0건입니다.')
        return False

    import re
    print(f'{OK} 조회 성공 — {len(items)}건')
    for item in items[:3]:
        raw = item.get('category', '')
        title = re.sub(r'<[^>]+>', '', item.get('title', ''))
        print(f'       {title}  |  {raw}  →  {places.map_category(raw)}')
    return True


def check_claude() -> bool:
    key = os.environ.get('ANTHROPIC_API_KEY')
    print('\n[Claude API]')
    if not key:
        print(f'{SKIP} ANTHROPIC_API_KEY 환경변수가 없습니다.')
        return False
    try:
        from anthropic import Anthropic
    except ImportError:
        print(f'{NG} anthropic 패키지가 없습니다:  pip install anthropic')
        return False

    try:
        client = Anthropic(api_key=key)
        response = client.messages.create(
            model='claude-opus-5',
            max_tokens=64,
            messages=[{'role': 'user', 'content': 'OK 라고만 답하세요.'}],
        )
    except Exception as e:
        message = str(e)
        if 'authentication' in message.lower() or '401' in message:
            print(f'{NG} 키가 틀렸습니다.')
        elif 'credit' in message.lower() or 'billing' in message.lower():
            print(f'{NG} 크레딧이 없습니다. console.anthropic.com > Billing 에서 충전하세요.')
        else:
            print(f'{NG} {message[:300]}')
        return False

    text = next((b.text for b in response.content if b.type == 'text'), '')
    print(f'{OK} 응답 받음 — "{text.strip()[:40]}"')
    return True


def main() -> None:
    print('키 점검을 시작합니다. 하나라도 통과하면 지도 조회를 쓸 수 있습니다.\n')
    results = [check_kakao(), check_naver(), check_claude()]
    print()
    if results[0] or results[1]:
        print('지도 조회 사용 가능 — 미분류 상호를 업종으로 채울 수 있습니다.')
    else:
        print('지도 조회 불가 — 규칙 엔진만으로 동작합니다 (미분류가 남습니다).')
    if results[2]:
        print('Claude 사용 가능 — 지도에 없는 상호까지 처리합니다.')
    sys.exit(0 if any(results) else 1)


if __name__ == '__main__':
    main()
