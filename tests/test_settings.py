"""配置测试：SECRET_KEY 强度下限与 PROD 模式启动校验。

这些校验是「配置错误尽早失败」的护栏，此前无任何测试覆盖。
所有用例都传 `_env_file=None`，避免受本地 .env 文件影响。
"""

# `_env_file` 是 pydantic-settings 的私有关键字参数（用于屏蔽本地 .env 干扰），
# mypy 无法从生成的 __init__ 签名中识别，故在本文件内豁免该错误码。
# mypy: disable-error-code=call-arg

import pytest
from pydantic import ValidationError
from sqlalchemy.engine import make_url

from app.core.settings import _DEV_SECRET_KEY, Settings

STRONG_KEY = "x" * 32  # 恰好 32 字节，为允许下限


@pytest.fixture(autouse=True)
def clear_db_host_env(monkeypatch):
    """屏蔽外部 MYSQL_HOST / REDIS_HOST。

    下面多个用例验证的是「未配置时按 MODE 回落到 docker 服务名」这一默认行为，
    而 CI/本地可能为迁移步骤设置了 MYSQL_HOST，不隔离会让断言误失败。
    """
    monkeypatch.delenv("MYSQL_HOST", raising=False)
    monkeypatch.delenv("REDIS_HOST", raising=False)


def test_dev_default_secret_key_passes_strength_check():
    """开发默认密钥（46 字节）满足强度要求，DEV 模式可正常启动。"""
    assert Settings(_env_file=None).SECRET_KEY == _DEV_SECRET_KEY


@pytest.mark.parametrize("short_key", ["", "short", "x" * 31])
def test_short_secret_key_rejected(short_key):
    """短于 32 字节的 SECRET_KEY 一律拒绝（含 DEV）。"""
    with pytest.raises(ValidationError):
        Settings(SECRET_KEY=short_key, _env_file=None)


def test_secret_key_at_minimum_length_accepted():
    """恰好 32 字节通过（边界值）。"""
    assert Settings(SECRET_KEY=STRONG_KEY, _env_file=None).SECRET_KEY == STRONG_KEY


def test_prod_requires_explicit_secret_key():
    """PROD 模式沿用开发默认密钥时拒绝启动。"""
    with pytest.raises(ValidationError):
        Settings(MODE="PROD", MYSQL_PASSWORD="strong-password", _env_file=None)


def test_prod_requires_explicit_mysql_password():
    """PROD 模式沿用默认 MySQL 密码时拒绝启动。"""
    with pytest.raises(ValidationError):
        Settings(MODE="PROD", SECRET_KEY=STRONG_KEY, _env_file=None)


def test_prod_accepts_strong_secret_key_and_password():
    """PROD 模式下显式配置强密钥与密码可正常启动，主机名回落到 docker 服务名。"""
    s = Settings(
        MODE="PROD", SECRET_KEY=STRONG_KEY, MYSQL_PASSWORD="strong-password", _env_file=None
    )
    assert s.MYSQL_HOST == "mysql"
    assert s.REDIS_HOST == "redis"
    assert s.docs_enabled is False  # PROD 默认关闭 API 文档
    assert s.cookie_secure is True  # PROD 默认开启 Secure Cookie


def test_jwt_algorithm_whitelist():
    """JWT_ALGORITHM 仅允许 HS256/HS384/HS512，防止误配 none 等不安全算法。"""
    for alg in ("HS256", "HS384", "HS512"):
        assert alg == Settings(JWT_ALGORITHM=alg, _env_file=None).JWT_ALGORITHM
    for bad in ("none", "RS256", "hs256"):
        with pytest.raises(ValidationError):
            Settings(JWT_ALGORITHM=bad, _env_file=None)


def test_cors_origin_list_splits_and_trims():
    s = Settings(CORS_ORIGINS="http://a.com, http://b.com ,", _env_file=None)
    assert s.cors_origin_list == ["http://a.com", "http://b.com"]


def test_mode_is_case_insensitive():
    """MODE 去空白并转大写后比较：`prod` 此前会被当成 DEV，整套生产策略失效。"""
    s = Settings(
        MODE=" prod ", SECRET_KEY=STRONG_KEY, MYSQL_PASSWORD="strong-password", _env_file=None
    )
    assert s.MODE == "PROD"
    assert s.docs_enabled is False
    assert s.cookie_secure is True


