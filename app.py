import os
import json
import shutil
import subprocess
import tempfile
import uuid
import io
from pathlib import Path
from datetime import datetime, timezone, timedelta
from functools import wraps
from urllib.parse import urljoin, urlparse

from flask import (
    Flask, abort, flash, jsonify, redirect, render_template, request, url_for
)
from flask_login import (
    LoginManager, UserMixin, current_user, login_required,
    login_user, logout_user
)
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv
import cloudinary
import cloudinary.uploader
import cloudinary.utils
import cloudinary.api
from flask_wtf.csrf import CSRFError, CSRFProtect
from sqlalchemy import delete, or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.exceptions import RequestEntityTooLarge


# ==================================================
# APP CONFIGURATION
# ==================================================

load_dotenv()  # Loads a local .env file when present; Vercel uses dashboard variables.
app = Flask(__name__)

_secret = os.environ.get("SECRET_KEY")
if not _secret and os.environ.get("VERCEL"):
    raise RuntimeError("SECRET_KEY must be configured in the Vercel environment variables.")
if not _secret:
    app.logger.warning("SECRET_KEY not set - using an insecure development-only key.")

app.config["SECRET_KEY"] = _secret or "dev-only-change-me"
_database_url = os.environ.get("DATABASE_URL", "sqlite:///funaab_one.db").strip()
# Supabase may provide postgres:// or postgresql:// URLs; psycopg 3 is installed in production.
if _database_url.startswith("postgres://"):
    _database_url = "postgresql+psycopg://" + _database_url[len("postgres://"):]
elif _database_url.startswith("postgresql://"):
    _database_url = "postgresql+psycopg://" + _database_url[len("postgresql://"):]
app.config["SQLALCHEMY_DATABASE_URI"] = _database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
# Drop dead connections instead of crashing on the first request after idle (serverless + pooler).
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}
# Vercel functions have a small request-body limit. Larger media must use direct-to-Cloudinary uploads.
app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("MAX_CONTENT_LENGTH", str(4 * 1024 * 1024))) if os.environ.get("VERCEL") else int(os.environ.get("MAX_CONTENT_LENGTH", str(1024 * 1024 * 1024)))
UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", "/tmp/funaab_one_uploads" if os.environ.get("VERCEL") else str(Path(app.static_folder) / "uploads")))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

CLOUDINARY_CLOUD_NAME = os.environ.get("CLOUDINARY_CLOUD_NAME", "").strip()
CLOUDINARY_API_KEY = os.environ.get("CLOUDINARY_API_KEY", "").strip()
CLOUDINARY_API_SECRET = os.environ.get("CLOUDINARY_API_SECRET", "").strip()
CLOUDINARY_ENABLED = bool(CLOUDINARY_CLOUD_NAME and CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET)
if CLOUDINARY_ENABLED:
    cloudinary.config(
        cloud_name=CLOUDINARY_CLOUD_NAME,
        api_key=CLOUDINARY_API_KEY,
        api_secret=CLOUDINARY_API_SECRET,
        secure=True,
    )
MAX_IMAGE_BYTES = 15 * 1024 * 1024
MAX_VIDEO_BYTES = 1024 * 1024 * 1024
COMPRESSION_THRESHOLD = 25 * 1024 * 1024
TARGET_VIDEO_BYTES = 24 * 1024 * 1024
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
MAX_AVATAR_BYTES = 5 * 1024 * 1024
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}

app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

if os.environ.get("FLASK_HTTPS_ONLY") == "1" or os.environ.get("VERCEL"):
    # Vercel is always HTTPS, so the secure flag is safe there.
    app.config["SESSION_COOKIE_SECURE"] = True

db = SQLAlchemy(app)
csrf = CSRFProtect(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"
login_manager.login_message = "Please log in to continue."
login_manager.login_message_category = "info"


# ==================================================
# CONSTANTS
# ==================================================

LEVELS = {"100", "200", "300", "400", "500", "600"}

DEPARTMENT_GROUPS = {
    "Agriculture, Environment and Related Programmes": [
        "Agricultural Administration",
        "Agricultural Economics and Farm Management",
        "Agricultural Extension and Rural Development",
        "Animal Breeding and Genetics",
        "Animal Nutrition",
        "Animal Physiology",
        "Animal Production and Health",
        "Aquaculture and Fisheries Management",
        "Climate Science and Agricultural Meteorology",
        "Crop Protection",
        "Environmental Management and Toxicology",
        "Forest Resource Management",
        "Geology",
        "Horticulture",
        "Hydrology and Water Resources Management",
        "Pasture and Range Management",
        "Plant Breeding and Seed Technology",
        "Plant Physiology and Crop Production",
        "Soil Science and Land Management",
        "Water Resources Management and Agro-meteorology",
        "Wildlife and Eco-tourism Management",
        "Water Sanitation and Hygiene",
    ],
    "COLBIOS": [
        "Biochemistry",
        "Biotechnology",
        "Microbiology",
        "Public Health",
        "Pure and Applied Botany",
        "Pure and Applied Zoology",
        "Science Lab Technology",
    ],
    "COLCOMPS": [
        "Computer Science",
        "Cyber Security",
        "Data Science",
        "Information Communication Technology",
        "Information Systems",
        "Information Technology",
        "Software Engineering",
    ],
    "COLFHEC": [
        "Clothing & Textile Design",
        "Food Science and Technology",
        "Home Science and Management",
        "Hospitality and Tourism",
        "Nutrition and Dietetics",
    ],
    "COLPHYS": [
        "Chemistry",
        "Geophysics",
        "Industrial Chemistry",
        "Mathematics",
        "Physics",
        "Statistics",
    ],
    "COLENG": [
        "Agricultural Engineering",
        "Civil Engineering",
        "Electrical and Electronics Engineering",
        "Mechanical Engineering",
        "Mechatronic Engineering",
    ],
    "COLVET": [
        "Veterinary Medicine",
    ],
    "COLENDS": [
        "Accounting",
        "Banking and Finance",
        "Business Administration",
        "Cooperative Studies",
        "Development Studies",
        "Economics",
        "Entrepreneurial Studies",
        "Library and Information Science",
    ],
    "FUNAAB - FCAIB": [
        "Agriculture (FCAIB)",
        "Home Science and Management (FCAIB)",
    ],
    "FUNAAB - OYSCATECH": [
        "Agricultural Engineering & Technology (OYSCATECH)",
        "Agriculture (OYSCATECH)",
        "Chemistry (OYSCATECH)",
        "Computer Science (OYSCATECH)",
        "Forestry and Wildlife Management (OYSCATECH)",
        "Microbiology (OYSCATECH)",
        "Statistics (OYSCATECH)",
    ],
}

DEPARTMENTS = {
    name
    for group in DEPARTMENT_GROUPS.values()
    for name in group
}

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128
MAX_POST_LENGTH = 280
MAX_COMMENT_LENGTH = 500

ADMIN_USERNAMES = {
    "rex4u_"
}

REPORT_REASONS = {
    "spam": "Spam",
    "harassment": "Harassment or bullying",
    "inappropriate": "Inappropriate content",
    "other": "Something else",
}

DUMMY_HASH = generate_password_hash("not-a-real-password")


# ==================================================
# DATABASE MODELS
# ==================================================

def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def create_notification(recipient_id, actor_id, kind, target_type="profile", target_id=None, conversation_id=None):
    """Queue a notification without notifying users about their own actions."""
    if not recipient_id or not actor_id or recipient_id == actor_id:
        return
    db.session.add(Notification(
        recipient_id=recipient_id,
        actor_id=actor_id,
        kind=kind,
        target_type=target_type,
        target_id=target_id,
        conversation_id=conversation_id,
    ))


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(100), nullable=False)
    username = db.Column(
        db.String(30), unique=True, nullable=False, index=True
    )
    email = db.Column(
        db.String(254), unique=True, nullable=False, index=True
    )
    level = db.Column(db.String(3), nullable=False)
    department = db.Column(db.String(120), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    # Optional profile fields. Existing users receive NULL values safely.
    profile_picture = db.Column(db.String(500), nullable=True)
    bio = db.Column(db.String(300), nullable=False, default="")
    # Additive account moderation flag; existing accounts default to active.
    is_suspended = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())

    @property
    def is_admin(self):
        return self.username.lower() in ADMIN_USERNAMES


class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    body = db.Column(db.String(MAX_POST_LENGTH), nullable=False, default="")
    created_at = db.Column(
        db.DateTime, default=utcnow, nullable=False, index=True
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False, index=True
    )

    author = db.relationship("User", backref="posts")

    # Optional media fields; already added to the existing SQLite database.
    media_url = db.Column(db.String(500), nullable=True)
    media_type = db.Column(db.String(10), nullable=True)

    comments = db.relationship(
        "Comment",
        backref="post",
        cascade="all, delete-orphan",
        order_by="Comment.created_at.asc()",
    )

    likes = db.relationship(
        "PostLike",
        backref="post",
        cascade="all, delete-orphan",
    )

    reposts = db.relationship(
        "Repost",
        backref="original_post",
        cascade="all, delete-orphan",
    )

    reports = db.relationship(
        "Report",
        backref="post",
        cascade="all, delete-orphan",
    )

    bookmarks = db.relationship(
        "Bookmark",
        backref="post",
        cascade="all, delete-orphan",
    )

    poll = db.relationship("Poll", back_populates="post", uselist=False, cascade="all, delete-orphan")


