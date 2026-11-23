"""캠슐랭 (Campuslin) - 같은 학교 학생끼리 만드는 학교 주변 맛집 지도
실행: PyCharm에서 app.py 실행 → http://localhost:5000
"""
import os
import secrets
import uuid
from datetime import datetime, timedelta
from functools import wraps

from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
from flask import (Flask, abort, flash, jsonify, redirect, render_template,
                   request, send_from_directory, session, url_for)
from flask_login import (LoginManager, current_user, login_required,
                         login_user, logout_user)
from flask_wtf.csrf import CSRFProtect
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

import utils
from models import (Ballot, EmailVerification, KakaoReview, Place, Report, Review, ReviewPhoto,
                    School, User, Vote, VoteOption, db)

try:  # 사진 크기 줄이기 + 위치정보(EXIF) 제거용
    from PIL import Image, ImageOps
    HAS_PIL = True
except ImportError:
    HAS_PIL = False
    print("[설정] Pillow가 없어 사진을 원본 그대로 저장합니다. → python -m pip install Pillow 권장")

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")
load_dotenv(ENV_PATH)
print(f"[설정] .env 파일: {'찾음' if os.path.exists(ENV_PATH) else '없음'} ({ENV_PATH})")
print(f"[설정] 카카오 JS 키: {'있음' if os.getenv('KAKAO_JS_KEY') else '없음 → 지도가 표시되지 않습니다'}")
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")
UPLOAD_DIR = os.path.join(INSTANCE_DIR, "receipts")   # 영수증은 공개 폴더(static)가 아닌 곳에 저장
os.makedirs(UPLOAD_DIR, exist_ok=True)
PHOTO_DIR = os.path.join(INSTANCE_DIR, "photos")       # 음식 사진 (로그인한 같은 학교 학생만 볼 수 있음)
os.makedirs(PHOTO_DIR, exist_ok=True)

DEV_LOGIN = os.getenv("DEV_LOGIN", "0") == "1"
MAX_DISTANCE_KM = float(os.getenv("MAX_DISTANCE_KM", "3"))

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.getenv("SECRET_KEY") or "dev-only-change-me",
    SQLALCHEMY_DATABASE_URI=os.getenv("DATABASE_URL") or "sqlite:///" + os.path.join(INSTANCE_DIR, "campuslin.db"),
    MAX_CONTENT_LENGTH=25 * 1024 * 1024,   # 한 번에 올릴 수 있는 최대 용량 25MB (사진 여러 장)
)

