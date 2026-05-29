# Changelog

All notable changes to this project are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [17.0.0] - 2026-05-29

### Added
- **Packaging**: project is now pip-installable and publishable — added
  `[build-system]` (setuptools), package discovery, modern SPDX license metadata,
  a `LICENSE` file, keywords, and richer trove classifiers.
- Real-git integration tests for all three integration strategies
  (`cherry-pick` / `merge` / `rebase`) that mock only the integrator agent, so
  a wrong git completion/abort command surfaces as a genuine failure.
- Consistency test locking the embedded sandbox token checker
  (`_CHECK_TOKENS_STATIC`) to the in-process `_check_tokens`, preventing silent
  drift between the two copies.

### Fixed
- **[HIGH]** `rebase` integration strategy resolved conflicts with
  `git rebase --continue`, but it replays commits via `git cherry-pick`; with no
  rebase in progress this always failed and left a dangling cherry-pick that
  cascaded into the next task. Now completes via `git cherry-pick --continue`.
  (The bug had been masked by over-mocked tests.)
- `EventParser` no longer crashes the streaming loop on malformed
  `tool_result` payloads (e.g. a list of plain strings); unexpected event shapes
  degrade to a raw text event instead of failing the whole task.
- Config precedence: an explicit CLI value equal to the built-in default
  (e.g. `--strategy cherry-pick`, `--jobs 4`) is no longer silently overridden by
  a differing config-file value. Introduced an `UNSET` sentinel to distinguish
  "not provided" from "provided default".
- Fixed an incorrect repository URL in package metadata.

### Changed
- README now documents accurate install paths (pipx / `git+https` / clone) and
  build/test commands; version/badges/module map updated.
- Documented that the `merge` strategy's `branch -f`/`branch -D` calls are
  best-effort no-ops while worker worktrees are still checked out.

### Quality
- 792 tests passing, mypy clean (26 files), ~88% coverage, zero RuntimeWarnings.

## [16.0.0] - 2026-05-27

### Added
- Dashboard token authentication for the WebSocket/HTTP server (`?token=...`).
- Coverage raised across five modules (64–71% → 82–97%).

### Fixed
- `_validate_cmd_str` now rejects `$(...)` command substitution.
- WebSocket close frames send RFC 6455 status codes; Windows process-tree kill;
  PID-reuse protection on the run lock; `enable_ansi()` returns a bool.
- Many silently-swallowed exceptions now log a warning.

## [15.0.0] - 2026-05-27

### Changed
- Performance: WebSocket XOR masking ~18×, JSON serialization ~4×,
  `prepare_sandbox` ~4.2×, and reduced memory footprint. 704 tests.

## [14.0.0] - 2026-05-27

### Fixed
- WebSocket `readexactly` framing, `_extract_section` exact matching, atomic
  memory writes, and I/O throttle race fixes (8 security & bug fixes). 700 tests.

## [13.0.0] - 2026-05-26

### Fixed
- 5 security and architecture fixes; integrator coverage to 92%. 675 tests.

## [12.0.0] - 2026-05-25

### Fixed
- Comprehensive bug-fix and code-review pass (14 fixes). 613 tests, 80% coverage.

## [9.0.0 – 11.0.0] - 2026-05-23 / 24

- Iterative security hardening, reliability, performance, and coverage work
  across multiple full-project review rounds. See `ARCHIVE.md` and `PLAN.md`
  for the detailed phase-by-phase history.

## [1.0.0 – 8.0.0]

- Initial implementation through the first several review cycles: concurrent
  dispatcher, git-worktree isolation, dependency graph scheduling, three
  integration strategies, safety sandbox, live dashboard, and cross-platform
  support. Detailed history in `ARCHIVE.md`.

[17.0.0]: https://github.com/Wayne-Lee-cs/multi-agent-codeflow/releases/tag/v17.0.0
[16.0.0]: https://github.com/Wayne-Lee-cs/multi-agent-codeflow/releases/tag/v16.0.0
