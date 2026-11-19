import email
import email.policy
import re

import pytest
from sqlalchemy import select, text

from app.models import User, UserSession
from tests.conftest import PASSWORD

pytestmark = pytest.mark.integration
API = "/api/v1"


def _mail(path):
    msg = email.message_from_bytes(path.read_bytes(), policy=email.policy.default)
    return msg, msg.get_content()


def test_first_user_becomes_platform_admin_and_second_does_not(make_user):
    a = make_user("first@archive.org")
    b = make_user("second@archive.org")
    assert a.get(f"{API}/me").json()["is_platform_admin"] is True
    assert b.get(f"{API}/me").json()["is_platform_admin"] is False


def test_register_rejects_duplicate_email_case_insensitively(client, make_user):
    make_user("dup@archive.org")
    r = client.post(f"{API}/auth/register", json={"email": "DUP@Archive.org", "full_name": "X", "password": PASSWORD})
    assert r.status_code == 409 and r.json()["error"]["code"] == "email_taken"


@pytest.mark.parametrize("pw,code", [("short", "password_too_short"), ("aaaaaaaaaaaa", "password_too_simple")])
def test_password_policy(client, pw, code):
    r = client.post(f"{API}/auth/register", json={"email": "p@archive.org", "full_name": "X", "password": pw})
    assert r.status_code == 422 and r.json()["error"]["code"] == code


def test_password_stored_as_argon2id(make_user, db):
    make_user("hash@archive.org")
    h = db.scalar(select(User.password_hash))
    assert h.startswith("$argon2id$") and PASSWORD not in h


def test_login_wrong_password_and_unknown_user_are_indistinguishable(client, make_user):
    make_user("known@archive.org")
    r1 = client.post(f"{API}/auth/login", json={"email": "known@archive.org", "password": "wrong-password-1"})
    r2 = client.post(f"{API}/auth/login", json={"email": "unknown@archive.org", "password": "wrong-password-1"})
    assert r1.status_code == r2.status_code == 401
    assert r1.json()["error"]["code"] == r2.json()["error"]["code"] == "invalid_credentials"


def test_login_lockout_after_repeated_failures(client, make_user):
    make_user("lock@archive.org")
    for _ in range(5):
        client.post(f"{API}/auth/login", json={"email": "lock@archive.org", "password": "wrong-password-1"})
    r = client.post(f"{API}/auth/login", json={"email": "lock@archive.org", "password": PASSWORD})
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0


