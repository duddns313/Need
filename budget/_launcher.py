"""시작.bat / 시작.command 가 부르는 준비 담당.

윈도우 CMD는 .bat 파일을 UTF-8로 읽지 않는다. 한글을 넣으면 명령어까지
깨져서 엉뚱하게 실행된다(실제로 그 사고가 났다). 그래서 .bat 은 영문만 두고,
사람이 읽을 안내는 전부 여기서 출력한다. 파이썬은 콘솔 코드페이지에 맞춰
한글을 제대로 찍는다.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NEEDED = ('flask', 'openpyxl', 'waitress')


def say(text: str = '') -> None:
    """콘솔이 한글을 못 찍는 환경(드묾)에서도 죽지 않게."""
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode('ascii', 'replace').decode('ascii'))


def missing() -> list[str]:
    out = []
    for name in NEEDED:
        try:
            __import__(name)
        except ImportError:
            out.append(name)
    return out


def install() -> bool:
    requirements = ROOT / 'requirements.txt'
    command = [sys.executable, '-m', 'pip', 'install',
               '--disable-pip-version-check', '--quiet']
    command += ['-r', str(requirements)] if requirements.exists() else list(NEEDED)

    result = subprocess.run(command)
    if result.returncode != 0:
        say()
        say('  [!] 필요한 것들을 받지 못했습니다.')
        say('      인터넷 연결을 확인하고 다시 실행해 주세요.')
        say()
        say('      계속 안 되면 아래를 그대로 복사해 알려주세요:')
        say(f'      {" ".join(command)}')
        return False
    return True


def main() -> int:
    say()
    say('  부부 공동 가계부')
    say('  ' + '─' * 34)
    say()

    if sys.version_info < (3, 9):
        say(f'  [!] 파이썬이 너무 낮은 버전입니다 (현재 {sys.version.split()[0]}).')
        say('      python.org 에서 최신 버전을 설치해 주세요.')
        return 1

    if missing():
        say('  처음 실행이라 필요한 것들을 받는 중입니다. 1~2분 걸립니다...')
        say()
        if not install():
            return 1
        still = missing()
        if still:
            say(f'  [!] 아직 준비가 안 된 것: {", ".join(still)}')
            return 1
        say('  준비가 끝났습니다.')
        say()

    say('  브라우저가 곧 열립니다.')
    say('  가계부를 끄려면 이 검은 창을 닫으세요.')
    say()

    sys.path.insert(0, str(ROOT))
    from budget.app.main import serve

    try:
        serve(port=8734, open_browser=True)
    except KeyboardInterrupt:
        say()
        say('  종료합니다.')
    except OSError as e:
        say()
        say(f'  [!] 서버를 시작하지 못했습니다: {e}')
        say('      가계부가 이미 켜져 있는지 확인해 보세요.')
        say('      (브라우저에서 http://127.0.0.1:8734 접속)')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
