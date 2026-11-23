"""캠슐랭 데이터 백업

- DB(가게·리뷰·회원)를 날짜별 파일로 복사하고, 최근 30개만 남깁니다.
- 리뷰 사진·영수증은 새로 생긴 파일만 백업 폴더에 추가로 복사합니다.
- 서버가 켜져 있어도 안전하게 백업됩니다. (SQLite 공식 백업 기능 사용)

백업 위치: 프로젝트 폴더 바깥의 campuslin_backups
           (예: Downloads\\campuslin\\campuslin_backups)
           → instance 폴더를 지워도 백업은 남아 있도록

직접 실행:   python backup_db.py
자동 실행:   serve.py가 켜질 때 1번, 그 뒤 6시간마다 자동으로 실행됩니다.

복구 방법:
  1) 서버 창(campuslin-server)을 닫는다
  2) campuslin_backups 안의 원하는 campuslin_날짜.db 를 복사해서
     instance\\campuslin.db 에 덮어쓴다
  3) start_campuslin.bat 으로 다시 켠다
"""
import glob
import os
import shutil
import sqlite3
from datetime import datetime

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")
BACKUP_DIR = os.getenv("BACKUP_DIR") or os.path.join(os.path.dirname(BASE_DIR), "campuslin_backups")
KEEP = 30   # DB 백업은 최근 30개까지 보관


def db_path():
    url = os.getenv("DATABASE_URL") or ""
    if url.startswith("sqlite:///"):
        return url[len("sqlite:///"):]
    return os.path.join(INSTANCE_DIR, "campuslin.db")


def copy_new_files(src_dir, dst_dir):
    """사진 폴더는 새로 생긴 파일만 복사 (이미 백업된 건 건너뜀)"""
    if not os.path.isdir(src_dir):
        return 0
    os.makedirs(dst_dir, exist_ok=True)
    n = 0
    for name in os.listdir(src_dir):
        src, dst = os.path.join(src_dir, name), os.path.join(dst_dir, name)
        if os.path.isfile(src) and not os.path.exists(dst):
            shutil.copy2(src, dst)
            n += 1
    return n


def make_backup(quiet=False):
    src = db_path()
    if not os.path.exists(src):
        if not quiet:
            print(f"[백업] DB 파일이 아직 없어요: {src}")
        return None
    os.makedirs(BACKUP_DIR, exist_ok=True)
    dst = os.path.join(BACKUP_DIR, f"campuslin_{datetime.now():%Y%m%d_%H%M%S}.db")

    # 서버가 쓰는 중이어도 깨지지 않게 SQLite 백업 기능으로 복사
    with sqlite3.connect(src) as s, sqlite3.connect(dst) as d:
        s.backup(d)

    # 오래된 DB 백업 정리 (최근 KEEP개만 유지)
    olds = sorted(glob.glob(os.path.join(BACKUP_DIR, "campuslin_*.db")))
    for old in olds[:-KEEP]:
        try:
            os.remove(old)
        except OSError:
            pass

    photos = copy_new_files(os.path.join(INSTANCE_DIR, "photos"), os.path.join(BACKUP_DIR, "photos"))
    receipts = copy_new_files(os.path.join(INSTANCE_DIR, "receipts"), os.path.join(BACKUP_DIR, "receipts"))
    print(f"[백업] {datetime.now():%m-%d %H:%M} 완료 → {dst} (새 사진 {photos}장, 영수증 {receipts}장)", flush=True)
    return dst


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE_DIR, ".env"))
    make_backup()
    print(f"백업 폴더: {BACKUP_DIR}")