db.init_app(app)
csrf = CSRFProtect(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "로그인이 필요합니다."


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


# ---------------- OAuth 설정 (키가 있는 것만 활성화) ----------------
oauth = OAuth(app)
PROVIDERS = {}
if os.getenv("GOOGLE_CLIENT_ID"):
    oauth.register(
        "google",
        client_id=os.getenv("GOOGLE_CLIENT_ID"),
        client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )
    PROVIDERS["google"] = "Google로 로그인"
if os.getenv("KAKAO_REST_KEY"):
    oauth.register(
        "kakao",
        client_id=os.getenv("KAKAO_REST_KEY"),
        client_secret=os.getenv("KAKAO_CLIENT_SECRET") or None,
        authorize_url="https://kauth.kakao.com/oauth/authorize",
        access_token_url="https://kauth.kakao.com/oauth/token",
        api_base_url="https://kapi.kakao.com/",
        client_kwargs={
            "scope": "profile_nickname",
            # 카카오 콘솔에서 Client Secret을 켰으면 함께 보내고, 안 켰으면 보내지 않음
            "token_endpoint_auth_method": "client_secret_post" if os.getenv("KAKAO_CLIENT_SECRET") else "none",
        },
    )
    PROVIDERS["kakao"] = "카카오로 로그인"


# ---------------- 공통 ----------------
def get_default_school():
    return School.query.order_by(School.id).first()


def is_api():
    return request.path.startswith("/api/")


def verified_required(f):
    """로그인 + 학교 인증을 마친 사용자만 접근"""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            if is_api():
                return jsonify(error="로그인이 필요합니다."), 401
            return redirect(url_for("login", next=request.full_path))
        if not current_user.is_verified:
            if is_api():
                return jsonify(error="학교 인증이 필요합니다."), 403
            return redirect(url_for("verify"))
        return f(*args, **kwargs)
    return wrapper


def safe_next(target):
    # 외부 사이트로 튕기는 공격(Open Redirect) 방지
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return url_for("index")


# 운영자: .env 의 ADMIN_EMAILS 에 적힌 학교 이메일로 인증한 계정 (쉼표로 여러 명)
ADMIN_EMAILS = {e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()}


# 시연용 카카오맵 리뷰 표시·가져오기 (.env 에 KAKAO_REVIEW_DEMO=1 일 때만 켜짐)
KAKAO_REVIEW_DEMO = os.getenv("KAKAO_REVIEW_DEMO", "0") == "1"
KAKAO_REVIEW_MAX = 5          # 가게당 최대 개수
KAKAO_ORIGIN = "https://place.map.kakao.com"


def is_admin(user=None):
    user = user or current_user
    return bool(user.is_authenticated and user.is_verified
                and (user.school_email or "").lower() in ADMIN_EMAILS)


@app.context_processor
def inject_globals():
    return {
        "is_admin": is_admin() if current_user else False,
        "kakao_js_key": os.getenv("KAKAO_JS_KEY", ""),
        "PRICE_TAGS": utils.PRICE_TAGS,
        "SEAT_TAGS": utils.SEAT_TAGS,
        "EXTRA_TAGS": utils.EXTRA_TAGS,
        "CATEGORIES": utils.CATEGORIES,
        "KAKAO_REVIEW_DEMO": KAKAO_REVIEW_DEMO,
    }


@app.errorhandler(413)
def too_large(_):
    if is_api():
        return jsonify(error="사진 용량이 너무 커요. 한 번에 25MB 이하로 올려 주세요."), 413
    flash("사진 용량이 너무 커요. 한 번에 25MB 이하로 올려 주세요.")
    return redirect(request.referrer or url_for("index"))


# ---------------- 로그인 ----------------
@app.route("/login")
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    session["next"] = safe_next(request.args.get("next"))
    return render_template("login.html", providers=PROVIDERS, dev_login=DEV_LOGIN)


@app.route("/login/<provider>")
def login_provider(provider):
    if provider not in PROVIDERS:
        abort(404)
    redirect_uri = url_for("auth_callback", provider=provider, _external=True)
    # 터널(ngrok 등)로 들어온 접속은 항상 https 주소로 돌려받기 (카카오에 등록한 주소와 맞추기)
    if request.host.split(":")[0] not in ("localhost", "127.0.0.1"):
        redirect_uri = url_for("auth_callback", provider=provider, _external=True, _scheme="https")
    return oauth.create_client(provider).authorize_redirect(redirect_uri)


@app.route("/auth/<provider>/callback")
def auth_callback(provider):
    if provider not in PROVIDERS:
        abort(404)
    client = oauth.create_client(provider)
    try:
        token = client.authorize_access_token()
        if provider == "google":
            info = token.get("userinfo") or client.userinfo()
            pid, name = info["sub"], info.get("name") or "Google 사용자"
        else:
            data = client.get("v2/user/me", token=token).json()
            pid = str(data["id"])
            name = (data.get("properties") or {}).get("nickname") or "카카오 사용자"
    except Exception as e:  # 사용자가 취소하거나 설정이 잘못된 경우
        app.logger.warning("OAuth 실패: %s", e)
        flash("로그인에 실패했습니다. 다시 시도해 주세요.")
        return redirect(url_for("login"))
    return finish_login(provider, pid, name)


@app.route("/dev-login", methods=["POST"])
def dev_login():
    """개발용 로그인 (OAuth 키 없이 테스트). 배포 시 DEV_LOGIN=0 으로 끄세요."""
    if not DEV_LOGIN:
        abort(404)
    name = (request.form.get("name") or "").strip()
    if not 2 <= len(name) <= 20:
        flash("닉네임은 2자 이상 20자 이하로 입력해 주세요.")
        return redirect(url_for("login"))
    return finish_login("dev", name, name)


def finish_login(provider, pid, name):
    user = User.query.filter_by(provider=provider, provider_id=pid).first()
    if not user:
        user = User(provider=provider, provider_id=pid, name=name)
        db.session.add(user)
        db.session.commit()
    login_user(user, remember=True)
    return redirect(session.pop("next", None) or url_for("index"))


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


# ---------------- 학교 이메일 인증 ----------------
def email_taken_by_other(email):
    """다른 계정이 이미 인증한 이메일인지. 단, 실제 운영(DEV_LOGIN=0)에서는
    테스트용 닉네임 계정이 쓰던 이메일을 풀어 줘서 진짜 계정으로 다시 인증할 수 있게 함"""
    other = User.query.filter(User.school_email == email, User.id != current_user.id).first()
    if other and other.provider == "dev" and not DEV_LOGIN:
        other.school_email = None
        other.verified_at = None
        db.session.flush()
        return False
    return other is not None

@app.route("/verify", methods=["GET", "POST"])
@login_required
def verify():
    if current_user.is_verified:
        return redirect(url_for("index"))
    school = get_default_school()

    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        if not request.form.get("agree"):
            flash("개인정보 수집·이용에 동의해 주세요.")
        elif not utils.valid_school_email(email, school.domains()):
            flash(f"{school.name} 이메일({', '.join('@' + d for d in school.domains())})만 사용할 수 있습니다.")
        elif email_taken_by_other(email):
            flash("이미 다른 계정에서 인증한 이메일입니다.")
        else:
            last = (EmailVerification.query.filter_by(user_id=current_user.id)
                    .order_by(EmailVerification.id.desc()).first())
            if last and last.created_at > datetime.utcnow() - timedelta(seconds=60):
                flash("인증 코드는 1분에 한 번만 요청할 수 있습니다.")
                return redirect(url_for("verify", step="code"))

            code = f"{secrets.randbelow(10 ** 6):06d}"
            EmailVerification.query.filter_by(user_id=current_user.id).delete()
            db.session.add(EmailVerification(
                user_id=current_user.id, school_email=email,
                code_hash=generate_password_hash(code),
                expires_at=datetime.utcnow() + timedelta(minutes=10),
            ))
            current_user.privacy_agreed_at = datetime.utcnow()
            db.session.commit()
            try:
                sent = utils.send_code(email, code, school.name)
            except Exception as e:
                app.logger.error("메일 발송 실패: %s", e)
                flash("메일 발송에 실패했습니다. 잠시 후 다시 시도해 주세요.")
                return redirect(url_for("verify"))
            session["verify_email"] = email
            flash(f"{email}로 인증 코드를 보냈습니다." if sent
                  else "[개발 모드] 메일 서버가 없어 인증 코드를 PyCharm 실행 콘솔에 출력했습니다.")
            return redirect(url_for("verify", step="code"))

    return render_template("verify.html", school=school,
                           step=request.args.get("step"), email=session.get("verify_email"))


@app.route("/verify/confirm", methods=["POST"])
@login_required
def verify_confirm():
    ev = (EmailVerification.query.filter_by(user_id=current_user.id)
          .order_by(EmailVerification.id.desc()).first())
    code = (request.form.get("code") or "").strip()
    if not ev or ev.expires_at < datetime.utcnow():
        flash("인증 코드가 만료되었습니다. 다시 요청해 주세요.")
        return redirect(url_for("verify"))
    if ev.attempts >= 5:
        flash("입력 횟수를 초과했습니다. 코드를 다시 요청해 주세요.")
        return redirect(url_for("verify"))

    ev.attempts += 1
    if not check_password_hash(ev.code_hash, code):
        db.session.commit()
        flash(f"인증 코드가 올바르지 않습니다. ({ev.attempts}/5)")
        return redirect(url_for("verify", step="code"))

    school = get_default_school()
    if email_taken_by_other(ev.school_email):
        flash("이미 다른 계정에서 인증한 이메일입니다.")
        return redirect(url_for("verify"))
    current_user.school_email = ev.school_email
    current_user.school_id = school.id
    current_user.verified_at = datetime.utcnow()
    EmailVerification.query.filter_by(user_id=current_user.id).delete()
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("이미 다른 계정에서 인증한 이메일입니다.")
        return redirect(url_for("verify"))
    session.pop("verify_email", None)
    flash(f"{school.name} 재학생 인증이 완료되었습니다!")
    return redirect(url_for("index"))


# ---------------- 지도 메인 ----------------
@app.route("/")
@verified_required
def index():
    return render_template("map.html", school=current_user.school)


def place_summary(p, agg):
    return {
        "id": p.id, "name": p.name, "address": p.address, "category": p.category_text, "categories": p.category_list(),
        "lat": p.lat, "lng": p.lng, "rating": agg["rating"], "reviewCount": agg["count"],
        "prices": agg["prices"], "tags": agg["tags"], "verifiedCount": agg["verified"],
        # 리뷰가 있는데 마지막 리뷰가 6개월 넘게 지났으면 폐업 가능성 안내
        "stale": bool(agg["last"] and agg["last"] < datetime.utcnow() - timedelta(days=utils.STALE_DAYS)),
        "lastReview": agg["last"].strftime("%Y.%m.%d") if agg["last"] else None,
        "thumb": place_thumb(p),
        # 리뷰가 시연용 샘플뿐이면 목록에 🧪 표시
        "sampleOnly": bool(agg["count"]) and not utils.real_reviews(p.reviews),
    }


def photo_url(ph):
    return url_for("photo_file", name=ph.filename)


def place_photos(p):
    """가게의 공개 사진 (최신순). 숨겨진 리뷰·신고된 사진은 제외"""
    photos = [ph for r in utils.visible_reviews(p.reviews) for ph in utils.visible_photos(r)]
    return sorted(photos, key=lambda ph: ph.id, reverse=True)


def place_thumb(p):
    photos = place_photos(p)
    return photo_url(photos[0]) if photos else None


@app.route("/api/places")
@verified_required
def api_places():
    """필터: price(가격대), seat(좌석, 하나라도 맞으면), category(종류), extra(모두 만족), q(이름 검색)"""
    prices = request.args.getlist("price")
    seats = request.args.getlist("seat")
    cats = request.args.getlist("category")
    extras = request.args.getlist("extra")
    q = (request.args.get("q") or "").strip()

    query = Place.query.filter(Place.school_id == current_user.school_id,
                               Place.closed_report_count < utils.CLOSED_HIDE_THRESHOLD,
                               Place.hidden.is_(False))
    if q:
        query = query.filter(Place.name.contains(q))

    info_filter = bool(prices or seats or extras)   # 리뷰로만 알 수 있는 필터를 켰는지
    result, unknown = [], []
    for p in query.all():
        if cats and not set(cats) & set(p.category_list()):   # 고른 종류 중 하나라도 해당
            continue
        agg = utils.aggregate(p.reviews)
        if info_filter and agg["count"] == 0:
            unknown.append(place_summary(p, agg))   # 리뷰가 없어 가격·좌석 정보를 아직 모르는 가게
            continue
        if prices and not set(prices) & set(agg["prices"]):     # 고른 가격대 중 하나라도 해당
            continue
        if seats and not any(s in agg["tags"] for s in seats):
            continue
        if extras and not all(e in agg["tags"] for e in extras):
            continue
        result.append(place_summary(p, agg))
    result.sort(key=lambda x: (x["rating"], x["reviewCount"]), reverse=True)
    unknown.sort(key=lambda x: x["name"])
    return jsonify(places=result, unknown=unknown)


@app.route("/api/places/<int:place_id>")
@verified_required
def api_place_detail(place_id):
    p = Place.query.filter_by(id=place_id, school_id=current_user.school_id).first_or_404()
    agg = utils.aggregate(p.reviews)
    my_reports = {r.target_id for r in Report.query.filter_by(user_id=current_user.id, target_type="review")}
    my_photo_reports = {r.target_id for r in Report.query.filter_by(user_id=current_user.id, target_type="photo")}
    reviews = sorted(utils.visible_reviews(p.reviews), key=lambda r: r.created_at, reverse=True)
    pioneer_id = min((r.id for r in utils.real_reviews(p.reviews)), default=None)  # 샘플은 개척자 제외   # 가장 먼저 쓴 리뷰 = 개척자
    data = place_summary(p, agg)
    data.update({
        "phone": p.phone, "kakaoUrl": p.kakao_url,
        "myReview": any(r.user_id == current_user.id for r in p.reviews),
        "closedReported": Report.query.filter_by(user_id=current_user.id, target_type="closed",
                                                 target_id=p.id).first() is not None,
        "reviews": [{
            "id": r.id, "rating": r.rating, "content": r.content, "prices": r.price_list(),
            "tags": r.tag_list(), "receipt": bool(r.receipt_path),
            "date": r.created_at.strftime("%Y.%m.%d"),
            "mine": r.user_id == current_user.id, "reported": r.id in my_reports,
            "pioneer": r.id == pioneer_id,
            "sample": r.is_sample,
            "photos": [{"id": ph.id, "url": photo_url(ph)} for ph in utils.visible_photos(r)],
        } for r in reviews],
        "photos": [{"id": ph.id, "url": photo_url(ph), "mine": ph.review.user_id == current_user.id,
                    "reported": ph.id in my_photo_reports} for ph in place_photos(p)],
    })
    if KAKAO_REVIEW_DEMO:
        kr = sorted(p.kakao_reviews, key=lambda k: k.written_on or "", reverse=True)[:KAKAO_REVIEW_MAX]
        data["kakaoReviews"] = [{"rating": k.rating or 0, "content": k.content, "date": k.written_on or ""}
                                for k in kr]
    return jsonify(data)


def save_receipt(file):
    if not file or not file.filename:
        return None, None
    if not utils.allowed_image(file.filename):
        return None, "영수증은 이미지 파일(jpg, png, webp, heic)만 올릴 수 있습니다."
    ext = secure_filename(file.filename).rsplit(".", 1)[-1].lower()
    name = f"{uuid.uuid4().hex}.{ext}"
    file.save(os.path.join(UPLOAD_DIR, name))
    return name, None


def remove_files(folder, names):
    for n in names:
        if n:
            try:
                os.remove(os.path.join(folder, n))
            except OSError:
                pass


def save_photos(files):
    """음식·메뉴 사진 저장. 크기를 줄이고 위치정보(EXIF)를 지운 JPG로 다시 저장한다."""
    files = [f for f in files if f and f.filename]
    if len(files) > utils.MAX_PHOTOS:
        return None, f"사진은 최대 {utils.MAX_PHOTOS}장까지 올릴 수 있어요."
    names = []
    for f in files:
        if not utils.allowed_image(f.filename):
            remove_files(PHOTO_DIR, names)
            return None, "사진은 이미지 파일(jpg, png, webp)만 올릴 수 있어요."
        if HAS_PIL:
            name = f"{uuid.uuid4().hex}.jpg"
            try:
                img = ImageOps.exif_transpose(Image.open(f.stream))   # 휴대폰 사진 방향 바로잡기
                img = img.convert("RGB")
                img.thumbnail((utils.PHOTO_MAX_PX, utils.PHOTO_MAX_PX))
                img.save(os.path.join(PHOTO_DIR, name), "JPEG", quality=82)   # 새로 저장 → EXIF 위치정보 제거
            except Exception:
                remove_files(PHOTO_DIR, names)
                return None, "사진을 읽을 수 없어요. jpg나 png 파일로 올려 주세요."
        else:
            ext = secure_filename(f.filename).rsplit(".", 1)[-1].lower()
            name = f"{uuid.uuid4().hex}.{ext}"
            f.save(os.path.join(PHOTO_DIR, name))
        names.append(name)
    return names, None


@app.route("/photos/<name>")
@verified_required
def photo_file(name):
    ph = ReviewPhoto.query.filter_by(filename=name).first_or_404()
    hidden = (ph.report_count or 0) >= utils.REPORT_HIDE_THRESHOLD
    if ph.review.place.school_id != current_user.school_id or (hidden and not is_admin()):
        abort(404)
    return send_from_directory(PHOTO_DIR, name, max_age=60 * 60 * 24)


@app.route("/api/photos/<int:photo_id>/report", methods=["POST"])
@verified_required
def api_photo_report(photo_id):
    ph = ReviewPhoto.query.get_or_404(photo_id)
    if ph.review.place.school_id != current_user.school_id:
        abort(404)
    if ph.review.user_id == current_user.id:
        return jsonify(error="내 사진은 신고할 수 없어요. 리뷰를 삭제하면 사진도 함께 지워져요."), 400
    if not add_report("photo", ph.id):
        return jsonify(error="이미 신고한 사진입니다."), 400
    ph.report_count = (ph.report_count or 0) + 1
    db.session.commit()
    return jsonify(ok=True, hidden=ph.report_count >= utils.REPORT_HIDE_THRESHOLD)


# ---------------- 리뷰어 랭킹 ----------------
def mask_name(name):
    name = name or "익명"
    return name[0] + "*" * (len(name) - 1) if len(name) >= 2 else name


def month_start_utc():
    """한국 시간 기준 이번 달 1일 0시를 UTC로 변환"""
    kst_now = datetime.utcnow() + timedelta(hours=9)
    return kst_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0) - timedelta(hours=9)


