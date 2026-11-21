"""Persist attachment cleanup after deleting contract history."""
from alembic import op
from app.db import FileRemoval

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    FileRemoval.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    FileRemoval.__table__.drop(op.get_bind(), checkfirst=True)
