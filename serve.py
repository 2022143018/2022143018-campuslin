"""캠슐랭 실제 서비스용 실행 파일

개발할 때:      app.py 실행 (디버그 모드, 코드 고치면 자동 재시작)
실제 서비스:    python serve.py  → 내 PC는 터널(ngrok)과 함께, Replit은 Run 버튼으로 실행

- Waitress: 여러 사람이 동시에 접속해도 버티는 운영용 서버 (윈도우에서 동작)
- ProxyFix: 터널(https) 뒤에서 돌 때 주소를 https로 올바르게 만들어 줌
            → 카카오 로그인 돌아오는 주소(Redirect URI)가 맞게 만들어짐
"""
import os
import sys

from waitress import serve
from werkzeug.middleware.proxy_fix import ProxyFix

from app import DEV_LOGIN, PROVIDERS, app

problems = []
if DEV_LOGIN:
    problems.append("DEV_LOGIN=1 → 닉네임만으로 아무나 로그인할 수 있어요. .env에서 DEV_LOGIN=0 으로 바꾸세요.")
if not os.getenv("SECRET_KEY") or os.getenv("SECRET_KEY") in ("dev-only-change-me", "여기에-랜덤-문자열"):
    problems.append("SECRET_KEY가 기본값이에요. 긴 랜덤 문자열로 바꾸세요.")
if not PROVIDERS:
    problems.append("카카오/구글 로그인 키가 없어서 아무도 로그인할 수 없어요. .env의 KAKAO_REST_KEY를 확인하세요.")
if not os.getenv("MAIL_SERVER"):
    problems.append("MAIL_SERVER가 비어 있어 학교 인증 메일이 실제로 발송되지 않아요.")
if problems:
    print("\n[실행 중단] 실제 서비스로 열기 전에 고쳐야 할 것:")
    for p in problems:
        print("  -", p)
    sys.exit(1)

app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.config.update(
    SESSION_COOKIE_SECURE=True,      # https에서만 로그인 쿠키 전송
    REMEMBER_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    PREFERRED_URL_SCHEME="https",
)

def backup_loop(hours=6):
    """서버가 켜질 때 1번, 그 뒤 6시간마다 DB·사진 자동 백업"""
    import time
    from backup_db import make_backup
    while True:
        try:
            make_backup(quiet=True)
        except Exception as e:   # 백업이 실패해도 서버는 계속 동작
            print(f"[백업] 실패: {e}", flush=True)
        time.sleep(hours * 60 * 60)


if __name__ == "__main__":
    import threading
    on_replit = bool(os.getenv("REPL_ID"))           # Replit에서 실행 중인지
    if not on_replit:                                # 내 PC에서만 자동 백업 (Replit은 시연용)
        threading.Thread(target=backup_loop, daemon=True).start()
    port = int(os.getenv("PORT", 5000))
    host = "0.0.0.0" if on_replit else "127.0.0.1"   # Replit은 외부 접속을 받아야 함
    print(f"\n캠슐랭 운영 서버 시작: http://{host}:{port}")
    print("끄려면 Ctrl + C\n")
    serve(app, host=host, port=port, threads=8)
