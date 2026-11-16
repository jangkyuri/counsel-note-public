# Offline server verification

Run these checks from a separate checkout of the test branch using its Python environment with the app dependencies and pytest. No login, credentials, listening ports, or test Google document are needed.

```powershell
python -B -m pytest -q tests/test_offline_server_qa.py tests/test_admin_workspace.py tests/test_docs_qa_auth.py tests/test_connect_log_openai.py
```

On Windows, if the default temporary path is too long, pass `--basetemp` with a short, new temporary directory that does not already exist.

The tests create a disposable app with fake configuration and block connections. They check that two signed synthetic ADMIN users see only their own drafts, transcript text, audio queue and CSV progress; reopening restores the same user's state. Missing identity and a switched identity in an existing session must be denied. A second test runs two CSV workers at the same time, checks separate leases and checkpoints, and verifies a simulated lost save response without sending a second write.

The restart checks reuse a persisted interrupted batch. They leave the already confirmed row alone and read the unfinished row first; a repeat write is permitted only when the fake document confirms that row is missing.

Passing does not confirm the real ADMIN gateway's user identity handoff, server OpenAI access, or Google OAuth. Before changing any operating links, verify those separately with the approved server credentials and synthetic-only test destinations. Do not reuse a synthetic signing key, touch the 8003 listener, or connect production Docs from this test.
