from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# ---- 本项目接线：URL 来自 app settings（ASHARE_DATABASE_URL），metadata 为全量 Base ----
from app.core.config import settings
from app.models import watchlist as _watchlist  # noqa: F401  Base 所在模块
from app.models.paper import PaperAccount, PaperOrder, PaperPosition  # noqa: F401
from app.models.watchlist import Base
from app.predict.models import PredictionReportRow, PredictionThemeRow  # noqa: F401
from app.review.models import (  # noqa: F401
    MinuteDecisionRow,
    ReviewActionItemRow,
    ReviewMetaInsightRow,
    ReviewReportRow,
)

config = context.config

# URL 默认来自 app settings（ASHARE_DATABASE_URL）；程序化调用（run_migrations）
# 可预先 set_main_option 覆盖——此处仅在未设置时回填，避免覆盖调用方目标库
if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", settings.database_url)

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    支持连接共享：调用方（app.core.migrations）可通过
    ``config.attributes["connection"]`` 传入既有连接——
    使 :memory:/测试库与迁移在同一连接上执行。
    """
    shared = config.attributes.get("connection")
    if shared is not None:
        context.configure(connection=shared, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
        return

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
