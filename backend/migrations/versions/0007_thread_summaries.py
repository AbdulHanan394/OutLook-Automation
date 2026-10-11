"""private cached summaries of cited email threads"""
from alembic import op

revision = "0007_thread_summaries"
down_revision = "0006_thread_grouping"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE thread_summary_cache (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        mailbox_connection_id uuid NOT NULL REFERENCES mailbox_connections(id) ON DELETE CASCADE,
        group_key varchar(64) NOT NULL,
        source_hash varchar(64) NOT NULL,
        summary_json jsonb NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE(user_id, mailbox_connection_id, group_key)
    );
    ALTER TABLE thread_summary_cache ENABLE ROW LEVEL SECURITY;
    ALTER TABLE thread_summary_cache FORCE ROW LEVEL SECURITY;
    CREATE POLICY thread_summary_cache_owner ON thread_summary_cache
        USING (user_id = current_setting('app.user_id', true)::uuid)
        WITH CHECK (user_id = current_setting('app.user_id', true)::uuid);
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS thread_summary_cache CASCADE")