@app.route("/ranking")
@verified_required
def ranking():
    since = month_start_utc()
    # 우리 학교 가게 리뷰 중 숨김 안 된 것
    reviews = (Review.query.join(Place)
               .filter(Place.school_id == current_user.school_id,
                       Review.report_count < utils.REPORT_HIDE_THRESHOLD,
                       Review.is_sample.is_(False)).all())          # 샘플 리뷰는 랭킹 제외
    first_review = {}   # 가게별 첫 리뷰 id
    for r in sorted(reviews, key=lambda r: r.id):
        first_review.setdefault(r.place_id, r.id)

    stats = {}
    for r in reviews:
        s = stats.setdefault(r.user_id, {"month": 0, "total": 0, "pioneer": 0})
        s["total"] += 1
        if r.created_at >= since:
            s["month"] += 1
        if first_review.get(r.place_id) == r.id:
            s["pioneer"] += 1

    # 이번 달 리뷰 수 → 개척자 수 순서로 정렬
    ranked = sorted(((uid, st) for uid, st in stats.items() if st["month"] > 0),
                    key=lambda x: (x[1]["month"], x[1]["pioneer"]), reverse=True)
    users = {u.id: u for u in User.query.filter(User.id.in_([uid for uid, _ in ranked]))} if ranked else {}
    top = [{"rank": i + 1, "name": mask_name(users[uid].name), "me": uid == current_user.id, **st}
           for i, (uid, st) in enumerate(ranked[:10])]
    my_rank = next((i + 1 for i, (uid, _) in enumerate(ranked) if uid == current_user.id), None)
    mine = stats.get(current_user.id, {"month": 0, "total": 0, "pioneer": 0})

    waiting = (Place.query.filter(Place.school_id == current_user.school_id,
                                  Place.closed_report_count < utils.CLOSED_HIDE_THRESHOLD,
                                  Place.hidden.is_(False)).all())
    waiting = [p for p in waiting if not utils.real_reviews(p.reviews)][:5]
    month_label = (datetime.utcnow() + timedelta(hours=9)).month
    return render_template("ranking.html", top=top, mine=mine, my_rank=my_rank,
                           waiting=waiting, month=month_label)


