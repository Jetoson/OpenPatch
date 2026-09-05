"""Running the schema migrations from inside the program.
"""

import os

import paths
from database import engine
from alembic import command
from sqlalchemy import inspect
from alembic.config import Config
from config import DATABASE_URL


def alembic_config() -> Config:
    """An Alembic configuration that does not depend on the working directory."""
    ini = paths.alembic_ini()
    cfg = Config(ini if os.path.exists(ini) else None)
    cfg.set_main_option("script_location", paths.alembic_dir())

    cfg.set_main_option("sqlalchemy.url", DATABASE_URL)
    return cfg


def upgrade_to_head() -> None:
    """Brings the database up to date"""
    paths.ensure_data_dir()
    command.upgrade(alembic_config(), "head")


def current_revision() -> str | None:
    """The revision the database is on, or None if it has never been migrated."""
    with engine.connect() as connection:
        if not inspect(connection).has_table("alembic_version"):
            return None
        from alembic.migration import MigrationContext

        return MigrationContext.configure(connection).get_current_revision()
