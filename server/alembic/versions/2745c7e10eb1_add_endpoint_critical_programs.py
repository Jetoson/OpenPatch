"""add endpoint critical_programs

Revision ID: 2745c7e10eb1
Revises: 2930c896baae
Create Date: 2026-08-31 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '2745c7e10eb1'
down_revision: str | Sequence[str] | None = '2930c896baae'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('endpoints', sa.Column('critical_programs', sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('endpoints', 'critical_programs')
