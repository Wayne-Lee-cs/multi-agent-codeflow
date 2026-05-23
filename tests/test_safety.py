"""Unit tests for cagent/safety.py — regex pattern matching and sandbox injection."""

import json
import re
from pathlib import Path

import pytest

from cagent.safety import DENY_PATTERNS, _check_tokens, prepare_sandbox


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
        "node -e \"require('child_process').execSync('git push')\"",
        "powershell -Command \"git push\"",
        "powershell -command \"Remove-Item -Recurse -Force C:\\temp\"",
        "pwsh -Command \"git push\"",
        "pwsh -c \"Remove-Item -Recurse -Force /tmp\"",
        "cmd /c \"git push\"",
        "cmd /C \"del /s /q C:\\temp\"",
        "deno eval \"Deno.run({cmd: ['git','push']})\"",
        "deno run script.ts",
    ])
    def test_command_chain_blocked(self, cmd):
        assert any(re.search(p, cmd, re.IGNORECASE) for p in DENY_PATTERNS)

    @pytest.mark.parametrize("cmd", [
        "python3.11 -c 'import os; os.system(\"rm -rf /\")'",
        "python3.12 -c 'import subprocess'",
        "python3.10 -c 'print(1)'",
    ])
    def test_python_versioned_binary_blocked(self, cmd):
        """python3.x -c should be blocked (versioned binary bypass)."""
        assert any(re.search(p, cmd, re.IGNORECASE) for p in DENY_PATTERNS)

    @pytest.mark.parametrize("cmd", [
        "echo 'test' | zsh",
        "echo 'test' | ksh",
        "echo 'test' | dash",
        "echo 'test' | ash",
        "cat file | zsh",
    ])
    def test_pipe_to_shell_extended_blocked(self, cmd):
        """Piping to zsh/ksh/dash/ash should be blocked."""
        assert any(re.search(p, cmd, re.IGNORECASE) for p in DENY_PATTERNS)

    @pytest.mark.parametrize("cmd", [
        "git clean --force",
        "git clean --force -d",
        "git clean -d --force",
    ])
    def test_git_clean_long_flag_blocked(self, cmd):
        """git clean --force (long flag) should be blocked."""
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


