# Private test Docs OAuth

These optional settings are for an isolated QA Streamlit process only. Do not set them on the production app.

- `CONNECT_LOG_DOCS_QA_URL`: the exact owner-only QA Apps Script address ending in `/exec`. Use the same address in the app's existing test Docs settings.
- `CONNECT_LOG_DOCS_QA_ID`: the one synthetic QA document ID. The test mapping must point to that document only.
- `CONNECT_LOG_DOCS_QA_BEARER_TOKEN`: a short-lived Google OAuth **access token** from an already approved server authentication flow for the same academy account that approved the private deployment. Supply it securely to the QA process, not in Git, logs, shell command arguments, or chat. This change does not grant consent, store tokens, open the deployment, or refresh tokens. Stop if no approved server flow is available.

With QA settings present, requests to other documents and URLs fail before being sent. The original app sends the token both on save and on verify-only readback; the Content Service response redirect is followed without sending the token again. With no QA settings, the existing transport is unchanged.

Keep the test server on loopback, use only synthetic data, and leave production configuration, 8003, and production links alone. OAuth acceptance is only the first check. The QA Apps Script's fixture restrictions still apply to the final summary **after** the app formats it. If that fixture guard rejects a request, report it separately without enabling production targets or relaxing the test document restriction.
