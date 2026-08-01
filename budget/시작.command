#!/bin/bash
# macOS: 이 파일을 더블클릭하면 가계부가 열립니다.
# (처음 한 번은 우클릭 → 열기 를 눌러야 할 수 있습니다)

cd "$(dirname "$0")/.." || exit 1

echo
echo "  부부 공동 가계부를 시작합니다."
echo

if ! command -v python3 > /dev/null 2>&1; then
  echo "  [!] 파이썬이 설치되어 있지 않습니다."
  echo
  echo "  1) https://www.python.org/downloads/ 에 접속"
  echo "  2) [Download Python] 버튼을 눌러 설치"
  echo "  3) 설치가 끝나면 이 파일을 다시 더블클릭"
  echo
  read -r -p "  엔터를 누르면 닫힙니다."
  exit 1
fi

if ! python3 -c "import flask, openpyxl, waitress" > /dev/null 2>&1; then
  echo "  처음 실행이라 필요한 것들을 받는 중입니다. 1~2분 걸립니다..."
  echo
  python3 -m pip install --quiet --disable-pip-version-check -r requirements.txt || {
    echo
    echo "  [!] 설치에 실패했습니다. 인터넷 연결을 확인해 주세요."
    read -r -p "  엔터를 누르면 닫힙니다."
    exit 1
  }
fi

echo "  브라우저가 곧 열립니다."
echo "  가계부를 끄려면 이 터미널 창을 닫으세요."
echo

python3 budget/run.py
