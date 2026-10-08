"""配置测试：SECRET_KEY 强度下限与 PROD 模式启动校验。

这些校验是「配置错误尽早失败」的护栏，此前无任何测试覆盖。
所有用例都传 `_env_file=None`，避免受本地 .env 文件影响。
"""

import pytest
from pydantic import ValidationError

from app.core.settings import _DEV_SECRET_KEY, Settings

STRONG_KEY = "x" * 32  # 恰好 32 字节，为允许下限


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
