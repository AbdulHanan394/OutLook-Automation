"""backfill email conversation and normalized-content grouping keys"""
from alembic import op

revision = "0006_thread_grouping"
down_revision = "0005_search_result_feedback"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    ALTER TABLE emails ADD COLUMN thread_key text;
    ALTER TABLE emails ADD COLUMN normalized_content_hash varchar(64);
    UPDATE emails
    SET thread_key = NULLIF(conversation_id, ''),
        normalized_content_hash = CASE
            WHEN NULLIF(btrim(concat_ws(' ', coalesce(subject, ''), coalesce(body_text, ''))), '') IS NULL THEN NULL
            ELSE encode(digest(
                lower(regexp_replace(btrim(concat_ws(' ', coalesce(subject, ''), coalesce(body_text, ''))), '[[:space:]]+', ' ', 'g')),
                'sha256'
            ), 'hex')
        END;
    CREATE INDEX emails_thread_key_idx ON emails(thread_key) WHERE thread_key IS NOT NULL;
    CREATE INDEX emails_normalized_hash_idx ON emails(normalized_content_hash) WHERE normalized_content_hash IS NOT NULL;
    """)


def downgrade():
    op.execute("""
    DROP INDEX IF EXISTS emails_thread_key_idx;
    DROP INDEX IF EXISTS emails_normalized_hash_idx;
    ALTER TABLE emails DROP COLUMN IF EXISTS thread_key;
    ALTER TABLE emails DROP COLUMN IF EXISTS normalized_content_hash;
    """)
