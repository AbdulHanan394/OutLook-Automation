"""saved searches and pinned emails"""
from alembic import op

revision = "0004_saved_searches_pins"
down_revision = "0003_search_refinement"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE saved_searches (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        mailbox_connection_id uuid NOT NULL REFERENCES mailbox_connections(id) ON DELETE CASCADE,
        name varchar(80) NOT NULL,
        query text NOT NULL,
        filters jsonb NOT NULL DEFAULT '{}'::jsonb,
        created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX saved_searches_owner_mailbox_created_idx
        ON saved_searches(user_id, mailbox_connection_id, created_at DESC);
    ALTER TABLE saved_searches ENABLE ROW LEVEL SECURITY;
    ALTER TABLE saved_searches FORCE ROW LEVEL SECURITY;
    CREATE POLICY saved_searches_owner ON saved_searches
        USING (user_id = current_setting('app.user_id', true)::uuid)
        WITH CHECK (user_id = current_setting('app.user_id', true)::uuid);

    CREATE TABLE pinned_emails (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        mailbox_connection_id uuid NOT NULL REFERENCES mailbox_connections(id) ON DELETE CASCADE,
        email_id uuid NOT NULL REFERENCES emails(id) ON DELETE CASCADE,
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE(user_id, email_id)
    );
    CREATE INDEX pinned_emails_owner_mailbox_created_idx
        ON pinned_emails(user_id, mailbox_connection_id, created_at DESC);
    ALTER TABLE pinned_emails ENABLE ROW LEVEL SECURITY;
    ALTER TABLE pinned_emails FORCE ROW LEVEL SECURITY;
    CREATE POLICY pinned_emails_owner ON pinned_emails
        USING (user_id = current_setting('app.user_id', true)::uuid)
        WITH CHECK (user_id = current_setting('app.user_id', true)::uuid);
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS pinned_emails, saved_searches CASCADE")
