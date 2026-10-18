"""memory_notes: things the assistant learns about how the reader wants answers.

A memory is a short, human-readable statement the reader can see, edit and
delete -- never an inferred profile. That is the whole design: a note is only
written when the reader asks for it in so many words, it says what was asked
for, and the reader can remove it. Nothing is inferred from behaviour.

Carries ``mailbox_connection_id`` and cascades with the mailbox for the same
reason chat history does: a preference learned from someone's mail must not
outlive the mail.
"""
from alembic import op

revision = "0012_memory_notes"
down_revision = "0011_conversations"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE memory_notes (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        mailbox_connection_id uuid REFERENCES mailbox_connections(id) ON DELETE CASCADE,
        -- Where the note came from. "preference" is the only kind today; the
        -- column exists so the reader's own list can group them later without a
        -- migration.
        kind varchar(24) NOT NULL DEFAULT 'preference'
            CHECK (kind IN ('preference')),
        -- The reader's own words, trimmed. Never derived from their mail.
        note_text varchar(400) NOT NULL,
        -- The turn that asked for it, so the note can be traced or revoked.
        source_message_id uuid REFERENCES messages(id) ON DELETE SET NULL,
        active boolean NOT NULL DEFAULT true,
        use_count integer NOT NULL DEFAULT 0,
        created_at timestamptz NOT NULL DEFAULT now(),
        last_used_at timestamptz
    );
    CREATE INDEX memory_notes_owner_active_idx
        ON memory_notes(user_id, active, created_at DESC);
    ALTER TABLE memory_notes ENABLE ROW LEVEL SECURITY;
    ALTER TABLE memory_notes FORCE ROW LEVEL SECURITY;
    CREATE POLICY memory_notes_owner ON memory_notes
        USING (user_id = current_setting('app.user_id', true)::uuid)
        WITH CHECK (user_id = current_setting('app.user_id', true)::uuid);
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS memory_notes CASCADE")