class Poll(db.Model):
    __tablename__ = "poll"
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey("post.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    closes_at = db.Column(db.DateTime, nullable=True)
    allow_vote_change = db.Column(db.Boolean, nullable=False, default=True, server_default=db.true())
    post = db.relationship("Post", back_populates="poll")
    options = db.relationship("PollOption", back_populates="poll", cascade="all, delete-orphan", order_by="PollOption.position")


class PollOption(db.Model):
    __tablename__ = "poll_option"
    id = db.Column(db.Integer, primary_key=True)
    poll_id = db.Column(db.Integer, db.ForeignKey("poll.id", ondelete="CASCADE"), nullable=False, index=True)
    text = db.Column(db.String(120), nullable=False)
    position = db.Column(db.Integer, nullable=False, default=0)
    poll = db.relationship("Poll", back_populates="options")
    votes = db.relationship("PollVote", back_populates="option", cascade="all, delete-orphan")


class PollVote(db.Model):
    __tablename__ = "poll_vote"
    __table_args__ = (db.UniqueConstraint("poll_id", "user_id", name="uq_poll_vote_user"),)
    id = db.Column(db.Integer, primary_key=True)
    poll_id = db.Column(db.Integer, db.ForeignKey("poll.id", ondelete="CASCADE"), nullable=False, index=True)
    option_id = db.Column(db.Integer, db.ForeignKey("poll_option.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    option = db.relationship("PollOption", back_populates="votes")


class Follow(db.Model):
    """A user following another user; stored in its own table."""
    __table_args__ = (
        db.UniqueConstraint("follower_id", "followed_id", name="unique_user_follow"),
        db.CheckConstraint("follower_id != followed_id", name="no_self_follow"),
    )

    id = db.Column(db.Integer, primary_key=True)
    follower_id = db.Column(
        db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    followed_id = db.Column(
        db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)


class Story(db.Model):
    """A short-lived campus story backed by a verified Cloudinary asset."""
    __tablename__ = "story"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    media_url = db.Column(db.String(500), nullable=False)
    public_id = db.Column(db.String(500), nullable=False, unique=True)
    media_type = db.Column(db.String(10), nullable=False)
    caption = db.Column(db.String(180), nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    expires_at = db.Column(db.DateTime, nullable=False, index=True)
    author = db.relationship("User", backref=db.backref("stories", cascade="all, delete-orphan"))
    views = db.relationship("StoryView", back_populates="story", cascade="all, delete-orphan")


class StoryView(db.Model):
    __tablename__ = "story_view"
    __table_args__ = (db.UniqueConstraint("story_id", "viewer_id", name="uq_story_viewer"),)
    id = db.Column(db.Integer, primary_key=True)
    story_id = db.Column(db.Integer, db.ForeignKey("story.id", ondelete="CASCADE"), nullable=False, index=True)
    viewer_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    viewed_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    story = db.relationship("Story", back_populates="views")
    viewer = db.relationship("User")



class Community(db.Model):
    """A student-created campus group."""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False, unique=True, index=True)
    description = db.Column(db.String(500), nullable=False, default="")
    category = db.Column(db.String(30), nullable=False, default="General")
    creator_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)
    creator = db.relationship("User", foreign_keys=[creator_id])
    memberships = db.relationship("CommunityMember", back_populates="community", cascade="all, delete-orphan")


class CommunityMember(db.Model):
    __table_args__ = (
        db.UniqueConstraint("community_id", "user_id", name="unique_community_member"),
    )
    id = db.Column(db.Integer, primary_key=True)
    community_id = db.Column(db.Integer, db.ForeignKey("community.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    joined_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    community = db.relationship("Community", back_populates="memberships")
    user = db.relationship("User")


class CommunityPost(db.Model):
    """A post shared inside a community."""
    id = db.Column(db.Integer, primary_key=True)
    community_id = db.Column(db.Integer, db.ForeignKey("community.id", ondelete="CASCADE"), nullable=False, index=True)
    author_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    body = db.Column(db.String(3000), nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)
    community = db.relationship("Community", backref=db.backref("posts", cascade="all, delete-orphan", order_by="CommunityPost.created_at.desc()"))
    author = db.relationship("User", foreign_keys=[author_id])
    comments = db.relationship("CommunityPostComment", back_populates="post", cascade="all, delete-orphan", order_by="CommunityPostComment.created_at.asc()")


class CommunityPostComment(db.Model):
    """A comment or nested reply on a community post."""
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey("community_post.id", ondelete="CASCADE"), nullable=False, index=True)
    author_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    parent_id = db.Column(db.Integer, db.ForeignKey("community_post_comment.id", ondelete="CASCADE"), nullable=True, index=True)
    body = db.Column(db.String(1200), nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)
    post = db.relationship("CommunityPost", back_populates="comments")
    author = db.relationship("User", foreign_keys=[author_id])
    parent = db.relationship("CommunityPostComment", remote_side=[id], backref=db.backref("replies", order_by="CommunityPostComment.created_at.asc()"))


class Conversation(db.Model):
    """A private direct conversation or a multi-member group chat."""
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=True)  # set for group chats
    group_description = db.Column(db.String(500), nullable=True)
    group_rules = db.Column(db.String(1000), nullable=True)
    group_avatar_url = db.Column(db.String(500), nullable=True)
    is_group = db.Column(db.Boolean, nullable=False, default=False)
    creator_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)
    creator = db.relationship("User", foreign_keys=[creator_id])
    members = db.relationship("ConversationMember", back_populates="conversation", cascade="all, delete-orphan")
    messages = db.relationship("Message", back_populates="conversation", cascade="all, delete-orphan", order_by="Message.created_at.asc()")


class ConversationMember(db.Model):
    __table_args__ = (db.UniqueConstraint("conversation_id", "user_id", name="unique_conversation_member"),)
    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey("conversation.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    last_read_at = db.Column(db.DateTime, nullable=True)
    typing_until = db.Column(db.DateTime, nullable=True)
    joined_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    conversation = db.relationship("Conversation", back_populates="members")
    user = db.relationship("User")


class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey("conversation.id", ondelete="CASCADE"), nullable=False, index=True)
    sender_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    body = db.Column(db.String(2000), nullable=False, default="")
    image_url = db.Column(db.String(500), nullable=True)
    audio_url = db.Column(db.String(500), nullable=True)
    reply_to_message_id = db.Column(db.Integer, db.ForeignKey("message.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)
    conversation = db.relationship("Conversation", back_populates="messages")
    sender = db.relationship("User")
    reply_to = db.relationship("Message", remote_side=[id], foreign_keys=[reply_to_message_id], uselist=False)
    reactions = db.relationship("MessageReaction", back_populates="message", cascade="all, delete-orphan", passive_deletes=True)


class MessageReaction(db.Model):
    __table_args__ = (db.UniqueConstraint("message_id", "user_id", name="unique_message_reaction_user"),)
    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.Integer, db.ForeignKey("message.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    emoji = db.Column(db.String(8), nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    message = db.relationship("Message", back_populates="reactions")
    user = db.relationship("User")


class Comment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    body = db.Column(db.String(MAX_COMMENT_LENGTH), nullable=False)
    created_at = db.Column(
        db.DateTime, default=utcnow, nullable=False
    )
    post_id = db.Column(
        db.Integer, db.ForeignKey("post.id"), nullable=False, index=True
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False, index=True
    )

    author = db.relationship("User")


class PostLike(db.Model):
    __table_args__ = (
        db.UniqueConstraint(
            "post_id", "user_id", name="unique_post_like"
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(
        db.Integer, db.ForeignKey("post.id"), nullable=False, index=True
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False, index=True
    )
    created_at = db.Column(
        db.DateTime, default=utcnow, nullable=False
    )


class Bookmark(db.Model):
    """A private saved-post relationship; each user can save a post once."""
    __table_args__ = (
        db.UniqueConstraint("post_id", "user_id", name="unique_post_bookmark"),
    )

    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(
        db.Integer, db.ForeignKey("post.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    user = db.relationship("User", backref=db.backref("bookmarks", cascade="all, delete-orphan"))


class Repost(db.Model):
    __table_args__ = (
        db.UniqueConstraint(
            "post_id", "user_id", name="unique_user_repost"
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(
        db.Integer, db.ForeignKey("post.id"), nullable=False, index=True
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False, index=True
    )
    created_at = db.Column(
        db.DateTime, default=utcnow, nullable=False
    )
    author = db.relationship("User")


class Notification(db.Model):
    """An in-app activity notification for a recipient."""
    id = db.Column(db.Integer, primary_key=True)
    recipient_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    actor_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    kind = db.Column(db.String(20), nullable=False, index=True)  # follow, like, comment, reply, message
    target_type = db.Column(db.String(30), nullable=False, default="profile")
    target_id = db.Column(db.Integer, nullable=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey("conversation.id", ondelete="CASCADE"), nullable=True, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    is_read = db.Column(db.Boolean, nullable=False, default=False, index=True)
    recipient = db.relationship("User", foreign_keys=[recipient_id])
    actor = db.relationship("User", foreign_keys=[actor_id])
    conversation = db.relationship("Conversation", foreign_keys=[conversation_id])


class Report(db.Model):
    __table_args__ = (
        db.UniqueConstraint(
            "post_id", "reporter_id", name="unique_post_reporter"
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(
        db.Integer, db.ForeignKey("post.id"), nullable=False, index=True
    )
    reporter_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False, index=True
    )
    reason = db.Column(db.String(30), nullable=False)
    created_at = db.Column(
        db.DateTime, default=utcnow, nullable=False
    )
    reporter = db.relationship("User")


class UserBlock(db.Model):
    """Directional block relationship. Either direction prevents interaction."""
    __table_args__ = (
        db.UniqueConstraint("blocker_id", "blocked_id", name="unique_user_block"),
        db.CheckConstraint("blocker_id != blocked_id", name="no_self_block"),
    )
    id = db.Column(db.Integer, primary_key=True)
    blocker_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    blocked_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    blocker = db.relationship("User", foreign_keys=[blocker_id])
    blocked = db.relationship("User", foreign_keys=[blocked_id])


class SafetyReport(db.Model):
    """Reports for accounts, comments, and community content, separate from legacy post reports."""
    __table_args__ = (
        db.UniqueConstraint("reporter_id", "target_type", "target_id", name="unique_safety_report_target"),
    )
    id = db.Column(db.Integer, primary_key=True)
    reporter_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    target_type = db.Column(db.String(30), nullable=False, index=True)  # user, comment, community_post, community_comment
    target_id = db.Column(db.Integer, nullable=False, index=True)
    reason = db.Column(db.String(30), nullable=False)
    details = db.Column(db.String(500), nullable=False, default="")
    status = db.Column(db.String(20), nullable=False, default="pending", index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="SET NULL"), nullable=True)
    reporter = db.relationship("User", foreign_keys=[reporter_id])
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_id])


class MarketListing(db.Model):
    """A student marketplace listing or wanted request."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    listing_type = db.Column(db.String(20), nullable=False, default="sale", index=True)  # sale or wanted
    title = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(1500), nullable=False, default="")
    price = db.Column(db.Integer, nullable=True)
    category = db.Column(db.String(40), nullable=False, default="Other", index=True)
    item_condition = db.Column(db.String(20), nullable=True)
    image_url = db.Column(db.String(500), nullable=True)
    status = db.Column(db.String(20), nullable=False, default="active", index=True)  # active, sold, closed
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    seller = db.relationship("User", foreign_keys=[user_id])


class AcademicMaterial(db.Model):
    """Student-shared course notes and academic documents."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    title = db.Column(db.String(160), nullable=False, index=True)
    course_code = db.Column(db.String(40), nullable=False, index=True)
    course_name = db.Column(db.String(160), nullable=False, default="")
    department = db.Column(db.String(120), nullable=False, index=True)
    level = db.Column(db.String(3), nullable=False, index=True)
    description = db.Column(db.String(1200), nullable=False, default="")
    original_filename = db.Column(db.String(255), nullable=False)
    file_url = db.Column(db.String(1000), nullable=False)
    file_extension = db.Column(db.String(12), nullable=False)
    file_size = db.Column(db.Integer, nullable=False, default=0)
    downloads = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    uploader = db.relationship("User", foreign_keys=[user_id])


# ==================================================
# HELPERS
# ==================================================

class AdminAction(db.Model):
    """Audit trail for administrative actions; additive and append-only."""
    id = db.Column(db.Integer, primary_key=True)
    admin_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    target_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True, index=True)
    action = db.Column(db.String(40), nullable=False)
    details = db.Column(db.String(500), nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    admin = db.relationship("User", foreign_keys=[admin_id])
    target_user = db.relationship("User", foreign_keys=[target_user_id])


@login_manager.user_loader
def load_user(user_id):
    try:
        return db.session.get(User, int(user_id))
    except (ValueError, TypeError):
        return None


@app.before_request
def enforce_suspension_for_active_sessions():
    # Login checks suspension too; this also ends sessions that were active before suspension.
    if current_user.is_authenticated and getattr(current_user, "is_suspended", False):
        logout_user()
        flash("This account is suspended. Please contact FUNAAB ONE support.", "error")
        return redirect(url_for("login"))


@app.context_processor
def inject_department_groups():
    return {
        "department_groups": DEPARTMENT_GROUPS,
        "report_reasons": REPORT_REASONS,
    }


@app.template_filter("timeago")
def timeago(dt):
    seconds = int((utcnow() - dt).total_seconds())

    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    if seconds < 604800:
        return f"{seconds // 86400}d"

    return dt.strftime("%b %d")


def is_safe_redirect(target):
    if not target:
        return False

    host = urlparse(request.host_url)
    destination = urlparse(urljoin(request.host_url, target))

    return (
        destination.scheme in ("http", "https")
        and destination.netloc == host.netloc
    )


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def users_blocked_between(user_a_id, user_b_id):
    """True when either user has blocked the other."""
    if not user_a_id or not user_b_id or user_a_id == user_b_id:
        return False
    return db.session.scalar(select(UserBlock.id).where(or_(
        (UserBlock.blocker_id == user_a_id) & (UserBlock.blocked_id == user_b_id),
        (UserBlock.blocker_id == user_b_id) & (UserBlock.blocked_id == user_a_id),
    )).limit(1)) is not None


def blocked_user_ids_for(user_id):
    """Return both outgoing and incoming block IDs to suppress mutual visibility."""
    outgoing = set(db.session.scalars(select(UserBlock.blocked_id).where(UserBlock.blocker_id == user_id)).all())
    incoming = set(db.session.scalars(select(UserBlock.blocker_id).where(UserBlock.blocked_id == user_id)).all())
    return outgoing | incoming


def safety_redirect(default_endpoint="dashboard"):
    target = request.form.get("next") or request.referrer
    if is_safe_redirect(target):
        return redirect(target)
    return redirect(url_for(default_endpoint))


def _submit_safety_report(target_type, target_id, target_author_id, redirect_target=None):
    if target_author_id == current_user.id:
        flash("You cannot report your own content.", "error")
        return safety_redirect()
    reason = request.form.get("reason", "").strip()
    details = request.form.get("details", "").strip()[:500]
    if reason not in REPORT_REASONS:
        flash("Please choose a valid reason for your report.", "error")
        return safety_redirect()
    try:
        db.session.add(SafetyReport(
            reporter_id=current_user.id, target_type=target_type, target_id=target_id,
            reason=reason, details=details,
        ))
        db.session.commit()
        flash("Thanks. Your report was sent to the moderation team.", "success")
    except IntegrityError:
        db.session.rollback()
        flash("You have already reported this item.", "info")
    except SQLAlchemyError:
        db.session.rollback()
        app.logger.exception("Safety report submission failed")
        flash("Could not submit the report. Please try again.", "error")
    if redirect_target and is_safe_redirect(redirect_target):
        return redirect(redirect_target)
    return safety_redirect()


def dashboard_redirect():
    target = request.referrer

    if is_safe_redirect(target):
        return redirect(target)

    return redirect(url_for("dashboard"))


@app.errorhandler(CSRFError)
def handle_csrf_error(error):
    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.accept_mimetypes.best == "application/json":
        return jsonify({"ok": False, "error": "Your session expired. Refresh the page and try again."}), 400
    flash("Your session expired. Please try again.", "error")

    if is_safe_redirect(request.referrer):
        return redirect(request.referrer)

    return redirect(url_for("home"))


@app.after_request
def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return response


def _error_response(code, title, message):
    if request.path.endswith("/poll"):
        return {"error": title}, code
    return render_template("error.html", code=code, title=title, message=message), code


@app.errorhandler(403)
def forbidden_error(error):
    return _error_response(403, "Access denied", getattr(error, "description", None) or "You don't have permission to view this page.")


@app.errorhandler(404)
def not_found_error(error):
    return _error_response(404, "Page not found", "That page doesn't exist or may have been removed.")


@app.errorhandler(500)
def server_error(error):
    db.session.rollback()
    return _error_response(500, "Something went wrong", "We hit a problem on our side. Please try again in a moment.")


def validate_registration(data):
    errors = []

    if not all(data.values()):
        errors.append("Please complete all fields.")

    if len(data["full_name"]) > 100:
        errors.append("Your full name must be 100 characters or fewer.")

    username = data["username"]

    if username and not (
        3 <= len(username) <= 30
        and username.replace("_", "").isalnum()
    ):
        errors.append(
            "Username must be 3-30 characters using letters, "
            "numbers, and underscores only."
        )

    email = data["email"]

    if email and (
        len(email) > 254
        or "@" not in email
        or "." not in email.rsplit("@", 1)[-1]
    ):
        errors.append("Please enter a valid email address.")

    if data["level"] not in LEVELS:
        errors.append("Please select a valid level.")

    if data["department"] not in DEPARTMENTS:
        errors.append("Please select a valid department.")

    password = data["password"]

    if password and not (
        MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH
    ):
        errors.append(
            f"Your password must be {MIN_PASSWORD_LENGTH}-"
            f"{MAX_PASSWORD_LENGTH} characters."
        )

    if password != data["confirm_password"]:
        errors.append("Your passwords do not match.")

    if username and db.session.scalar(
        select(User.id).where(User.username == username)
    ):
        errors.append("That username is already taken.")

    if email and db.session.scalar(
        select(User.id).where(User.email == email)
    ):
        errors.append("That email is already registered.")

    return errors


# ==================================================
# CLOUDINARY HELPERS
# ==================================================

def upload_media_to_cloudinary(source, *, resource_type="image", folder="funaab-one"):
    """Upload a validated local file or byte stream to Cloudinary and return its HTTPS URL."""
    if not CLOUDINARY_ENABLED:
        return None
    result = cloudinary.uploader.upload(
        source,
        resource_type=resource_type,
        folder=folder,
        unique_filename=True,
        overwrite=False,
        use_filename=False,
    )
    secure_url = result.get("secure_url")
    if not secure_url:
        raise RuntimeError("Cloudinary did not return a secure media URL.")
    return secure_url


# ==================================================
# MEDIA HELPERS
# ==================================================

def detect_image_type(path):
    """Check image signatures rather than trusting the filename or MIME header."""
    with open(path, "rb") as f:
        head = f.read(16)
    if head.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    return None


def save_profile_picture(file_storage):
    """Validate and save an optional profile image; return its static URL."""
    if not file_storage or not file_storage.filename:
        return None

    data = file_storage.stream.read(MAX_AVATAR_BYTES + 1)
    if not data:
        raise ValueError("The selected profile picture is empty.")
    if len(data) > MAX_AVATAR_BYTES:
        raise ValueError("Profile pictures must be 5 MB or smaller.")

    if data.startswith(b"\xff\xd8\xff"):
        extension = ".jpg"
    elif data.startswith(b"\x89PNG\r\n\x1a\n"):
        extension = ".png"
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        extension = ".webp"
    elif data[:6] in (b"GIF87a", b"GIF89a"):
        extension = ".gif"
    else:
        raise ValueError("Choose a valid JPG, PNG, WEBP, or GIF image.")

    if CLOUDINARY_ENABLED:
        return upload_media_to_cloudinary(
            io.BytesIO(data), resource_type="image", folder="funaab-one/profile-pictures"
        )

    filename = f"avatar_{uuid.uuid4().hex}{extension}"
    destination = UPLOAD_DIR / filename
    destination.write_bytes(data)
    return url_for("static", filename=f"uploads/{filename}")


def validate_video(path):
    """Use ffprobe to ensure the uploaded file contains a video stream."""
    probe = shutil.which("ffprobe")
    if not probe:
        raise RuntimeError("Install FFmpeg (including ffprobe) to enable video uploads.")
    result = subprocess.run(
        [probe, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=codec_type", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode or not json.loads(result.stdout or "{}").get("streams"):
        raise ValueError("The uploaded file is not a valid video.")


def convert_video(source, destination, large_video):
    """Convert to broadly compatible MP4 without forcing a new aspect ratio."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg is not installed. Run ffmpeg -version.")

    # Balanced quality-first settings. For large files, make progressively
    # smaller attempts, but never resize, crop, stretch, or force a frame rate.
    crf_steps = [20, 22, 24, 26] if large_video else [20]
    for crf in crf_steps:
        destination.unlink(missing_ok=True)
        command = [
            ffmpeg, "-nostdin", "-y", "-i", str(source),
            "-map", "0:v:0", "-map", "0:a?", "-sn", "-dn",
            "-c:v", "libx264", "-preset", "slow", "-crf", str(crf),
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
            "-movflags", "+faststart", "-map_metadata", "0",
            str(destination),
        ]
        try:
            result = subprocess.run(command, capture_output=True, timeout=3600)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Video processing timed out.") from exc
        if result.returncode != 0 or not destination.is_file() or destination.stat().st_size == 0:
            app.logger.error("FFmpeg failed: %s", result.stderr[-3000:].decode("utf-8", "replace") if result.stderr else "no details")
            raise RuntimeError("FFmpeg could not convert the uploaded video.")
        if not large_video or destination.stat().st_size <= TARGET_VIDEO_BYTES:
            return
    # 24 MB is a best-effort target; never reduce resolution/frame rate just
    # to hit it. The final attempt favors size while retaining the source size.


def create_video_thumbnail(video_path):
    """Create a lightweight first-frame poster beside the MP4, if possible."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    poster_path = video_path.with_suffix(".jpg")
    command = [
        ffmpeg, "-nostdin", "-y", "-ss", "1", "-i", str(video_path),
        "-frames:v", "1", "-vf",
        "scale=900:900:force_original_aspect_ratio=decrease",
        "-q:v", "3", str(poster_path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, timeout=90)
    except (subprocess.TimeoutExpired, OSError):
        poster_path.unlink(missing_ok=True)
        return None
    if result.returncode == 0 and poster_path.is_file() and poster_path.stat().st_size:
        return poster_path
    poster_path.unlink(missing_ok=True)
    return None


@app.errorhandler(RequestEntityTooLarge)
def handle_large_upload(error):
    flash("That upload is too large (maximum request size: 1 GB).", "error")
    return redirect(url_for("dashboard"))


# ==================================================
# PUBLIC PAGES
# ==================================================

@app.route("/")
def home():
    return render_template("index.html")




MARKET_CATEGORIES = {"Electronics", "Textbooks", "Fashion", "Hostel & Home", "Food", "Beauty", "Services", "Other"}
MARKET_CONDITIONS = {"New", "Like new", "Good", "Fair"}
MAX_MARKET_IMAGE_BYTES = 10 * 1024 * 1024


def save_market_image(file_storage):
    """Validate a listing image by file signature and save it under static/uploads."""
    if not file_storage or not file_storage.filename:
        return None
    data = file_storage.stream.read(MAX_MARKET_IMAGE_BYTES + 1)
    if not data:
        raise ValueError("The selected image is empty.")
    if len(data) > MAX_MARKET_IMAGE_BYTES:
        raise ValueError("Marketplace images must be 10 MB or smaller.")
    if data.startswith(b"\xff\xd8\xff"):
        extension = ".jpg"
    elif data.startswith(b"\x89PNG\r\n\x1a\n"):
        extension = ".png"
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        extension = ".webp"
    elif data[:6] in (b"GIF87a", b"GIF89a"):
        extension = ".gif"
    else:
        raise ValueError("Choose a valid JPG, PNG, WEBP, or GIF image.")
    if CLOUDINARY_ENABLED:
        return upload_media_to_cloudinary(
            io.BytesIO(data), resource_type="image", folder="funaab-one/marketplace"
        )

    filename = f"market_{uuid.uuid4().hex}{extension}"
    destination = UPLOAD_DIR / filename
    destination.write_bytes(data)
    return url_for("static", filename=f"uploads/{filename}")


ACADEMIC_ALLOWED_EXTENSIONS = {".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".txt", ".rtf"}
ACADEMIC_MAX_BYTES = 3 * 1024 * 1024


def _valid_academic_document(data, extension):
    if extension == ".pdf":
        return data.startswith(b"%PDF-")
    if extension in {".doc", ".xls"}:
        return data.startswith(bytes.fromhex("D0CF11E0A1B11AE1"))
    if extension in {".docx", ".pptx", ".xlsx"}:
        return data.startswith(b"PK\x03\x04")
    if extension == ".ppt":
        return data.startswith(bytes.fromhex("D0CF11E0A1B11AE1"))
    if extension == ".txt":
        try:
            data[:4096].decode("utf-8")
            return True
        except UnicodeDecodeError:
            return False
    if extension == ".rtf":
        return data.lstrip().startswith(b"{\\rtf")
    return False


@app.route("/academics", methods=["GET", "POST"])
@login_required
def academics():
    query = request.args.get("q", "").strip()[:100]
    department_filter = request.args.get("department", "").strip()[:120]
    level_filter = request.args.get("level", "").strip()
    if level_filter not in {"100", "200", "300", "400", "500", "600"}:
        level_filter = ""
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        course_code = request.form.get("course_code", "").strip().upper()
        course_name = request.form.get("course_name", "").strip()
        department = request.form.get("department", "").strip()
        level = request.form.get("level", "").strip()
        description = request.form.get("description", "").strip()
        uploaded = request.files.get("document")
        if not title or len(title) > 160 or not course_code or len(course_code) > 40:
            flash("Add a title and a valid course code.", "error")
        elif len(course_name) > 160 or len(description) > 1200:
            flash("Course name or description is too long.", "error")
        elif not department or len(department) > 120 or level not in {"100", "200", "300", "400", "500", "600"}:
            flash("Choose a department and academic level.", "error")
        elif not uploaded or not uploaded.filename:
            flash("Choose a PDF or supported document to upload.", "error")
        else:
            filename = Path(uploaded.filename).name
            extension = Path(filename).suffix.lower()
            try:
                if extension not in ACADEMIC_ALLOWED_EXTENSIONS:
                    raise ValueError("Supported files: PDF, DOC/DOCX, PPT/PPTX, XLS/XLSX, TXT and RTF.")
                data = uploaded.stream.read(ACADEMIC_MAX_BYTES + 1)
                if not data or len(data) > ACADEMIC_MAX_BYTES:
                    raise ValueError("Academic files must be 3 MB or smaller for this upload flow.")
                if not _valid_academic_document(data, extension):
                    raise ValueError("The file content does not match the selected document type.")
                if not CLOUDINARY_ENABLED:
                    raise ValueError("Academic uploads are unavailable until Cloudinary is configured.")
                result = cloudinary.uploader.upload(
                    io.BytesIO(data), resource_type="raw", folder="funaab-one/academic-materials",
                    public_id=f"{uuid.uuid4().hex}_{Path(filename).stem[:60]}",
                    use_filename=False, unique_filename=False, overwrite=False,
                    filename_override=filename,
                )
                file_url = result.get("secure_url")
                if not file_url:
                    raise ValueError("The document upload did not return a download link.")
                material = AcademicMaterial(
                    user_id=current_user.id, title=title, course_code=course_code,
                    course_name=course_name, department=department, level=level,
                    description=description, original_filename=filename,
                    file_url=file_url, file_extension=extension,
                    file_size=len(data), downloads=0,
                )
                db.session.add(material)
                db.session.commit()
                flash("Academic material uploaded successfully.", "success")
                return redirect(url_for("academics"))
            except ValueError as exc:
                db.session.rollback()
                flash(str(exc), "error")
            except Exception:
                db.session.rollback()
                app.logger.exception("Academic material upload failed")
                flash("Could not upload this document. Please try again.", "error")

    statement = select(AcademicMaterial)
    if query:
        pattern = f"%{query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')}%"
        statement = statement.where(or_(
            AcademicMaterial.title.ilike(pattern, escape="\\"),
            AcademicMaterial.course_code.ilike(pattern, escape="\\"),
            AcademicMaterial.course_name.ilike(pattern, escape="\\"),
            AcademicMaterial.description.ilike(pattern, escape="\\"),
            AcademicMaterial.department.ilike(pattern, escape="\\"),
        ))
    if department_filter:
        statement = statement.where(AcademicMaterial.department == department_filter)
    if level_filter:
        statement = statement.where(AcademicMaterial.level == level_filter)
    materials = db.session.scalars(statement.order_by(AcademicMaterial.created_at.desc()).limit(200)).all()
    departments = db.session.scalars(select(User.department).distinct().order_by(User.department)).all()
    return render_template("academics.html", materials=materials, query=query,
                           department_filter=department_filter, level_filter=level_filter,
                           departments=[d for d in departments if d], student=current_user)


@app.route("/academics/<int:material_id>/download")
@login_required
def download_academic_material(material_id):
    material = db.session.get(AcademicMaterial, material_id)
    if material is None:
        abort(404)
    material.downloads += 1
    db.session.commit()
    return redirect(material.file_url, code=302)


@app.route("/marketplace", methods=["GET", "POST"])
@login_required
def market_place():
    if request.method == "POST":
        listing_type = request.form.get("listing_type", "sale").strip().lower()
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        category = request.form.get("category", "Other").strip()
        condition = request.form.get("item_condition", "").strip()
        price_raw = request.form.get("price", "").strip().replace(",", "")
        errors = []
        if listing_type not in {"sale", "wanted"}:
            errors.append("Choose either For Sale or Wanted.")
        if not 3 <= len(title) <= 100:
            errors.append("Title must be between 3 and 100 characters.")
        if not description or len(description) > 1500:
            errors.append("Description must be between 1 and 1,500 characters.")
        if category not in MARKET_CATEGORIES:
            errors.append("Choose a valid category.")
        if listing_type == "sale" and condition not in MARKET_CONDITIONS:
            errors.append("Choose the item's condition.")
        price = None
        if price_raw:
            try:
                price = int(price_raw)
                if price < 0 or price > 2_000_000_000:
                    raise ValueError
            except ValueError:
                errors.append("Enter a valid price in naira.")
        if listing_type == "sale" and price is None:
            errors.append("Enter an asking price for the item.")
        image_url = None
        image_file = request.files.get("image")
        if image_file and image_file.filename:
            try:
                image_url = save_market_image(image_file)
            except ValueError as exc:
                errors.append(str(exc))
        if errors:
            for error in errors:
                flash(error, "error")
        else:
            try:
                listing = MarketListing(
                    user_id=current_user.id, listing_type=listing_type, title=title,
                    description=description, price=price, category=category,
                    item_condition=condition if listing_type == "sale" else None,
                    image_url=image_url,
                )
                db.session.add(listing)
                db.session.commit()
                flash("Your marketplace post is live!", "success")
                return redirect(url_for("market_place", kind=listing_type))
            except SQLAlchemyError:
                db.session.rollback()
                app.logger.exception("Marketplace listing creation failed")
                flash("Could not publish your listing. Please try again.", "error")

    query = request.args.get("q", "").strip()[:100]
    kind = request.args.get("kind", "all").strip().lower()
    category_filter = request.args.get("category", "").strip()
    status_filter = request.args.get("status", "active").strip().lower()
    hidden_user_ids = blocked_user_ids_for(current_user.id)
    statement = select(MarketListing).join(User, MarketListing.user_id == User.id)
    if hidden_user_ids:
        statement = statement.where(MarketListing.user_id.not_in(hidden_user_ids))
    if kind in {"sale", "wanted"}:
        statement = statement.where(MarketListing.listing_type == kind)
    if status_filter == "mine":
        statement = statement.where(MarketListing.user_id == current_user.id)
    elif status_filter == "sold":
        statement = statement.where(MarketListing.status == "sold")
    else:
        statement = statement.where(MarketListing.status == "active")
    if category_filter in MARKET_CATEGORIES:
        statement = statement.where(MarketListing.category == category_filter)
    if query:
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        statement = statement.where(or_(
            MarketListing.title.ilike(f"%{escaped}%", escape="\\"),
            MarketListing.description.ilike(f"%{escaped}%", escape="\\"),
            MarketListing.category.ilike(f"%{escaped}%", escape="\\"),
            User.username.ilike(f"%{escaped}%", escape="\\"),
            User.full_name.ilike(f"%{escaped}%", escape="\\"),
        ))
    listings = db.session.scalars(statement.order_by(MarketListing.created_at.desc()).limit(200)).all()
    return render_template(
        "marketplace.html", student=current_user, listings=listings, query=query,
        kind=kind, category_filter=category_filter, status_filter=status_filter,
        market_categories=sorted(MARKET_CATEGORIES), market_conditions=sorted(MARKET_CONDITIONS),
    )


@app.route("/marketplace/<int:listing_id>/sold", methods=["POST"])
@login_required
def market_mark_sold(listing_id):
    listing = db.session.get(MarketListing, listing_id)
    if listing is None:
        abort(404)
    if listing.user_id != current_user.id:
        abort(403)
    listing.status = "sold"
    db.session.commit()
    flash("Listing marked as sold.", "success")
    return redirect(url_for("market_place", status="mine"))


@app.route("/marketplace/<int:listing_id>/reopen", methods=["POST"])
@login_required
def market_reopen_listing(listing_id):
    listing = db.session.get(MarketListing, listing_id)
    if listing is None:
        abort(404)
    if listing.user_id != current_user.id:
        abort(403)
    listing.status = "active"
    db.session.commit()
    flash("Your listing is available again.", "success")
    return redirect(url_for("market_place", status="mine"))


@app.route("/marketplace/<int:listing_id>/delete", methods=["POST"])
@login_required
def market_delete_listing(listing_id):
    listing = db.session.get(MarketListing, listing_id)
    if listing is None:
        abort(404)
    if listing.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    db.session.delete(listing)
    db.session.commit()
    flash("Marketplace post deleted.", "success")
    return redirect(url_for("market_place", status="mine"))


# ==================================================
# REGISTRATION
# ==================================================

@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "GET":
        return render_template(
            "register.html",
            errors=[],
            form_data={},
        )

    data = {
        "full_name": request.form.get("full_name", "").strip(),
        "username": request.form.get("username", "").strip().lower(),
        "email": request.form.get("email", "").strip().lower(),
        "level": request.form.get("level", "").strip(),
        "department": request.form.get("department", "").strip(),
        "password": request.form.get("password", ""),
        "confirm_password": request.form.get("confirm_password", ""),
    }

    form_data = {
        key: value
        for key, value in data.items()
        if "password" not in key
    }

    errors = validate_registration(data)
    avatar_file = request.files.get("profile_picture")
    avatar_url = None
    if avatar_file and avatar_file.filename:
        try:
            avatar_url = save_profile_picture(avatar_file)
        except ValueError as exc:
            errors.append(str(exc))

    if errors:
        return render_template(
            "register.html",
            errors=errors,
            form_data=form_data,
        )

    student = User(
        full_name=data["full_name"],
        username=data["username"],
        email=data["email"],
        level=data["level"],
        department=data["department"],
        password_hash=generate_password_hash(data["password"]),
        profile_picture=avatar_url,
    )

    try:
        db.session.add(student)
        db.session.commit()

    except IntegrityError:
        db.session.rollback()
        return render_template(
            "register.html",
            errors=["That username or email is already registered."],
            form_data=form_data,
        )

    except SQLAlchemyError:
        db.session.rollback()
        app.logger.exception("Registration failed")
        return render_template(
            "register.html",
            errors=["We could not create your account. Please try again."],
            form_data=form_data,
        )

    flash("Your account was created successfully. Please log in.", "success")
    return redirect(url_for("login"))


# ==================================================
# LOGIN / LOGOUT
# ==================================================

@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "GET":
        return render_template(
            "login.html",
            error=None,
            identifier="",
        )

    identifier = request.form.get("identifier", "").strip().lower()
    password = request.form.get("password", "")

    user = None

    if identifier:
        user = db.session.scalar(
            select(User).where(
                or_(
                    User.username == identifier,
                    User.email == identifier,
                )
            )
        )

    password_ok = check_password_hash(
        user.password_hash if user else DUMMY_HASH,
        password,
    )

    if not user or not password_ok:
        return (
            render_template(
                "login.html",
                error="Invalid username/email or password.",
                identifier=identifier,
            ),
            401,
        )

    if user.is_suspended:
        return (
            render_template(
                "login.html",
                error="This account is currently suspended. Please contact FUNAAB ONE support.",
                identifier=identifier,
            ),
            403,
        )

    login_user(user)
    flash(f"Welcome back, {user.full_name}!", "success")

    next_page = request.args.get("next")

    if is_safe_redirect(next_page):
        return redirect(next_page)

    return redirect(url_for("dashboard"))


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))


# ==================================================
# DASHBOARD / CAMPUS FEED
# ==================================================

@app.route("/dashboard")
@login_required
def dashboard():
    hidden_user_ids = blocked_user_ids_for(current_user.id)
    post_query = select(Post)
    if hidden_user_ids:
        post_query = post_query.where(Post.user_id.not_in(hidden_user_ids))
    posts = db.session.scalars(
        post_query.order_by(Post.created_at.desc()).limit(100)
    ).all()

    liked_post_ids = set(
        db.session.scalars(
            select(PostLike.post_id).where(
                PostLike.user_id == current_user.id
            )
        ).all()
    )

    reposted_post_ids = set(
        db.session.scalars(
            select(Repost.post_id).where(
                Repost.user_id == current_user.id
            )
        ).all()
    )
    bookmarked_post_ids = set(
        db.session.scalars(
            select(Bookmark.post_id).where(Bookmark.user_id == current_user.id)
        ).all()
    )

    now_utc = utcnow()
    active_stories = db.session.scalars(
        select(Story).where(Story.expires_at > now_utc)
        .order_by(Story.created_at.desc()).limit(150)
    ).all()
    stories_by_user = {}
    for story in active_stories:
        stories_by_user.setdefault(story.user_id, []).append(story)
    # Fetch all of the current user's story views in one query instead of
    # issuing a separate database query for every story (N+1 query pattern).
    other_story_ids = [
        story.id for story in active_stories
        if story.user_id != current_user.id
    ]
    seen_story_ids = set()
    if other_story_ids:
        seen_story_ids = set(db.session.scalars(
            select(StoryView.story_id).where(
                StoryView.viewer_id == current_user.id,
                StoryView.story_id.in_(other_story_ids),
            )
        ).all())

    story_groups = []
    for owner_id, owner_stories in stories_by_user.items():
        owner = owner_stories[0].author
        story_groups.append({
            "user_id": owner_id,
            "username": owner.username,
            "name": owner.full_name or owner.username,
            "avatar": owner.profile_picture,
            "is_mine": owner_id == current_user.id,
            "stories": owner_stories,
            "unseen": any(
                item.id not in seen_story_ids for item in owner_stories
            ) if owner_id != current_user.id else False,
        })
    story_groups.sort(key=lambda group: (not group["is_mine"], not group["unseen"], group["name"].lower()))
    story_json = [{
        "user_id": group["user_id"],
        "username": group["username"],
        "name": group["name"],
        "avatar": group["avatar"],
        "stories": [{
            "id": story.id,
            "media_url": story.media_url,
            "media_type": story.media_type,
            "caption": story.caption or "",
            "created_at": story.created_at.isoformat() if story.created_at else "",
            "expires_at": story.expires_at.isoformat() if story.expires_at else "",
        } for story in group["stories"]],
    } for group in story_groups]

    return render_template(
        "dashboard.html",
        student=current_user,
        posts=posts,
        story_groups=story_groups,
        story_json=story_json,
        liked_post_ids=liked_post_ids,
        reposted_post_ids=reposted_post_ids,
        bookmarked_post_ids=bookmarked_post_ids,
        media_enabled=True,
        now=datetime.now(),
    )


# ==================================================
# CAMPUS STORIES
# ==================================================

@app.route("/api/stories/upload-signature", methods=["POST"])
@login_required
def story_upload_signature():
    if not CLOUDINARY_ENABLED:
        return {"error": "Media uploads are not configured."}, 503
    payload = request.get_json(silent=True) or {}
    resource_type = payload.get("resource_type")
    if resource_type not in {"image", "video"}:
        return {"error": "Choose a photo or video."}, 400
    timestamp = int(datetime.now(timezone.utc).timestamp())
    public_id = f"story_{current_user.id}_{uuid.uuid4().hex}"
    # Sign only the parameters sent by the browser to Cloudinary.
    params = {
        "timestamp": timestamp,
        "folder": "funaab-one/stories",
        "public_id": public_id,
    }
    signature = cloudinary.utils.api_sign_request(
        params, CLOUDINARY_API_SECRET
    )
    return {
        "cloud_name": CLOUDINARY_CLOUD_NAME,
        "api_key": CLOUDINARY_API_KEY,
        "timestamp": timestamp,
        "folder": params["folder"],
        "public_id": public_id,
        "signature": signature,
        "resource_type": resource_type,
    }


@app.route("/stories/create", methods=["POST"])
@login_required
def create_story():
    media_url = request.form.get("story_media_url", "").strip()
    public_id = request.form.get("story_public_id", "").strip()
    media_type = request.form.get("story_media_type", "").strip()
    caption = request.form.get("caption", "").strip()[:180]
    if not media_url or not public_id or media_type not in {"image", "video"}:
        flash("Choose a photo or video for your story.", "error")
        return redirect(url_for("dashboard"))
    expected_prefix = f"funaab-one/stories/story_{current_user.id}_"
    parsed_url = urlparse(media_url)
    if (not CLOUDINARY_ENABLED or not public_id.startswith(expected_prefix)
            or parsed_url.scheme != "https" or not parsed_url.hostname
            or not parsed_url.hostname.endswith("cloudinary.com")):
        flash("This story media could not be verified for your account.", "error")
        return redirect(url_for("dashboard"))
    try:
        asset = cloudinary.api.resource(public_id, resource_type=media_type, type="upload")
        verified_url = asset.get("secure_url")
        if not verified_url or asset.get("public_id") != public_id:
            raise ValueError("Asset verification failed.")
        story = Story(
            user_id=current_user.id,
            media_url=verified_url,
            public_id=public_id,
            media_type=media_type,
            caption=caption,
            expires_at=utcnow() + timedelta(hours=24),
        )
        db.session.add(story)
        db.session.commit()
        flash("Your story is live for 24 hours!", "success")
    except Exception:
        db.session.rollback()
        app.logger.exception("Story creation failed")
        flash("We couldn't publish that story. Please try again.", "error")
    return redirect(url_for("dashboard"))


@app.route("/stories/<int:story_id>/view", methods=["POST"])
@login_required
def view_story(story_id):
    story = db.session.get(Story, story_id)
    if story is None or story.expires_at <= utcnow():
        abort(404)
    if story.user_id != current_user.id and users_blocked_between(current_user.id, story.user_id):
        abort(404)
    if story.user_id != current_user.id:
        existing = db.session.scalar(select(StoryView).where(
            StoryView.story_id == story.id, StoryView.viewer_id == current_user.id
        ))
        if existing is None:
            db.session.add(StoryView(story_id=story.id, viewer_id=current_user.id))
            db.session.commit()
    return {"ok": True, "views": db.session.scalar(
        select(db.func.count(StoryView.id)).where(StoryView.story_id == story.id)
    ) or 0}


@app.route("/stories/<int:story_id>/delete", methods=["POST"])
@login_required
def delete_story(story_id):
    story = db.session.get(Story, story_id)
    if story is None:
        abort(404)
    if story.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    db.session.delete(story)
    db.session.commit()
    flash("Story deleted.", "success")
    return redirect(url_for("dashboard"))


# ==================================================
# USER PROFILES AND FOLLOWING
# ==================================================

@app.route("/u/<string:username>")
@login_required
def profile(username):
    profile_user = db.session.scalar(
        select(User).where(User.username == username)
    )
    if profile_user is None:
        abort(404)
    if profile_user.id != current_user.id and users_blocked_between(current_user.id, profile_user.id):
        abort(404)

    user_posts = db.session.scalars(
        select(Post)
        .where(Post.user_id == profile_user.id)
        .order_by(Post.created_at.desc())
    ).all()
    posts_count = len(user_posts)

    followers_count = db.session.scalar(
        select(db.func.count(Follow.id)).where(Follow.followed_id == profile_user.id)
    ) or 0
    following_count = db.session.scalar(
        select(db.func.count(Follow.id)).where(Follow.follower_id == profile_user.id)
    ) or 0

    is_following = False
    if profile_user.id != current_user.id:
        is_following = db.session.scalar(
            select(Follow.id).where(
                Follow.follower_id == current_user.id,
                Follow.followed_id == profile_user.id,
            )
        ) is not None

    return render_template(
        "profile.html",
        student=current_user,
        profile_user=profile_user,
        posts=user_posts,
        posts_count=posts_count,
        followers_count=followers_count,
        following_count=following_count,
        is_following=is_following,
    )


@app.route("/settings/profile", methods=["GET", "POST"])
@login_required
def edit_profile():
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        department = request.form.get("department", "").strip()
        level = request.form.get("level", "").strip()
        bio = request.form.get("bio", "").strip()
        avatar_file = request.files.get("profile_picture")

        errors = []
        if not full_name or len(full_name) > 100:
            errors.append("Enter a full name of 1–100 characters.")
        if department not in DEPARTMENTS:
            errors.append("Please select a valid department.")
        if level not in LEVELS:
            errors.append("Please select a valid academic level.")
        if len(bio) > 300:
            errors.append("Your bio must be 300 characters or fewer.")

        avatar_url = None
        if avatar_file and avatar_file.filename:
            try:
                avatar_url = save_profile_picture(avatar_file)
            except ValueError as exc:
                errors.append(str(exc))

        is_ajax_profile = request.headers.get("X-Requested-With") == "XMLHttpRequest"
        if errors:
            if is_ajax_profile:
                return {"ok": False, "error": " ".join(errors)}, 400
            for error in errors:
                flash(error, "error")
            return render_template("edit_profile.html", student=current_user), 400

        current_user.full_name = full_name
        current_user.department = department
        current_user.level = level
        current_user.bio = bio
        if avatar_url:
            current_user.profile_picture = avatar_url

        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            app.logger.exception("Profile update failed")
            if is_ajax_profile:
                return {"ok": False, "error": "Could not save your profile. Please try again."}, 500
            flash("Could not save your profile. Please try again.", "error")
            return render_template("edit_profile.html", student=current_user), 500

        if is_ajax_profile:
            return {"ok": True, "message": "Profile updated successfully.",
                    "full_name": current_user.full_name,
                    "department": current_user.department,
                    "level": current_user.level,
                    "bio": current_user.bio or "",
                    "profile_picture": current_user.profile_picture or "",
                    "profile_url": url_for("profile", username=current_user.username)}
        flash("Your profile has been updated.", "success")
        return redirect(url_for("profile", username=current_user.username))

    return render_template("edit_profile.html", student=current_user)


@app.route("/students")
@login_required
def find_students():
    query = request.args.get("q", "").strip().lower()[:30]
    hidden_user_ids = blocked_user_ids_for(current_user.id)
    statement = select(User).where(User.id != current_user.id)
    if hidden_user_ids:
        statement = statement.where(User.id.not_in(hidden_user_ids))
    if query:
        # Escape LIKE wildcard characters so the search is a literal username substring.
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        statement = statement.where(User.username.ilike(f"%{escaped}%", escape="\\"))
    students = db.session.scalars(statement.order_by(User.username.asc()).limit(100)).all()
    following_ids = set(db.session.scalars(
        select(Follow.followed_id).where(Follow.follower_id == current_user.id)
    ).all())
    return render_template(
        "find_students.html",
        student=current_user,
        students=students,
        following_ids=following_ids,
        query=query,
    )


@app.route("/api/search-suggestions")
@login_required
def search_suggestions():
    """Small, permission-aware search results for the live search box."""
    query = request.args.get("q", "").strip()[:100]
    if len(query) < 2:
        return jsonify({"students": [], "posts": []})

    hidden_user_ids = blocked_user_ids_for(current_user.id)
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped}%"

    user_statement = select(User).where(
        User.id != current_user.id,
        or_(User.username.ilike(pattern, escape="\\"),
            User.full_name.ilike(pattern, escape="\\"),
            User.department.ilike(pattern, escape="\\")),
    )
    if hidden_user_ids:
        user_statement = user_statement.where(User.id.not_in(hidden_user_ids))
    users = db.session.scalars(user_statement.order_by(User.username.asc()).limit(6)).all()

    post_statement = select(Post).join(User, Post.user_id == User.id).where(
        Post.body.ilike(pattern, escape="\\"),
        User.id != current_user.id,
    )
    if hidden_user_ids:
        post_statement = post_statement.where(User.id.not_in(hidden_user_ids))
    matching_posts = db.session.scalars(
        post_statement.order_by(Post.created_at.desc()).limit(6)
    ).all()

    return jsonify({
        "students": [{
            "name": user.full_name or user.username,
            "username": user.username,
            "department": user.department or "",
            "avatar": user.profile_picture or "",
            "url": url_for("profile", username=user.username),
        } for user in users],
        "posts": [{
            "id": post.id,
            "text": (post.body or "").strip()[:180],
            "username": post.author.username,
            "name": post.author.full_name or post.author.username,
            "url": url_for("general_search", q=query) + f"#post-{post.id}",
        } for post in matching_posts],
    })


@app.route("/search")
@login_required
def general_search():
    query = request.args.get("q", "").strip()[:100]
    hidden_user_ids = blocked_user_ids_for(current_user.id)
    students = []
    posts = []

    if query:
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        user_statement = select(User).where(
            User.id != current_user.id,
            or_(User.username.ilike(pattern, escape="\\"),
                User.full_name.ilike(pattern, escape="\\"),
                User.department.ilike(pattern, escape="\\")),
        )
        if hidden_user_ids:
            user_statement = user_statement.where(User.id.not_in(hidden_user_ids))
        students = db.session.scalars(user_statement.order_by(User.username.asc()).limit(30)).all()

        post_statement = select(Post).join(User, Post.user_id == User.id).where(
            Post.body.ilike(pattern, escape="\\"),
            User.id != current_user.id,
        )
        if hidden_user_ids:
            post_statement = post_statement.where(User.id.not_in(hidden_user_ids))
        posts = db.session.scalars(post_statement.order_by(Post.created_at.desc()).limit(50)).all()

    return render_template(
        "search.html", student=current_user, query=query,
        students=students, posts=posts,
    )


@app.route("/u/<string:username>/follow", methods=["POST"])
@login_required
def toggle_follow(username):
    profile_user = db.session.scalar(
        select(User).where(User.username == username)
    )
    if profile_user is None:
        abort(404)
    if profile_user.id == current_user.id:
        flash("You cannot follow your own profile.", "info")
        return redirect(url_for("profile", username=profile_user.username))
    if users_blocked_between(current_user.id, profile_user.id):
        flash("You cannot follow this student because a block is active.", "error")
        return redirect(url_for("dashboard"))

    existing = db.session.scalar(
        select(Follow).where(
            Follow.follower_id == current_user.id,
            Follow.followed_id == profile_user.id,
        )
    )
    try:
        if existing:
            db.session.delete(existing)
            db.session.commit()
            flash(f"You unfollowed @{profile_user.username}.", "success")
        else:
            db.session.add(Follow(
                follower_id=current_user.id,
                followed_id=profile_user.id,
            ))
            create_notification(profile_user.id, current_user.id, "follow", "profile", profile_user.id)
            db.session.commit()
            flash(f"You are now following @{profile_user.username}.", "success")
    except IntegrityError:
        db.session.rollback()
        flash("That follow action could not be completed. Please try again.", "error")
    except SQLAlchemyError:
        db.session.rollback()
        app.logger.exception("Follow action failed")
        flash("Could not update following right now. Please try again.", "error")

    return redirect(url_for("profile", username=profile_user.username))


# ==================================================
# COMMUNITIES
# ==================================================

COMMUNITY_CATEGORIES = {"General", "Academic", "Department", "Club", "Hostel", "Sports", "Technology"}


@app.route("/communities", methods=["GET", "POST"])
@login_required
def communities():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        category = request.form.get("category", "General").strip()
        errors = []
        if not 3 <= len(name) <= 80:
            errors.append("Community name must be between 3 and 80 characters.")
        if len(description) > 500:
            errors.append("Description must be 500 characters or fewer.")
        if category not in COMMUNITY_CATEGORIES:
            errors.append("Please choose a valid community category.")
        if name and db.session.scalar(select(Community.id).where(db.func.lower(Community.name) == name.lower())):
            errors.append("A community with that name already exists.")
        if errors:
            for error in errors:
                flash(error, "error")
        else:
            group = Community(name=name, description=description, category=category, creator_id=current_user.id)
            try:
                db.session.add(group)
                db.session.flush()
                db.session.add(CommunityMember(community_id=group.id, user_id=current_user.id))
                db.session.commit()
                flash(f"Community '{group.name}' created. You're now a member!", "success")
                return redirect(url_for("community_detail", community_id=group.id))
            except IntegrityError:
                db.session.rollback()
                flash("That community name is already in use. Try another name.", "error")
            except SQLAlchemyError:
                db.session.rollback()
                app.logger.exception("Community creation failed")
                flash("Could not create the community. Please try again.", "error")

    query = request.args.get("q", "").strip()[:80]
    category_filter = request.args.get("category", "").strip()
    statement = select(Community)
    if query:
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        statement = statement.where(or_(Community.name.ilike(f"%{escaped}%", escape="\\"), Community.description.ilike(f"%{escaped}%", escape="\\")))
    if category_filter in COMMUNITY_CATEGORIES:
        statement = statement.where(Community.category == category_filter)
    groups = db.session.scalars(statement.order_by(Community.created_at.desc()).limit(200)).all()
    joined_ids = set(db.session.scalars(select(CommunityMember.community_id).where(CommunityMember.user_id == current_user.id)).all())
    member_counts = {group.id: len(group.memberships) for group in groups}
    return render_template("communities.html", student=current_user, communities=groups,
                           joined_ids=joined_ids, member_counts=member_counts, query=query,
                           category_filter=category_filter, community_categories=sorted(COMMUNITY_CATEGORIES))


@app.route("/communities/<int:community_id>")
@login_required
def community_detail(community_id):
    group = db.session.get(Community, community_id)
    if group is None:
        abort(404)
    is_member = db.session.scalar(select(CommunityMember.id).where(
        CommunityMember.community_id == group.id, CommunityMember.user_id == current_user.id
    )) is not None
    members = db.session.scalars(select(CommunityMember).where(
        CommunityMember.community_id == group.id
    ).order_by(CommunityMember.joined_at.asc()).limit(100)).all()
    hidden_user_ids = blocked_user_ids_for(current_user.id)
    community_post_query = select(CommunityPost).where(CommunityPost.community_id == group.id)
    if hidden_user_ids:
        community_post_query = community_post_query.where(CommunityPost.author_id.not_in(hidden_user_ids))
    community_posts = db.session.scalars(
        community_post_query.order_by(CommunityPost.created_at.desc()).limit(100)
    ).all()
    # Only root comments are passed to the page; each root exposes its nested replies.
    return render_template("community_detail.html", student=current_user, community=group,
                           is_member=is_member, members=members, member_count=len(group.memberships),
                           community_posts=community_posts)


@app.route("/communities/<int:community_id>/posts", methods=["POST"])
@login_required
def create_community_post(community_id):
    group = db.session.get(Community, community_id)
    if group is None:
        abort(404)
    is_member = db.session.scalar(select(CommunityMember.id).where(
        CommunityMember.community_id == group.id, CommunityMember.user_id == current_user.id
    )) is not None
    if not is_member:
        abort(403)
    body = request.form.get("body", "").strip()
    if not body or len(body) > 3000:
        flash("A community post must contain between 1 and 3,000 characters.", "error")
        return redirect(url_for("community_detail", community_id=group.id))
    try:
        db.session.add(CommunityPost(community_id=group.id, author_id=current_user.id, body=body))
        db.session.commit()
        flash("Your community post was published.", "success")
    except SQLAlchemyError:
        db.session.rollback()
        app.logger.exception("Community post creation failed")
        flash("Could not publish your post. Please try again.", "error")
    return redirect(url_for("community_detail", community_id=group.id) + "#community-posts")


@app.route("/communities/<int:community_id>/posts/<int:post_id>/comments", methods=["POST"])
@login_required
def comment_on_community_post(community_id, post_id):
    group = db.session.get(Community, community_id)
    post = db.session.get(CommunityPost, post_id)
    if group is None or post is None or post.community_id != community_id:
        abort(404)
    is_member = db.session.scalar(select(CommunityMember.id).where(
        CommunityMember.community_id == group.id, CommunityMember.user_id == current_user.id
    )) is not None
    if not is_member:
        abort(403)
    if users_blocked_between(current_user.id, post.author_id):
        abort(403, description="You cannot interact with this post because a block is active.")
    body = request.form.get("body", "").strip()
    parent_raw = request.form.get("parent_id", "").strip()
    parent = None
    if parent_raw:
        try:
            parent_id = int(parent_raw)
        except ValueError:
            abort(400)
        parent = db.session.get(CommunityPostComment, parent_id)
        if parent is None or parent.post_id != post.id:
            abort(400, description="Replies must belong to the same community post.")
        if users_blocked_between(current_user.id, parent.author_id):
            abort(403, description="You cannot reply to this student because a block is active.")
    if not body or len(body) > 1200:
        flash("A comment must contain between 1 and 1,200 characters.", "error")
        return redirect(url_for("community_detail", community_id=group.id) + f"#post-{post.id}")
    try:
        comment = CommunityPostComment(post_id=post.id, author_id=current_user.id,
                                       parent_id=parent.id if parent else None, body=body)
        db.session.add(comment)
        recipient_id = parent.author_id if parent else post.author_id
        create_notification(recipient_id, current_user.id, "reply" if parent else "comment",
                            "community_post", post.id)
        db.session.commit()
        flash("Reply added." if parent else "Comment added.", "success")
    except SQLAlchemyError:
        db.session.rollback()
        app.logger.exception("Community comment failed")
        flash("Could not add your comment. Please try again.", "error")
    return redirect(url_for("community_detail", community_id=group.id) + f"#post-{post.id}")


@app.route("/communities/<int:community_id>/join", methods=["POST"])
@login_required
def join_community(community_id):
    group = db.session.get(Community, community_id)
    if group is None:
        abort(404)
    existing = db.session.scalar(select(CommunityMember.id).where(
        CommunityMember.community_id == group.id, CommunityMember.user_id == current_user.id
    ))
    if existing:
        flash("You're already a member of this community.", "info")
    else:
        try:
            db.session.add(CommunityMember(community_id=group.id, user_id=current_user.id))
            db.session.commit()
            flash(f"You joined {group.name}.", "success")
        except IntegrityError:
            db.session.rollback()
            flash("You're already a member of this community.", "info")
        except SQLAlchemyError:
            db.session.rollback()
            app.logger.exception("Joining community failed")
            flash("Could not join this community. Please try again.", "error")
    return redirect(url_for("community_detail", community_id=group.id))


@app.route("/communities/<int:community_id>/leave", methods=["POST"])
@login_required
def leave_community(community_id):
    group = db.session.get(Community, community_id)
    if group is None:
        abort(404)
    membership = db.session.scalar(select(CommunityMember).where(
        CommunityMember.community_id == group.id, CommunityMember.user_id == current_user.id
    ))
    if membership:
        db.session.delete(membership)
        db.session.commit()
        flash(f"You left {group.name}.", "success")
    else:
        flash("You're not a member of this community.", "info")
    return redirect(url_for("communities"))


# ==================================================
# NOTIFICATION CENTER
# ==================================================

@app.route("/notifications")
@login_required
def notifications_center():
    rows = db.session.scalars(
        select(Notification).where(Notification.recipient_id == current_user.id)
        .order_by(Notification.created_at.desc(), Notification.id.desc()).limit(200)
    ).all()
    unread_notification_count = db.session.scalar(select(db.func.count(Notification.id)).where(
        Notification.recipient_id == current_user.id, Notification.is_read.is_(False)
    )) or 0
    return render_template("notifications.html", student=current_user, notifications=rows,
                           unread_notification_count=unread_notification_count)


@app.route("/notifications/<int:notification_id>/open", methods=["POST"])
@login_required
def open_notification(notification_id):
    notification = db.session.get(Notification, notification_id)
    if notification is None or notification.recipient_id != current_user.id:
        abort(404)
    notification.is_read = True
    db.session.commit()
    if notification.kind == "message" and notification.conversation_id:
        membership = db.session.scalar(select(ConversationMember.id).where(
            ConversationMember.conversation_id == notification.conversation_id,
            ConversationMember.user_id == current_user.id
        ))
        if membership:
            return redirect(url_for("conversation_view", conversation_id=notification.conversation_id))
        return redirect(url_for("messages_inbox"))
    if notification.target_type == "community_post" and notification.target_id:
        post = db.session.get(CommunityPost, notification.target_id)
        if post:
            return redirect(url_for("community_detail", community_id=post.community_id) + f"#post-{post.id}")
    if notification.kind in {"like", "comment"} and notification.target_type == "post" and notification.target_id:
        return redirect(url_for("dashboard") + f"#post-{notification.target_id}")
    actor = db.session.get(User, notification.actor_id)
    if actor:
        return redirect(url_for("profile", username=actor.username))
    return redirect(url_for("notifications_center"))


@app.route("/notifications/read-all", methods=["POST"])
@login_required
def mark_all_notifications_read():
    db.session.execute(
        db.update(Notification).where(Notification.recipient_id == current_user.id,
                                      Notification.is_read.is_(False)).values(is_read=True)
    )
    db.session.commit()
    flash("All notifications marked as read.", "success")
    return redirect(url_for("notifications_center"))


# ==================================================
# PRIVATE MESSAGES AND GROUP CHATS
# ==================================================

def count_unread_messages(user_id):
    """Count incoming messages newer than each conversation's last-read time (one query)."""
    query = (
        select(db.func.count(Message.id))
        .join(ConversationMember, ConversationMember.conversation_id == Message.conversation_id)
        .where(
            ConversationMember.user_id == user_id,
            Message.sender_id != user_id,
            or_(
                ConversationMember.last_read_at.is_(None),
                Message.created_at > ConversationMember.last_read_at,
            ),
        )
    )
    return db.session.scalar(query) or 0


@app.context_processor
def inject_unread_message_count():
    """Make the unread count available to dashboard/navigation templates."""
    if not current_user.is_authenticated:
        return {"unread_message_count": 0, "unread_notification_count": 0}
    try:
        unread_notifications = db.session.scalar(select(db.func.count(Notification.id)).where(
            Notification.recipient_id == current_user.id, Notification.is_read.is_(False)
        )) or 0
        return {"unread_message_count": count_unread_messages(current_user.id),
                "unread_notification_count": unread_notifications}
    except Exception:
        # Do not break unrelated pages if a database migration is incomplete.
        app.logger.exception("Could not calculate unread message count")
        return {"unread_message_count": 0, "unread_notification_count": 0}

@app.route("/messages")
@login_required
def messages_inbox():
    memberships = db.session.scalars(
        select(ConversationMember).where(ConversationMember.user_id == current_user.id)
    ).all()
    conversations = [membership.conversation for membership in memberships]
    conversations.sort(key=lambda c: c.messages[-1].created_at if c.messages else c.created_at, reverse=True)
    unread_by_conversation = {}
    for membership in memberships:
        query = select(db.func.count(Message.id)).where(
            Message.conversation_id == membership.conversation_id,
            Message.sender_id != current_user.id,
        )
        if membership.last_read_at is not None:
            query = query.where(Message.created_at > membership.last_read_at)
        unread_by_conversation[membership.conversation_id] = db.session.scalar(query) or 0
    return render_template("messages.html", student=current_user, conversations=conversations,
                           memberships={m.conversation_id: m for m in memberships},
                           unread_by_conversation=unread_by_conversation, users=[])


@app.route("/messages", methods=["POST"])
@login_required
def create_group_conversation():
    title = request.form.get("title", "").strip()
    usernames_raw = request.form.get("usernames", "")
    usernames = list(dict.fromkeys(u.strip().lstrip("@").lower() for u in usernames_raw.split(",") if u.strip()))
    if not 3 <= len(title) <= 100:
        flash("Group chat name must be between 3 and 100 characters.", "error")
        return redirect(url_for("messages_inbox"))
    if not usernames:
        flash("Add at least one other student's username, separated by commas.", "error")
        return redirect(url_for("messages_inbox"))
    users = db.session.scalars(select(User).where(User.username.in_(usernames))).all()
    found = {u.username.lower(): u for u in users}
    missing = [name for name in usernames if name not in found]
    if missing:
        flash("These usernames were not found: " + ", ".join("@" + n for n in missing), "error")
        return redirect(url_for("messages_inbox"))
    if current_user.id in {u.id for u in users}:
        users = [u for u in users if u.id != current_user.id]
    if not users:
        flash("Choose at least one other student for the group.", "error")
        return redirect(url_for("messages_inbox"))
    try:
        conversation = Conversation(title=title, is_group=True, creator_id=current_user.id)
        db.session.add(conversation)
        db.session.flush()
        member_ids = {current_user.id, *(u.id for u in users)}
        for user_id in member_ids:
            db.session.add(ConversationMember(conversation_id=conversation.id, user_id=user_id))
        db.session.commit()
        return redirect(url_for("conversation_view", conversation_id=conversation.id))
    except SQLAlchemyError:
        db.session.rollback()
        app.logger.exception("Group chat creation failed")
        flash("Could not create the group chat. Please try again.", "error")
        return redirect(url_for("messages_inbox"))



@app.route("/messages/<int:conversation_id>/customize", methods=["POST"])
@login_required
def customize_group(conversation_id):
    """Update group details without changing its membership or message history."""
    conversation = db.session.get(Conversation, conversation_id)
    if conversation is None:
        abort(404)
    if not conversation.is_group:
        abort(400, description="Only group chats can be customized.")
    if _conversation_member(conversation_id) is None:
        abort(403)
    if conversation.creator_id != current_user.id and not current_user.is_admin:
        abort(403, description="Only the group creator or a site administrator can customize this group.")

    title = request.form.get("title", "").strip()
    description = request.form.get("group_description", "").strip()
    rules = request.form.get("group_rules", "").strip()
    avatar_file = request.files.get("group_avatar")
    if not 3 <= len(title) <= 100:
        flash("Group name must be between 3 and 100 characters.", "error")
        return redirect(url_for("conversation_view", conversation_id=conversation.id))
    if len(description) > 500 or len(rules) > 1000:
        flash("Description must be 500 characters or fewer and rules 1,000 characters or fewer.", "error")
        return redirect(url_for("conversation_view", conversation_id=conversation.id))

    avatar_url = conversation.group_avatar_url
    try:
        if avatar_file and avatar_file.filename:
            data = avatar_file.stream.read(3 * 1024 * 1024 + 1)
            if not data or len(data) > 3 * 1024 * 1024:
                raise ValueError("Group pictures must be 3 MB or smaller.")
            valid_image = (
                data.startswith(b"\\xff\\xd8\\xff")
                or data.startswith(b"\\x89PNG\\r\\n\\x1a\\n")
                or data[:6] in (b"GIF87a", b"GIF89a")
                or (data[:4] == b"RIFF" and data[8:12] == b"WEBP")
            )
            if not valid_image:
                raise ValueError("Choose a valid JPG, PNG, GIF, or WEBP group picture.")
            if not CLOUDINARY_ENABLED:
                raise ValueError("Group picture uploads are temporarily unavailable.")
            avatar_url = upload_media_to_cloudinary(
                io.BytesIO(data), resource_type="image", folder="funaab-one/groups"
            )
            if not avatar_url:
                raise ValueError("The group picture could not be uploaded. Please try again.")
        conversation.title = title
        conversation.group_description = description or None
        conversation.group_rules = rules or None
        conversation.group_avatar_url = avatar_url
        db.session.commit()
        flash("Group details updated.", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    except Exception:
        db.session.rollback()
        app.logger.exception("Customizing group chat failed")
        flash("Could not update group details. Please try again.", "error")
    return redirect(url_for("conversation_view", conversation_id=conversation.id))


@app.route("/messages/<int:conversation_id>/members/add", methods=["POST"])
@login_required
def add_group_members(conversation_id):
    """Add existing students to a group chat without changing its history."""
    conversation = db.session.get(Conversation, conversation_id)
    if conversation is None:
        abort(404)
    if not conversation.is_group:
        abort(400, description="Members can only be added to group chats.")
    if _conversation_member(conversation_id) is None:
        abort(403)
    # The creator or a site administrator can add people to an existing group.
    if conversation.creator_id != current_user.id and not current_user.is_admin:
        abort(403, description="Only the group creator or an administrator can add members.")

    raw_usernames = request.form.get("usernames", "")
    usernames = list(dict.fromkeys(
        item.strip().lstrip("@").lower()
        for item in raw_usernames.replace("\n", ",").split(",")
        if item.strip()
    ))
    if not usernames:
        flash("Enter at least one student's username.", "error")
        return redirect(url_for("conversation_view", conversation_id=conversation.id))
    if len(usernames) > 50:
        flash("You can add up to 50 students at a time.", "error")
        return redirect(url_for("conversation_view", conversation_id=conversation.id))

    users = db.session.scalars(select(User).where(User.username.in_(usernames))).all()
    found = {user.username.lower(): user for user in users}
    missing = [name for name in usernames if name not in found]
    if missing:
        flash("These usernames were not found: " + ", ".join("@" + name for name in missing), "error")
        return redirect(url_for("conversation_view", conversation_id=conversation.id))

    existing_ids = {member.user_id for member in conversation.members}
    new_users = [found[name] for name in usernames if found[name].id not in existing_ids]
    if not new_users:
        flash("Those students are already in this group.", "info")
        return redirect(url_for("conversation_view", conversation_id=conversation.id))
    if len(existing_ids) + len(new_users) > 200:
        flash("This group has reached its 200-member limit.", "error")
        return redirect(url_for("conversation_view", conversation_id=conversation.id))

    try:
        for user in new_users:
            db.session.add(ConversationMember(conversation_id=conversation.id, user_id=user.id))
            create_notification(user.id, current_user.id, "message", "conversation",
                                conversation_id=conversation.id)
        db.session.commit()
        added_names = ", ".join("@" + user.username for user in new_users)
        flash(f"Added {added_names} to {conversation.title or 'the group'}.", "success")
    except SQLAlchemyError:
        db.session.rollback()
        app.logger.exception("Adding members to group chat failed")
        flash("Could not add members right now. Please try again.", "error")
    return redirect(url_for("conversation_view", conversation_id=conversation.id))


def _conversation_member(conversation_id):
    return db.session.scalar(select(ConversationMember).where(
        ConversationMember.conversation_id == conversation_id,
        ConversationMember.user_id == current_user.id,
    ))


@app.route("/messages/start/<string:username>", methods=["POST"])
@login_required
def start_direct_message(username):
    other = db.session.scalar(select(User).where(User.username == username.lower()))
    if other is None:
        abort(404)
    if other.id == current_user.id:
        flash("You cannot message yourself.", "info")
        return redirect(url_for("profile", username=other.username))
    if users_blocked_between(current_user.id, other.id):
        flash("You cannot message this student because a block is active.", "error")
        return redirect(url_for("messages_inbox"))
    candidates = db.session.scalars(select(Conversation).where(Conversation.is_group.is_(False))).all()
    for convo in candidates:
        ids = {m.user_id for m in convo.members}
        if ids == {current_user.id, other.id}:
            return redirect(url_for("conversation_view", conversation_id=convo.id))
    try:
        convo = Conversation(is_group=False, creator_id=current_user.id)
        db.session.add(convo)
        db.session.flush()
        db.session.add_all([
            ConversationMember(conversation_id=convo.id, user_id=current_user.id),
            ConversationMember(conversation_id=convo.id, user_id=other.id),
        ])
        db.session.commit()
        return redirect(url_for("conversation_view", conversation_id=convo.id))
    except SQLAlchemyError:
        db.session.rollback()
        app.logger.exception("Starting direct message failed")
        flash("Could not start the conversation. Please try again.", "error")
        return redirect(url_for("messages_inbox"))


@app.route("/messages/<int:conversation_id>", methods=["GET", "POST"])
@login_required
def conversation_view(conversation_id):
    conversation = db.session.get(Conversation, conversation_id)
    if conversation is None:
        abort(404)
    membership = _conversation_member(conversation_id)
    if membership is None:
        abort(403)
    if not conversation.is_group:
        other_member = next((m.user for m in conversation.members if m.user_id != current_user.id), None)
        if other_member and users_blocked_between(current_user.id, other_member.id):
            abort(403, description="This private conversation is unavailable because a block is active.")
    if request.method == "POST":
        body = request.form.get("body", "").strip()
        image_file = request.files.get("image")
        audio_file = request.files.get("audio")
        image_url = None
        audio_url = None
        is_ajax_message = request.headers.get("X-Requested-With") == "XMLHttpRequest"
        if len(body) > 2000:
            if is_ajax_message:
                return {"ok": False, "error": "Messages must be 2,000 characters or fewer."}, 400
            flash("Messages must be 2,000 characters or fewer.", "error")
        elif not body and not ((image_file and image_file.filename) or (audio_file and audio_file.filename)):
            if is_ajax_message:
                return {"ok": False, "error": "Write a message or attach an image or voice note first."}, 400
            flash("Write a message or attach an image or voice note first.", "error")
        else:
            try:
                if image_file and image_file.filename:
                    # Keep uploads within Vercel's request limit and accept only real image signatures.
                    data = image_file.stream.read(3 * 1024 * 1024 + 1)
                    if not data or len(data) > 3 * 1024 * 1024:
                        raise ValueError("Chat images must be 3 MB or smaller.")
                    valid_image = (
                        data.startswith(b"\xff\xd8\xff")
                        or data.startswith(b"\x89PNG\r\n\x1a\n")
                        or data[:6] in (b"GIF87a", b"GIF89a")
                        or (data[:4] == b"RIFF" and data[8:12] == b"WEBP")
                    )
                    if not valid_image:
                        raise ValueError("Choose a valid JPG, PNG, GIF, or WEBP image.")
                    if not CLOUDINARY_ENABLED:
                        raise ValueError("Image messaging is temporarily unavailable. Please try again later.")
                    image_url = upload_media_to_cloudinary(io.BytesIO(data), resource_type="image", folder="funaab-one/messages")
                    if not image_url:
                        raise ValueError("The image could not be uploaded. Please try again.")
                if audio_file and audio_file.filename:
                    # Keep audio small enough to pass safely through the serverless request.
                    audio_data = audio_file.stream.read(2 * 1024 * 1024 + 1)
                    if not audio_data or len(audio_data) > 2 * 1024 * 1024:
                        raise ValueError("Voice notes must be 2 MB or smaller. Please record a shorter note.")
                    audio_type = audio_file.mimetype or ""
                    valid_audio = (
                        audio_data.startswith(b"RIFF") and audio_data[8:12] == b"WAVE"
                        or audio_data.startswith(b"OggS")
                        or audio_data.startswith(bytes.fromhex("1a45dfa3"))
                        or audio_data[4:8] == b"ftyp"
                    )
                    if not valid_audio or audio_type not in ("audio/webm", "audio/ogg", "audio/mp4", "audio/wav", "audio/x-wav", "video/webm", "video/mp4", "application/octet-stream"):
                        raise ValueError("The voice note format is not supported. Please record again using your browser's default format.")
                    if not CLOUDINARY_ENABLED:
                        raise ValueError("Voice notes are temporarily unavailable. Please try again later.")
                    audio_url = upload_media_to_cloudinary(io.BytesIO(audio_data), resource_type="video", folder="funaab-one/messages/audio")
                    if not audio_url:
                        raise ValueError("The voice note could not be uploaded. Please try again.")
                reply_to_raw = request.form.get("reply_to_message_id", "").strip()
                reply_to_message = None
                if reply_to_raw:
                    try:
                        reply_to_id = int(reply_to_raw)
                    except ValueError:
                        raise ValueError("That reply target is invalid. Please try again.")
                    reply_to_message = db.session.get(Message, reply_to_id)
                    if reply_to_message is None or reply_to_message.conversation_id != conversation.id:
                        raise ValueError("That message can no longer be replied to.")
                db.session.add(Message(conversation_id=conversation.id, sender_id=current_user.id, body=body,
                                       image_url=image_url, audio_url=audio_url, reply_to_message_id=reply_to_message.id if reply_to_message else None))
                for member in conversation.members:
                    if member.user_id != current_user.id:
                        create_notification(member.user_id, current_user.id, "message", "conversation",
                                            conversation_id=conversation.id)
                db.session.commit()
                if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                    return {"ok": True, "message": "Message sent."}
                return redirect(url_for("conversation_view", conversation_id=conversation.id))
            except ValueError as exc:
                db.session.rollback()
                if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                    return {"ok": False, "error": str(exc)}, 400
                flash(str(exc), "error")
            except Exception:
                db.session.rollback()
                app.logger.exception("Sending message or uploading chat image failed")
                if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                    return {"ok": False, "error": "Could not send your message. Please try again."}, 500
                flash("Could not send your message. Please try again.", "error")
    membership.last_read_at = utcnow()
    db.session.commit()
    members = [m.user for m in conversation.members]
    other_membership = next((m for m in conversation.members if m.user_id != current_user.id), None)
    if conversation.is_group:
        display_title = conversation.title or "Group chat"
    else:
        other = other_membership.user if other_membership else None
        display_title = other.full_name if other else "Conversation"
    seen_message_id = 0
    if other_membership and other_membership.last_read_at:
        seen_message_id = max((m.id for m in conversation.messages
                               if m.sender_id == current_user.id and m.created_at <= other_membership.last_read_at), default=0)
    reaction_summaries = {}
    for message in conversation.messages:
        reaction_summaries[message.id] = _message_reaction_summary(message.id, current_user.id)
    return render_template("conversation.html", student=current_user, conversation=conversation,
                           display_title=display_title, members=members,
                           other_membership=other_membership, seen_message_id=seen_message_id,
                           reaction_summaries=reaction_summaries)


@app.route("/messages/<int:conversation_id>/<int:message_id>/delete", methods=["POST"])
@login_required
def delete_message(conversation_id, message_id):
    conversation = db.session.get(Conversation, conversation_id)
    if conversation is None:
        abort(404)
    if _conversation_member(conversation_id) is None:
        abort(403)
    message = db.session.get(Message, message_id)
    if message is None or message.conversation_id != conversation_id:
        abort(404)
    if message.sender_id != current_user.id and not current_user.is_admin:
        abort(403)
    try:
        db.session.delete(message)
        db.session.commit()
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return {"ok": True, "message_id": message_id}
        flash("Message deleted.", "success")
    except SQLAlchemyError:
        db.session.rollback()
        app.logger.exception("Deleting message failed")
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return {"ok": False, "error": "Could not delete the message. Please try again."}, 500
        flash("Could not delete the message. Please try again.", "error")
    return redirect(url_for("conversation_view", conversation_id=conversation_id))


@app.route("/messages/<int:conversation_id>/<int:message_id>/react", methods=["POST"])
@login_required
def react_to_message(conversation_id, message_id):
    if _conversation_member(conversation_id) is None:
        abort(403)
    message = db.session.get(Message, message_id)
    if message is None or message.conversation_id != conversation_id:
        abort(404)
    allowed = {"❤️", "😂", "👍", "🔥", "😮"}
    emoji = request.form.get("emoji", "").strip()
    if emoji not in allowed:
        abort(400, description="Unsupported reaction.")
    existing = db.session.scalar(select(MessageReaction).where(
        MessageReaction.message_id == message_id, MessageReaction.user_id == current_user.id
    ))
    try:
        if existing and existing.emoji == emoji:
            db.session.delete(existing)
        elif existing:
            existing.emoji = emoji
        else:
            db.session.add(MessageReaction(message_id=message_id, user_id=current_user.id, emoji=emoji))
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        app.logger.exception("Toggling message reaction failed")
        return {"ok": False, "error": "Could not save reaction."}, 500
    return {"ok": True, "message_id": message_id, "reactions": _message_reaction_summary(message_id, current_user.id)}


def _message_reaction_summary(message_id, user_id):
    rows = db.session.execute(select(MessageReaction.emoji, db.func.count(MessageReaction.id))
                              .where(MessageReaction.message_id == message_id)
                              .group_by(MessageReaction.emoji)).all()
    # Return one count per emoji and whether the current user selected it.
    user_emojis = set(db.session.scalars(select(MessageReaction.emoji).where(
        MessageReaction.message_id == message_id, MessageReaction.user_id == user_id)).all())
    return [{"emoji": emoji, "count": int(count), "mine": emoji in user_emojis} for emoji, count in rows]


@app.route("/messages/<int:conversation_id>/poll")
@login_required
def poll_messages(conversation_id):
    conversation = db.session.get(Conversation, conversation_id)
    if conversation is None:
        abort(404)
    if _conversation_member(conversation_id) is None:
        abort(403)
    if not conversation.is_group:
        other_member = next((m.user for m in conversation.members if m.user_id != current_user.id), None)
        if other_member and users_blocked_between(current_user.id, other_member.id):
            abort(403)
    try:
        after_id = max(0, int(request.args.get("after", 0)))
    except (TypeError, ValueError):
        after_id = 0
    rows = db.session.scalars(select(Message).where(
        Message.conversation_id == conversation_id, Message.id > after_id
    ).order_by(Message.id.asc()).limit(100)).all()
    # Visiting an open chat counts incoming messages as read. Update the read marker
    # only when polling actually returns messages, avoiding needless writes every 3 seconds.
    if rows:
        membership = _conversation_member(conversation_id)
        if membership is not None:
            latest_read_time = max((m.created_at for m in rows), default=utcnow())
            if membership.last_read_at is None or latest_read_time > membership.last_read_at:
                membership.last_read_at = latest_read_time
                db.session.commit()
    other_membership = next((m for m in conversation.members if m.user_id != current_user.id), None)
    other_typing = bool(other_membership and other_membership.typing_until and other_membership.typing_until > utcnow())
    seen_message_id = 0
    if other_membership and other_membership.last_read_at:
        seen_message_id = db.session.scalar(select(db.func.max(Message.id)).where(
            Message.conversation_id == conversation_id,
            Message.sender_id == current_user.id,
            Message.created_at <= other_membership.last_read_at,
        )) or 0
    recent_messages = db.session.scalars(select(Message).where(
        Message.conversation_id == conversation_id
    ).order_by(Message.id.desc()).limit(100)).all()
    return {"messages": [{
        "id": m.id, "sender_id": m.sender_id, "sender_name": m.sender.full_name,
        "username": m.sender.username, "avatar": m.sender.profile_picture,
        "initial": (m.sender.full_name or m.sender.username)[:1].upper(),
        "body": m.body, "image_url": m.image_url, "audio_url": m.audio_url, "created_at": m.created_at.strftime("%b %d, %H:%M"),
        "reply_to": ({"id": m.reply_to.id, "sender_name": m.reply_to.sender.full_name,
                      "body": m.reply_to.body or ("📷 Image" if m.reply_to.image_url else "Message")}
                     if m.reply_to and m.reply_to.conversation_id == conversation_id else None),
        "reactions": _message_reaction_summary(m.id, current_user.id),
        "mine": m.sender_id == current_user.id,
    } for m in rows],
        "reaction_updates": [{"message_id": m.id, "reactions": _message_reaction_summary(m.id, current_user.id)}
                            for m in reversed(recent_messages)],
        "other_typing": other_typing, "seen_message_id": seen_message_id}


@app.route("/messages/<int:conversation_id>/typing", methods=["POST"])
@login_required
def update_typing_status(conversation_id):
    membership = _conversation_member(conversation_id)
    if membership is None:
        abort(403)
    raw = request.form.get("typing", "0")
    membership.typing_until = utcnow() + timedelta(seconds=5) if raw == "1" else None
    db.session.commit()
    return {"ok": True}


# ==================================================
# MEDIA UPLOAD PLACEHOLDER
# ==================================================

@app.route("/media/sign", methods=["POST"])
@login_required
def media_sign():
    return {
        "error": "Media uploads are not configured yet."
    }, 501


# ==================================================
# CREATE A POST
# ==================================================

@app.route("/api/posts/upload-signature", methods=["POST"])
@login_required
def post_upload_signature():
    """Authorize a browser-to-Cloudinary upload without routing media through Vercel."""
    if not CLOUDINARY_ENABLED:
        return {"error": "Media uploads are not configured."}, 503
    payload = request.get_json(silent=True) or {}
    resource_type = payload.get("resource_type")
    if resource_type not in {"image", "video"}:
        return {"error": "Choose a photo or video."}, 400

    timestamp = int(datetime.now(timezone.utc).timestamp())
    public_id = f"post_{current_user.id}_{uuid.uuid4().hex}"
    params = {
        "timestamp": timestamp,
        "folder": "funaab-one/posts",
        "public_id": public_id,
        "overwrite": "false",
        "unique_filename": "false",
    }
    signature = cloudinary.utils.api_sign_request(params, CLOUDINARY_API_SECRET)
    return {
        "cloud_name": CLOUDINARY_CLOUD_NAME,
        "api_key": CLOUDINARY_API_KEY,
        "timestamp": timestamp,
        "folder": params["folder"],
        "public_id": public_id,
        "signature": signature,
        "resource_type": resource_type,
    }


@app.route("/post", methods=["POST"])
@login_required
def create_post():
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.accept_mimetypes.best == "application/json"
    def post_error(message, status=400):
        if is_ajax:
            return {"ok": False, "error": message}, status
        flash(message, "error")
        return redirect(url_for("dashboard"))

    body = request.form.get("body", "").replace("\\r\\n", "\\n").strip()
    media = request.files.get("media")
    has_media = bool(media and media.filename)
    uploaded_url = request.form.get("uploaded_media_url", "").strip()
    uploaded_public_id = request.form.get("uploaded_media_public_id", "").strip()
    uploaded_type = request.form.get("uploaded_media_type", "").strip()
    has_direct_media = bool(uploaded_url and uploaded_public_id and uploaded_type)
    post_type = request.form.get("post_type", "standard")

    if post_type == "poll":
        option_values = request.form.getlist("poll_option")
        options = []
        seen = set()
        for value in option_values:
            value = value.strip()[:120]
            if value and value.casefold() not in seen:
                options.append(value)
                seen.add(value.casefold())
        if not body:
            return post_error("Enter a question for your poll.")
        if len(body) > MAX_POST_LENGTH:
            return post_error(f"Poll questions can be at most {MAX_POST_LENGTH} characters.")
        if not 2 <= len(options) <= 6:
            return post_error("A poll needs 2–6 different answer options.")
        closes_at = None
        closes_value = request.form.get("poll_closes_at", "").strip()
        if closes_value:
            try:
                closes_at = datetime.fromisoformat(closes_value)
                if closes_at <= datetime.now():
                    raise ValueError
            except ValueError:
                return post_error("Choose a valid poll closing time in the future.")
        try:
            post = Post(body=body, user_id=current_user.id)
            poll = Poll(closes_at=closes_at, allow_vote_change=True)
            poll.options = [PollOption(text=option, position=index) for index, option in enumerate(options)]
            post.poll = poll
            db.session.add(post)
            db.session.commit()
            if is_ajax:
                return {"ok": True, "post": {"id": post.id, "body": post.body, "type": "poll", "created_at": post.created_at.strftime("%b %d, %H:%M"), "options": [{"id": option.id, "text": option.text} for option in post.poll.options]}}
            flash("Poll published!", "success")
        except Exception:
            db.session.rollback()
            app.logger.exception("Poll creation failed")
            if is_ajax:
                return {"ok": False, "error": "Could not publish the poll. Please try again."}, 500
            flash("Could not publish the poll. Please try again.", "error")
        return redirect(url_for("dashboard"))

    if len(body) > MAX_POST_LENGTH:
        return post_error(f"Posts can be at most {MAX_POST_LENGTH} characters.")
    if not body and not has_media and not has_direct_media:
        return post_error("Write something or attach a photo or video.")
    if has_media and has_direct_media:
        return post_error("Please select only one photo or video.")

    final_path = None
    try:
        media_url = None
        media_type = None
        if has_direct_media:
            # Verify the uploaded asset with Cloudinary's authenticated API rather than
            # trusting a URL supplied by the browser.
            if not CLOUDINARY_ENABLED:
                raise ValueError("Media uploads are not configured.")
            if uploaded_type not in {"image", "video"}:
                raise ValueError("Unsupported media type.")
            # Cloudinary returns the public_id with its folder prefix
            # (funaab-one/posts/...) when the upload uses the folder parameter.
            # Validate both the expected folder and the current user's unique ID.
            expected_prefix = f"funaab-one/posts/post_{current_user.id}_"
            if not uploaded_public_id.startswith(expected_prefix):
                raise ValueError("This uploaded media is not valid for your account.")
            parsed_url = urlparse(uploaded_url)
            if (parsed_url.scheme != "https" or not parsed_url.hostname
                    or not (parsed_url.hostname == "cloudinary.com"
                            or parsed_url.hostname.endswith(".cloudinary.com"))):
                raise ValueError("The media URL is not a valid Cloudinary URL.")
            try:
                asset = cloudinary.api.resource(
                    uploaded_public_id,
                    resource_type=uploaded_type,
                    type="upload",
                )
            except Exception as exc:
                app.logger.warning("Could not verify uploaded post media: %s", exc)
                raise ValueError("Cloudinary could not verify the uploaded media. Please try again.")
            verified_url = asset.get("secure_url")
            verified_public_id = asset.get("public_id", "")
            if not verified_url or verified_public_id != uploaded_public_id:
                raise ValueError("The uploaded media could not be verified.")
            if not verified_public_id.startswith(expected_prefix):
                raise ValueError("This uploaded media is not valid for your account.")
            media_url = verified_url
            media_type = uploaded_type
        elif has_media:
            # Backward-compatible local upload path for small media on local development.
            extension = Path(media.filename).suffix.lower()
            if extension not in IMAGE_EXTENSIONS | VIDEO_EXTENSIONS:
                return post_error("Unsupported file type. Choose a photo or video.")
            with tempfile.TemporaryDirectory(prefix="funaab_upload_") as workdir:
                temp = Path(workdir) / ("incoming" + extension)
                media.save(temp)
                size = temp.stat().st_size
                if not size:
                    raise ValueError("The selected file is empty.")
                if extension in IMAGE_EXTENSIONS:
                    if size > MAX_IMAGE_BYTES:
                        raise ValueError("Photos must be 15 MB or smaller.")
                    actual_ext = detect_image_type(temp)
                    if actual_ext is None:
                        raise ValueError("This is not a supported image file.")
                    final_path = UPLOAD_DIR / (uuid.uuid4().hex + actual_ext)
                    shutil.copyfile(temp, final_path)
                    media_type = "image"
                else:
                    if size > MAX_VIDEO_BYTES:
                        raise ValueError("Videos must be 1 GB or smaller.")
                    validate_video(temp)
                    final_path = UPLOAD_DIR / (uuid.uuid4().hex + ".mp4")
                    convert_video(temp, final_path, size > COMPRESSION_THRESHOLD)
                    create_video_thumbnail(final_path)
                    media_type = "video"
            if CLOUDINARY_ENABLED:
                media_url = upload_media_to_cloudinary(
                    str(final_path),
                    resource_type="video" if media_type == "video" else "image",
                    folder="funaab-one/posts",
                )
                final_path.unlink(missing_ok=True)
                final_path.with_suffix(".jpg").unlink(missing_ok=True)
                final_path = None
            else:
                media_url = url_for("static", filename="uploads/" + final_path.name)

        post = Post(body=body, user_id=current_user.id, media_url=media_url, media_type=media_type)
        db.session.add(post)
        db.session.commit()
        if is_ajax:
            return {"ok": True, "post": {"id": post.id, "body": post.body, "type": "standard", "media_url": post.media_url, "media_type": post.media_type, "created_at": post.created_at.strftime("%b %d, %H:%M")}} 
        flash("Post published!", "success")
    except (ValueError, RuntimeError) as exc:
        db.session.rollback()
        if final_path:
            final_path.unlink(missing_ok=True)
            final_path.with_suffix(".jpg").unlink(missing_ok=True)
        if is_ajax:
            return {"ok": False, "error": str(exc)}, 400
        flash(str(exc), "error")
    except Exception:
        db.session.rollback()
        if final_path:
            final_path.unlink(missing_ok=True)
            final_path.with_suffix(".jpg").unlink(missing_ok=True)
        app.logger.exception("Post creation failed")
        if is_ajax:
            return {"ok": False, "error": "Could not publish the post. Please try again."}, 500
        flash("Could not publish the post. Please try again.", "error")
    return redirect(url_for("dashboard"))


@app.route("/poll/<int:poll_id>/vote", methods=["POST"])
@login_required
def vote_poll(poll_id):
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.accept_mimetypes.best == "application/json"

    def vote_response(ok, message, status=200):
        if is_ajax:
            return jsonify({"ok": ok, "message": message}), status
        flash(message, "success" if ok else "error")
        return redirect(url_for("dashboard"))

    poll = db.get_or_404(Poll, poll_id)
    option_id = request.form.get("option_id", type=int)
    option = next((item for item in poll.options if item.id == option_id), None)
    if option is None:
        return vote_response(False, "Choose a valid poll option.", 400)
    if poll.closes_at and poll.closes_at <= datetime.now():
        return vote_response(False, "This poll is closed.", 400)

    existing = db.session.scalar(select(PollVote).where(
        PollVote.poll_id == poll.id, PollVote.user_id == current_user.id
    ))
    if existing:
        if not poll.allow_vote_change:
            return vote_response(False, "You've already voted in this poll.", 409)
        existing.option_id = option.id
        existing.created_at = utcnow()
    else:
        db.session.add(PollVote(poll_id=poll.id, option_id=option.id, user_id=current_user.id))

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return vote_response(False, "Your vote could not be saved. Please try again.", 409)
    except SQLAlchemyError:
        db.session.rollback()
        app.logger.exception("Poll vote failed")
        return vote_response(False, "Your vote could not be saved. Please try again.", 500)

    # Return authoritative counts from the database so every option and percentage
    # can be refreshed in place without trusting client-side vote totals.
    options_data = []
    total_votes = 0
    for poll_option in poll.options:
        count = db.session.scalar(select(db.func.count(PollVote.id)).where(PollVote.option_id == poll_option.id)) or 0
        total_votes += count
        options_data.append({"id": poll_option.id, "text": poll_option.text, "votes": count})
    for item in options_data:
        item["percent"] = round(item["votes"] * 100 / total_votes) if total_votes else 0
    my_vote = db.session.scalar(select(PollVote.option_id).where(
        PollVote.poll_id == poll.id, PollVote.user_id == current_user.id
    ))
    closed = bool(poll.closes_at and poll.closes_at <= datetime.now())
    if is_ajax:
        return jsonify({
            "ok": True,
            "message": "Your vote has been saved!",
            "poll_id": poll.id,
            "options": options_data,
            "total_votes": total_votes,
            "my_vote": my_vote,
            "closed": closed,
            "allow_vote_change": poll.allow_vote_change,
            "closes_at": poll.closes_at.strftime("%b %d, %H:%M") if poll.closes_at else None,
        })
    flash("Your vote has been saved!", "success")
    return redirect(url_for("dashboard"))


# ==================================================
# EDIT A POST
# ==================================================

@app.route("/post/<int:post_id>/edit", methods=["GET", "POST"])
@login_required
def edit_post(post_id):
    post = db.session.get(Post, post_id)
    if post is None:
        abort(404)
    # Students can edit only their own posts. Admin status does not transfer ownership.
    if post.user_id != current_user.id:
        abort(403)

    if request.method == "POST":
        body = request.form.get("body", "").replace("\r\n", "\n").strip()
        if len(body) > MAX_POST_LENGTH:
            flash(f"Posts can be at most {MAX_POST_LENGTH} characters.", "error")
            return render_template("edit_post.html", post=post, max_post_length=MAX_POST_LENGTH)
        if not body and not post.media_url:
            flash("A post must contain text or attached media.", "error")
            return render_template("edit_post.html", post=post, max_post_length=MAX_POST_LENGTH)
        try:
            post.body = body
            db.session.commit()
            flash("Post updated.", "success")
        except SQLAlchemyError:
            db.session.rollback()
            app.logger.exception("Post edit failed")
            flash("Could not update this post. Please try again.", "error")
            return render_template("edit_post.html", post=post, max_post_length=MAX_POST_LENGTH)

        target = request.form.get("next")
        if is_safe_redirect(target):
            return redirect(target)
        return redirect(url_for("dashboard"))

    return render_template("edit_post.html", post=post, max_post_length=MAX_POST_LENGTH)


# ==================================================
# PRIVATE BOOKMARKS
# ==================================================

@app.route("/post/<int:post_id>/bookmark", methods=["POST"])
@login_required
def toggle_bookmark(post_id):
    post = db.session.get(Post, post_id)
    if post is None:
        abort(404)

    existing = db.session.scalar(
        select(Bookmark).where(Bookmark.post_id == post_id, Bookmark.user_id == current_user.id)
    )
    try:
        if existing:
            db.session.delete(existing)
            flash("Post removed from your bookmarks.", "success")
        else:
            db.session.add(Bookmark(post_id=post_id, user_id=current_user.id))
            flash("Post saved to your private bookmarks.", "success")
        db.session.commit()
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return {"ok": True, "saved": existing is None, "post_id": post_id}
    except IntegrityError:
        db.session.rollback()
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return {"ok": False, "error": "Bookmark state changed. Please try again."}, 409
    except SQLAlchemyError:
        db.session.rollback()
        app.logger.exception("Bookmark action failed")
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return {"ok": False, "error": "Could not update your bookmark. Please try again."}, 500
        flash("Could not update your bookmark. Please try again.", "error")

    target = request.form.get("next")
    if is_safe_redirect(target):
        return redirect(target)
    return redirect(url_for("dashboard"))


@app.route("/bookmarks")
@login_required
def bookmarks_page():
    saved_posts = db.session.scalars(
        select(Post)
        .join(Bookmark, Bookmark.post_id == Post.id)
        .where(Bookmark.user_id == current_user.id)
        .order_by(Bookmark.created_at.desc())
    ).all()
    bookmarked_post_ids = {post.id for post in saved_posts}
    liked_post_ids = set(db.session.scalars(
        select(PostLike.post_id).where(PostLike.user_id == current_user.id)
    ).all())
    reposted_post_ids = set(db.session.scalars(
        select(Repost.post_id).where(Repost.user_id == current_user.id)
    ).all())
    return render_template(
        "bookmarks.html", student=current_user, posts=saved_posts,
        bookmarked_post_ids=bookmarked_post_ids, liked_post_ids=liked_post_ids,
        reposted_post_ids=reposted_post_ids, media_enabled=True,
    )


# ==================================================
# DELETE A POST
# ==================================================

@app.route("/post/<int:post_id>/delete", methods=["POST"])
@login_required
def delete_post(post_id):
    post = db.session.get(Post, post_id)

    if post is None:
        abort(404)

    if post.user_id != current_user.id and not current_user.is_admin:
        abort(403)

    local_media = post.media_url if post.media_url and post.media_url.startswith("/static/uploads/") else None
    db.session.delete(post)
    db.session.commit()
    if local_media:
        candidate = UPLOAD_DIR / Path(local_media).name
        candidate.unlink(missing_ok=True)
        # Video posters share the media basename but use .jpg; harmless for photos.
        candidate.with_suffix(".jpg").unlink(missing_ok=True)
    flash("Post deleted.", "success")

    target = request.form.get("next")

    if is_safe_redirect(target):
        return redirect(target)

    return redirect(url_for("dashboard"))


# ==================================================
# REPORT A POST
# ==================================================

@app.route("/post/<int:post_id>/report", methods=["POST"])
@login_required
def report_post(post_id):
    post = db.session.get(Post, post_id)

    if post is None:
        abort(404)

    if post.user_id == current_user.id:
        flash("You can't report your own post.", "error")
        return safety_redirect()

    reason = request.form.get("reason", "")

    if reason not in REPORT_REASONS:
        flash("Please choose a valid reason for your report.", "error")
        return safety_redirect()

    try:
        report = Report(
            post_id=post_id,
            reporter_id=current_user.id,
            reason=reason,
        )

        db.session.add(report)
        db.session.commit()
        flash("Thanks. An admin will review this post.", "success")

    except IntegrityError:
        db.session.rollback()
        flash("You already reported this post.", "info")

    except SQLAlchemyError:
        db.session.rollback()
        app.logger.exception("Reporting post failed")
        flash("Could not submit your report. Please try again.", "error")

    return safety_redirect()


# ==================================================
# ADMIN: REVIEW REPORTED POSTS
# ==================================================

@app.route("/admin/reports")
@admin_required
def admin_reports():
    posts = db.session.scalars(
        select(Post)
        .where(Post.reports.any())
        .order_by(Post.created_at.desc())
    ).all()

    return render_template(
        "admin_reports.html",
        student=current_user,
        posts=posts,
    )


@app.route("/admin/posts/<int:post_id>/dismiss", methods=["POST"])
@admin_required
def dismiss_reports(post_id):
    db.session.execute(
        delete(Report).where(Report.post_id == post_id)
    )
    db.session.add(AdminAction(
        admin_id=current_user.id,
        target_user_id=None,
        action="dismiss_post_reports",
        details=f"Dismissed legacy reports for post #{post_id}",
    ))
    db.session.commit()

    flash("Reports dismissed. The post stays up.", "success")
    return redirect(url_for("admin_reports"))


# ==================================================
# LIKE / UNLIKE A POST
# ==================================================

@app.route("/post/<int:post_id>/like", methods=["POST"])
@login_required
def toggle_like(post_id):
    post = db.session.get(Post, post_id)

    if post is None:
        abort(404)
    if users_blocked_between(current_user.id, post.user_id):
        abort(403, description="You cannot interact with this post because a block is active.")

    existing = db.session.scalar(
        select(PostLike).where(
            PostLike.post_id == post_id,
            PostLike.user_id == current_user.id,
        )
    )

    try:
        if existing:
            db.session.delete(existing)
            liked = False
        else:
            db.session.add(
                PostLike(
                    post_id=post_id,
                    user_id=current_user.id,
                )
            )
            liked = True
            if post.user_id != current_user.id:
                create_notification(post.user_id, current_user.id, "like", "post", post.id)

        db.session.commit()

        # AJAX clients receive the updated state and can update the feed in place.
        # Normal form submissions retain the existing redirect behaviour.
        if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json:
            like_count = db.session.scalar(
                select(db.func.count()).select_from(PostLike).where(
                    PostLike.post_id == post_id
                )
            ) or 0
            return {"ok": True, "liked": liked, "like_count": like_count, "post_id": post_id}

    except IntegrityError:
        db.session.rollback()
        if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json:
            return {"ok": False, "error": "Like state changed. Please try again."}, 409

    except SQLAlchemyError:
        db.session.rollback()
        app.logger.exception("Like action failed")
        if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json:
            return {"ok": False, "error": "Could not update your like. Please try again."}, 500
        flash("Could not update your like. Please try again.", "error")

    return dashboard_redirect()


# ==================================================
# ADD A COMMENT
# ==================================================

@app.route("/post/<int:post_id>/comment", methods=["POST"])
@login_required
def add_comment(post_id):
    post = db.session.get(Post, post_id)

    if post is None:
        abort(404)
    if users_blocked_between(current_user.id, post.user_id):
        abort(403, description="You cannot comment on this post because a block is active.")

    body = request.form.get("body", "").strip()

    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    if not body:
        if is_ajax:
            return {"ok": False, "error": "Write a comment first."}, 400
        flash("Write a comment first.", "error")
        return dashboard_redirect()

    if len(body) > MAX_COMMENT_LENGTH:
        message = f"Comments must be {MAX_COMMENT_LENGTH} characters or fewer."
        if is_ajax:
            return {"ok": False, "error": message}, 400
        flash(message, "error")
        return dashboard_redirect()

    try:
        comment = Comment(
            body=body,
            post_id=post.id,
            user_id=current_user.id,
        )

        db.session.add(comment)
        create_notification(post.user_id, current_user.id, "comment", "post", post.id)
        db.session.commit()
        if is_ajax:
            return {
                "ok": True, "post_id": post.id, "comment_id": comment.id,
                "body": comment.body, "author_name": current_user.full_name or current_user.username,
                "username": current_user.username, "profile_picture": current_user.profile_picture,
                "created_at": comment.created_at.strftime("just now"),
                "comment_count": db.session.scalar(select(db.func.count()).select_from(Comment).where(Comment.post_id == post.id)) or 0,
            }
        flash("Comment added.", "success")

    except SQLAlchemyError:
        db.session.rollback()
        app.logger.exception("Adding comment failed")
        if is_ajax:
            return {"ok": False, "error": "Could not add your comment. Please try again."}, 500
        flash("Could not add your comment. Please try again.", "error")

    return dashboard_redirect()


@app.route("/comment/<int:comment_id>/delete", methods=["POST"])
@login_required
def delete_comment(comment_id):
    comment = db.session.get(Comment, comment_id)
    if comment is None:
        abort(404)
    if comment.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    try:
        db.session.delete(comment)
        db.session.commit()
        flash("Comment deleted.", "success")
    except SQLAlchemyError:
        db.session.rollback()
        app.logger.exception("Deleting comment failed")
        flash("Could not delete the comment. Please try again.", "error")
    target = request.form.get("next")
    if is_safe_redirect(target):
        return redirect(target)
    return dashboard_redirect()


@app.route("/communities/<int:community_id>/comments/<int:comment_id>/delete", methods=["POST"])
@login_required
def delete_community_comment(community_id, comment_id):
    group = db.session.get(Community, community_id)
    comment = db.session.get(CommunityPostComment, comment_id)
    if group is None or comment is None or comment.post.community_id != group.id:
        abort(404)
    if comment.author_id != current_user.id and not current_user.is_admin:
        abort(403)
    is_member = db.session.scalar(select(CommunityMember.id).where(
        CommunityMember.community_id == group.id, CommunityMember.user_id == current_user.id
    )) is not None
    if not is_member and not current_user.is_admin:
        abort(403)
    post_id = comment.post_id
    try:
        db.session.delete(comment)
        db.session.commit()
        flash("Comment deleted.", "success")
    except SQLAlchemyError:
        db.session.rollback()
        app.logger.exception("Deleting community comment failed")
        flash("Could not delete the comment. Please try again.", "error")
    return redirect(url_for("community_detail", community_id=group.id) + f"#post-{post_id}")


# ==================================================
# SAFETY & PRIVACY: BLOCKING AND REPORTING
# ==================================================

@app.route("/u/<string:username>/block", methods=["POST"])
@login_required
def block_user(username):
    target = db.session.scalar(select(User).where(User.username == username.lower()))
    if target is None:
        abort(404)
    if target.id == current_user.id:
        flash("You cannot block your own account.", "error")
        return safety_redirect()
    existing = db.session.scalar(select(UserBlock).where(
        UserBlock.blocker_id == current_user.id, UserBlock.blocked_id == target.id
    ))
    if existing:
        flash(f"@{target.username} is already blocked.", "info")
        return safety_redirect()
    try:
        db.session.add(UserBlock(blocker_id=current_user.id, blocked_id=target.id))
        # Remove existing follows both ways so the block takes effect immediately.
        db.session.execute(delete(Follow).where(or_(
            (Follow.follower_id == current_user.id) & (Follow.followed_id == target.id),
            (Follow.follower_id == target.id) & (Follow.followed_id == current_user.id),
        )))
        db.session.commit()
        flash(f"You blocked @{target.username}. Their profile and posts are hidden from you, and interactions between you are restricted.", "success")
    except IntegrityError:
        db.session.rollback()
        flash("That block could not be saved. Please try again.", "error")
    except SQLAlchemyError:
        db.session.rollback()
        app.logger.exception("Blocking user failed")
        flash("Could not block this student. Please try again.", "error")
    return safety_redirect()


@app.route("/u/<string:username>/unblock", methods=["POST"])
@login_required
def unblock_user(username):
    target = db.session.scalar(select(User).where(User.username == username.lower()))
    if target is None:
        abort(404)
    row = db.session.scalar(select(UserBlock).where(
        UserBlock.blocker_id == current_user.id, UserBlock.blocked_id == target.id
    ))
    if row is None:
        flash("That student is not on your block list.", "info")
    else:
        db.session.delete(row)
        db.session.commit()
        flash(f"You unblocked @{target.username}.", "success")
    return safety_redirect("blocked_users")


@app.route("/settings/blocked-users")
@login_required
def blocked_users():
    rows = db.session.scalars(
        select(UserBlock).where(UserBlock.blocker_id == current_user.id)
        .order_by(UserBlock.created_at.desc())
    ).all()
    return render_template("blocked_users.html", student=current_user, blocked_rows=rows)


@app.route("/u/<string:username>/report", methods=["POST"])
@login_required
def report_user(username):
    target = db.session.scalar(select(User).where(User.username == username.lower()))
    if target is None:
        abort(404)
    return _submit_safety_report("user", target.id, target.id)


@app.route("/comment/<int:comment_id>/report", methods=["POST"])
@login_required
def report_comment(comment_id):
    comment = db.session.get(Comment, comment_id)
    if comment is None:
        abort(404)
    return _submit_safety_report("comment", comment.id, comment.user_id)


@app.route("/communities/<int:community_id>/posts/<int:post_id>/report", methods=["POST"])
@login_required
def report_community_post(community_id, post_id):
    post = db.session.get(CommunityPost, post_id)
    if post is None or post.community_id != community_id:
        abort(404)
    return _submit_safety_report("community_post", post.id, post.author_id)


@app.route("/communities/<int:community_id>/comments/<int:comment_id>/report", methods=["POST"])
@login_required
def report_community_comment(community_id, comment_id):
    comment = db.session.get(CommunityPostComment, comment_id)
    if comment is None or comment.post.community_id != community_id:
        abort(404)
    return _submit_safety_report("community_comment", comment.id, comment.author_id)


@app.route("/admin/safety-reports")
@admin_required
def admin_safety_reports():
    reports = db.session.scalars(
        select(SafetyReport).where(SafetyReport.status == "pending")
        .order_by(SafetyReport.created_at.asc()).limit(300)
    ).all()
    target_items = []
    for report in reports:
        target = None
        author = None
        if report.target_type == "user":
            target = db.session.get(User, report.target_id)
            author = target
        elif report.target_type == "comment":
            target = db.session.get(Comment, report.target_id)
            author = target.author if target else None
        elif report.target_type == "community_post":
            target = db.session.get(CommunityPost, report.target_id)
            author = target.author if target else None
        elif report.target_type == "community_comment":
            target = db.session.get(CommunityPostComment, report.target_id)
            author = target.author if target else None
        target_items.append({"report": report, "target": target, "author": author})
    return render_template("admin_safety_reports.html", student=current_user, report_items=target_items)


@app.route("/admin/safety-reports/<int:report_id>/<string:action>", methods=["POST"])
@admin_required
def resolve_safety_report(report_id, action):
    report = db.session.get(SafetyReport, report_id)
    if report is None:
        abort(404)
    if action not in {"dismiss", "resolve"}:
        abort(400)
    report.status = "dismissed" if action == "dismiss" else "resolved"
    report.reviewed_at = utcnow()
    report.reviewed_by_id = current_user.id
    db.session.add(AdminAction(
        admin_id=current_user.id,
        target_user_id=report.reporter_id,
        action=f"safety_report_{action}",
        details=f"{action.title()} safety report #{report.id} ({report.target_type} #{report.target_id})",
    ))
    db.session.commit()
    flash("Report dismissed." if action == "dismiss" else "Report marked resolved.", "success")
    return redirect(url_for("admin_safety_reports"))


# ==================================================
# ADMIN DASHBOARD AND ACCOUNT MODERATION
# ==================================================

@app.route("/admin")
@admin_required
def admin_dashboard():
    """High-level moderation dashboard; requires the existing admin allowlist."""
    from sqlalchemy import func

    query = request.args.get("q", "").strip()[:80]
    users_query = select(User).order_by(User.id.desc())
    if query:
        pattern = f"%{query}%"
        users_query = users_query.where(or_(User.username.ilike(pattern), User.full_name.ilike(pattern), User.email.ilike(pattern)))
    users = db.session.scalars(users_query.limit(100)).all()

    pending_safety = db.session.scalar(select(func.count(SafetyReport.id)).where(SafetyReport.status == "pending")) or 0
    legacy_reported_posts = db.session.scalar(select(func.count(Post.id)).where(Post.reports.any())) or 0
    stats = {
        "users": db.session.scalar(select(func.count(User.id))) or 0,
        "active_users": db.session.scalar(select(func.count(User.id)).where(User.is_suspended.is_(False))) or 0,
        "suspended_users": db.session.scalar(select(func.count(User.id)).where(User.is_suspended.is_(True))) or 0,
        "posts": db.session.scalar(select(func.count(Post.id))) or 0,
        "communities": db.session.scalar(select(func.count(Community.id))) or 0,
        "messages": db.session.scalar(select(func.count(Message.id))) or 0,
        "pending_reports": pending_safety + legacy_reported_posts,
    }
    actions = db.session.scalars(select(AdminAction).order_by(AdminAction.created_at.desc()).limit(12)).all()
    recent_posts = db.session.scalars(select(Post).order_by(Post.created_at.desc()).limit(8)).all()
    recent_community_posts = db.session.scalars(select(CommunityPost).order_by(CommunityPost.created_at.desc()).limit(8)).all()
    return render_template(
        "admin_dashboard.html", student=current_user, users=users, query=query,
        stats=stats, actions=actions, recent_posts=recent_posts,
        recent_community_posts=recent_community_posts,
    )


@app.route("/admin/users/<int:user_id>/suspend", methods=["POST"])
@admin_required
def admin_suspend_user(user_id):
    target = db.session.get(User, user_id)
    if target is None:
        abort(404)
    if target.id == current_user.id:
        flash("You cannot suspend your own administrator account.", "error")
        return redirect(url_for("admin_dashboard"))
    if target.is_admin:
        flash("Administrator accounts cannot be suspended from this page.", "error")
        return redirect(url_for("admin_dashboard"))
    if target.is_suspended:
        flash("This account is already suspended.", "info")
        return redirect(url_for("admin_dashboard"))
    target.is_suspended = True
    db.session.add(AdminAction(admin_id=current_user.id, target_user_id=target.id, action="suspend_user", details=f"Suspended @{target.username}"))
    db.session.commit()
    flash(f"@{target.username} has been suspended.", "success")
    return redirect(url_for("admin_dashboard", q=request.form.get("q", "")))


@app.route("/admin/users/<int:user_id>/restore", methods=["POST"])
@admin_required
def admin_restore_user(user_id):
    target = db.session.get(User, user_id)
    if target is None:
        abort(404)
    if target.is_admin:
        flash("Administrator accounts are not managed through student suspension controls.", "error")
        return redirect(url_for("admin_dashboard"))
    if not target.is_suspended:
        flash("This account is already active.", "info")
        return redirect(url_for("admin_dashboard"))
    target.is_suspended = False
    db.session.add(AdminAction(admin_id=current_user.id, target_user_id=target.id, action="restore_user", details=f"Restored @{target.username}"))
    db.session.commit()
    flash(f"@{target.username} has been restored.", "success")
    return redirect(url_for("admin_dashboard", q=request.form.get("q", "")))


@app.route("/admin/posts/<int:post_id>/remove", methods=["POST"])
@admin_required
def admin_remove_post(post_id):
    post = db.session.get(Post, post_id)
    if post is None:
        abort(404)
    details = f"Removed post #{post.id} by @{post.author.username}"
    db.session.delete(post)
    db.session.add(AdminAction(admin_id=current_user.id, target_user_id=post.user_id, action="remove_post", details=details))
    db.session.commit()
    flash("Post removed.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/comments/<int:comment_id>/remove", methods=["POST"])
@admin_required
def admin_remove_comment(comment_id):
    comment = db.session.get(Comment, comment_id)
    if comment is None:
        abort(404)
    details = f"Removed feed comment #{comment.id} by @{comment.author.username}"
    target_user_id = comment.user_id
    db.session.delete(comment)
    db.session.add(AdminAction(admin_id=current_user.id, target_user_id=target_user_id, action="remove_comment", details=details))
    db.session.commit()
    flash("Comment removed.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/community-posts/<int:post_id>/remove", methods=["POST"])
@admin_required
def admin_remove_community_post(post_id):
    post = db.session.get(CommunityPost, post_id)
    if post is None:
        abort(404)
    target_user_id = post.author_id
    details = f"Removed community post #{post.id} in community #{post.community_id}"
    db.session.delete(post)
    db.session.add(AdminAction(admin_id=current_user.id, target_user_id=target_user_id, action="remove_community_post", details=details))
    db.session.commit()
    flash("Community post removed.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/community-comments/<int:comment_id>/remove", methods=["POST"])
@admin_required
def admin_remove_community_comment(comment_id):
    comment = db.session.get(CommunityPostComment, comment_id)
    if comment is None:
        abort(404)
    target_user_id = comment.author_id
    details = f"Removed community comment #{comment.id}"
    db.session.delete(comment)
    db.session.add(AdminAction(admin_id=current_user.id, target_user_id=target_user_id, action="remove_community_comment", details=details))
    db.session.commit()
    flash("Community comment removed.", "success")
    return redirect(url_for("admin_dashboard"))


# ==================================================
# ABOUT FUNAAB ONE
# ==================================================

@app.route("/about")
def about():
    return render_template(
        "about.html",
        current_year=datetime.now().year,
    )

# ==================================================
# REPOST A POST
# ==================================================


@app.route("/post/<int:post_id>/repost", methods=["POST"])
@login_required
def repost_post(post_id):
    post = db.session.get(Post, post_id)

    if post is None:
        abort(404)
    if users_blocked_between(current_user.id, post.user_id):
        abort(403, description="You cannot repost this content because a block is active.")

    existing = db.session.scalar(
        select(Repost).where(
            Repost.post_id == post_id,
            Repost.user_id == current_user.id,
        )
    )

    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    if existing:
        if is_ajax:
            return {"ok": True, "reposted": True, "already_reposted": True, "post_id": post_id}
        flash("You have already reposted this post.", "info")
        return dashboard_redirect()

    try:
        db.session.add(
            Repost(
                post_id=post.id,
                user_id=current_user.id,
            )
        )

        db.session.commit()
        if is_ajax:
            repost_count = db.session.scalar(select(db.func.count()).select_from(Repost).where(Repost.post_id == post_id)) or 0
            return {"ok": True, "reposted": True, "already_reposted": False, "post_id": post_id, "repost_count": repost_count}
        flash("Post reposted.", "success")

    except IntegrityError:
        db.session.rollback()
        if is_ajax:
            return {"ok": True, "reposted": True, "already_reposted": True, "post_id": post_id}
        flash("You have already reposted this post.", "info")

    except SQLAlchemyError:
        db.session.rollback()
        app.logger.exception("Reposting failed")
        if is_ajax:
            return {"ok": False, "error": "Could not repost this post. Please try again."}, 500
        flash("Could not repost this post. Please try again.", "error")

    return dashboard_redirect()


# ==================================================
# RUN APP
# ==================================================

def migrate_optional_profile_columns():
    """Add optional profile columns safely to an existing SQLite or PostgreSQL schema."""
    from sqlalchemy import inspect, text
    inspector = inspect(db.engine)
    if "user" not in inspector.get_table_names():
        return
    existing_columns = {column["name"] for column in inspector.get_columns("user")}
    boolean_default = "FALSE" if db.engine.dialect.name == "postgresql" else "0"
    with db.engine.begin() as connection:
        if "profile_picture" not in existing_columns:
            connection.execute(text("ALTER TABLE user ADD COLUMN profile_picture VARCHAR(500)"))
        if "bio" not in existing_columns:
            connection.execute(text("ALTER TABLE user ADD COLUMN bio VARCHAR(300) NOT NULL DEFAULT ''"))
        if "is_suspended" not in existing_columns:
            connection.execute(text(f"ALTER TABLE user ADD COLUMN is_suspended BOOLEAN NOT NULL DEFAULT {boolean_default}"))


# Vercel imports this module instead of running it as __main__.
# Create tables on the first serverless initialization so a fresh Supabase
# database is ready for registration/login. This does not import SQLite data.
if os.environ.get("VERCEL"):
    with app.app_context():
        db.create_all()
        migrate_optional_profile_columns()


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        migrate_optional_profile_columns()

    app.run(
        host=os.environ.get("FLASK_HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "5000")),
        debug=os.environ.get("FLASK_DEBUG", "0") == "1",
    )
