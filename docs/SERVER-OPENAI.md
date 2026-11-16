# Server-side OpenAI processing

This is an opt-in update for the original Streamlit app. It replaces local speech processing and the Codex CLI for transcription and summary generation only. The existing summary template, validation, checkpointing, CSV, Docs, and ADMIN save logic are retained.

## Prepare an isolated server instance

1. Follow [SERVER-HANDOFF.md](SERVER-HANDOFF.md) to use a separate private test instance. Keep its fake Docs mapping and blocked ADMIN URL until the test completes.
2. Set `CONNECT_LOG_AI_BACKEND=openai` in the environment of the Streamlit server process. Supply `OPENAI_API_KEY` as a private server environment variable. Do not copy credentials from this PC into the repository or expose them in the browser.
3. Restart only the test Streamlit process. Upload a synthetic recording and check transcription, summary formatting, and progress. Do not test with real student data.
4. Roll back by removing `CONNECT_LOG_AI_BACKEND` and restarting the same process. If API mode is enabled and the key is absent, the app reports a configuration error rather than using local models.

The default models are `gpt-transcribe` for Korean audio and `gpt-5-mini` for summaries. Optional server variables are `CONNECT_LOG_OPENAI_TRANSCRIPTION_MODEL` and `CONNECT_LOG_OPENAI_SUMMARY_MODEL`. The default file transcription model supplies text, not speaker labels or timestamps; the app does not invent them. Do not treat a legacy speaker-separation setting as a permanent substitute. Files over 24 MB are compressed temporarily using server FFmpeg; recordings still above the limit are rejected without truncation. Most original files can be sent as-is. API mode does not require local speech models, a GPU, or a Codex CLI login.

Only the API step handles data differently: audio is sent to OpenAI without the original filename, and the existing summary prompt and transcript are used by the Responses API with `store: false`. This setting is not a guarantee of zero provider retention. Authentication, rate-limit, and connection failures pause a batch without marking an item saved. Establish the academy's authorization and data-handling rules before sending actual student recordings.

For production access, use the existing ADMIN access boundary with no extra account list or app login. Keep Streamlit on loopback; do not open its port or expose 8003 externally. This update does not change any proxy, Apps Script, test Google credentials, or operating links.
