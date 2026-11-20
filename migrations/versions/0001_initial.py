"""Initial application schema."""
from alembic import op
from app.db import Base
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    Base.metadata.create_all(op.get_bind())


def downgrade():
    raise RuntimeError("Use a verified data backup to roll back this initial schema.")
