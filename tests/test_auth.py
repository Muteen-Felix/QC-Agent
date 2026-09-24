"""Xác thực: mật khẩu, domain email, đăng nhập/khoá, phiên, token API theo project, CLI quản trị."""
from datetime import timedelta

import pytest
from sqlalchemy import text

from qc_agent import settings
from qc_agent.auth import passwords, service
from qc_agent.auth.cli import main as admin_main
from qc_agent.jobs import repository as repo
from qc_agent.jobs.db import session_scope
from tests.dbfix import db_url, engine, make_db, requires_pg  # noqa: F401  (fixtures)

PW = "correct horse battery"


@pytest.fixture(autouse=True)
def _domains(monkeypatch):
    monkeypatch.setenv("QC_ALLOWED_EMAIL_DOMAINS", "corp.test, Other.TEST")
    monkeypatch.setenv("QC_LOGIN_MAX_FAILURES", "3")
    monkeypatch.setenv("QC_LOGIN_LOCKOUT_MINUTES", "15")


# ---- không cần DB ----

def test_password_policy_and_hash_format():
    for bad in ("", "short", "x" * 129, " " * 12, None, 12345678901):
        with pytest.raises(passwords.WeakPassword):
            passwords.check_policy(bad)
    h = passwords.hash_password(PW)
    assert h.startswith("$argon2id$") and PW not in h
    assert passwords.verify(h, PW) is True and passwords.verify(h, PW + "x") is False
    assert passwords.verify(None, PW) is False and passwords.verify("không-phải-hash", PW) is False
    assert passwords.verify(h, "x" * 500) is False  # quá dài: từ chối mà không băm


@pytest.mark.parametrize("raw,expected", [("  Alice@Corp.Test ", "alice@corp.test"), ("a.b+tag@corp.test", "a.b+tag@corp.test")])
def test_normalize_email_ok(raw, expected):
    assert service.normalize_email(raw) == expected


@pytest.mark.parametrize("bad", ["", "khong-co-a-cong", "a@b", "a@@corp.test", "a b@corp.test", "ａ@corp.test", "é@corp.test", None, 5, "a@" + "b" * 400 + ".com"])
def test_normalize_email_rejects(bad):
    with pytest.raises(service.InvalidEmail):
        service.normalize_email(bad)


def test_domain_check_is_exact_case_insensitive_and_fail_closed(monkeypatch):
    service.check_domain("a@corp.test")
    service.check_domain("a@other.test")  # cấu hình viết hoa vẫn khớp
    for evil in ("a@evil.test", "a@sub.corp.test", "a@corp.test.evil.com", "a@notcorp.test"):
        with pytest.raises(service.DomainNotAllowed):
            service.check_domain(evil)
    monkeypatch.setenv("QC_ALLOWED_EMAIL_DOMAINS", "")
    with pytest.raises(service.DomainNotAllowed, match="chưa cấu hình"):
        service.check_domain("a@corp.test")  # để trống => không ai được thêm


def test_settings_defaults_are_safe(monkeypatch):
    monkeypatch.delenv("QC_ALLOWED_EMAIL_DOMAINS", raising=False)
    monkeypatch.delenv("QC_LOGIN_MAX_FAILURES", raising=False)
    cfg = settings.get()
    assert cfg.allowed_email_domains == "" and cfg.login_max_failures == 5 and cfg.session_ttl_hours == 168


# ---- người dùng + đăng nhập (PostgreSQL) ----

@requires_pg
def test_create_user_normalizes_hashes_and_rejects_duplicates_and_domains(engine):
    with session_scope(engine) as s:
        user, pw = service.create_user(s, "  Alice@Corp.Test ", display_name="Alice")
        assert user.email == "alice@corp.test" and user.password_hash.startswith("$argon2id$") and len(pw) >= 20
    with pytest.raises(service.UserExists):
        with session_scope(engine) as s:
            service.create_user(s, "ALICE@corp.test", PW)  # khác hoa/thường vẫn là một người
    with pytest.raises(service.DomainNotAllowed):
        with session_scope(engine) as s:
            service.create_user(s, "bob@evil.test", PW)
    with pytest.raises(passwords.WeakPassword):
        with session_scope(engine) as s:
            service.create_user(s, "carol@corp.test", "short")
    with session_scope(engine) as s:
        assert s.execute(text("SELECT count(*) FROM users")).scalar_one() == 1


@requires_pg
def test_duplicate_user_does_not_roll_back_the_callers_transaction(engine):
    with session_scope(engine) as s:
        repo.sync_project(s, "keep-me")
        service.create_user(s, "a@corp.test", PW)
        with pytest.raises(service.UserExists):
            service.create_user(s, "a@corp.test", PW)  # savepoint: chỉ huỷ phần insert lỗi
    with session_scope(engine) as s:
        assert s.execute(text("SELECT count(*) FROM projects WHERE slug='keep-me'")).scalar_one() == 1


