# Architecture

The frontend only calls the FastAPI service using an HttpOnly session cookie and CSRF header. FastAPI owns OAuth, encrypted refresh tokens, Graph synchronization, deterministic retrieval, the job queue, and provider clients. PostgreSQL stores every mailbox row with a `user_id`; the database policy uses `app.user_id` for RLS.

## Per-user isolation: what actually enforces it

Every user-scoped table has `ENABLE` + `FORCE ROW LEVEL SECURITY` and a policy keyed on `current_setting('app.user_id', true)::uuid`, and every query in `app/` also filters on `user_id` explicitly.

**In the shipped databases the policy layer is inert.** The application connects as `email_finder`, which is created as a superuser by the Postgres image's `POSTGRES_USER`, and superusers bypass RLS even when it is forced:

```sql
select rolname, rolsuper, rolbypassrls from pg_roles where rolname = current_user;
--  email_finder | t | t
```

Isolation therefore rests entirely on the explicit `user_id` predicate in the application code. That is currently sufficient — every handler applies it, and `backend/tests/integration/test_chats.py` exercises the isolation through the real code path — but it is a single layer, and a future query that forgets the predicate would read another user's mail with no error.

To make the second layer real, connect as a non-superuser role and grant it only what the app needs:

```sql
CREATE ROLE email_finder_app LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD '...';
GRANT USAGE ON SCHEMA public TO email_finder_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO email_finder_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO email_finder_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO email_finder_app;
-- then set DATABASE_URL to use email_finder_app
```

`backend/tests/integration/test_chats.py::test_isolation_rests_on_the_explicit_predicate_not_on_rls` skips while the role bypasses RLS and will start asserting instead once it does not.

Graph supplies mailbox data. The embedding client sends only consented chunk text to its configured provider. Claude receives only the minimum retrieved excerpts, enclosed as untrusted `<email>` data; it has no tools or Graph credentials. Citation quotes are validated server-side before an answer is returned.

The worker claims PostgreSQL jobs with `FOR UPDATE SKIP LOCKED`. Redis and a separate vector database are deliberately not used.
