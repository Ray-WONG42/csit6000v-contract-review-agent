"""Persist account output language for unattended email reviews."""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    if "language" not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("users")}:
        op.add_column("users", sa.Column("language", sa.String(), nullable=False, server_default="en"))


def downgrade():
    op.drop_column("users", "language")
