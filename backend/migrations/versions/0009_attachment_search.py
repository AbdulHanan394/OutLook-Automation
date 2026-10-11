"""index supported Outlook attachment text for private email search"""
from alembic import op

revision = "0009_attachment_search"
down_revision = "0008_commitments"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE attachments (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        mailbox_connection_id uuid NOT NULL REFERENCES mailbox_connections(id) ON DELETE CASCADE,
        email_id uuid NOT NULL REFERENCES emails(id) ON DELETE CASCADE,
        graph_attachment_id text NOT NULL,
        filename text NOT NULL,
        content_type text,
        last_modified_at timestamptz,
        size_bytes integer NOT NULL CHECK (size_bytes >= 0),
        file_type varchar(8) NOT NULL CHECK (file_type IN ('pdf', 'docx', 'xlsx', 'txt', 'csv')),
        status varchar(32) NOT NULL,
        UNIQUE(email_id, graph_attachment_id)
    );
    CREATE INDEX attachments_owner_email_type_idx
        ON attachments(user_id, mailbox_connection_id, email_id, file_type);
    ALTER TABLE attachments ENABLE ROW LEVEL SECURITY;
    ALTER TABLE attachments FORCE ROW LEVEL SECURITY;
    CREATE POLICY attachments_owner ON attachments
        USING (user_id = current_setting('app.user_id', true)::uuid)
        WITH CHECK (user_id = current_setting('app.user_id', true)::uuid);

    ALTER TABLE email_chunks ADD COLUMN source varchar(16) NOT NULL DEFAULT 'email'
        CHECK (source IN ('email', 'attachment'));
    ALTER TABLE email_chunks ADD COLUMN attachment_id uuid
        REFERENCES attachments(id) ON DELETE CASCADE;
    CREATE INDEX email_chunks_attachment_idx
        ON email_chunks(user_id, mailbox_connection_id, email_id, attachment_id)
        WHERE source = 'attachment';
    """)


def downgrade():
    op.execute("""
    DELETE FROM email_chunks WHERE source = 'attachment';
    DROP INDEX IF EXISTS email_chunks_attachment_idx;
    ALTER TABLE email_chunks DROP COLUMN IF EXISTS attachment_id;
    ALTER TABLE email_chunks DROP COLUMN IF EXISTS source;
    DROP TABLE IF EXISTS attachments CASCADE;
    """)
