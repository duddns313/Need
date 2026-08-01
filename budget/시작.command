#!/bin/bash
# macOS: 이 파일을 더블클릭하면 가계부가 열립니다.
# (처음 한 번은 우클릭 → 열기 를 눌러야 할 수 있습니다)
#
# 안내 문구는 budget/_launcher.py 가 출력합니다.
# .bat 과 달리 여기엔 한글을 써도 되지만, 두 파일의 동작을 같게 유지합니다.

cd "$(dirname "$0")/.." || exit 1

if command -v python3 > /dev/null 2>&1; then
  python3 budget/_launcher.py
elif command -v python > /dev/null 2>&1; then
  python budget/_launcher.py
else
  echo
  echo "  [!] 파이썬이 설치되어 있지 않습니다."
  echo
  echo "  1) https://www.python.org/downloads/ 접속"
  echo "  2) [Download Python] 버튼을 눌러 설치"
  echo "  3) 설치가 끝나면 이 파일을 다시 더블클릭"
  echo
fi

echo
read -r -p "  엔터를 누르면 창이 닫힙니다."
