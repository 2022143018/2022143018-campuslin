"""태그 정의, 리뷰 집계, 이메일 발송 등 도우미 함수"""
import math
import os
import re
import smtplib
from collections import Counter
from email.message import EmailMessage

# ---------------- 필터/태그 정의 ----------------
PRICE_TAGS = [("p1", "7천 원 이하"), ("p2", "7천~1만 원"), ("p3", "1만~2만 원"), ("p4", "2만 원 이상")]
SEAT_TAGS = [("solo", "혼밥 가능"), ("small", "2~4인"), ("mid", "5~10인"), ("group", "단체석(10인+)")]
EXTRA_TAGS = [("late", "밤 10시 이후 영업"), ("discount", "학생 할인")]
CATEGORIES = ["한식", "중식", "일식", "양식", "분식", "술집", "카페", "기타"]

PRICE_KEYS = {k for k, _ in PRICE_TAGS}
TAG_KEYS = {k for k, _ in SEAT_TAGS + EXTRA_TAGS}
LABELS = dict(PRICE_TAGS + SEAT_TAGS + EXTRA_TAGS)

STALE_DAYS = 180           # 마지막 리뷰가 이보다 오래되면 '최근 리뷰 없음' 표시
REPORT_HIDE_THRESHOLD = 3   # 신고 3회 이상이면 리뷰 숨김
CLOSED_HIDE_THRESHOLD = 3   # 폐업 신고 3회 이상이면 가게 숨김

# 간단한 욕설 필터 (필요하면 단어 추가)
BAD_WORDS = ["시발", "씨발", "ㅅㅂ", "병신", "ㅂㅅ", "개새끼", "좆", "존나", "미친놈", "꺼져"]

ALLOWED_IMAGE_EXT = {"jpg", "jpeg", "png", "webp", "heic"}
MAX_PHOTOS = 3            # 리뷰 1개당 음식 사진 최대 장수
PHOTO_MAX_PX = 1280       # 사진 긴 변을 이 크기로 줄여서 저장


def has_bad_word(text):
    compact = re.sub(r"[\s\W_]", "", text or "")
    return any(w in compact for w in BAD_WORDS)


def visible_reviews(reviews):
    return [r for r in reviews if (r.report_count or 0) < REPORT_HIDE_THRESHOLD]


def real_reviews(reviews):
    """숨김 안 된 리뷰 중 시연용 샘플을 뺀 '진짜 학생 리뷰'"""
    return [r for r in visible_reviews(reviews) if not r.is_sample]


def visible_photos(review):
    return [ph for ph in review.photos if (ph.report_count or 0) < REPORT_HIDE_THRESHOLD]


def aggregate(reviews):
    """리뷰를 모아 평균 별점, 대표 가격대, 과반 태그를 계산한다.
    - 가격대: 리뷰어 과반이 선택한 값 (없으면 가장 많이 선택된 값)
    - 태그: 리뷰어 과반이 선택한 태그만 표시
    """
    rv = visible_reviews(reviews)
    n = len(rv)
    if n == 0:
        return {"count": 0, "rating": 0, "prices": [], "tags": [], "verified": 0, "last": None}
    # 가격대도 과반이 고른 것만 표시. 과반인 게 없으면 가장 많이 고른 것 하나
    price_counter = Counter(p for r in rv for p in r.price_list())
    prices = [k for k, _ in PRICE_TAGS if price_counter[k] * 2 > n]
    if not prices and price_counter:
        prices = [price_counter.most_common(1)[0][0]]
    tag_counter = Counter(t for r in rv for t in r.tag_list())
    tags = [k for k, _ in SEAT_TAGS + EXTRA_TAGS if tag_counter[k] * 2 > n]
    return {
        "count": n,
        "rating": round(sum(r.rating for r in rv) / n, 1),
        "prices": prices,
        "tags": tags,
        "verified": sum(1 for r in rv if r.receipt_path),
        "last": max(r.created_at for r in rv),   # 가장 최근 리뷰 시각
    }


def parse_review_form(form):
    """리뷰 입력값 검사. (데이터, 오류메시지) 반환"""
    try:
        rating = int(form.get("rating", 0))
    except ValueError:
        rating = 0
    content = (form.get("content") or "").strip()
    prices = [k for k, _ in PRICE_TAGS if k in form.getlist("price")]   # 여러 개 선택 가능
    tags = [t for t in form.getlist("tags") if t in TAG_KEYS]

    if not 1 <= rating <= 5:
        return None, "별점을 선택해 주세요."
    if len(content) > 500:   # 한줄평은 선택 (10초 리뷰)
        return None, "리뷰는 500자 이하로 작성해 주세요."
    if has_bad_word(content):
        return None, "욕설이나 비방 표현이 포함되어 있습니다. 리뷰 가이드를 지켜 주세요."
    if not prices:
        return None, "1인 기준 가격대를 하나 이상 선택해 주세요."
    if not any(t in dict(SEAT_TAGS) for t in tags):
        return None, "좌석 규모를 하나 이상 선택해 주세요."
    return {"rating": rating, "content": content, "price": ",".join(prices), "tags": ",".join(tags)}, None


def parse_categories(form):
    """음식 종류 여러 개 선택. 정해진 목록 순서대로 정렬해 반환"""
    chosen = form.getlist("category")
    return [c for c in CATEGORIES if c in chosen]


def allowed_image(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_IMAGE_EXT


def valid_school_email(email, domains):
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email or ""):
        return False
    domain = email.split("@", 1)[1].lower()
    return any(domain == d or domain.endswith("." + d) for d in domains)


def haversine_km(lat1, lng1, lat2, lng2):
    r = 6371
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def send_code(to_email, code, school_name):
    """인증 코드 메일 발송. 메일 서버 설정이 없으면 콘솔에 출력하고 False 반환(개발 모드)."""
    server = os.getenv("MAIL_SERVER")
    if not server:
        print("\n" + "=" * 50)
        print(f"[개발 모드] {to_email} 인증 코드: {code}")
        print("=" * 50 + "\n", flush=True)
        return False

    msg = EmailMessage()
    msg["Subject"] = f"[캠슐랭] {school_name} 재학생 인증 코드"
    msg["From"] = os.getenv("MAIL_FROM") or os.getenv("MAIL_USERNAME")
    msg["To"] = to_email
    msg.set_content(f"캠슐랭 재학생 인증 코드는 {code} 입니다.\n10분 안에 입력해 주세요.")
    with smtplib.SMTP(server, int(os.getenv("MAIL_PORT", 587))) as smtp:
        smtp.starttls()
        smtp.login(os.getenv("MAIL_USERNAME"), os.getenv("MAIL_PASSWORD"))
        smtp.send_message(msg)
    return True
