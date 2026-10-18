"""conversations, messages and per-conversation context

Every table carries ``user_id`` and the same forced row-level policy as the rest
of the schema: chat history is derived from private email, so it is readable and
deletable only by the user who owns it.
"""
from alembic import op

revision = "0011_conversations"
down_revision = "0010_mail_client"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE conversations (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        mailbox_connection_id uuid REFERENCES mailbox_connections(id) ON DELETE CASCADE,
        title varchar(120) NOT NULL DEFAULT 'New chat',
        pinned boolean NOT NULL DEFAULT false,
        archived boolean NOT NULL DEFAULT false,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
    );
    -- The sidebar is "pinned first, then most recently touched"; this serves it
    -- without a sort at request time.
    CREATE INDEX conversations_owner_recent_idx
        ON conversations(user_id, pinned DESC, updated_at DESC);
    ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;
    ALTER TABLE conversations FORCE ROW LEVEL SECURITY;
    CREATE POLICY conversations_owner ON conversations
        USING (user_id = current_setting('app.user_id', true)::uuid)
        WITH CHECK (user_id = current_setting('app.user_id', true)::uuid);

    CREATE TABLE messages (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        conversation_id uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        role varchar(16) NOT NULL CHECK (role IN ('user', 'assistant')),
        -- Plain text for rendering and for full-text search. Never holds an
        -- email body: citations store ids and quoted passages only.
        text text NOT NULL DEFAULT '',
        content_json jsonb NOT NULL DEFAULT '{}'::jsonb,
        citations_json jsonb NOT NULL DEFAULT '[]'::jsonb,
        tool_trace_json jsonb NOT NULL DEFAULT '[]'::jsonb,
        input_tokens integer NOT NULL DEFAULT 0,
        output_tokens integer NOT NULL DEFAULT 0,
        search_tsv tsvector GENERATED ALWAYS AS (
            setweight(to_tsvector('english', coalesce(text, '')), 'A')
        ) STORED,
        created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX messages_conversation_created_idx
        ON messages(conversation_id, created_at);
    -- History search matches a word from anywhere in the chat, not just the
    -- title, so the index has to live on the messages.
    CREATE INDEX messages_search_idx ON messages USING GIN (search_tsv);
    ALTER TABLE messages ENABLE ROW LEVEL SECURITY;
    ALTER TABLE messages FORCE ROW LEVEL SECURITY;
    CREATE POLICY messages_owner ON messages
        USING (user_id = current_setting('app.user_id', true)::uuid)
        WITH CHECK (user_id = current_setting('app.user_id', true)::uuid);

    CREATE TABLE conversation_context (
        conversation_id uuid PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE,
        user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        -- Email ids only. Bodies are re-read from `emails` on demand so a
        -- deleted email simply stops resolving.
        working_set_json jsonb NOT NULL DEFAULT '[]'::jsonb,
        summary_text text,
        summary_upto_message_id uuid REFERENCES messages(id) ON DELETE SET NULL,
        updated_at timestamptz NOT NULL DEFAULT now()
    );
    ALTER TABLE conversation_context ENABLE ROW LEVEL SECURITY;
    ALTER TABLE conversation_context FORCE ROW LEVEL SECURITY;
    CREATE POLICY conversation_context_owner ON conversation_context
        USING (user_id = current_setting('app.user_id', true)::uuid)
        WITH CHECK (user_id = current_setting('app.user_id', true)::uuid);
    """)


def downgrade():
    op.execute(
        "DROP TABLE IF EXISTS conversation_context, messages, conversations CASCADE"
    )