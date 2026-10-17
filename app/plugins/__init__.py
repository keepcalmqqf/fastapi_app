from fastapi import FastAPI

from app.core.settings import settings


def setup_plugins(app: FastAPI) -> None:
    """按配置开关挂载可插拔能力。新增插件：建模块、写 setup(app)、在此登记。

    启用日志由各插件的 setup() 自己打（能带上后端、worker 数、时区等具体参数），
    这里不重复输出，否则同一插件在启动日志里会出现两行。
    """
    if settings.ENABLE_REQUEST_ID:
        from app.plugins import request_id

        request_id.setup(app)
    if settings.ENABLE_RATE_LIMIT:
        from app.plugins import rate_limit

        rate_limit.setup(app)
    if settings.ENABLE_METRICS:
        from app.plugins import metrics

        metrics.setup(app)
    if settings.ENABLE_I18N:
        from app.plugins import i18n

        i18n.setup(app)
    if settings.ENABLE_WEBSOCKET:
        from app.plugins import websocket

        websocket.setup(app)
    if settings.ENABLE_TASKS:
        from app.plugins import tasks

        tasks.setup(app)
    if settings.ENABLE_SCHEDULER:
        from app.plugins import scheduler

        scheduler.setup(app)
