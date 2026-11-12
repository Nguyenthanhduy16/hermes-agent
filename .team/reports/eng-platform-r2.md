# Platform — Round 2 complete

Codex takeover, 2026-11-01.

- Verified existing server-side asset content hashes on every console/legacy JS and CSS URL, including extensions. Only matching hashes get one-year immutable caching; stale/unversioned assets revalidate, duplicate version parameters fail, HTML stays no-store. Gzip preserves the cache contract.
- Retained setup test environment isolation. Added cleanup-backed environment restoration to assistant adapter tests and class cleanups before runtime setup in Hermes assistant integration tests, including setup-failure paths. Package-qualified test invocation now works too.
- README/admin production links are present. No deployment or configuration/secret mutation was performed.

Files completed: `tests/test_assistant_adapter.py`, `tests/test_hermes_assistant.py`. Existing partial changes verified in `admin_web.py`, `tests/test_admin_web.py`, `tests/test_setup.py`, README and production docs.

Full gate, actual counts and logs: [codex-r2-handoff.md](codex-r2-handoff.md). Build includes 16 Admin assets and copies no runtime data or environment values. Preview processes used free temporary ports and were cleaned; existing demos 8790/8799 were untouched.