# ---------------- 가게 등록 ----------------
@app.route("/places/new", methods=["GET", "POST"])
@verified_required
def place_new():
    school = current_user.school
    if request.method == "GET":
        return render_template("place_new.html", school=school, max_km=MAX_DISTANCE_KM)

    f = request.form
    kakao_id = (f.get("kakao_id") or "").strip()
    try:
        lat, lng = float(f.get("lat")), float(f.get("lng"))
    except (TypeError, ValueError):
        lat = lng = None

    error = None
    if not kakao_id.isdigit() or lat is None or not f.get("name"):
        error = "장소 검색에서 가게를 선택해 주세요."
    elif utils.haversine_km(school.lat, school.lng, lat, lng) > MAX_DISTANCE_KM:
        error = f"학교에서 {MAX_DISTANCE_KM:g}km 이내의 가게만 등록할 수 있습니다."
    elif not utils.parse_categories(f):
        error = "음식 종류를 하나 이상 선택해 주세요."

    existing = Place.query.filter_by(school_id=school.id, kakao_id=kakao_id).first() if not error else None
    if existing:
        if existing.hidden:   # 숨겨 둔 가게를 학생이 다시 등록하면 = 영업 중이라는 뜻 → 다시 표시
            existing.hidden = False
            db.session.commit()
            flash("숨겨져 있던 가게를 다시 지도에 표시했어요. 리뷰를 남겨 주세요!")
            return redirect(url_for("index", place=existing.id))
        flash("이미 등록된 가게입니다. 리뷰를 남겨 주세요!")
        return redirect(url_for("index", place=existing.id))

    review_data, review_error = utils.parse_review_form(f) if not error else (None, None)
    error = error or review_error
    photos, photo_error = ([], None) if error else save_photos(request.files.getlist("photos"))
    error = error or photo_error
    receipt, receipt_error = (None, None) if error else save_receipt(request.files.get("receipt"))
    error = error or receipt_error
    if error:
        remove_files(PHOTO_DIR, photos or [])
        flash(error)
        return render_template("place_new.html", school=school, max_km=MAX_DISTANCE_KM, form=f), 400

    place = Place(school_id=school.id, kakao_id=kakao_id, name=f.get("name")[:100],
                  address=(f.get("address") or "")[:255], phone=(f.get("phone") or "")[:30],
                  kakao_url=(f.get("kakao_url") or "")[:255] if (f.get("kakao_url") or "").startswith("http") else None,
                  lat=lat, lng=lng, category=",".join(utils.parse_categories(f)), created_by=current_user.id)
    db.session.add(place)
    db.session.flush()
    review = Review(place_id=place.id, user_id=current_user.id, receipt_path=receipt, **review_data)
    review.photos = [ReviewPhoto(filename=n) for n in photos]
    db.session.add(review)
    db.session.commit()
    flash(f"'{place.name}'을(를) 등록했습니다. 고마워요!")
    return redirect(url_for("index", place=place.id))