class TestCheckTokens:
    """Test token-based split-flag detection."""

    @pytest.mark.parametrize("cmd", [
        "rm -i -r -f dir/",
        "rm -r -f /tmp/test",
        "rm -v -r -i -f .",
        "rm --recursive --force /tmp",
        "rm --recursive -f /tmp",
        "rm -r --force /tmp",
        "rm -R -f /tmp",
    ])
    def test_split_flags_blocked(self, cmd):
        reason = _check_tokens(cmd)
        assert reason is not None
        assert "recursive" in reason

    @pytest.mark.parametrize("cmd", [
        "rm -r /tmp/test",
        "rm -R dir/",
        "rm --recursive dir/",
    ])
    def test_recursive_only_blocked(self, cmd):
        reason = _check_tokens(cmd)
        assert reason is not None
        assert "recursive" in reason

    @pytest.mark.parametrize("cmd", [
        "rm -f file.txt",
        "rm file.txt",
        "ls -la",
        "git add .",
    ])
    def test_safe_commands_pass(self, cmd):
        assert _check_tokens(cmd) is None

    def test_split_flags_in_chain(self):
        reason = _check_tokens("echo hi && rm -r -f /tmp")
        assert reason is not None

    def test_env_var_only_no_infinite_loop(self):
        """Pure env-var assignment with no command should not loop."""
        assert _check_tokens("A=B C=D") is None

    def test_env_var_prefix_with_rm(self):
        """Env var prefix before rm is handled correctly."""
        reason = _check_tokens("PATH=/usr/bin rm -r -f /tmp")
        assert reason is not None

    def test_end_of_options_separator(self):
        """rm -r -f -- /tmp should still be caught (flags before --)."""
        reason = _check_tokens("rm -r -f -- /tmp")
        assert reason is not None

    def test_end_of_options_no_flags_after(self):
        """rm -- -r -f should NOT be caught (everything after -- is positional)."""
        reason = _check_tokens("rm -- -r -f")
        assert reason is None

    def test_hook_blocks_split_flags(self, tmp_path):
        """End-to-end: hook script blocks rm with split flags."""
        prepare_sandbox(tmp_path)
        hook_script = tmp_path / ".claude" / "hooks" / "cagent-guard.py"

        import subprocess
        import sys

        inp = json.dumps({"tool_name": "Bash", "tool_input": {"command": "rm -i -r -f dir/"}})
        result = subprocess.run(
            [sys.executable, str(hook_script)],
            input=inp, capture_output=True, text=True,
        )
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"


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

    def test_no_gitignore_written(self, tmp_path):
        """prepare_sandbox no longer writes .gitignore — agent.py handles it."""
        prepare_sandbox(tmp_path)
        gitignore = tmp_path / ".gitignore"
        assert not gitignore.exists()

    def test_hook_script_blocks_dangerous_commands(self, tmp_path):
        """End-to-end: run the hook script with a dangerous command."""
        prepare_sandbox(tmp_path)
        hook_script = tmp_path / ".claude" / "hooks" / "cagent-guard.py"

        import subprocess
        import sys

        # Test blocking git push
        inp = json.dumps({"tool_name": "Bash", "tool_input": {"command": "git push origin main"}})
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

        inp = json.dumps({"tool_name": "Bash", "tool_input": {"command": "git add ."}})
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
        inp = json.dumps({"tool_name": "Bash", "tool_input": {"command": "bash -c 'git push'"}})
        result = subprocess.run(
            [sys.executable, str(hook_script)],
            input=inp, capture_output=True, text=True,
        )
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"

        # Test blocking command chain with &&
        inp = json.dumps({"tool_name": "Bash", "tool_input": {"command": "cd /tmp && git push origin main"}})
        result = subprocess.run(
            [sys.executable, str(hook_script)],
            input=inp, capture_output=True, text=True,
        )
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_hook_script_blocks_write_dangerous_content(self, tmp_path):
        """Write tool with dangerous content in file is blocked."""
        prepare_sandbox(tmp_path)
        hook_script = tmp_path / ".claude" / "hooks" / "cagent-guard.py"

        import subprocess
        import sys

        # Write a file containing git push
        inp = json.dumps({
            "tool_name": "Write",
            "tool_input": {"file_path": "/tmp/deploy.sh", "content": "#!/bin/bash\ngit push origin main\n"},
        })
        result = subprocess.run(
            [sys.executable, str(hook_script)],
            input=inp, capture_output=True, text=True,
        )
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_hook_script_allows_write_safe_content(self, tmp_path):
        """Write tool with safe content is allowed."""
        prepare_sandbox(tmp_path)
        hook_script = tmp_path / ".claude" / "hooks" / "cagent-guard.py"

        import subprocess
        import sys

        inp = json.dumps({
            "tool_name": "Write",
            "tool_input": {"file_path": "/tmp/app.py", "content": "print('hello world')\n"},
        })
        result = subprocess.run(
            [sys.executable, str(hook_script)],
            input=inp, capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""  # no deny output

    def test_hook_script_blocks_edit_dangerous_content(self, tmp_path):
        """Edit tool with dangerous new_string is blocked."""
        prepare_sandbox(tmp_path)
        hook_script = tmp_path / ".claude" / "hooks" / "cagent-guard.py"

        import subprocess
        import sys

        inp = json.dumps({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": "/tmp/deploy.sh",
                "old_string": "echo safe",
                "new_string": "git push origin main",
            },
        })
        result = subprocess.run(
            [sys.executable, str(hook_script)],
            input=inp, capture_output=True, text=True,
        )
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_hook_script_allows_edit_safe_content(self, tmp_path):
        """Edit tool with safe new_string is allowed."""
        prepare_sandbox(tmp_path)
        hook_script = tmp_path / ".claude" / "hooks" / "cagent-guard.py"

        import subprocess
        import sys

        inp = json.dumps({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": "/tmp/app.py",
                "old_string": "old code",
                "new_string": "new safe code",
            },
        })
        result = subprocess.run(
            [sys.executable, str(hook_script)],
            input=inp, capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_settings_includes_edit_matcher(self, tmp_path):
        """settings.local.json should include Edit matcher."""
        prepare_sandbox(tmp_path)
        settings_path = tmp_path / ".claude" / "settings.local.json"
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        matchers = [h["matcher"] for h in data["hooks"]["PreToolUse"]]
        assert "Edit" in matchers
