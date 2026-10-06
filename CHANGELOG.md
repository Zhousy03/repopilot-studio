# Changelog

## Unreleased

- Simplified Chinese interface copy and moved advanced runtime details into expandable sections.
- Added mobile file/workspace drawers, source-reference access, keyboard focus handling and question starters.
- Connection checks now use the current form and selected adapter without saving credentials or requiring source-upload consent.
- Added clear syntax/test summaries, conditional Docker consent and unavailable-environment controls.
- Prevented offline review/change requests and duplicate form submissions; fixed empty-workspace cleanup and history-search races.
- Queued runs keep their submitted model configuration; remote HTTP clients respect environment proxy settings.
- Queued task cancellation now finishes the saved record and releases queue capacity.
- Replaced browser API bridging with real local HTTP/SSE verification and added release packaging with credential scans.
- Removed runtime databases, saved settings, caches, installation metadata and obsolete validation artifacts from the release tree.

## 1.1.0

- Repository import from GitHub, local folders and ZIP archives.
- Immutable source versions, bounded code investigation and streamed model responses.
- Reviewed change proposals, patch exports, inverse proposals and draft GitHub pull requests.
- Static checks and fixed Docker test profiles.
- Local application persistence, optional LangGraph and PostgreSQL checkpoints.