# ---------------- 리뷰 / 신고 ----------------
@app.route("/api/places/<int:place_id>/reviews", methods=["POST"])
@verified_required
def api_review_create(place_id):
    place = Place.query.filter_by(id=place_id, school_id=current_user.school_id).first_or_404()
    if Review.query.filter_by(place_id=place.id, user_id=current_user.id).first():
        return jsonify(error="이 가게에는 이미 리뷰를 작성했습니다. (1인 1리뷰)"), 400
    data, error = utils.parse_review_form(request.form)
    if error:
        return jsonify(error=error), 400
    photos, error = save_photos(request.files.getlist("photos"))
    if error:
        return jsonify(error=error), 400
    receipt, error = save_receipt(request.files.get("receipt"))
    if error:
        remove_files(PHOTO_DIR, photos)
        return jsonify(error=error), 400
    review = Review(place_id=place.id, user_id=current_user.id, receipt_path=receipt, **data)
    review.photos = [ReviewPhoto(filename=n) for n in photos]
    db.session.add(review)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        remove_files(PHOTO_DIR, photos)
        remove_files(UPLOAD_DIR, [receipt])
        return jsonify(error="이 가게에는 이미 리뷰를 작성했습니다."), 400
    return jsonify(ok=True)


@app.route("/api/reviews/<int:review_id>", methods=["DELETE"])
@verified_required
def api_review_delete(review_id):
    r = Review.query.filter_by(id=review_id, user_id=current_user.id).first_or_404()
    remove_files(UPLOAD_DIR, [r.receipt_path])
    remove_files(PHOTO_DIR, [ph.filename for ph in r.photos])   # 리뷰를 지우면 사진도 함께 삭제
    db.session.delete(r)
    db.session.commit()
    return jsonify(ok=True)


