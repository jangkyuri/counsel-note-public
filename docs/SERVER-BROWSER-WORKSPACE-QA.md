# Browser workspaces: 8511 preview

Run the fixture from the test checkout with Streamlit 1.63 and the preview
dependencies:

```powershell
python -B -m scripts.browser_workspace_qa
```

The fixture starts a disposable copy at `http://127.0.0.1:8511/` with no live
mapping or credentials. App HTTP calls are intercepted; the fake ADMIN starts
with `QA-KEEP` and permits one write to the single pending synthetic item.
Do not restart any unrelated listener if this port is occupied.

1. In two different browser profiles, enter `QA-DRAFT-A` and `QA-DRAFT-B` as
   the student names and `QA-BROWSER-A` and `QA-BROWSER-B` as the corresponding
   authors. Press Enter to commit each input.
2. After each seed is reported, reload both. Each must show its own draft.
   A shows 1/2 saves, with a resume button. B shows 1/1 saves.
3. Resume A. A must show 2/2, and the fixture should report one synthetic
   write with the existing notes preserved. Reload again: A stays 2/2 and B
   stays 1/1, with no additional write.

The 8511-only launcher for a separate preview checkout is
`python -B -m scripts.run_browser_workspace_preview`. It requires a dedicated
`CONNECT_LOG_BROWSER_WORKSPACE_SECRET` (at least 32 bytes); keeping the same
secret retains profiles across server restarts. It does not migrate shared
drafts. It sets a first-party cookie with a 30-day lifetime; clearing it
starts an empty workspace. Tabs of the same browser profile share state.
The cookie selects a workspace, not an identity or permission to access
ADMIN. Do not use the loopback-only launcher to expose the app.
