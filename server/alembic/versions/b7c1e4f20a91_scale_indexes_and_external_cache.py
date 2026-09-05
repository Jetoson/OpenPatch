"""scale indexes, telemetry watermark and external cache

Revision ID: b7c1e4f20a91
Revises: c4321924d5a5
Create Date: 2026-08-21 23:40:00.000000

Three groups of changes, all in service of a fleet in the thousands:

- indexes for the queries that run per heartbeat and per dashboard load, which
  were full scans on tables that grow with the fleet;
- endpoints.telemetry_recorded_at, so the heartbeat can decide whether a
  telemetry sample is worth storing without a MAX() per request;
- external_cache, which persists endoflife.date responses so a restart does
  not re-issue the entire catalogue of lookups.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b7c1e4f20a91'
down_revision: str | Sequence[str] | None = 'c4321924d5a5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('endpoints', sa.Column('telemetry_recorded_at', sa.DateTime(), nullable=True))

    # "How much of the fleet is online" is a range scan over this column on
    # every dashboard load.
    op.create_index(op.f('ix_endpoints_last_seen'), 'endpoints', ['last_seen'], unique=False)

    # Telemetry is the largest table in the database and every read of it is
    # "recent samples for one device".
    op.create_index(
        'ix_telemetry_device_recorded', 'telemetry_history', ['device_id', 'recorded_at'],
        unique=False,
    )

    # The heartbeat's exact predicate: this device's PENDING tasks.
    op.create_index(op.f('ix_task_queue_device_id'), 'task_queue', ['device_id'], unique=False)
    op.create_index(op.f('ix_task_queue_status'), 'task_queue', ['status'], unique=False)
    op.create_index(op.f('ix_task_queue_created_at'), 'task_queue', ['created_at'], unique=False)
    op.create_index(
        'ix_task_queue_device_status', 'task_queue', ['device_id', 'status'], unique=False
    )

    # Inventory is replaced wholesale per device on each scan (a DELETE on
    # device_id), and the fleet rollup groups by (name, version).
    op.create_index(
        op.f('ix_software_inventory_device_id'), 'software_inventory', ['device_id'], unique=False
    )
    op.create_index(
        'ix_software_inventory_name_version', 'software_inventory', ['name', 'version'],
        unique=False,
    )

    op.create_table(
        'external_cache',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('cache_key', sa.String(), nullable=True),
        sa.Column('payload', sa.Text(), nullable=True),
        sa.Column('ok', sa.Boolean(), nullable=True),
        sa.Column('fetched_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_external_cache_id'), 'external_cache', ['id'], unique=False)
    op.create_index(op.f('ix_external_cache_cache_key'), 'external_cache', ['cache_key'], unique=True)
    op.create_index(op.f('ix_external_cache_fetched_at'), 'external_cache', ['fetched_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_external_cache_fetched_at'), table_name='external_cache')
    op.drop_index(op.f('ix_external_cache_cache_key'), table_name='external_cache')
    op.drop_index(op.f('ix_external_cache_id'), table_name='external_cache')
    op.drop_table('external_cache')

    op.drop_index('ix_software_inventory_name_version', table_name='software_inventory')
    op.drop_index(op.f('ix_software_inventory_device_id'), table_name='software_inventory')

    op.drop_index('ix_task_queue_device_status', table_name='task_queue')
    op.drop_index(op.f('ix_task_queue_created_at'), table_name='task_queue')
    op.drop_index(op.f('ix_task_queue_status'), table_name='task_queue')
    op.drop_index(op.f('ix_task_queue_device_id'), table_name='task_queue')

    op.drop_index('ix_telemetry_device_recorded', table_name='telemetry_history')
    op.drop_index(op.f('ix_endpoints_last_seen'), table_name='endpoints')

    op.drop_column('endpoints', 'telemetry_recorded_at')
