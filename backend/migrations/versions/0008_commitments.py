"""commitment extraction, thread references, and per-user Claude quotas"""
from alembic import op

revision = "0008_commitments"
down_revision = "0007_thread_summaries"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    ALTER TABLE emails ADD COLUMN folder_alias text;
    ALTER TABLE emails ADD COLUMN sent_at timestamptz;
    ALTER TABLE emails ADD COLUMN commitment_extracted_at timestamptz;
    CREATE INDEX emails_commitment_backfill_idx
        ON emails(user_id, mailbox_connection_id, folder_alias, sent_at DESC)
        WHERE commitment_extracted_at IS NULL AND is_deleted = false;

    CREATE TABLE commitments (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        email_id uuid NOT NULL REFERENCES emails(id) ON DELETE CASCADE,
        thread_id text,
        text text NOT NULL,
        quote text NOT NULL,
        quote_hash varchar(64) NOT NULL,
        due_date date,
        due_text text,
        status varchar(16) NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'done', 'dismissed')),
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE(email_id, quote_hash)
    );
    CREATE INDEX commitments_user_status_created_idx
        ON commitments(user_id, status, created_at DESC);
    ALTER TABLE commitments ENABLE ROW LEVEL SECURITY;
    ALTER TABLE commitments FORCE ROW LEVEL SECURITY;
    CREATE POLICY commitments_owner ON commitments
        USING (user_id = current_setting('app.user_id', true)::uuid)
        WITH CHECK (user_id = current_setting('app.user_id', true)::uuid);

    CREATE TABLE claude_daily_usage (
        user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        usage_date date NOT NULL,
        call_count integer NOT NULL DEFAULT 0 CHECK (call_count >= 0),
        updated_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY(user_id, usage_date)
    );
    ALTER TABLE claude_daily_usage ENABLE ROW LEVEL SECURITY;
    ALTER TABLE claude_daily_usage FORCE ROW LEVEL SECURITY;
    CREATE POLICY claude_daily_usage_owner ON claude_daily_usage
        USING (user_id = current_setting('app.user_id', true)::uuid)
        WITH CHECK (user_id = current_setting('app.user_id', true)::uuid);
    """)


def downgrade():
    op.execute("""
    DROP TABLE IF EXISTS claude_daily_usage CASCADE;
    DROP TABLE IF EXISTS commitments CASCADE;
    DROP INDEX IF EXISTS emails_commitment_backfill_idx;
    ALTER TABLE emails DROP COLUMN IF EXISTS commitment_extracted_at;
    ALTER TABLE emails DROP COLUMN IF EXISTS sent_at;
    ALTER TABLE emails DROP COLUMN IF EXISTS folder_alias;
    """)
