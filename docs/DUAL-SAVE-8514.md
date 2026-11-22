# 8514 dual-save deployment

The feature is off unless the 8514 process alone sets
`CONNECT_LOG_DUAL_SAVE_ENABLED=1`. The patch contains no roster, Google Docs
mapping, tokens, recordings or production settings. Do not apply a full source
replacement over the current 8514 API/recordings changes.

1. On the server, identify the exact running 8514 Python and application root.
   Back up its code and launcher configuration, then check the patch on a copy.
   The reference branch is `server-openai-preview-6156062` at `ae3a3c5`.
   If the current build diverges, port the small batch save and progress changes
   without losing the server's existing OpenAI processing or upload persistence.
2. Run the three `tests/test_dual_save*.py` suites with the feature set only
   by tests. They use synthetic targets, not 8003 or live Google Docs.
   Install `requirements-dual-save.txt` only for the candidate 8514 runtime.
3. Without writing a student record, confirm that the current local ADMIN
   server supports `GET /api/attend?date=...`, `GET /api/attendance?date=...`
   and the existing `/api/logs/entry` and `/api/logs/patch` protocol.
   Check current server code for base-notes conflict detection even when the
   original note is blank. There are no 8003 code or configuration changes in
   this patch. Do not enable if that guard is absent.
4. Check that 8514 already reads the academy's private canonical
   `data/students/google_docs_map.csv` (student name and document ID or URL).
   This is the permanent mapping, not a per-batch confirmation file. Use the
   academy-approved server Google OAuth authorized-user credentials with the
   Docs edit scope, outside the repository and available only to the runtime.
   Set its existing protected path as `CONNECT_LOG_DUAL_SAVE_GOOGLE_CREDENTIALS`
   in 8514, never in source or a browser. Do not change document or folder shares.
5. If there is no active or unverified batch that might resume, apply only the
   checked code to 8514, set the flag for that process and restart 8514 only.
   Verify the health and initial screen without uploading a real recording.
   If 8514 cannot start, restore its prior launcher and code. Do not restart
   ADMIN or relay a real pending batch to validate this release.

The feature does not guess when two students attended, the subject is unclear,
the document title and mapping disagree, the same month/day lacks a year, or the
document has no safe table location. It does not automatically create a row
when there is no empty template slot. It adds text only, and uses the current
Google Docs revision to avoid concurrent replacement. A lost write response
causes another read, not a blind write. After restart, the existing retry action
can reuse the prepared summary and skip an already verified destination.

Recording replay state lives in each existing private workspace's
`data/drafts/recording_replay.sqlite3` path. Keep that database with the workspace
across restarts and releases. A matching file SHA-256, exact normalized student
name and counseling date reuse the first prepared summary; the first resolved
SID, subject and Docs ID are then pinned. A changed summary cannot trigger a
second automatic write. The record stores destination confirmations separately
and does not contain audio bytes or credentials. It does not backfill completed
uploads made before the replay database was introduced. Use a fresh synthetic
recording once, then upload those identical bytes again to verify replay.
Include `tests/test_recording_replay.py` in isolated QA.

## Existing Apps Script transport (no new OAuth app)

The opt-in `clg8514-v1` action is distinct from the existing app's save and
verify actions. Keep the live script's existing version and behavior. Port only
the dispatch hunk to the live script source after comparing the current
deployment, and add `scripts/google_apps_script_8514_append.js` as a separate
Apps Script source file. Do not replace the live script with this repository's
older base copy. Verify the signed route on an isolated deployment before
updating the existing production deployment in place. Never make a QA
deployment of the unrestricted legacy save handler.

* Script Properties: configure `CLG8514_APPEND_SECRET` (64 random lower-case hex
  characters) and `CLG8514_APPEND_ALLOWED_DOC_IDS` (JSON string array of exact
  document IDs). In QA, include only the dedicated synthetic Doc. Absent
  properties reject the new action before opening any document.
* 8514 only: set `CONNECT_LOG_DUAL_SAVE_APPS_SCRIPT_URL` to the corresponding
  existing HTTPS Apps Script exec URL and
  `CONNECT_LOG_DUAL_SAVE_APPS_SCRIPT_SECRET_FILE` to the protected file
  containing the same secret. Keep the secret out of Git, URLs and logs.
  This transport does not use `CONNECT_LOG_DUAL_SAVE_GOOGLE_CREDENTIALS`.
  An incomplete Apps Script configuration does not fall back to the old writer.
* Do not alter 8003. If allowed on this installation, permit only the existing
  internal ADMIN endpoints and the configured Google Apps Script origin plus the
  same Google Content Service response origin. Do not forward or replay a
  signed POST to a redirect target.
* Run `node scripts/check_google_apps_script_8514_append.js` and
  `tests/test_dual_save_script.py`, plus the original script regression checks.
  Use a brand-new synthetic record with existing progress and homework:
  read, request append, read, repeat the request, and read again. Verify the
  existing text and formatting and the new record count `0 -> 1 -> 1`.

The new mode only appends paragraphs. It never calls the legacy replace-cell
operation. It fails closed on an unknown year, subject or document name,
multiple candidates, no safe empty row, or a known change to the target row.
A follow-up read is required before completion. The Google Docs synthetic
readback done through the connector checks the live document contents, but is
not a test of the authorized Apps Script transport. Do not conflate the two.

Google Docs write contract:
https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/batchUpdate
