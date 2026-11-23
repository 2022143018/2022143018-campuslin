"""🧪 시연용 샘플 리뷰 넣기 / 지우기

발표·시연 때 지도가 비어 보이지 않도록, 리뷰가 없는 가게에 샘플 리뷰를 넣습니다.
- 모든 샘플 리뷰에는 화면에 '🧪 시연용 샘플' 배지가 붙습니다. (진짜 학생 리뷰처럼 보이지 않게)
- 랭킹·개척자 배지·운영자 통계에서는 제외됩니다.
- 진짜 학생 리뷰가 있는 가게에는 넣지 않습니다.
- 실제 운영을 시작할 때 --clear 로 한 번에 지우세요. 진짜 리뷰는 건드리지 않습니다.

실행 (app.py가 있는 폴더에서):
    python sample_reviews.py              # 리뷰 없는 가게 40곳에 샘플 넣기
    python sample_reviews.py --count 80   # 80곳에 넣기
    python sample_reviews.py --clear      # 샘플 리뷰 전부 삭제
"""
import argparse
import random

from app import app
from models import Place, Review, School, User, db
import utils

SAMPLE_PROVIDER = "sample"
COMMENTS = [
    "", "", "",   # 한줄평 없는 10초 리뷰도 섞기
    "(샘플) 양이 넉넉한 편",
    "(샘플) 혼자 가기 편해요",
    "(샘플) 점심시간엔 줄이 있어요",
    "(샘플) 단체로 가기 좋아요",
    "(샘플) 가격 대비 괜찮아요",
    "(샘플) 학교에서 가까워요",
]
# 음식 종류별로 그럴듯한 가격대
PRICE_BY_CATEGORY = {"분식": ["p1"], "카페": ["p1", "p2"], "한식": ["p2"], "중식": ["p2"],
                     "일식": ["p2", "p3"], "양식": ["p3"], "술집": ["p3"], "기타": ["p2"]}


def sample_user(school):
    u = User.query.filter_by(provider=SAMPLE_PROVIDER, provider_id="demo").first()
    if not u:
        u = User(provider=SAMPLE_PROVIDER, provider_id="demo", name="시연용 샘플", school_id=school.id)
        db.session.add(u)
        db.session.flush()
    return u


def add(count, seed):
    rnd = random.Random(seed)
    school = School.query.order_by(School.id).first()
    user = sample_user(school)
    targets = [p for p in Place.query.filter(Place.school_id == school.id, Place.hidden.is_(False),
                                             Place.closed_report_count < utils.CLOSED_HIDE_THRESHOLD)
               if not utils.visible_reviews(p.reviews)]          # 리뷰가 하나도 없는 가게만
    rnd.shuffle(targets)
    targets = targets[:count]
    for p in targets:
        cat = (p.category_list() or ["기타"])[0]
        seats = rnd.sample(["solo", "small", "mid", "group"], k=rnd.choice([1, 2]))
        if cat == "술집" and "group" not in seats:
            seats.append("group")
        extras = ["late"] if cat == "술집" or rnd.random() < 0.15 else []
        db.session.add(Review(
            place_id=p.id, user_id=user.id, is_sample=True,
            rating=rnd.choices([3, 4, 5], weights=[2, 5, 3])[0],
            price=",".join(PRICE_BY_CATEGORY.get(cat, ["p2"])),
            tags=",".join(seats + extras),
            content=rnd.choice(COMMENTS),
        ))
    db.session.commit()
    print(f"🧪 샘플 리뷰 {len(targets)}개를 넣었어요. (리뷰 없는 가게 대상)")
    print("   지우려면: python sample_reviews.py --clear")


def clear():
    n = Review.query.filter_by(is_sample=True).delete()
    User.query.filter_by(provider=SAMPLE_PROVIDER).delete()
    db.session.commit()
    print(f"🧪 샘플 리뷰 {n}개를 지웠어요. 진짜 학생 리뷰는 그대로예요.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="시연용 샘플 리뷰 넣기/지우기")
    ap.add_argument("--count", type=int, default=40, help="샘플을 넣을 가게 수 (기본 40)")
    ap.add_argument("--clear", action="store_true", help="샘플 리뷰 전부 삭제")
    ap.add_argument("--seed", type=int, default=2026, help="같은 결과를 원하면 같은 숫자")
    args = ap.parse_args()
    with app.app_context():
        clear() if args.clear else add(args.count, args.seed)
