# Per-user workspaces behind ADMIN

Leave the desktop app unchanged. Use `CONNECT_LOG_MULTI_USER=1` only on a separate server instance after an approved ADMIN gateway is ready. Nothing here publishes the app, changes the existing ADMIN listener or link, or migrates desktop drafts.

## ADMIN handoff, no second login

The gateway must authenticate and authorize the existing ADMIN session before proxying the app and its WebSocket. Strip any incoming `X-Connect-Log-Admin-Assertion` supplied by the browser. Add exactly one signed header to the upstream request, using only a stable, unique ADMIN account ID from trusted server state. Never take the ID from a URL, cookie value supplied by the client, form, display name, or student identifier. If the existing ADMIN does not distinguish users, do not turn this mode on.

- Format: `v1.<expiry_epoch_seconds>.<base64url_account_id_without_padding>.<lowercase_hex_hmac>`.
- Sign the ASCII bytes of `v1.<expiry_epoch_seconds>.<base64url_account_id_without_padding>` with HMAC-SHA256 and a dedicated random signing secret (at least 32 bytes in UTF-8) shared securely through `CONNECT_LOG_ADMIN_PROXY_SECRET`. Do not log the assertion or the secret.
- Expiry may not exceed the ADMIN session expiry or eight hours from issuance, whichever is earlier. Keep the Streamlit port on loopback; retain the ADMIN access boundary, HTTPS/XSRF protections and WebSocket revocation controls. An expired session must reopen from ADMIN. Streamlit reads the assertion from the initial request, not from a continuously updated ADMIN session.

## State isolation

The authenticated account gets a stable private directory under `data/admin_workspaces/`. Drafts, uploaded audio, transcripts, audio queue, CSV checkpoints, worker leases, CSV history/results, and personal backups/debug files use that directory. In-progress CSV workers keep the account they were started under. Two sessions of one account share that account's saved state by design. Missing, duplicate, expired or invalid assertions are rejected instead of reading the legacy shared files; an existing Streamlit session cannot switch accounts in place.

The hosted mode uses browser uploads. It does not run desktop-wide download import, recorder cleanup, Drive backup, or the host clipboard, and does not let browser users edit the central Docs mapping or endpoint. Configure required shared mappings on the server. Keep workspace files outside any served static tree and accessible only to the server operator and runtime.

Test with two synthetic signed identities and a rejected unsigned request on a separate server instance. Do not use real students or change ADMIN 8003 to test this feature.
