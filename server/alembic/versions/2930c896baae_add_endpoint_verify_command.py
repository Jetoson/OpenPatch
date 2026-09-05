"""add endpoint verify_command

Revision ID: 2930c896baae
Revises: cd5f382e7962
Create Date: 2026-08-31 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '2930c896baae'
down_revision: str | Sequence[str] | None = 'cd5f382e7962'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('endpoints', sa.Column('verify_command', sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('endpoints', 'verify_command')