def add_report(target_type, target_id):
    db.session.add(Report(user_id=current_user.id, target_type=target_type, target_id=target_id,
                          reason=((request.get_json(silent=True) or {}).get("reason") or "")[:200]))
    try:
        db.session.flush()
        return True
    except IntegrityError:
        db.session.rollback()
        return False


@app.route("/api/reviews/<int:review_id>/report", methods=["POST"])
@verified_required
def api_review_report(review_id):
    r = Review.query.join(Place).filter(Review.id == review_id,
                                        Place.school_id == current_user.school_id).first_or_404()
    if r.user_id == current_user.id:
        return jsonify(error="내 리뷰는 신고할 수 없습니다."), 400
    if not add_report("review", r.id):
        return jsonify(error="이미 신고한 리뷰입니다."), 400
    r.report_count = (r.report_count or 0) + 1
    db.session.commit()
    return jsonify(ok=True, hidden=r.report_count >= utils.REPORT_HIDE_THRESHOLD)


@app.route("/api/places/<int:place_id>/closed", methods=["POST"])
@verified_required
def api_place_closed(place_id):
    p = Place.query.filter_by(id=place_id, school_id=current_user.school_id).first_or_404()
    if not add_report("closed", p.id):
        return jsonify(error="이미 폐업 신고한 가게입니다."), 400
    p.closed_report_count = (p.closed_report_count or 0) + 1
    db.session.commit()
    return jsonify(ok=True, hidden=p.closed_report_count >= utils.CLOSED_HIDE_THRESHOLD)


# ---------------- 모임 장소 투표 ----------------
@app.route("/votes/new", methods=["GET", "POST"])
@verified_required
def vote_new():
    places = (Place.query.filter(Place.school_id == current_user.school_id,
                                 Place.closed_report_count < utils.CLOSED_HIDE_THRESHOLD,
                                 Place.hidden.is_(False))
              .order_by(Place.name).all())
    if request.method == "POST":
        ids = {int(i) for i in request.form.getlist("place_ids") if i.isdigit()}
        chosen = [p for p in places if p.id in ids]
        title = (request.form.get("title") or "").strip()[:100] or "어디 갈까?"
        if not 2 <= len(chosen) <= 5:
            flash("후보 가게를 2~5곳 선택해 주세요.")
        else:
            vote = Vote(slug=secrets.token_urlsafe(6), title=title, school_id=current_user.school_id,
                        creator_id=current_user.id, expires_at=datetime.utcnow() + timedelta(days=7))
            vote.options = [VoteOption(place_id=p.id) for p in chosen]
            db.session.add(vote)
            db.session.commit()
            return redirect(url_for("vote_page", slug=vote.slug))
    preselect = {int(i) for i in request.args.getlist("place") if i.isdigit()}
    return render_template("vote_new.html", places=places, preselect=preselect)


def voter_key(create=False):
    if current_user.is_authenticated:
        return f"u{current_user.id}", None
    key = request.cookies.get("vk")
    if key and len(key) <= 40:
        return f"c{key}", None
    if create:
        new = uuid.uuid4().hex
        return f"c{new}", new
    return None, None


@app.route("/v/<slug>")
def vote_page(slug):
    vote = Vote.query.filter_by(slug=slug).first_or_404()
    return render_template("vote.html", vote=vote)


def vote_result(vote):
    key, _ = voter_key()
    counts = {o.id: 0 for o in vote.options}
    mine = None
    for b in Ballot.query.filter_by(vote_id=vote.id):
        counts[b.option_id] = counts.get(b.option_id, 0) + 1
        if key and b.voter_key == key:
            mine = b.option_id
    return {
        "title": vote.title, "expired": vote.expires_at < datetime.utcnow(),
        "total": sum(counts.values()), "mine": mine,
        "options": [{
            "id": o.id, "count": counts[o.id], "name": o.place.name,
            "address": o.place.address, "category": o.place.category_text,
            "kakaoUrl": o.place.kakao_url,
        } for o in vote.options],
    }


@app.route("/api/v/<slug>")
def api_vote(slug):
    return jsonify(vote_result(Vote.query.filter_by(slug=slug).first_or_404()))


@app.route("/api/v/<slug>/ballot", methods=["POST"])
def api_vote_ballot(slug):
    vote = Vote.query.filter_by(slug=slug).first_or_404()
    if vote.expires_at < datetime.utcnow():
        return jsonify(error="마감된 투표입니다."), 400
    option_id = (request.get_json(silent=True) or {}).get("option_id")
    if option_id not in {o.id for o in vote.options}:
        return jsonify(error="잘못된 선택입니다."), 400

    key, new_cookie = voter_key(create=True)
    ballot = Ballot.query.filter_by(vote_id=vote.id, voter_key=key).first()
    if ballot:
        ballot.option_id = option_id   # 다시 누르면 선택 변경
    else:
        db.session.add(Ballot(vote_id=vote.id, option_id=option_id, voter_key=key))
    db.session.commit()

    resp = jsonify(vote_result_for_key(vote, key))
    if new_cookie:
        resp.set_cookie("vk", new_cookie, max_age=60 * 60 * 24 * 30, httponly=True, samesite="Lax")
    return resp


def vote_result_for_key(vote, key):
    data = vote_result(vote)
    b = Ballot.query.filter_by(vote_id=vote.id, voter_key=key).first()
    data["mine"] = b.option_id if b else None
    return data


