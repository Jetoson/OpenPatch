"""drop unused ml_model column

Revision ID: cd5f382e7962
Revises: b7c1e4f20a91
Create Date: 2026-08-30 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'cd5f382e7962'
down_revision: str | Sequence[str] | None = 'b7c1e4f20a91'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Carried since the baseline schema, never read or written by anything
    # in the codebase.
    op.drop_column('endpoints', 'ml_model')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column('endpoints', sa.Column('ml_model', sa.LargeBinary(), nullable=True))
