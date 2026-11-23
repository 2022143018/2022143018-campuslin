"""데이터베이스 모델 (SQLAlchemy)"""
from datetime import datetime

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class School(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    email_domains = db.Column(db.String(255), nullable=False)  # 쉼표로 구분: "shingu.ac.kr,g.shingu.ac.kr"
    lat = db.Column(db.Float, nullable=False)
    lng = db.Column(db.Float, nullable=False)

    def domains(self):
        return [d.strip().lower() for d in self.email_domains.split(",") if d.strip()]


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    provider = db.Column(db.String(20), nullable=False)       # google / kakao / dev
    provider_id = db.Column(db.String(255), nullable=False)
    name = db.Column(db.String(100))
    school_id = db.Column(db.Integer, db.ForeignKey("school.id"))
    school_email = db.Column(db.String(255), unique=True)     # 인증된 학교 이메일
    verified_at = db.Column(db.DateTime)
    privacy_agreed_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    school = db.relationship("School")

    __table_args__ = (db.UniqueConstraint("provider", "provider_id"),)

    @property
    def is_verified(self):
        return self.verified_at is not None and self.school_id is not None


class EmailVerification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    school_email = db.Column(db.String(255), nullable=False)
    code_hash = db.Column(db.String(255), nullable=False)
    attempts = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)


class Place(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("school.id"), nullable=False)
    kakao_id = db.Column(db.String(30), nullable=False)        # 카카오 장소 고유 ID (중복 등록 방지)
    name = db.Column(db.String(100), nullable=False)
    address = db.Column(db.String(255))
    phone = db.Column(db.String(30))
    kakao_url = db.Column(db.String(255))
    lat = db.Column(db.Float, nullable=False)
    lng = db.Column(db.Float, nullable=False)
    category = db.Column(db.String(100), nullable=False)       # 음식 종류, 여러 개면 쉼표 구분
    closed_report_count = db.Column(db.Integer, default=0)      # 폐업 신고 수
    hidden = db.Column(db.Boolean, nullable=False, default=False, server_default="0")  # 운영자가 숨김 (check_naver.py)
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    reviews = db.relationship("Review", backref="place", cascade="all, delete-orphan")
    kakao_reviews = db.relationship("KakaoReview", backref="place", cascade="all, delete-orphan")

    __table_args__ = (db.UniqueConstraint("school_id", "kakao_id"),)

    def category_list(self):
        return [c for c in (self.category or "").split(",") if c]

    @property
    def category_text(self):
        return " · ".join(self.category_list())


class Review(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    place_id = db.Column(db.Integer, db.ForeignKey("place.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    rating = db.Column(db.Integer, nullable=False)              # 1~5
    content = db.Column(db.Text, nullable=False)
    price = db.Column(db.String(30), nullable=False)            # 가격대 키 (p1~p4), 여러 개면 쉼표 구분
    tags = db.Column(db.String(255), default="")                # 좌석·기타 태그 키, 쉼표 구분
    receipt_path = db.Column(db.String(255))                    # 영수증 파일 (비공개 저장)
    is_sample = db.Column(db.Boolean, nullable=False, default=False, server_default="0")  # 🧪 시연용 샘플 리뷰
    report_count = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    photos = db.relationship("ReviewPhoto", backref="review", cascade="all, delete-orphan",
                             order_by="ReviewPhoto.id")

    # 1인 1가게 1리뷰
    __table_args__ = (db.UniqueConstraint("place_id", "user_id"),)

    def price_list(self):
        return [p for p in (self.price or "").split(",") if p]

    def tag_list(self):
        return [t for t in (self.tags or "").split(",") if t]


class KakaoReview(db.Model):
    """시연용: 운영자가 카카오맵 가게 페이지에서 북마클릿으로 옮겨 온 리뷰 (출처 표시, 통계 제외)"""
    id = db.Column(db.Integer, primary_key=True)
    place_id = db.Column(db.Integer, db.ForeignKey("place.id"), nullable=False)
    kakao_review_id = db.Column(db.String(30), nullable=False)
    rating = db.Column(db.Integer)
    content = db.Column(db.Text, nullable=False)
    written_on = db.Column(db.String(10))                         # 카카오맵 작성일 (YYYY-MM-DD)
    imported_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint("place_id", "kakao_review_id"),)


class ReviewPhoto(db.Model):
    """리뷰에 올린 음식·메뉴 사진 (같은 학교 학생에게 공개)"""
    id = db.Column(db.Integer, primary_key=True)
    review_id = db.Column(db.Integer, db.ForeignKey("review.id"), nullable=False)
    filename = db.Column(db.String(64), nullable=False, unique=True)
    report_count = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Report(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    target_type = db.Column(db.String(10), nullable=False)      # review / closed / photo
    target_id = db.Column(db.Integer, nullable=False)
    reason = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint("user_id", "target_type", "target_id"),)


class Vote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(20), unique=True, nullable=False)
    title = db.Column(db.String(100), nullable=False)
    school_id = db.Column(db.Integer, db.ForeignKey("school.id"), nullable=False)
    creator_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)

    options = db.relationship("VoteOption", backref="vote", cascade="all, delete-orphan")


class VoteOption(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    vote_id = db.Column(db.Integer, db.ForeignKey("vote.id"), nullable=False)
    place_id = db.Column(db.Integer, db.ForeignKey("place.id"), nullable=False)

    place = db.relationship("Place")


class Ballot(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    vote_id = db.Column(db.Integer, db.ForeignKey("vote.id"), nullable=False)
    option_id = db.Column(db.Integer, db.ForeignKey("vote_option.id"), nullable=False)
    voter_key = db.Column(db.String(64), nullable=False)        # 로그인 사용자 ID 또는 브라우저 쿠키

    __table_args__ = (db.UniqueConstraint("vote_id", "voter_key"),)
