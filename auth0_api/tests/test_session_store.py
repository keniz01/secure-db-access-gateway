from app.auth import session_store


def test_redis_password_is_passed_separately_from_url(monkeypatch, mocker):
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("REDIS_PASSWORD", "test-password")
    monkeypatch.setattr(session_store, "_redis_client", None)
    monkeypatch.setattr(session_store, "_redis_url_cached", None)
    from_url = mocker.patch("redis.asyncio.from_url", return_value=object())

    session_store._get_redis()

    from_url.assert_called_once_with(
        "redis://redis:6379/0",
        decode_responses=True,
        password="test-password",
    )


def test_redis_password_env_overrides_url_password(monkeypatch, mocker):
    monkeypatch.setenv("REDIS_URL", "redis://:url-password@redis:6379/0")
    monkeypatch.setenv("REDIS_PASSWORD", "env-password")
    monkeypatch.setattr(session_store, "_redis_client", None)
    monkeypatch.setattr(session_store, "_redis_url_cached", None)
    from_url = mocker.patch("redis.asyncio.from_url", return_value=object())

    session_store._get_redis()

    from_url.assert_called_once_with(
        "redis://:url-password@redis:6379/0",
        decode_responses=True,
        password="env-password",
    )


def test_redis_url_credentials_are_preserved_without_password_env(monkeypatch, mocker):
    monkeypatch.setenv("REDIS_URL", "redis://:url-password@redis:6379/0")
    monkeypatch.delenv("REDIS_PASSWORD", raising=False)
    monkeypatch.setattr(session_store, "_redis_client", None)
    monkeypatch.setattr(session_store, "_redis_url_cached", None)
    from_url = mocker.patch("redis.asyncio.from_url", return_value=object())

    session_store._get_redis()

    from_url.assert_called_once_with(
        "redis://:url-password@redis:6379/0",
        decode_responses=True,
    )


def test_redis_password_file_is_used_when_env_value_is_empty(monkeypatch, mocker, tmp_path):
    password_file = tmp_path / "redis-password"
    password_file.write_text("file-password\n")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("REDIS_PASSWORD", "")
    monkeypatch.setenv("REDIS_PASSWORD_FILE", str(password_file))
    monkeypatch.setattr(session_store, "_redis_client", None)
    monkeypatch.setattr(session_store, "_redis_url_cached", None)
    from_url = mocker.patch("redis.asyncio.from_url", return_value=object())

    session_store._get_redis()

    from_url.assert_called_once_with(
        "redis://redis:6379/0",
        decode_responses=True,
        password="file-password",
    )