def test_protected_endpoint_requires_token(client):
    r = client.get(f"{API}/me")
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"
    r = client.get(f"{API}/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert r.status_code == 401


def test_refresh_rotates_token_and_detects_reuse(make_user, db):
    a = make_user("rot@archive.org")
    old_cookie = a.client.cookies.get("scriptorium_refresh")
    r = a.client.post(f"{API}/auth/refresh")
    assert r.status_code == 200
    new_cookie = a.client.cookies.get("scriptorium_refresh")
    assert new_cookie and new_cookie != old_cookie
    a.token = r.json()["access_token"]
    assert a.get(f"{API}/me").status_code == 200

    # Replay the rotated (stolen) token: the whole session family must be revoked.
    a.client.cookies.set("scriptorium_refresh", old_cookie, path="/api/v1/auth")
    assert a.client.post(f"{API}/auth/refresh").status_code == 401
    assert a.get(f"{API}/me").status_code == 401  # access token died with the family
    assert db.scalar(select(UserSession).where(UserSession.revoked_at.is_(None))) is None


def test_logout_revokes_session_immediately(make_user):
    a = make_user("out@archive.org")
    assert a.get(f"{API}/me").status_code == 200
    assert a.client.post(f"{API}/auth/logout").status_code == 204
    assert a.get(f"{API}/me").status_code == 401
    assert a.client.post(f"{API}/auth/refresh").status_code == 401


def test_session_listing_and_revocation(make_user):
    a = make_user("multi@archive.org")
    first_token = a.token
    a.client.cookies.clear()
    a.login()  # second device
    sessions = a.get(f"{API}/me/sessions").json()
    assert len(sessions) == 2 and sum(s["current"] for s in sessions) == 1
    other = next(s for s in sessions if not s["current"])
    assert a.delete(f"{API}/me/sessions/{other['family_id']}").status_code == 204
    assert a.client.get(f"{API}/me", headers={"Authorization": f"Bearer {first_token}"}).status_code == 401
    assert a.get(f"{API}/me").status_code == 200


def test_password_reset_full_flow(client, make_user, outbox):
    a = make_user("reset@archive.org")
    r = client.post(f"{API}/auth/password-reset/request", json={"email": "reset@archive.org"})
    assert r.status_code == 202
    # Unknown address: same response, no e-mail.
    assert client.post(f"{API}/auth/password-reset/request", json={"email": "nobody@archive.org"}).status_code == 202
    mails = list(outbox.glob("*.eml"))
    assert len(mails) == 1
    msg, body = _mail(mails[0])
    assert msg["To"] == "reset@archive.org"
    token = re.search(r"token=([\w-]+)", body).group(1)

    new_pw = "A-brand-new-passphrase-42"
    assert client.post(f"{API}/auth/password-reset/confirm", json={"token": token, "new_password": new_pw}).status_code == 204
    # Single use.
    r = client.post(f"{API}/auth/password-reset/confirm", json={"token": token, "new_password": new_pw})
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_reset_token"
    # Old sessions revoked, old password rejected, new password accepted.
    assert a.get(f"{API}/me").status_code == 401
    assert client.post(f"{API}/auth/login", json={"email": a.email, "password": PASSWORD}).status_code == 401
    assert client.post(f"{API}/auth/login", json={"email": a.email, "password": new_pw}).status_code == 200


def test_password_reset_token_expiry(client, make_user, outbox, db):
    make_user("exp@archive.org")
    client.post(f"{API}/auth/password-reset/request", json={"email": "exp@archive.org"})
    token = re.search(r"token=([\w-]+)", _mail(next(outbox.glob("*.eml")))[1]).group(1)
    db.execute(text("UPDATE password_reset_tokens SET expires_at = now() - interval '1 minute'"))
    db.commit()
    r = client.post(f"{API}/auth/password-reset/confirm", json={"token": token, "new_password": "Another-valid-pass-1"})
    assert r.status_code == 400


def test_password_change_keeps_current_session_revokes_others(make_user):
    a = make_user("chg@archive.org")
    old = a.token
    a.client.cookies.clear()
    a.login()
    r = a.post(f"{API}/auth/password-change", json={"current_password": PASSWORD, "new_password": "Changed-Passphrase-77"})
    assert r.status_code == 204
    assert a.get(f"{API}/me").status_code == 200
    assert a.client.get(f"{API}/me", headers={"Authorization": f"Bearer {old}"}).status_code == 401


def test_deactivated_user_cannot_login(make_user):
    admin = make_user("admin@archive.org")
    b = make_user("b@archive.org")
    assert admin.patch(f"{API}/admin/users/{b.user['id']}", json={"is_active": False}).status_code == 200
    assert b.get(f"{API}/me").status_code == 401
    r = b.client.post(f"{API}/auth/login", json={"email": b.email, "password": PASSWORD})
    assert r.status_code == 401


def test_admin_cannot_lock_themselves_out(make_user):
    admin = make_user("admin@archive.org")
    r = admin.patch(f"{API}/admin/users/{admin.user['id']}", json={"is_platform_admin": False})
    assert r.status_code == 400


def test_non_admin_cannot_use_admin_endpoints(make_user):
    make_user("admin@archive.org")
    b = make_user("b@archive.org")
    assert b.get(f"{API}/admin/users").status_code == 403


def test_profile_update_validates_orcid(make_user):
    a = make_user("orcid@archive.org")
    assert a.patch(f"{API}/me", json={"orcid": "1234"}).status_code == 422
    r = a.patch(f"{API}/me", json={"orcid": "0000-0002-1825-0097", "preferred_locale": "pt"})
    assert r.status_code == 200 and r.json()["orcid"] == "0000-0002-1825-0097" and r.json()["preferred_locale"] == "pt"
