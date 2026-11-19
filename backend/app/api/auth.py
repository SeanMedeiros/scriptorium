"""Authentication: registration, login, refresh rotation, logout, password reset."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request, Response
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.api.deps import DB, CurrentUser
from app.config import get_settings
from app.core.errors import ApiError
from app.core.security import (
    create_access_token,
    hash_password,
    hash_token,
    needs_rehash,
    new_opaque_token,
    validate_password_strength,
    verify_password,
)
from app.models import PasswordResetToken, User, UserSession
from app.schemas import (
    LoginIn,
    PasswordChangeIn,
    PasswordResetConfirmIn,
    PasswordResetRequestIn,
    RegisterIn,
    TokenOut,
    UserOut,
)
from app.services import audit, mail, throttle
from app.services.audit import client_ip

router = APIRouter(prefix="/auth", tags=["authentication"])
REFRESH_COOKIE = "scriptorium_refresh"
COOKIE_PATH = "/api/v1/auth"
# Dummy hash so that unknown e-mails cost the same time as wrong passwords.
_DUMMY_HASH = hash_password("timing-equaliser-not-a-password")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _set_refresh_cookie(response: Response, token: str) -> None:
    s = get_settings()
    response.set_cookie(
        REFRESH_COOKIE, token, max_age=s.refresh_token_ttl_seconds, httponly=True,
        secure=s.cookie_secure, samesite="lax", path=COOKIE_PATH,
    )


def _issue_session(db: Session, user: User, request: Request, family_id: uuid.UUID | None = None) -> tuple[UserSession, str]:
    s = get_settings()
    token = new_opaque_token()
    sess = UserSession(
        user_id=user.id,
        family_id=family_id or uuid.uuid4(),
        token_hash=hash_token(token),
        expires_at=_now() + timedelta(seconds=s.refresh_token_ttl_seconds),
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent", "")[:500],
    )
    db.add(sess)
    db.flush()
    return sess, token


def _password_policy(password: str) -> None:
    if err := validate_password_strength(password):
        raise ApiError(422, err, "Password does not meet the policy (minimum 10 characters, not trivially simple)")


@router.post("/register", response_model=UserOut, status_code=201)
def register(body: RegisterIn, request: Request, db: DB):
    s = get_settings()
    first_user = db.scalar(select(func.count(User.id))) == 0
    if not s.allow_self_registration and not first_user:
        raise ApiError(403, "registration_closed", "Self-registration is disabled on this installation")
    _password_policy(body.password)
    if db.scalar(select(User.id).where(func.lower(User.email) == body.email.lower())):
        raise ApiError(409, "email_taken", "An account with this e-mail already exists")
    user = User(
        email=body.email.lower(),
        full_name=body.full_name.strip(),
        password_hash=hash_password(body.password),
        preferred_locale=body.preferred_locale,
        # The first account of a fresh installation becomes the platform administrator.
        is_platform_admin=first_user,
    )
    db.add(user)
    db.flush()
    audit.record(db, "user.register", actor_id=user.id, request=request, entity_type="user", entity_id=user.id,
                 details={"bootstrap_admin": first_user})
    db.commit()
    return user


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, request: Request, response: Response, db: DB):
    identity = body.email.lower()
    if remaining := throttle.is_locked(identity):
        raise ApiError(429, "too_many_attempts", "Too many failed attempts; try again later",
                       details={"retry_after": remaining}, headers={"Retry-After": str(remaining)})
    user = db.scalar(select(User).where(func.lower(User.email) == identity))
    ok = verify_password(body.password, user.password_hash if user else _DUMMY_HASH)
    if not user or not ok or not user.is_active:
        throttle.register_failure(identity)
        audit.record(db, "auth.login_failed", actor_id=user.id if user else None, request=request,
                     details={"email": identity, "reason": "inactive" if user and ok else "bad_credentials"})
        db.commit()
        raise ApiError(401, "invalid_credentials", "Invalid e-mail or password")
    throttle.reset(identity)
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    user.last_login_at = _now()
    sess, refresh = _issue_session(db, user, request)
    audit.record(db, "auth.login", actor_id=user.id, request=request, entity_type="session", entity_id=sess.family_id)
    db.commit()
    access, ttl = create_access_token(user.id, sess.family_id)
    _set_refresh_cookie(response, refresh)
    return TokenOut(access_token=access, expires_in=ttl)


@router.post("/refresh", response_model=TokenOut)
def refresh(request: Request, response: Response, db: DB):
    token = request.cookies.get(REFRESH_COOKIE)
    if not token:
        raise ApiError(401, "no_session", "No refresh session")
    sess = db.scalar(select(UserSession).where(UserSession.token_hash == hash_token(token)).with_for_update())
    if sess is None:
        raise ApiError(401, "invalid_session", "Unknown refresh token")
    if sess.revoked_at is not None:
        # A rotated token was replayed: assume theft and kill the whole family.
        if sess.replaced_by_id is not None:
            db.execute(update(UserSession).where(UserSession.family_id == sess.family_id, UserSession.revoked_at.is_(None))
                       .values(revoked_at=_now()))
            audit.record(db, "auth.refresh_reuse_detected", actor_id=sess.user_id, request=request,
                         entity_type="session", entity_id=sess.family_id)
            db.commit()
        raise ApiError(401, "invalid_session", "Session revoked")
    if sess.expires_at <= _now():
        raise ApiError(401, "session_expired", "Session expired")
    user = db.get(User, sess.user_id)
    if user is None or not user.is_active:
        raise ApiError(401, "invalid_session", "Account inactive")
    new_sess, new_token = _issue_session(db, user, request, family_id=sess.family_id)
    sess.revoked_at = _now()
    sess.replaced_by_id = new_sess.id
    new_sess.created_at = sess.created_at  # keep the original sign-in time of the family
    new_sess.last_used_at = _now()
    db.commit()
    access, ttl = create_access_token(user.id, new_sess.family_id)
    _set_refresh_cookie(response, new_token)
    return TokenOut(access_token=access, expires_in=ttl)


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, db: DB):
    token = request.cookies.get(REFRESH_COOKIE)
    if token:
        sess = db.scalar(select(UserSession).where(UserSession.token_hash == hash_token(token)))
        if sess:
            db.execute(update(UserSession).where(UserSession.family_id == sess.family_id, UserSession.revoked_at.is_(None))
                       .values(revoked_at=_now()))
            audit.record(db, "auth.logout", actor_id=sess.user_id, request=request, entity_type="session", entity_id=sess.family_id)
            db.commit()
    response.delete_cookie(REFRESH_COOKIE, path=COOKIE_PATH)
    response.status_code = 204
    return response


@router.post("/password-reset/request", status_code=202)
def password_reset_request(body: PasswordResetRequestIn, request: Request, db: DB):
    s = get_settings()
    user = db.scalar(select(User).where(func.lower(User.email) == body.email.lower()))
    if user and user.is_active:
        token = new_opaque_token()
        db.add(PasswordResetToken(user_id=user.id, token_hash=hash_token(token),
                                  expires_at=_now() + timedelta(seconds=s.password_reset_ttl_seconds)))
        audit.record(db, "auth.password_reset_requested", actor_id=user.id, request=request, entity_type="user", entity_id=user.id)
        db.commit()
        link = f"{s.public_base_url.rstrip('/')}/reset-password?token={token}"
        mail.send_mail(
            user.email,
            "SCRIPTORIUM — password reset",
            f"Hello {user.full_name},\n\nA password reset was requested for your SCRIPTORIUM account.\n"
            f"Open the following link within {s.password_reset_ttl_seconds // 60} minutes:\n\n{link}\n\n"
            "If you did not request this, you can ignore this message.\n",
        )
    # Identical response whether or not the account exists (no enumeration).
    return {"status": "accepted"}


@router.post("/password-reset/confirm", status_code=204)
def password_reset_confirm(body: PasswordResetConfirmIn, request: Request, db: DB):
    _password_policy(body.new_password)
    rec = db.scalar(select(PasswordResetToken).where(PasswordResetToken.token_hash == hash_token(body.token)).with_for_update())
    if rec is None or rec.used_at is not None or rec.expires_at <= _now():
        raise ApiError(400, "invalid_reset_token", "The reset link is invalid or has expired")
    user = db.get(User, rec.user_id)
    rec.used_at = _now()
    user.password_hash = hash_password(body.new_password)
    user.password_changed_at = _now()
    # Revoke every session of the user.
    db.execute(update(UserSession).where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None)).values(revoked_at=_now()))
    audit.record(db, "auth.password_reset", actor_id=user.id, request=request, entity_type="user", entity_id=user.id)
    db.commit()
    throttle.reset(user.email)
    return Response(status_code=204)


@router.post("/password-change", status_code=204)
def password_change(body: PasswordChangeIn, request: Request, user: CurrentUser, db: DB):
    if not verify_password(body.current_password, user.password_hash):
        raise ApiError(400, "wrong_password", "Current password is incorrect")
    _password_policy(body.new_password)
    user.password_hash = hash_password(body.new_password)
    user.password_changed_at = _now()
    # Keep the current session, revoke the others.
    db.execute(update(UserSession).where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None),
                                         UserSession.family_id != request.state.session_family).values(revoked_at=_now()))
    audit.record(db, "auth.password_change", actor_id=user.id, request=request, entity_type="user", entity_id=user.id)
    db.commit()
    return Response(status_code=204)
