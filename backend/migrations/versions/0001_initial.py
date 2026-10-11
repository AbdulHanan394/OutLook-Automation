"""initial schema with mailbox isolation and pgvector extension"""
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("""
    CREATE TABLE users (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), email text UNIQUE NOT NULL, ms_object_id text, created_at timestamptz NOT NULL DEFAULT now(), deleted_at timestamptz);
    CREATE TABLE sessions (id varchar(128) PRIMARY KEY, user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE, csrf_token varchar(128) NOT NULL, expires_at timestamptz NOT NULL, created_at timestamptz NOT NULL DEFAULT now());
    CREATE TABLE oauth_states (state varchar(128) PRIMARY KEY, user_id uuid REFERENCES users(id) ON DELETE CASCADE, verifier varchar(256) NOT NULL, nonce varchar(128) NOT NULL, purpose varchar(32) NOT NULL, expires_at timestamptz NOT NULL, created_at timestamptz NOT NULL DEFAULT now());
    CREATE TABLE mailbox_connections (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE, ms_tenant_id text NOT NULL, ms_user_id text NOT NULL, account_email text NOT NULL, granted_scopes text NOT NULL, encrypted_refresh_token bytea, status varchar(32) NOT NULL, index_scope jsonb NOT NULL, consented_at timestamptz NOT NULL DEFAULT now(), external_ai_consent_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(), UNIQUE(user_id, ms_tenant_id, ms_user_id));
    CREATE TABLE emails (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE, mailbox_connection_id uuid NOT NULL REFERENCES mailbox_connections(id) ON DELETE CASCADE, graph_message_id text NOT NULL, internet_message_id text, conversation_id text, folder_id text NOT NULL, subject text, sender_name text, sender_address text, recipients jsonb, received_at timestamptz NOT NULL, has_attachments boolean NOT NULL DEFAULT false, body_text text, body_preview text, web_link text, content_hash varchar(64) NOT NULL, is_deleted boolean NOT NULL DEFAULT false, search_tsv tsvector GENERATED ALWAYS AS (setweight(to_tsvector('english', coalesce(subject,'')), 'A') || setweight(to_tsvector('english', coalesce(body_text,'')), 'B')) STORED, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(), UNIQUE(mailbox_connection_id, graph_message_id));
    CREATE INDEX emails_tsv_idx ON emails USING gin(search_tsv); CREATE INDEX emails_mailbox_idx ON emails(mailbox_connection_id, received_at DESC) WHERE NOT is_deleted;
    CREATE TABLE email_chunks (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL, mailbox_connection_id uuid NOT NULL REFERENCES mailbox_connections(id) ON DELETE CASCADE, email_id uuid NOT NULL REFERENCES emails(id) ON DELETE CASCADE, chunk_index int NOT NULL, chunk_text text NOT NULL, content_hash varchar(64) NOT NULL, embedding vector(1024), embedding_model text, embedding_dim int, embedded_at timestamptz, UNIQUE(email_id, chunk_index));
    CREATE INDEX chunks_vec_idx ON email_chunks USING hnsw (embedding vector_cosine_ops); CREATE TABLE indexing_jobs (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL, mailbox_connection_id uuid NOT NULL REFERENCES mailbox_connections(id) ON DELETE CASCADE, type varchar(32) NOT NULL, payload jsonb NOT NULL DEFAULT '{}', status varchar(16) NOT NULL DEFAULT 'queued', attempts int NOT NULL DEFAULT 0, run_after timestamptz NOT NULL DEFAULT now(), progress_done int NOT NULL DEFAULT 0, progress_total int, error_code text, created_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz);
    """)
    for table in ["mailbox_connections", "emails", "email_chunks", "indexing_jobs"]:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY; ALTER TABLE {table} FORCE ROW LEVEL SECURITY; CREATE POLICY {table}_owner ON {table} USING (user_id = current_setting('app.user_id', true)::uuid);")

def downgrade():
    op.execute("DROP TABLE IF EXISTS indexing_jobs, email_chunks, emails, mailbox_connections, oauth_states, sessions, users CASCADE")
