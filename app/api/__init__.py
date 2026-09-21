import importlib
import pkgutil

from fastapi import APIRouter, FastAPI


def register_routers(app: FastAPI) -> None:
    """自动发现并注册 api 包下所有模块中的 router。"""
    for module_info in pkgutil.iter_modules(__path__):
        module = importlib.import_module(f"app.api.{module_info.name}")
        router = getattr(module, "router", None)
        if isinstance(router, APIRouter):
            app.include_router(router)
