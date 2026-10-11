# Runbook

## Deployment prerequisites

Use TLS for frontend, API, webhook and database connections. Put every `.env.example` value in the target platform's secret manager, run `alembic upgrade head` as a release step, then start API, worker and frontend containers as non-root. Register both Entra redirect URIs and the public HTTPS Graph webhook URL.

## Entra registration

Use organizational accounts by default, authorization-code flow with PKCE, and delegated `Mail.Read`, `openid`, `profile`, and `offline_access`. Confirm tenant consent policy before use. Do not request write permissions.

## Recovery

If Graph delta state expires, enqueue a full folder sync. Renew subscriptions well before Microsoft expiry. If a refresh token fails, mark the connection `needs_reauth` and require user reconnect.

## Backup and deletion

Database backups require encryption and a documented retention/deletion schedule configured by the hosting provider. Test restore before production. Deleting indexed data removes indexed emails, chunks, vectors and checkpoints; provider backups/log retention are a deployment responsibility. Revoking Entra access is a separate user action in Microsoft account settings.
