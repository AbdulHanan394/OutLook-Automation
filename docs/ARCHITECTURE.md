# Architecture

The frontend only calls the FastAPI service using an HttpOnly session cookie and CSRF header. FastAPI owns OAuth, encrypted refresh tokens, Graph synchronization, deterministic retrieval, the job queue, and provider clients. PostgreSQL stores every mailbox row with a `user_id`; the database policy uses `app.user_id` for RLS.

Graph supplies mailbox data. The embedding client sends only consented chunk text to its configured provider. Claude receives only the minimum retrieved excerpts, enclosed as untrusted `<email>` data; it has no tools or Graph credentials. Citation quotes are validated server-side before an answer is returned.

The worker claims PostgreSQL jobs with `FOR UPDATE SKIP LOCKED`. Redis and a separate vector database are deliberately not used.
