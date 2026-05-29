# Contributing to cagent

Thanks for your interest in improving cagent! This guide covers the local setup
and the checks your change must pass.

## Development setup

Requires Python ≥ 3.11 and Git.

```bash
git clone https://github.com/Wayne-Lee-cs/multi-agent-codeflow.git
cd multi-agent-codeflow
python -m pip install -e ".[dev]"
```

This installs the dev extras: `mypy`, `pytest`, `pytest-asyncio`, `pytest-cov`,
and `build`. The runtime itself has **zero third-party dependencies** — please
keep it that way (standard library only).

## Required checks

Your change must pass all three before it can be merged (CI runs the same on
Linux and Windows for Python 3.11 and 3.12):

```bash
python -m pytest --cov=cagent --cov-report=term-missing   # tests + coverage gate (fail_under = 78)
python -m mypy cagent/                                     # type check (0 errors)
python -m build                                            # wheel + sdist must build
```

Tests must also be free of `RuntimeWarning`:

```bash
python -m pytest -W error::RuntimeWarning
```

## Guidelines

- **Match the surrounding style.** See [`conventions.md`](conventions.md) for the
  project's coding conventions.
- **Cross-platform first.** cagent supports Windows and Unix; guard
  platform-specific code (`sys.platform`) and prefer the `cagent.compat` helpers.
- **Don't over-mock.** For git-touching logic, prefer real-git tests against a
  temporary repository (see `tests/test_integrator.py::TestStrategiesRealGitConflict`
  and the `tmp_repo` fixture). Over-mocking has hidden real bugs before.
- **Keep the safety sandbox in sync.** If you edit `_check_tokens` in
  `cagent/safety.py`, update the embedded `_CHECK_TOKENS_STATIC` copy too — the
  consistency test will fail otherwise (that's intentional).
- **Add tests** for new behavior and bug fixes; add an entry to
  [`CHANGELOG.md`](CHANGELOG.md) under a new "Unreleased" section.

## Submitting changes

1. Create a feature branch.
2. Make your change with tests and a changelog entry.
3. Ensure the three required checks pass locally.
4. Open a pull request against `master` describing the change and its rationale.

## Reporting bugs

Open an issue at
<https://github.com/Wayne-Lee-cs/multi-agent-codeflow/issues> with the command
you ran, what you expected, what happened, and your OS / Python / `cagent --version`.