# ---------------- 운영자 페이지 (통계 + 신고 관리) ----------------
def admin_required(f):
    @wraps(f)
    @verified_required
    def wrapper(*args, **kwargs):
        if not is_admin():
            abort(404)   # 운영자가 아니면 페이지가 없는 것처럼 보이게
        return f(*args, **kwargs)
    return wrapper


def kst_date(dt):
    return (dt + timedelta(hours=9)).date()


@app.route("/admin")
@admin_required
def admin():
    sid = current_user.school_id
    places = Place.query.filter_by(school_id=sid).all()
    all_reviews = Review.query.join(Place).filter(Place.school_id == sid).all()
    sample_count = sum(1 for r in all_reviews if r.is_sample)
    reviews = [r for r in all_reviews if not r.is_sample]          # 통계·신고는 진짜 리뷰만
    photos = [ph for r in reviews for ph in r.photos]
    users = User.query.filter(User.school_id == sid, User.provider != "sample").all()
    th = utils.REPORT_HIDE_THRESHOLD

    visible_reviews = [r for r in reviews if (r.report_count or 0) < th]
    reviewed_places = {r.place_id for r in visible_reviews}
    active_places = [p for p in places if not p.hidden and (p.closed_report_count or 0) < utils.CLOSED_HIDE_THRESHOLD]

    stats = {
        "users": len(users),
        "reviews": len(visible_reviews),
        "photos": sum(1 for ph in photos if (ph.report_count or 0) < th),
        "receipts": sum(1 for r in visible_reviews if r.receipt_path),
        "places": len(active_places),
        "reviewed_places": len([p for p in active_places if p.id in reviewed_places]),
        "votes": Vote.query.filter_by(school_id=sid).count(),
        "ballots": Ballot.query.join(Vote).filter(Vote.school_id == sid).count(),
        "samples": sample_count,
    }
    stats["coverage"] = round(stats["reviewed_places"] / stats["places"] * 100) if stats["places"] else 0

    # 최근 14일 일별 추이 (한국 시간 기준)
    today = kst_date(datetime.utcnow())
    days = [today - timedelta(days=i) for i in range(13, -1, -1)]
    daily = {d: {"users": 0, "reviews": 0} for d in days}
    for u in users:
        if u.verified_at and kst_date(u.verified_at) in daily:
            daily[kst_date(u.verified_at)]["users"] += 1
    for r in reviews:
        if kst_date(r.created_at) in daily:
            daily[kst_date(r.created_at)]["reviews"] += 1
    peak = max([1] + [v["users"] + v["reviews"] for v in daily.values()])
    daily_rows = [{"date": d.strftime("%m/%d"), **daily[d]} for d in days]

    # 신고 내역
    def reasons(target_type, target_id):
        return [x.reason for x in Report.query.filter_by(target_type=target_type, target_id=target_id) if x.reason]

    reported_reviews = [{"r": r, "hidden": (r.report_count or 0) >= th, "reasons": reasons("review", r.id)}
                        for r in sorted(reviews, key=lambda r: -(r.report_count or 0)) if r.report_count]
    reported_photos = [{"ph": ph, "hidden": (ph.report_count or 0) >= th, "reasons": reasons("photo", ph.id)}
                       for ph in sorted(photos, key=lambda p: -(p.report_count or 0)) if ph.report_count]
    closed_places = sorted([p for p in places if p.closed_report_count or p.hidden],
                           key=lambda p: -(p.closed_report_count or 0))
    top_places = sorted(active_places, key=lambda p: -len([r for r in p.reviews if (r.report_count or 0) < th]))[:5]

    return render_template("admin.html", stats=stats, daily=daily_rows, peak=peak,
                           reported_reviews=reported_reviews, reported_photos=reported_photos,
                           closed_places=closed_places, top_places=top_places,
                           review_th=th, closed_th=utils.CLOSED_HIDE_THRESHOLD)


def clear_reports(target_type, target_id):
    Report.query.filter_by(target_type=target_type, target_id=target_id).delete()


@app.route("/admin/review/<int:rid>/<action>", methods=["POST"])
@admin_required
def admin_review(rid, action):
    r = Review.query.join(Place).filter(Review.id == rid, Place.school_id == current_user.school_id).first_or_404()
    if action == "restore":            # 신고가 잘못됐다고 판단 → 다시 보이게
        r.report_count = 0
        clear_reports("review", r.id)
        flash("리뷰를 복구했어요.")
    elif action == "delete":           # 정말 문제 있는 리뷰 → 사진·영수증까지 삭제
        remove_files(UPLOAD_DIR, [r.receipt_path])
        remove_files(PHOTO_DIR, [ph.filename for ph in r.photos])
        for ph in r.photos:
            clear_reports("photo", ph.id)
        clear_reports("review", r.id)
        db.session.delete(r)
        flash("리뷰를 삭제했어요.")
    else:
        abort(400)
    db.session.commit()
    return redirect(url_for("admin") + "#reports")


@app.route("/admin/photo/<int:pid>/<action>", methods=["POST"])
@admin_required
def admin_photo(pid, action):
    ph = ReviewPhoto.query.get_or_404(pid)
    if ph.review.place.school_id != current_user.school_id:
        abort(404)
    if action == "restore":
        ph.report_count = 0
        clear_reports("photo", ph.id)
        flash("사진을 복구했어요.")
    elif action == "delete":
        remove_files(PHOTO_DIR, [ph.filename])
        clear_reports("photo", ph.id)
        db.session.delete(ph)
        flash("사진을 삭제했어요.")
    else:
        abort(400)
    db.session.commit()
    return redirect(url_for("admin") + "#photos")


