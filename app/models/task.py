from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models.base import TimestampMixin


class TaskRun(Base, TimestampMixin):
    """异步任务执行历史：一条任务从入队到终态的全过程留痕。

    与插件里的内存记录 / Redis 结果哈希互补：后者只是「最近观测」（进程内存保留
    1000 条、Redis 哈希带 TTL 会过期），本表才是可回溯、可筛选、重启不丢的持久化
    记录。写入路径见 `app/services/task.py`，失败只记日志不影响任务执行。

    job_id 建唯一索引：任务状态是「按 job_id 覆盖式更新」的（入队→执行中→成功/失败，
    重试也复用同一个 job_id），不是每变一次状态插一行。
    """

    __tablename__ = "task_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 任务 id：memory 后端的 task_id 与 redis 后端的 job_id，语义相同
    job_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    # 具名任务名；本地闭包投递时为函数名
    name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # 执行后端：memory / redis
    backend: Mapped[str] = mapped_column(String(16), nullable=False)
    # 状态：queued / running / success / failed（memory 后端入队态为 pending，统一记为 queued）
    state: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    # 第几次尝试（首次为 0，重试逐次 +1）
    attempt: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # 该任务允许的最大重试次数（不含首次）
    retries: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    enqueued_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    # 执行耗时（毫秒），终态时结算；未完成的在途任务为空
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    # 失败原因（最后一次错误）；重试过程中会保留最近一次
    error: Mapped[str | None] = mapped_column(String(512))
    # 处理器返回值（JSON 字符串）；超过长度会截断，只作观测用
    result: Mapped[str | None] = mapped_column(String(512))
