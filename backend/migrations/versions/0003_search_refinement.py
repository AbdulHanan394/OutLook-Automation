"""search-scoped rejection feedback and refinement state"""
from alembic import op

revision = "0003_search_refinement"
down_revision = "0002_sync_observability"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE search_sessions (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        mailbox_connection_id uuid NOT NULL REFERENCES mailbox_connections(id) ON DELETE CASCADE,
        query_json jsonb NOT NULL,
        result_email_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
        refinement_count integer NOT NULL DEFAULT 0 CHECK (refinement_count BETWEEN 0 AND 5),
        created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX search_sessions_user_created_idx ON search_sessions(user_id, created_at DESC);
    CREATE TABLE search_feedback (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        search_id uuid NOT NULL REFERENCES search_sessions(id) ON DELETE CASCADE,
        email_id uuid NOT NULL REFERENCES emails(id) ON DELETE CASCADE,
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE(search_id, email_id)
    );
    CREATE INDEX search_feedback_user_search_idx ON search_feedback(user_id, search_id);
    ALTER TABLE search_sessions ENABLE ROW LEVEL SECURITY;
    ALTER TABLE search_sessions FORCE ROW LEVEL SECURITY;
    CREATE POLICY search_sessions_owner ON search_sessions
        USING (user_id = current_setting('app.user_id', true)::uuid)
        WITH CHECK (user_id = current_setting('app.user_id', true)::uuid);
    ALTER TABLE search_feedback ENABLE ROW LEVEL SECURITY;
    ALTER TABLE search_feedback FORCE ROW LEVEL SECURITY;
    CREATE POLICY search_feedback_owner ON search_feedback
        USING (user_id = current_setting('app.user_id', true)::uuid)
        WITH CHECK (user_id = current_setting('app.user_id', true)::uuid);
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS search_feedback, search_sessions CASCADE")
