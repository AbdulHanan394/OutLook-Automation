"""store read/importance/flag/categories for the mail client and add ask history"""
from alembic import op

revision = "0010_mail_client"
down_revision = "0009_attachment_search"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    ALTER TABLE emails ADD COLUMN is_read boolean NOT NULL DEFAULT true;
    ALTER TABLE emails ADD COLUMN importance varchar(16) NOT NULL DEFAULT 'normal'
        CHECK (importance IN ('low', 'normal', 'high'));
    ALTER TABLE emails ADD COLUMN is_flagged boolean NOT NULL DEFAULT false;
    ALTER TABLE emails ADD COLUMN categories JSONB NOT NULL DEFAULT '[]'::jsonb;
    -- Sanitised at ingest, never raw. The reading pane renders this inside a
    -- sandboxed iframe; nothing untrusted is ever written to the database.
    ALTER TABLE emails ADD COLUMN body_html text;

    -- Cursor pagination for the message list: (owner, folder, newest first, id
    -- tiebreak). The list endpoint never calls Graph, so this composite index is
    -- the only thing standing between a 5k-message folder and a sequential scan.
    CREATE INDEX emails_folder_list_idx
        ON emails(user_id, mailbox_connection_id, folder_alias, received_at DESC, id DESC);
    -- Unread counts per folder are computed by the folder endpoint on every open.
    CREATE INDEX emails_folder_unread_idx
        ON emails(user_id, mailbox_connection_id, folder_alias)
        WHERE is_read = false AND is_deleted = false;

    CREATE TABLE ask_history (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        query text NOT NULL,
        mode varchar(8) NOT NULL DEFAULT 'auto'
            CHECK (mode IN ('auto', 'find', 'ask')),
        created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX ask_history_owner_recent_idx
        ON ask_history(user_id, created_at DESC);
    ALTER TABLE ask_history ENABLE ROW LEVEL SECURITY;
    ALTER TABLE ask_history FORCE ROW LEVEL SECURITY;
    CREATE POLICY ask_history_owner ON ask_history
        USING (user_id = current_setting('app.user_id', true)::uuid)
        WITH CHECK (user_id = current_setting('app.user_id', true)::uuid);
    """)


def downgrade():
    op.execute("""
    DROP TABLE IF EXISTS ask_history;
    DROP INDEX IF EXISTS emails_folder_unread_idx;
    DROP INDEX IF EXISTS emails_folder_list_idx;
    ALTER TABLE emails DROP COLUMN IF EXISTS categories;
    ALTER TABLE emails DROP COLUMN IF EXISTS body_html;
    ALTER TABLE emails DROP COLUMN IF EXISTS is_flagged;
    ALTER TABLE emails DROP COLUMN IF EXISTS importance;
    ALTER TABLE emails DROP COLUMN IF EXISTS is_read;
    """)