@requires_pg
def test_authenticate_success_and_every_failure_returns_none(engine):
    with session_scope(engine) as s:
        service.create_user(s, "alice@corp.test", PW)
        service.create_user(s, "off@corp.test", PW)
        service.set_active(s, "off@corp.test", False)
    with session_scope(engine) as s:
        user = service.authenticate(s, " ALICE@corp.test ", PW)
        assert user is not None and user.last_login_at is not None and user.failed_attempts == 0
        assert service.authenticate(s, "alice@corp.test", "sai mat khau!!") is None
        assert service.authenticate(s, "khong-ton-tai@corp.test", PW) is None
        assert service.authenticate(s, "off@corp.test", PW) is None  # vô hiệu hoá
        assert service.authenticate(s, "email-hong", PW) is None
        assert service.authenticate(s, "alice@corp.test", "") is None


@requires_pg
def test_lockout_after_consecutive_failures_blocks_even_the_right_password(engine):
    with session_scope(engine) as s:
        service.create_user(s, "alice@corp.test", PW)
    for _ in range(3):
        with session_scope(engine) as s:
            assert service.authenticate(s, "alice@corp.test", "sai mat khau!!") is None
    with session_scope(engine) as s:
        assert service.authenticate(s, "alice@corp.test", PW) is None  # đang khoá: mật khẩu đúng cũng bị từ chối
        row = s.execute(text("SELECT locked_until > now(), failed_attempts FROM users")).one()
        assert row[0] is True and row[1] == 0
    with engine.begin() as conn:  # hết hạn khoá
        conn.execute(text("UPDATE users SET locked_until = now() - interval '1 second'"))
    with session_scope(engine) as s:
        assert service.authenticate(s, "alice@corp.test", PW) is not None
        assert s.execute(text("SELECT locked_until IS NULL AND failed_attempts = 0 FROM users")).scalar_one()


@requires_pg
def test_successful_login_resets_the_failure_counter(engine):
    with session_scope(engine) as s:
        service.create_user(s, "alice@corp.test", PW)
    for _ in range(2):
        with session_scope(engine) as s:
            service.authenticate(s, "alice@corp.test", "sai mat khau!!")
    with session_scope(engine) as s:
        assert service.authenticate(s, "alice@corp.test", PW) is not None
    for _ in range(2):  # đếm lại từ 0: 2 lần sai nữa vẫn chưa khoá
        with session_scope(engine) as s:
            service.authenticate(s, "alice@corp.test", "sai mat khau!!")
    with session_scope(engine) as s:
        assert service.authenticate(s, "alice@corp.test", PW) is not None


@requires_pg
def test_password_provider_matches_authenticate(engine):
    with session_scope(engine) as s:
        service.create_user(s, "alice@corp.test", PW)
    with session_scope(engine) as s:
        provider = service.PasswordProvider()
        assert provider.name == "password" and provider.authenticate(s, email="alice@corp.test", password=PW) is not None


# ---- phiên ----

@requires_pg
def test_session_lifecycle_and_token_is_stored_hashed(engine):
    with session_scope(engine) as s:
        user, _ = service.create_user(s, "alice@corp.test", PW)
        token = service.create_session(s, user)
        assert service.resolve_session(s, token).email == "alice@corp.test"
        stored = s.execute(text("SELECT id FROM sessions")).scalar_one()
        assert stored != token and len(stored) == 64 and token not in stored  # DB không giữ token thô
        assert service.resolve_session(s, token + "x") is None and service.resolve_session(s, None) is None
        service.revoke_session(s, token)
        assert service.resolve_session(s, token) is None


@requires_pg
def test_expired_session_deactivation_and_password_reset_invalidate_sessions(engine):
    with session_scope(engine) as s:
        user, _ = service.create_user(s, "alice@corp.test", PW)
        old = service.create_session(s, user, ttl=timedelta(seconds=-1))
        assert service.resolve_session(s, old) is None  # hết hạn
        assert service.purge_expired_sessions(s) == 1
        t1, t2 = service.create_session(s, user), service.create_session(s, user)
        service.reset_password(s, "alice@corp.test", "new password 12345")
        assert service.resolve_session(s, t1) is None and service.resolve_session(s, t2) is None  # đổi mật khẩu thu hồi phiên
        t3 = service.create_session(s, user)
        service.set_active(s, "alice@corp.test", False)
        assert service.resolve_session(s, t3) is None
        assert service.authenticate(s, "alice@corp.test", "new password 12345") is None
        service.set_active(s, "alice@corp.test", True)
        assert service.authenticate(s, "alice@corp.test", "new password 12345") is not None
        assert service.authenticate(s, "alice@corp.test", PW) is None  # mật khẩu cũ hết hiệu lực


