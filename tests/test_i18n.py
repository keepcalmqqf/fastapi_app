"""i18n 插件测试：语种解析、词条接口、响应翻译，以及开关关闭时行为不变。"""

import json

import pytest
from fastapi import FastAPI, HTTPException, Request
from starlette.testclient import TestClient

from app.core.result import Result
from app.core.settings import settings
from app.plugins.i18n import (
    CATALOGS,
    default_locale,
    resolve_locale,
    setup,
    translate,
)

ADMIN_PAYLOAD = {
    "name": "管理员",
    "email": "admin@test.com",
    "password": "abcd1234",
    "is_active": True,
}


def _app() -> TestClient:
    app = FastAPI()
    setup(app)
    return TestClient(app)


def test_resolve_locale_prefers_explicit_param():
    assert resolve_locale(preferred="en-US", accept_language="zh-CN") == "en-US"
    # 未支持的语种回退 Accept-Language，再回退默认
    assert resolve_locale(preferred="fr-FR", accept_language="zh-CN") == "zh-CN"
    assert resolve_locale(preferred="fr-FR") == default_locale()


def test_resolve_locale_honours_accept_language_quality():
    """按 q 权重排序：en 权重更高时优先 en。"""
    assert resolve_locale(accept_language="zh-CN;q=0.5,en-US;q=0.9") == "en-US"
    assert resolve_locale(accept_language="en-US;q=0.1,zh-CN;q=0.8") == "zh-CN"


def test_resolve_locale_matches_primary_language():
    """只写主语言（en / zh）也能命中对应语种。"""
    assert resolve_locale(preferred="en") == "en-US"
    assert resolve_locale(accept_language="zh") == "zh-CN"


def test_translate_falls_back_to_source_text():
    """缺词条时返回中文原文，绝不返回空串。"""
    assert translate("邮箱或密码错误", locale="en-US") == "Invalid email or password"
    assert translate("某个未翻译的文案", locale="en-US") == "某个未翻译的文案"
    assert translate("邮箱或密码错误", locale="zh-CN") == "邮箱或密码错误"


def test_translate_exception_detail_with_params():
    """带运行时数值的文案：模板与参数分离后才可能命中词条。

    回归：此前「请 {minutes} 分钟后重试」的分钟数是先拼进字符串的，拼好之后
    永远匹配不上词条模板，锁定提示在英文环境下仍是中文。
    """
    from app.plugins.i18n import translate_exception_detail

    detail = "登录失败次数过多，请 {minutes} 分钟后重试|" + json.dumps({"minutes": 15})
    assert translate_exception_detail(detail) == "登录失败次数过多，请 15 分钟后重试"
    from app.plugins.i18n import set_locale

    set_locale("en-US")
    try:
        assert (
            translate_exception_detail(detail)
            == "Too many failed sign-in attempts, please retry in 15 minutes"
        )
    finally:
        set_locale("zh-CN")


def test_translate_exception_detail_without_params():
    """普通文案走原路径，行为与 translate() 一致。"""
    from app.plugins.i18n import translate_exception_detail

    assert translate_exception_detail("邮箱或密码错误") == "邮箱或密码错误"


def test_locked_login_message_is_translated(i18n_enabled, monkeypatch: pytest.MonkeyPatch):
    """锁定提示（429）在英文请求下返回译文，含插值后的分钟数。"""
    from fastapi import FastAPI

    from app.core.exceptions import register_exception_handlers
    from app.login_guard import _locked_error

    async def boom(request: Request) -> None:
        raise _locked_error(900)

    app = FastAPI()
    register_exception_handlers(app)
    setup(app)
    app.get("/locked")(boom)
    client = TestClient(app)

    resp = client.get("/locked", headers={"Accept-Language": "en-US"})
    assert resp.status_code == 429
    assert resp.json()["message"] == (
        "Too many failed sign-in attempts, please retry in 15 minutes"
    )


def test_translate_supports_placeholder_interpolation():
    catalog = CATALOGS["en-US"]
    assert "缺少：{missing}" not in catalog  # 插值失败也不应抛异常
    assert translate("权限不足", locale="en-US", missing="user:read") == "Permission denied"


def test_i18n_endpoints_expose_catalog():
    """词条接口可直接喂给前端 i18n 运行时（/api/v1 前缀由 register_routers 统一加）。"""
    client = _app()
    locales = client.get("/api/v1/i18n/locales")
    assert locales.status_code == 200
    assert "en-US" in locales.json()["data"]["supported"]

    messages = client.get("/api/v1/i18n/messages", params={"locale": "en-US"})
    assert messages.status_code == 200
    assert messages.json()["data"]["邮箱或密码错误"] == "Invalid email or password"


def test_locale_middleware_sets_content_language():
    client = _app()
    resp = client.get("/i18n/locales", headers={"Accept-Language": "en-US"})
    assert resp.headers["Content-Language"] == "en-US"


