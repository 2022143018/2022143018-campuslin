"""학교 주변 음식점·카페를 카카오 로컬 API(공식)로 불러와 DB에 등록하는 스크립트

실행 (PyCharm 터미널, app.py가 있는 폴더에서):
    python import_places.py --dry-run          # 몇 곳이 들어올지 미리 보기 (DB 저장 안 함)
    python import_places.py                    # 반경 1km 음식점 등록
    python import_places.py --radius 1.5 --cafe  # 반경 1.5km, 카페까지 포함

필요한 것: .env 의 KAKAO_REST_KEY (카카오 개발자 콘솔 → 플랫폼 키 → REST API 키)

- 리뷰는 만들지 않습니다. 가게 정보(이름·주소·좌표·전화·카카오맵 링크)만 등록하고,
  리뷰는 학생들이 직접 남깁니다. (첫 리뷰 작성자 = 개척자 배지)
- 이미 등록된 가게(같은 카카오 장소 ID)는 건너뜁니다. 여러 번 실행해도 중복되지 않아요.
"""
import argparse
import math
import os
import sys
import time

import requests

from app import app
from models import Place, School, db
import utils

API_URL = "https://dapi.kakao.com/v2/local/search/category.json"
MAX_PAGEABLE = 45   # 카카오 API는 한 번 검색에 최대 45개(15개 x 3페이지)까지만 줌


def guess_category(category_name):
    """카카오 분류(예: '음식점 > 한식 > 국밥')를 캠슐랭 음식 종류로 변환"""
    rules = [
        ("술집", ["술집", "호프", "요리주점", "포차", "이자카야", "칵테일바", "와인바", "주점"]),
        ("카페", ["카페", "디저트", "제과", "베이커리", "아이스크림"]),
        ("분식", ["분식", "김밥", "떡볶이"]),
        ("중식", ["중식", "중국요리"]),
        ("일식", ["일식", "초밥", "돈까스", "라멘", "우동", "회"]),
        ("양식", ["양식", "피자", "패스트푸드", "햄버거", "이탈리안", "스테이크", "파스타"]),
        ("한식", ["한식"]),
    ]
    for cat, words in rules:
        if any(w in category_name for w in words):
            return cat
    return "기타"


def search_rect(session, code, rect, found, depth=0):
    """사각형 영역 검색. 결과가 45개를 넘으면 영역을 4등분해서 다시 검색(빠짐없이 가져오기)"""
    x1, y1, x2, y2 = rect
    params = {"category_group_code": code, "rect": f"{x1},{y1},{x2},{y2}", "size": 15, "page": 1}
    resp = session.get(API_URL, params=params, timeout=10)
    if resp.status_code != 200:
        raise RuntimeError(f"카카오 API 오류 {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    total = data["meta"]["total_count"]

    if total > MAX_PAGEABLE and depth < 6:
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        for sub in [(x1, y1, mx, my), (mx, y1, x2, my), (x1, my, mx, y2), (mx, my, x2, y2)]:
            search_rect(session, code, sub, found, depth + 1)
        return

    page = 1
    while True:
        for d in data["documents"]:
            found[d["id"]] = d
        if data["meta"]["is_end"] or page >= 3:
            break
        page += 1
        params["page"] = page
        time.sleep(0.1)   # API에 부담 주지 않게 살짝 쉬기
        data = session.get(API_URL, params=params, timeout=10).json()


def main():
    parser = argparse.ArgumentParser(description="학교 주변 가게 자동 등록 (카카오 로컬 API)")
    parser.add_argument("--radius", type=float, default=1.0, help="학교 기준 반경 km (기본 1.0)")
    parser.add_argument("--cafe", action="store_true", help="카페도 포함")
    parser.add_argument("--dry-run", action="store_true", help="DB에 저장하지 않고 미리 보기만")
    args = parser.parse_args()

    key = os.getenv("KAKAO_REST_KEY")
    if not key:
        sys.exit("[오류] .env 에 KAKAO_REST_KEY(REST API 키)를 넣어 주세요.")

    with app.app_context():
        school = School.query.order_by(School.id).first()
        max_km = float(os.getenv("MAX_DISTANCE_KM", "3"))
        if args.radius > max_km:
            sys.exit(f"[오류] 반경은 MAX_DISTANCE_KM({max_km:g}km) 이하로 지정해 주세요.")

        # 반경을 감싸는 사각형 (위도 1도 ≈ 111km)
        dlat = args.radius / 111.0
        dlng = args.radius / (111.0 * math.cos(math.radians(school.lat)))
        rect = (school.lng - dlng, school.lat - dlat, school.lng + dlng, school.lat + dlat)

        session = requests.Session()
        session.headers["Authorization"] = f"KakaoAK {key}"

        found = {}
        codes = [("FD6", "음식점")] + ([("CE7", "카페")] if args.cafe else [])
        for code, label in codes:
            before = len(found)
            print(f"{label} 검색 중...")
            search_rect(session, code, rect, found)
            print(f"  → {len(found) - before}곳 찾음")

        existing = {p.kakao_id for p in Place.query.filter_by(school_id=school.id)}
        new_places, skipped_far = [], 0
        for d in found.values():
            lat, lng = float(d["y"]), float(d["x"])
            if utils.haversine_km(school.lat, school.lng, lat, lng) > args.radius:
                skipped_far += 1   # 사각형 모서리 쪽(원 밖)은 제외
                continue
            if d["id"] in existing:
                continue
            new_places.append(Place(
                school_id=school.id, kakao_id=d["id"], name=d["place_name"][:100],
                address=(d.get("road_address_name") or d.get("address_name") or "")[:255],
                phone=(d.get("phone") or "")[:30], kakao_url=(d.get("place_url") or "")[:255] or None,
                lat=lat, lng=lng, category=guess_category(d.get("category_name", "")), created_by=None,
            ))

        print(f"\n{school.name} 반경 {args.radius:g}km: 새로 등록할 가게 {len(new_places)}곳 "
              f"(이미 등록됨 {len(found) - skipped_far - len(new_places)}곳, 반경 밖 {skipped_far}곳 제외)")
        for p in new_places[:10]:
            print(f"  - {p.name} [{p.category}] {p.address}")
        if len(new_places) > 10:
            print(f"  ... 외 {len(new_places) - 10}곳")

        if args.dry_run:
            print("\n(미리 보기 모드라 저장하지 않았습니다. --dry-run 을 빼고 실행하면 등록됩니다.)")
            return
        db.session.add_all(new_places)
        db.session.commit()
        print(f"\n완료! {len(new_places)}곳을 등록했습니다.")


if __name__ == "__main__":
    main()
