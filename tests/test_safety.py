"""Unit tests for cagent/safety.py — regex pattern matching and sandbox injection."""

import json
import re
from pathlib import Path

import pytest

from cagent.safety import DENY_PATTERNS, prepare_sandbox


class TestDenyPatterns:
    """Test that DENY_PATTERNS correctly intercepts dangerous commands."""

    @pytest.mark.parametrize("cmd", [
        "git push",
        "git push origin main",
        "git push -u origin HEAD",
        "  git push origin main",
        "cd /tmp && git push",
        "mkdir dir && cd dir && git push origin main",
    ])
    def test_git_push_blocked(self, cmd):
        assert any(re.search(p, cmd, re.IGNORECASE) for p in DENY_PATTERNS)

    @pytest.mark.parametrize("cmd", [
        "git reset --hard",
        "git reset --hard HEAD~1",
        "  git reset --hard origin/main",
    ])
    def test_git_reset_hard_blocked(self, cmd):
        assert any(re.search(p, cmd, re.IGNORECASE) for p in DENY_PATTERNS)

    @pytest.mark.parametrize("cmd", [
        "git clean -f",
        "git clean -fd",
        "git clean -fdx",
        "git clean -f -d",
    ])
    def test_git_clean_blocked(self, cmd):
        assert any(re.search(p, cmd, re.IGNORECASE) for p in DENY_PATTERNS)

    @pytest.mark.parametrize("cmd", [
        "rm -rf /tmp/test",
        "rm -fr /tmp/test",
        "rm -Rf /tmp/test",
        "rm -fR /tmp/test",
        "rm -rf .",
        "rm -r /tmp/test",
        "rm -R /tmp/test",
        "rm -r .",
        "rm -r/tmp/test",         # no space before path
        "rm -rf/tmp/test",        # no space before path
        "rm --recursive --force /tmp/test",
        "rm --recursive -f /tmp/test",
    ])
    def test_rm_recursive_blocked(self, cmd):
        assert any(re.search(p, cmd, re.IGNORECASE) for p in DENY_PATTERNS)

    @pytest.mark.parametrize("cmd", [
        "git update-ref refs/heads/main abc123",
    ])
    def test_git_update_ref_blocked(self, cmd):
        assert any(re.search(p, cmd, re.IGNORECASE) for p in DENY_PATTERNS)

    @pytest.mark.parametrize("cmd", [
        "git remote set-url origin https://x",
        "git remote add upstream https://x",
    ])
    def test_git_remote_blocked(self, cmd):
        assert any(re.search(p, cmd, re.IGNORECASE) for p in DENY_PATTERNS)

    @pytest.mark.parametrize("cmd", [
        "Remove-Item -Recurse -Force C:\\temp",
        "Remove-Item -Force -Recurse C:\\temp",
        "del /s /q C:\\temp",
        "rd /s /q C:\\temp",
        "del /S C:\\temp",
    ])
    def test_windows_commands_blocked(self, cmd):
        assert any(re.search(p, cmd, re.IGNORECASE) for p in DENY_PATTERNS)

    @pytest.mark.parametrize("cmd", [
        "bash -c 'git push'",
        "bash -c \"rm -rf /\"",
        "sh -c 'git reset --hard'",
        "python -c \"import subprocess; subprocess.run(['git','push'])\"",
        "python3 -c \"import subprocess; subprocess.run(['git','push'])\"",
        "echo 'test' | sh",
        "echo 'test' | bash",
    ])
    def test_command_chain_blocked(self, cmd):
        assert any(re.search(p, cmd, re.IGNORECASE) for p in DENY_PATTERNS)

    @pytest.mark.parametrize("cmd", [
        "git add .",
        "git commit -m 'test'",
        "git status",
        "git diff",
        "rm -f file.txt",
        "ls -la",
        "cat file.txt",
        "git pushpin",  # word boundary check
        "python script.py",  # not python -c
        "echo hello",  # no pipe to sh
    ])
    def test_safe_commands_allowed(self, cmd):
        assert not any(re.search(p, cmd, re.IGNORECASE) for p in DENY_PATTERNS)


class TestPrepareSandbox:
    def test_creates_settings_local_json(self, tmp_path):
        prepare_sandbox(tmp_path)
        settings_path = tmp_path / ".claude" / "settings.local.json"
        assert settings_path.exists()
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        assert "hooks" in data
        assert "PreToolUse" in data["hooks"]

    def test_hook_matches_bash(self, tmp_path):
        prepare_sandbox(tmp_path)
        settings_path = tmp_path / ".claude" / "settings.local.json"
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        matchers = [h["matcher"] for h in data["hooks"]["PreToolUse"]]
        assert "Bash" in matchers

    def test_hook_script_exists(self, tmp_path):
        prepare_sandbox(tmp_path)
        hook_script = tmp_path / ".claude" / "hooks" / "cagent-guard.py"
        assert hook_script.exists()

    def test_gitignore_updated(self, tmp_path):
        prepare_sandbox(tmp_path)
        gitignore = tmp_path / ".gitignore"
        assert gitignore.exists()
        assert ".claude/" in gitignore.read_text(encoding="utf-8")

    def test_gitignore_not_duplicated(self, tmp_path):
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text(".claude/\n", encoding="utf-8")
        prepare_sandbox(tmp_path)
        content = gitignore.read_text(encoding="utf-8")
        assert content.count(".claude/") == 1

    def test_hook_script_blocks_dangerous_commands(self, tmp_path):
        """End-to-end: run the hook script with a dangerous command."""
        prepare_sandbox(tmp_path)
        hook_script = tmp_path / ".claude" / "hooks" / "cagent-guard.py"

        import subprocess
        import sys

        # Test blocking git push
        inp = json.dumps({"tool_input": {"command": "git push origin main"}})
        result = subprocess.run(
            [sys.executable, str(hook_script)],
            input=inp, capture_output=True, text=True,
        )
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_hook_script_allows_safe_commands(self, tmp_path):
        """End-to-end: run the hook script with a safe command."""
        prepare_sandbox(tmp_path)
        hook_script = tmp_path / ".claude" / "hooks" / "cagent-guard.py"

        import subprocess
        import sys

        inp = json.dumps({"tool_input": {"command": "git add ."}})
        result = subprocess.run(
            [sys.executable, str(hook_script)],
            input=inp, capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""  # no deny output

    def test_hook_script_blocks_command_chains(self, tmp_path):
        """End-to-end: hook script blocks dangerous commands in chains."""
        prepare_sandbox(tmp_path)
        hook_script = tmp_path / ".claude" / "hooks" / "cagent-guard.py"

        import subprocess
        import sys

        # Test blocking bash -c with dangerous command
        inp = json.dumps({"tool_input": {"command": "bash -c 'git push'"}})
        result = subprocess.run(
            [sys.executable, str(hook_script)],
            input=inp, capture_output=True, text=True,
        )
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"

        # Test blocking command chain with &&
        inp = json.dumps({"tool_input": {"command": "cd /tmp && git push origin main"}})
        result = subprocess.run(
            [sys.executable, str(hook_script)],
            input=inp, capture_output=True, text=True,
        )
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