def test_lowercase_prod_still_enforces_strong_credentials():
    """小写 PROD 同样触发强校验，不能绕过。"""
    with pytest.raises(ValidationError):
        Settings(MODE="prod", SECRET_KEY=STRONG_KEY, _env_file=None)


def test_db_credentials_are_url_encoded():
    """用户名/密码含 `@ / : #` 时必须编码，否则拼出的是非法连接串。"""
    s = Settings(MYSQL_USER="u#1", MYSQL_PASSWORD="p@ss/w:rd", _env_file=None)
    assert "u%231" in s.SQLALCHEMY_DATABASE_URL
    assert "p%40ss%2Fw%3Ard" in s.SQLALCHEMY_DATABASE_URL
    assert "p%40ss%2Fw%3Ard" in s.SQLALCHEMY_ASYNC_DATABASE_URL


def test_db_password_space_encoded_as_percent20():
    """userinfo 中 `+` 是字面加号，空格必须编成 `%20`（quote_plus 会错编成 `+`）。"""
    s = Settings(MYSQL_PASSWORD="p ss", _env_file=None)
    url = s.SQLALCHEMY_DATABASE_URL
    assert "p%20ss" in url  # 空格编成 %20，而非 quote_plus 的 +
    assert make_url(url).password == "p ss"


def test_encoded_db_url_roundtrips_original_credentials():
    """编码后的连接串必须能被 SQLAlchemy 还原成原用户名与密码。"""
    s = Settings(MYSQL_USER="u#1", MYSQL_PASSWORD="p@ss w/!rd%", _env_file=None)
    for url in (s.SQLALCHEMY_DATABASE_URL, s.SQLALCHEMY_ASYNC_DATABASE_URL):
        parsed = make_url(url)
        assert parsed.username == "u#1"
        assert parsed.password == "p@ss w/!rd%"
    assert make_url(s.SQLALCHEMY_DATABASE_URL).get_driver_name() == "pymysql"
    assert make_url(s.SQLALCHEMY_ASYNC_DATABASE_URL).get_driver_name() == "asyncmy"


def test_encoded_url_must_not_be_written_into_alembic_config():
    """回归：含 `%` 的 URL 写回 alembic 配置会因 configparser 插值而崩溃。

    早期 env.py 用 `config.set_main_option("sqlalchemy.url", ...)` 注入连接串，
    密码含 `!`（编码为 `%21`）时 alembic 直接抛 "invalid interpolation syntax"。
    本用例锁定这个前提，说明 env.py 为什么必须绕过配置、直接把 URL 交给 SQLAlchemy。
    """
    from alembic.config import Config

    s = Settings(MYSQL_PASSWORD="Str0ngPass!2026", _env_file=None)
    url = s.SQLALCHEMY_DATABASE_URL
    assert "%" in url  # 前提：强密码编码后必然含百分号
    with pytest.raises(ValueError, match="interpolation"):
        Config().set_main_option("sqlalchemy.url", url)


def test_blank_padded_secret_key_rejected():
    """纯空白填充的密钥长度达标但熵为零，必须拒绝。"""
    with pytest.raises(ValidationError):
        Settings(SECRET_KEY=" " * 40, _env_file=None)


def test_wildcard_cors_origin_rejected():
    """`*` 与 allow_credentials 同用等于对全网开放带凭证跨域，配置层直接拒绝。"""
    with pytest.raises(ValidationError):
        Settings(CORS_ORIGINS="*", _env_file=None)


def test_bootstrap_allowed_defaults_to_mode():
    """空库自举开关：DEV 默认开、PROD 默认关，显式设置优先。"""
    assert Settings(_env_file=None).bootstrap_allowed is True
    assert (
        Settings(
            MODE="PROD", SECRET_KEY=STRONG_KEY, MYSQL_PASSWORD="strong-password", _env_file=None
        ).bootstrap_allowed
        is False
    )
    assert Settings(ALLOW_BOOTSTRAP=False, _env_file=None).bootstrap_allowed is False
