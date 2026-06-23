"""rename virtual_keys table to api_keys

Revision ID: 71683063682d
Revises: a3f9c1d7b2e8
Create Date: 2026-06-23 05:56:12
"""

from alembic import op

revision = "71683063682d"
down_revision = "a3f9c1d7b2e8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.rename_table("virtual_keys", "api_keys")
    with op.batch_alter_table("api_keys") as batch_op:
        batch_op.drop_index("ix_virtual_keys_key_prefix")
        batch_op.drop_index("ix_virtual_keys_tenant_id")
        batch_op.create_index("ix_api_keys_key_prefix", ["key_prefix"])
        batch_op.create_index("ix_api_keys_tenant_id", ["tenant_id"])


def downgrade() -> None:
    op.rename_table("api_keys", "virtual_keys")
    with op.batch_alter_table("virtual_keys") as batch_op:
        batch_op.drop_index("ix_api_keys_key_prefix")
        batch_op.drop_index("ix_api_keys_tenant_id")
        batch_op.create_index("ix_virtual_keys_key_prefix", ["key_prefix"])
        batch_op.create_index("ix_virtual_keys_tenant_id", ["tenant_id"])
