import importlib
import pkgutil
from collections.abc import Callable

from fastapi import APIRouter, FastAPI

from app.core.settings import settings

# 业务接口统一版本前缀：/api/v1/...。register_routers 会自动给新模块的路由带上，
# 出 v2 时只需在此新增前缀并让新模块改用，旧版本路由可平滑共存一段时间。
API_V1_PREFIX = "/api/v1"

# 不挂版本前缀的运维模块：健康检查由容器探针/监控按根路径约定访问，
# 地址不应随业务版本演进而变化
_UNVERSIONED_MODULES = {"health"}

# 可选业务模块的开关：模块名 -> 是否启用（模型始终导入，仅关闭路由注册）
_MODULE_SWITCHES: dict[str, Callable[[], bool]] = {
    "member": lambda: settings.ENABLE_MEMBER,
}


def register_routers(app: FastAPI) -> None:
    """自动发现并注册 api 包下所有模块中的 router，受开关关闭的模块跳过。"""
    for module_info in pkgutil.iter_modules(__path__):
        enabled = _MODULE_SWITCHES.get(module_info.name)
        if enabled is not None and not enabled():
            continue
        module = importlib.import_module(f"app.api.{module_info.name}")
        router = getattr(module, "router", None)
        if isinstance(router, APIRouter):
            if module_info.name in _UNVERSIONED_MODULES:
                app.include_router(router)
            else:
                app.include_router(router, prefix=API_V1_PREFIX)
