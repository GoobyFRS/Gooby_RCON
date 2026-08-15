## Plan: Audit app.py + write docs/audit_1.md, queue remediation

TL;DR: Audit `app.py` against AGENTS.md standards (security, quality, maturity), write a severity-ranked findings doc to `docs/audit_1.md` (currently empty), then execute a phased remediation plan (Critical/High first) once approved.

**Steps**
1. Write `docs/audit_1.md` containing: overview, severity-ranked findings (Critical/High/Medium/Low) each with issue, risk, and recommended fix; plus a phased remediation roadmap.
2. Phase 1 (High — correctness/security), *depends on 1 (doc approved)*:
   - Fix race condition: worker threads call `self._rcon.command(cmd)` but `_disconnect()` can set `self._rcon = None` concurrently from UI thread → capture a local reference to the client at command-issue time instead of reading `self._rcon` inside the thread.
   - Handle Source RCON multi-packet fragmented responses in `RCONClient._recv`/`_send` (large outputs like player lists can span multiple packets); current single-packet read can truncate/desync the stream.
   - Make `SETTINGS_FILE`/`LOG_FILE` paths stable regardless of CWD (use `Path(sys.argv[0]).resolve().parent` or platform user-config dir) so behavior is consistent for `python app.py` vs. frozen PyInstaller exe.
3. Phase 2 (Medium — quality), *parallel with Phase 1, depends on 1*:
   - Add type hints to all public methods on `GoobyRCON`/`OptionsDialog`.
   - Add Google-style docstrings to public classes/functions per AGENTS.md.
   - Split `_build_ui` (~130 lines) into smaller helpers (`_build_status_bar`, `_build_console`, `_build_input_row`) to satisfy the ~50-60 line function guideline.
   - Add `RotatingFileHandler` (bounded size + backup count) instead of unbounded `FileHandler`.
   - Narrow broad `except Exception` in `load_settings`/`save_settings` to `OSError`/`json.JSONDecodeError`.
   - Decide: implement auto-reconnect using the unused `RECONNECT_DELAY` constant, or remove it (dead code) — ask user.
4. Phase 3 (Low — polish), *depends on 1*:
   - Move `logging.basicConfig(...)` out of module import time into a `setup_logging()` called from the `if __name__ == "__main__":` guard.
   - Add a short comment clarifying the `PACKET_COMMAND == PACKET_AUTH_RESP == 2` protocol quirk.
5. Phase 4 (Optional, needs explicit user approval — new deps/scope):
   - Add `pytest` + `tests/test_rcon_client.py` covering `RCONClient` packet encode/decode against a fake socket (pure, testable logic).
   - Optional: OS keyring–backed password storage instead of plaintext JSON (`keyring` dependency) — flag only, do not add without explicit ask.

**Relevant files**
- `app.py` — all fixes above; classes `RCONClient`, `GoobyRCON`, `OptionsDialog`; functions `load_settings`/`save_settings`.
- `docs/audit_1.md` — audit findings + remediation roadmap document (currently empty, to be filled).
- `requirements.txt` — only touch if Phase 4 (pytest/keyring) is approved.

**Verification**
1. Manual: run `python app.py`, connect to a real/test RCON server, send a command with a long response (e.g., player list) and confirm no truncation after the fragmentation fix.
2. Manual: trigger disconnect mid-command (stop server while a command is in flight) to confirm no `AttributeError`/crash after the race-condition fix.
3. If Phase 4 approved: run `pytest -q` for new `RCONClient` tests.
4. Confirm settings/log files resolve to the same location whether run as `python app.py` or as the built `Gooby_RCON.exe`.
5. Confirm `gooby_rcon.log` stops growing unbounded (rotates) after a stress run.

**Decisions**
- Findings will be severity-ranked (Critical/High/Medium/Low) with concrete remediation steps, per user preference.
- This plan includes a queued implementation phase; audit doc is written first, then fixes proceed in priority order after user approval per phase.
- No Critical-severity issues identified (no RCE/eval/exec/shell=True/SQL injection); highest severity is High (race condition, protocol fragmentation, path handling).
- Settings file (`gooby_rcon_settings.json`) already correctly gitignored — plaintext password-at-rest risk noted but not force-fixed (Phase 4 optional, needs approval since it adds a dependency).

**Further Considerations**
1. `RECONNECT_DELAY` is currently dead code (defined, unused) — implement auto-reconnect or remove it? Recommend removing unless auto-reconnect is a wanted feature.
2. Add automated tests (Phase 4) — recommend yes, since `RCONClient` packet logic is pure and easy to unit test without a live server, and user wants a "mature" codebase to build on.
3. Optional keyring-based password storage — recommend flagging in the doc as a future consideration only, not implementing now (avoids adding a dependency not explicitly requested).