@requires_pg
def test_reset_password_unlocks_the_account(engine):
    with session_scope(engine) as s:
        service.create_user(s, "alice@corp.test", PW)
    for _ in range(3):
        with session_scope(engine) as s:
            service.authenticate(s, "alice@corp.test", "sai mat khau!!")
    with session_scope(engine) as s:
        _, new_pw = service.reset_password(s, "alice@corp.test")
        assert service.authenticate(s, "alice@corp.test", new_pw) is not None


# ---- token API theo project ----

@requires_pg
def test_api_token_scoped_to_its_project_hashed_and_revocable(engine):
    with session_scope(engine) as s:
        repo.sync_project(s, "alpha")
        repo.sync_project(s, "beta")
        row, raw = service.create_api_token(s, "alpha", "ci-alpha")
        assert raw.startswith("qca_") and row.token_hash != raw and len(row.token_hash) == 64
        token = service.resolve_api_token(s, raw)
        assert token.id == row.id and token.last_used_at is not None
        assert service.token_can_write(s, token, "alpha") is True
        assert service.token_can_write(s, token, "beta") is False  # chỉ ghi được vào project của nó
        assert service.resolve_api_token(s, raw + "x") is None
        assert service.resolve_api_token(s, "khong-co-tien-to") is None and service.resolve_api_token(s, None) is None
        assert service.revoke_api_token(s, row.id) is True and service.revoke_api_token(s, row.id) is False
        assert service.resolve_api_token(s, raw) is None
        with pytest.raises(service.AuthError):
            service.create_api_token(s, "khong-co", "x")


# ---- CLI quản trị ----

@pytest.fixture
def admin_env(monkeypatch, db_url, engine):
    monkeypatch.setenv("QC_DATABASE_URL", db_url)


@requires_pg
def test_cli_user_add_reset_deactivate_list(admin_env, engine, capsys):
    assert admin_main(["user", "add", "Alice@corp.test", "--name", "Alice"]) == 0
    pw = capsys.readouterr().out.split("(chỉ hiện một lần): ")[1].split()[0]
    with session_scope(engine) as s:
        assert service.authenticate(s, "alice@corp.test", pw) is not None
    assert admin_main(["user", "add", "alice@corp.test"]) == 3  # trùng
    assert admin_main(["user", "add", "x@evil.test"]) == 3  # sai domain
    assert "không thuộc domain" in capsys.readouterr().err
    assert admin_main(["user", "reset", "alice@corp.test"]) == 0
    new_pw = capsys.readouterr().out.split("(chỉ hiện một lần): ")[1].split()[0]
    with session_scope(engine) as s:
        assert service.authenticate(s, "alice@corp.test", pw) is None and service.authenticate(s, "alice@corp.test", new_pw)
    assert admin_main(["user", "deactivate", "alice@corp.test"]) == 0
    assert admin_main(["user", "reset", "khong@corp.test"]) == 3
    capsys.readouterr()
    assert admin_main(["user", "list"]) == 0 and "alice@corp.test\tdisabled" in capsys.readouterr().out


@requires_pg
def test_cli_secret_is_not_printed_when_the_write_fails(admin_env, engine, capsys, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("commit lỗi")
    monkeypatch.setattr("qc_agent.auth.cli.session_scope", lambda e: (_ for _ in ()).throw(RuntimeError("db down")))
    with pytest.raises(RuntimeError):
        admin_main(["user", "add", "alice@corp.test"])
    assert "mật khẩu" not in capsys.readouterr().out


@requires_pg
def test_cli_token_create_list_revoke(admin_env, engine, capsys):
    with session_scope(engine) as s:
        repo.sync_project(s, "alpha")
    assert admin_main(["token", "create", "--project", "alpha", "--name", "ci"]) == 0
    raw = capsys.readouterr().out.split("(chỉ hiện một lần): ")[1].split()[0]
    with session_scope(engine) as s:
        assert service.resolve_api_token(s, raw) is not None
    assert admin_main(["token", "list", "--project", "alpha"]) == 0 and "active" in capsys.readouterr().out
    assert admin_main(["token", "create", "--project", "khong-co", "--name", "x"]) == 3
    assert admin_main(["token", "revoke", "1"]) == 0 and admin_main(["token", "revoke", "1"]) == 3
    assert admin_main(["token", "revoke"]) == 2  # thiếu tham số


def test_cli_without_database_url_is_a_config_error(monkeypatch, capsys):
    monkeypatch.delenv("QC_DATABASE_URL", raising=False)
    assert admin_main(["user", "list"]) == 3 and "QC_DATABASE_URL" in capsys.readouterr().err
    from qc_agent.core.cli import main
    assert main(["user", "list"]) == 3  # được nối vào CLI chính
