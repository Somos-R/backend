from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

from app.core.config import settings
from app.core.database import Base
import app.domains.users.models  # noqa: F401 — registers models with Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = Base.metadata

# PostGIS / Tiger internal tables that Alembic should never touch
EXCLUDED_TABLES = {
    "spatial_ref_sys",
    "layer",
    "topology",
    "loader_variables",
    "loader_platform",
    "loader_lookuptables",
    "pagc_gaz",
    "pagc_lex",
    "pagc_rules",
    "geocode_settings",
    "geocode_settings_default",
    "state_lookup",
    "county_lookup",
    "countysub_lookup",
    "place_lookup",
    "direction_lookup",
    "secondary_unit_lookup",
    "street_type_lookup",
    "zip_lookup_all",
    "zip_lookup_base",
    "zip_lookup",
    "zip_state",
    "zip_state_loc",
    "state",
    "county",
    "cousub",
    "place",
    "bg",
    "tract",
    "tabblock",
    "tabblock20",
    "faces",
    "edges",
    "addrfeat",
    "featnames",
    "addr",
    "zcta5",
}


def include_object(object, name, type_, reflected, compare_to):
    if type_ == "table" and name in EXCLUDED_TABLES:
        return False
    return True


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
