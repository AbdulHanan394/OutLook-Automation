"""sync checkpoints and privacy-preserving search logs"""
from alembic import op

revision = "0002_sync_observability"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

def upgrade():
    op.execute("""
    CREATE TABLE sync_states (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL, mailbox_connection_id uuid NOT NULL REFERENCES mailbox_connections(id) ON DELETE CASCADE, folder_id text NOT NULL, folder_name text NOT NULL, delta_link text, next_link text, full_sync_required boolean NOT NULL DEFAULT true, last_success_at timestamptz, subscription_id text, subscription_expires_at timestamptz, UNIQUE(mailbox_connection_id, folder_id));
    CREATE TABLE search_logs (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL, feature text NOT NULL CHECK(feature IN ('find','ask')), latency_ms int, result_count int, confidence text, claude_used boolean NOT NULL DEFAULT false, model text, input_tokens int, output_tokens int, embed_tokens int, created_at timestamptz NOT NULL DEFAULT now());
    ALTER TABLE sync_states ENABLE ROW LEVEL SECURITY; ALTER TABLE sync_states FORCE ROW LEVEL SECURITY; CREATE POLICY sync_states_owner ON sync_states USING (user_id = current_setting('app.user_id', true)::uuid);
    ALTER TABLE search_logs ENABLE ROW LEVEL SECURITY; ALTER TABLE search_logs FORCE ROW LEVEL SECURITY; CREATE POLICY search_logs_owner ON search_logs USING (user_id = current_setting('app.user_id', true)::uuid);
    CREATE INDEX jobs_claim_idx ON indexing_jobs(status, run_after);
    """)
def downgrade():
    op.execute("DROP TABLE IF EXISTS search_logs, sync_states CASCADE")
