"""부부 공동 가계부 — 실행 진입점.

  실행:  python budget/run.py
  종료:  이 창에서 Ctrl+C

브라우저가 자동으로 열립니다. 안 열리면 http://127.0.0.1:8734 로 접속하세요.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from budget.app.main import serve  # noqa: E402

if __name__ == '__main__':
    port = int(os.environ.get('BUDGET_PORT', '8734'))
    try:
        serve(port=port, open_browser='--no-browser' not in sys.argv)
    except KeyboardInterrupt:
        print('\n종료합니다.')
