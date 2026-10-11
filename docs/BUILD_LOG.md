# Build log

## 2026-10-10 — initial build

- Defaults: organizational Microsoft accounts, `Mail.Read` delegated scope, Inbox/Sent Items, 365-day lookback, English full-text, 1024-dimensional `voyage-4`, RRF 60, 20 candidates and five alternatives.
- Provider work is feature-gated by explicit mailbox AI consent. No real email content was sent to external services.
- Graph delta parameters and immutable-ID message behavior were checked against Microsoft Graph documentation. The implementation uses immutable-ID preference and persists opaque delta links in the intended ingestion layer.
- Open human decisions: personal-account support; tenant/admin consent; legal approval for sending email excerpts to Anthropic and Voyage; target hosting; expected mailbox sizes; real/anonymized evaluation queries; target confidence accuracy and false-confident limit.
- Deviation: the first repository pass provides a runnable foundation and deterministic search API, but live OAuth exchange, complete Graph delta ingestion, semantic ranking, subscriptions, full RAG, comprehensive integration/e2e tests, and final deployment verification still require implementation and provider/tenant inputs. They must not be represented as verified.
- Verification performed: `python -m compileall backend/app eval` passed. A local Next build was attempted after `npm install`, but the local package manager did not materialize the `next` executable or a lockfile, so the frontend production build is **not verified** in this environment. CI performs a clean npm installation and build.
- Added unverified implementation paths for Graph delta ingestion, chunk replacement, embedding jobs, pgvector model guarding, Claude JSON wrapper, verified citations, and token key rotation. `python -m compileall backend/app backend/tests eval scripts` passed after this addition. Database migration and provider calls remain unexecuted locally because no configured PostgreSQL database or provider credentials were supplied.
