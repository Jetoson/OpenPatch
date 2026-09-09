"""The migrations and the models must describe the same schema.
"""

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from database import Base, engine


def test_the_models_match_the_migrated_schema():
    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        diff = compare_metadata(context, Base.metadata)

    structural = [
        entry for entry in diff
        if isinstance(entry, tuple) and entry[0] in (
            "add_table", "remove_table", "add_column", "remove_column",
        )
    ]

    assert structural == [], (
        "models and migrations have drifted; generate a migration for:\n"
        + "\n".join(str(entry) for entry in structural)
    )


def test_every_migration_has_a_single_head():
    """Two heads means a merge is needed and `alembic upgrade head` will
    refuse, which is only discovered on the next deployment."""
    import os

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from tests.server.conftest import SERVER

    cfg = Config(os.path.join(SERVER, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(SERVER, "alembic"))

    assert len(ScriptDirectory.from_config(cfg).get_heads()) == 1
