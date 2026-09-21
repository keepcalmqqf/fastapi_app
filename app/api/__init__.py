import importlib
import pkgutil
from collections.abc import Callable

from fastapi import APIRouter, FastAPI

from app.core.settings import settings

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
            app.include_router(router)