def test_enabled_plugin_registers_versioned_routes(monkeypatch: pytest.MonkeyPatch):
    """开启后路由挂在 /api/v1 下（与业务接口同一前缀），前端据此拉取词条。"""
    from fastapi import FastAPI

    from app.api import register_routers

    monkeypatch.setattr(settings, "ENABLE_I18N", True)
    app = FastAPI()
    from app.plugins import i18n

    i18n.setup(app)
    register_routers(app)
    client = TestClient(app)
    assert client.get("/api/v1/i18n/locales").status_code == 200


def test_without_plugin_keeps_chinese():
    """未挂载插件时：没有 i18n 路由，且文案逐字保持中文。

    用独立 app 而非全局 client：全局 app 在导入时就读了一次开关，本机若导出了
    ENABLE_I18N 会让它带着插件路由，断言 404 会误失败。
    """
    from app.core.exceptions import register_exception_handlers

    async def boom(request: Request) -> None:
        raise HTTPException(status_code=401, detail="邮箱或密码错误")

    app = FastAPI()
    register_exception_handlers(app)
    app.get("/boom")(boom)
    client = TestClient(app)

    assert client.get("/api/v1/i18n/locales").status_code == 404
    resp = client.get("/boom", headers={"Accept-Language": "en-US"})
    assert resp.status_code == 401
    assert resp.json()["message"] == "邮箱或密码错误"


@pytest.fixture()
def i18n_enabled(monkeypatch: pytest.MonkeyPatch):
    """显式开启插件（其余用例由 conftest 固定为关闭）。"""
    monkeypatch.setattr(settings, "ENABLE_I18N", True)
    yield


async def _login_failed(request: Request) -> None:
    """模拟业务抛出的 HTTPException，验证统一异常处理的翻译链路。"""
    raise HTTPException(status_code=401, detail="邮箱或密码错误")


def test_enabled_translates_error_message(i18n_enabled):
    """开启后同一错误按 Accept-Language 返回译文。"""
    from app.core.exceptions import register_exception_handlers

    # 用最小应用验证「中间件解析语种 → 异常处理翻译文案」这条链路，
    # 不往全局 app 上挂插件，避免污染其它用例的路由表
    app = FastAPI()
    register_exception_handlers(app)
    setup(app)
    app.get("/boom")(_login_failed)
    client = TestClient(app)

    resp = client.get("/boom", headers={"Accept-Language": "en-US"})
    assert resp.status_code == 401
    assert resp.json()["message"] == "Invalid email or password"

    # 未声明语种时按默认语种返回中文原文
    assert client.get("/boom").json()["message"] == "邮箱或密码错误"


def test_enabled_translates_success_message(i18n_enabled):
    """成功文案同样按语种返回（result.ok(translate=True)）。"""
    from app.core import result

    async def ok_endpoint() -> Result[str]:
        return result.ok(data="x", message="登录成功", translate=True)

    app = FastAPI()
    setup(app)
    app.get("/ok")(ok_endpoint)
    client = TestClient(app)

    assert client.get("/ok", headers={"Accept-Language": "en-US"}).json()["message"] == "Signed in"
    assert client.get("/ok").json()["message"] == "登录成功"


def test_success_message_translated_without_explicit_flag(i18n_enabled):
    """回归：普通接口不显式传 translate=True 时，英文请求也应翻译成功文案。

    此前翻译是逐路由 opt-in（只有 auth.py 显式打开），导致 user/member/role 等
    绝大多数接口在英文环境下 message 仍是中文。放开默认后，仅靠中间件解析出的语种
    就应自动翻译。
    """
    from app.core import result

    async def create_endpoint() -> Result[str]:
        # 不传 translate，验证默认行为
        return result.ok(data="x", message="创建成功")

    app = FastAPI()
    setup(app)
    app.get("/create")(create_endpoint)
    client = TestClient(app)

    assert client.get("/create", headers={"Accept-Language": "en-US"}).json()["message"] == (
        "Created successfully"
    )
    # 未声明语种时按默认语种返回中文原文
    assert client.get("/create").json()["message"] == "创建成功"


def test_failure_message_translated(i18n_enabled):
    """回归：result.failure 的文案同样按语种返回（此前完全不翻译）。"""
    from app.core import result

    async def boom_endpoint() -> Result[None]:
        return result.failure(code=403, message="未授权访问")

    app = FastAPI()
    setup(app)
    app.get("/boom")(boom_endpoint)
    client = TestClient(app)

    resp = client.get("/boom", headers={"Accept-Language": "en-US"})
    assert resp.status_code == 200
    assert resp.json()["code"] == 403
    assert resp.json()["message"] == "Unauthorized access"
    assert client.get("/boom").json()["message"] == "未授权访问"
