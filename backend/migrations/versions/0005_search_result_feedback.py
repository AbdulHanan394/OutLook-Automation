"""user-scoped search feedback for tie-breaking"""
from alembic import op

revision = "0005_search_result_feedback"
down_revision = "0004_saved_searches_pins"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE search_result_events (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        mailbox_connection_id uuid NOT NULL REFERENCES mailbox_connections(id) ON DELETE CASCADE,
        search_id uuid NOT NULL REFERENCES search_sessions(id) ON DELETE CASCADE,
        email_id uuid NOT NULL REFERENCES emails(id) ON DELETE CASCADE,
        signal smallint NOT NULL CHECK (signal IN (-1, 1)),
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE(search_id, email_id)
    );
    CREATE INDEX search_result_events_owner_email_created_idx
        ON search_result_events(user_id, email_id, created_at DESC);
    ALTER TABLE search_result_events ENABLE ROW LEVEL SECURITY;
    ALTER TABLE search_result_events FORCE ROW LEVEL SECURITY;
    CREATE POLICY search_result_events_owner ON search_result_events
        USING (user_id = current_setting('app.user_id', true)::uuid)
        WITH CHECK (user_id = current_setting('app.user_id', true)::uuid);
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS search_result_events CASCADE")
