"""
개발용 / exe 진입점.

  소스 실행 :  python run.py
  exe 실행  :  RSMS_요구사항추적기.exe  (더블클릭하면 브라우저가 열립니다)
"""
import os
import sys
from app.main import serve

if __name__ == '__main__':
    port = int(os.environ.get('RSMS_PORT', '8733'))
    try:
        serve(port=port, open_browser='--no-browser' not in sys.argv)
    except KeyboardInterrupt:
        print('\n종료합니다.')
