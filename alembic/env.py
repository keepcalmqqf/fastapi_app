from logging.config import fileConfig

from sqlalchemy import create_engine, pool

from alembic import context
from app.core.settings import settings
from app.models import Base

config = context.config
# 注意：不要把连接串写回 alembic 配置。configparser 用 `%(...)s` 做插值，
# 而编码后的密码里必然带 `%`（如 `!` → `%21`、`%` → `%25`），
# 一旦写入就会抛 "invalid interpolation syntax"。这里直接把 URL 交给
# SQLAlchemy，彻底绕开插值环节。
DATABASE_URL = settings.SQLALCHEMY_DATABASE_URL

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(DATABASE_URL, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