@app.route("/admin/place/<int:pid>/<action>", methods=["POST"])
@admin_required
def admin_place(pid, action):
    p = Place.query.filter_by(id=pid, school_id=current_user.school_id).first_or_404()
    if action == "restore":            # 영업 중 확인 → 폐업 신고 초기화하고 다시 표시
        p.closed_report_count = 0
        p.hidden = False
        clear_reports("closed", p.id)
        flash(f"'{p.name}'을(를) 다시 지도에 표시해요.")
    elif action == "hide":             # 폐업 확인 → 숨김 (리뷰는 보관)
        p.hidden = True
        flash(f"'{p.name}'을(를) 지도에서 숨겼어요.")
    elif action == "delete":           # 완전 삭제 (리뷰·사진·카카오 리뷰·투표 후보까지, 되돌릴 수 없음)
        review_ids = [r.id for r in p.reviews]
        photo_ids = [ph.id for r in p.reviews for ph in r.photos]
        remove_files(UPLOAD_DIR, [r.receipt_path for r in p.reviews])
        remove_files(PHOTO_DIR, [ph.filename for r in p.reviews for ph in r.photos])
        for t, ids in (("review", review_ids), ("photo", photo_ids), ("closed", [p.id])):
            if ids:
                Report.query.filter(Report.target_type == t, Report.target_id.in_(ids)).delete(synchronize_session=False)
        for opt in VoteOption.query.filter_by(place_id=p.id).all():
            Ballot.query.filter_by(option_id=opt.id).delete(synchronize_session=False)
            db.session.delete(opt)
        name = p.name
        db.session.delete(p)
        flash(f"'{name}'을(를) 완전히 삭제했어요.")
    else:
        abort(400)
    db.session.commit()
    return redirect(url_for("admin") + "#places")


# ---------------- DB 초기화 ----------------
# ---------------- 시연용: 카카오맵 리뷰 가져오기 ----------------
@app.route("/admin/kakao-import")
@admin_required
def kakao_import():
    if not KAKAO_REVIEW_DEMO:
        abort(404)
    places = (Place.query.filter_by(school_id=current_user.school_id, hidden=False)
              .order_by(Place.name).all())
    counts = dict(db.session.query(KakaoReview.place_id, db.func.count(KakaoReview.id))
                  .group_by(KakaoReview.place_id).all())
    rows = [{"name": p.name, "url": p.kakao_url, "n": counts.get(p.id, 0)} for p in places if p.kakao_url]
    return render_template("kakao_import.html", rows=rows, total=sum(counts.values()),
                           done=sum(1 for r in rows if r["n"]), origin=request.host_url.rstrip("/"),
                           kakao_origin=KAKAO_ORIGIN, max_n=KAKAO_REVIEW_MAX,
                           popup=request.args.get("popup") == "1")


@app.route("/admin/kakao-import", methods=["POST"])
@admin_required
def kakao_import_save():
    if not KAKAO_REVIEW_DEMO:
        abort(404)
    data = request.get_json(silent=True) or {}
    kakao_id = str(data.get("kakaoId", "")).strip()
    p = Place.query.filter_by(school_id=current_user.school_id, kakao_id=kakao_id).first()
    if not p:
        return jsonify(error="캠슐랭에 등록되지 않은 가게예요."), 404
    have = {k.kakao_review_id for k in p.kakao_reviews}
    added = 0
    for r in (data.get("reviews") or [])[:KAKAO_REVIEW_MAX]:
        rid, content = str(r.get("id", ""))[:30], str(r.get("content", "")).strip()[:1000]
        if not rid or not content or rid in have or len(have) >= KAKAO_REVIEW_MAX:
            continue
        try:
            rating = max(0, min(5, int(r.get("rating") or 0)))
        except (TypeError, ValueError):
            rating = 0
        db.session.add(KakaoReview(place_id=p.id, kakao_review_id=rid, rating=rating, content=content,
                                   written_on=str(r.get("date", ""))[:10]))
        have.add(rid)
        added += 1
    db.session.commit()
    return jsonify(ok=True, name=p.name, added=added, total=len(have))


@app.route("/admin/kakao-import/clear", methods=["POST"])
@admin_required
def kakao_import_clear():
    n = KakaoReview.query.delete()
    db.session.commit()
    flash(f"카카오맵 리뷰 {n}개를 모두 지웠어요.")
    return redirect(url_for("kakao_import") if KAKAO_REVIEW_DEMO else url_for("admin"))


def init_db():
    with app.app_context():
        db.create_all()
        # 예전 DB에 새로 생긴 칸 추가 (기존 데이터는 그대로 유지)
        cols = {c["name"] for c in db.inspect(db.engine).get_columns("place")}
        if "hidden" not in cols:
            db.session.execute(db.text("ALTER TABLE place ADD COLUMN hidden BOOLEAN NOT NULL DEFAULT 0"))
            db.session.commit()
            print("[설정] DB에 place.hidden 칸을 추가했습니다.")
        cols = {c["name"] for c in db.inspect(db.engine).get_columns("review")}
        if "is_sample" not in cols:
            db.session.execute(db.text("ALTER TABLE review ADD COLUMN is_sample BOOLEAN NOT NULL DEFAULT 0"))
            db.session.commit()
            print("[설정] DB에 review.is_sample 칸을 추가했습니다.")
        if not School.query.first():
            db.session.add(School(
                name=os.getenv("SCHOOL_NAME", "신구대학교"),
                email_domains=os.getenv("SCHOOL_EMAIL_DOMAINS", "shingu.ac.kr"),
                lat=float(os.getenv("SCHOOL_LAT", "37.4487")),
                lng=float(os.getenv("SCHOOL_LNG", "127.1675")),
            ))
            db.session.commit()


init_db()

if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_DEBUG", "1") == "1", port=int(os.getenv("PORT", 5000)))
