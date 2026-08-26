"""Publish completed daily reports into a Git-backed archive directory."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_OUTPUT_SUBDIR = "ops-daily-reports"
DEFAULT_COMMIT_TEMPLATE = "ops: publish daily report {report_date}"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.environ.get(name, "1" if default else "0")).strip().lower()
    return raw not in {"", "0", "false", "no", "off"}


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    raw = str(os.environ.get(name, default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def _safe_relative_subdir(raw: str) -> Path:
    value = str(raw or DEFAULT_OUTPUT_SUBDIR).strip() or DEFAULT_OUTPUT_SUBDIR
    path = Path(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("OPS_GITHUB_REPORTS_OUTPUT_SUBDIR must be a safe relative path")
    return path


def _read_optional_file(path: str) -> str:
    text = str(path or "").strip()
    if not text:
        return ""
    return Path(text).read_text(encoding="utf-8").strip()


@dataclass(frozen=True, slots=True)
class GitHubReportPublisherConfig:
    enabled: bool = False
    repo_dir: str = ""
    output_subdir: str = DEFAULT_OUTPUT_SUBDIR
    remote: str = "origin"
    branch: str = ""
    push_enabled: bool = True
    author_name: str = "i-zrhe2016"
    author_email: str = "zrhe2016@gmail.com"
    commit_message_template: str = DEFAULT_COMMIT_TEMPLATE
    github_username: str = "x-access-token"
    github_token: str = ""
    command_timeout_seconds: int = 60

    @classmethod
    def from_env(cls) -> "GitHubReportPublisherConfig":
        token = str(os.environ.get("OPS_GITHUB_REPORTS_TOKEN", "")).strip()
        if not token:
            token = _read_optional_file(str(os.environ.get("OPS_GITHUB_REPORTS_TOKEN_FILE", "")).strip())
        return cls(
            enabled=_env_bool("OPS_GITHUB_REPORTS_ENABLED", False),
            repo_dir=str(os.environ.get("OPS_GITHUB_REPORTS_REPO_DIR", "")).strip(),
            output_subdir=str(
                os.environ.get("OPS_GITHUB_REPORTS_OUTPUT_SUBDIR", DEFAULT_OUTPUT_SUBDIR)
            ).strip()
            or DEFAULT_OUTPUT_SUBDIR,
            remote=str(os.environ.get("OPS_GITHUB_REPORTS_REMOTE", "origin")).strip() or "origin",
            branch=str(os.environ.get("OPS_GITHUB_REPORTS_BRANCH", "")).strip(),
            push_enabled=_env_bool("OPS_GITHUB_REPORTS_PUSH_ENABLED", True),
            author_name=str(os.environ.get("OPS_GITHUB_REPORTS_AUTHOR_NAME", "i-zrhe2016")).strip()
            or "i-zrhe2016",
            author_email=str(
                os.environ.get("OPS_GITHUB_REPORTS_AUTHOR_EMAIL", "zrhe2016@gmail.com")
            ).strip()
            or "zrhe2016@gmail.com",
            commit_message_template=str(
                os.environ.get("OPS_GITHUB_REPORTS_COMMIT_MESSAGE", DEFAULT_COMMIT_TEMPLATE)
            ).strip()
            or DEFAULT_COMMIT_TEMPLATE,
            github_username=str(
                os.environ.get("OPS_GITHUB_REPORTS_USERNAME", "x-access-token")
            ).strip()
            or "x-access-token",
            github_token=token,
            command_timeout_seconds=_env_int("OPS_GITHUB_REPORTS_COMMAND_TIMEOUT_SECONDS", 60),
        )


class GitHubReportPublisher:
    def __init__(self, config: GitHubReportPublisherConfig):
        self.config = config

    def publish_report(self, report_date: str, json_path: str | Path, markdown_path: str | Path) -> dict[str, Any]:
        if not self.config.enabled:
            return {"status": "disabled"}

        repo_dir = Path(self.config.repo_dir).expanduser().resolve()
        if not repo_dir.is_dir() or not (repo_dir / ".git").exists():
            raise RuntimeError("github_reports_repo_missing")

        branch = self._current_branch(repo_dir)
        target_branch = self.config.branch or branch
        if not target_branch:
            raise RuntimeError("github_reports_detached_head")
        self._ensure_no_conflicts(repo_dir)
        if self.config.push_enabled:
            self._sync_upstream(repo_dir, target_branch)

        output_subdir = _safe_relative_subdir(self.config.output_subdir)
        year = report_date[:4]
        archive_dir = repo_dir / output_subdir / year
        archive_dir.mkdir(parents=True, exist_ok=True)

        targets = [
            self._copy_report_file(json_path, archive_dir / f"{report_date}.json"),
            self._copy_report_file(markdown_path, archive_dir / f"{report_date}.md"),
            self._ensure_readme(repo_dir / output_subdir),
        ]
        relative_paths = [str(path.relative_to(repo_dir)) for path in targets]

        ahead_before = self._ahead_count(repo_dir)
        path_status = self._git(repo_dir, "status", "--porcelain", "--", *relative_paths).stdout.strip()
        commit_created = False
        commit_hash = ""

        if path_status:
            self._ensure_not_behind(repo_dir)
            self._git(repo_dir, "add", "--", *relative_paths)
            commit_message = self.config.commit_message_template.format(report_date=report_date)
            self._git(
                repo_dir,
                "-c",
                f"user.name={self.config.author_name}",
                "-c",
                f"user.email={self.config.author_email}",
                "commit",
                "--message",
                commit_message,
                "--",
                *relative_paths,
            )
            commit_created = True
            commit_hash = self._git(repo_dir, "rev-parse", "--short", "HEAD").stdout.strip()

        push_result = self._push_if_needed(repo_dir, target_branch) if self.config.push_enabled else {
            "pushed": False,
            "push_status": "disabled",
        }

        status = "published" if commit_created or push_result["pushed"] else "no_changes"
        return {
            "status": status,
            "repo_dir": str(repo_dir),
            "output_subdir": str(output_subdir),
            "paths": relative_paths,
            "commit_created": commit_created,
            "commit": commit_hash,
            "ahead_before": ahead_before,
            **push_result,
        }

    def _copy_report_file(self, source: str | Path, target: Path) -> Path:
        source_path = Path(source)
        if not source_path.is_file():
            raise RuntimeError(f"github_reports_source_missing:{source_path.name}")
        shutil.copy2(source_path, target)
        return target

    def _ensure_readme(self, archive_root: Path) -> Path:
        readme = archive_root / "README.md"
        if not readme.exists():
            readme.write_text(
                "# Xray Ops Daily Reports\n\n"
                "This directory stores daily operations report artifacts published by "
                "`xray-ops-daily-reporter`.\n\n"
                "- Markdown files are the human-readable reports.\n"
                "- JSON files are the validated source reports that rendered the Markdown output.\n",
                encoding="utf-8",
            )
        return readme

    def _git(self, repo_dir: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        command = ["git", "-C", str(repo_dir), "-c", f"safe.directory={repo_dir}", *args]
        merged_env = os.environ.copy()
        merged_env["GIT_TERMINAL_PROMPT"] = "0"
        if env:
            merged_env.update(env)
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=self.config.command_timeout_seconds,
            env=merged_env,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or "git command failed"
            raise RuntimeError(detail[:500])
        return completed

    def _current_branch(self, repo_dir: Path) -> str:
        return self._git(repo_dir, "branch", "--show-current").stdout.strip()

    def _upstream(self, repo_dir: Path) -> str:
        completed = subprocess.run(
            [
                "git",
                "-C",
                str(repo_dir),
                "-c",
                f"safe.directory={repo_dir}",
                "rev-parse",
                "--abbrev-ref",
                "--symbolic-full-name",
                "@{u}",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=self.config.command_timeout_seconds,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        return completed.stdout.strip() if completed.returncode == 0 else ""

    def _ahead_behind(self, repo_dir: Path) -> tuple[int, int]:
        upstream = self._upstream(repo_dir)
        if not upstream:
            return 0, 0
        raw = self._git(repo_dir, "rev-list", "--left-right", "--count", f"HEAD...{upstream}").stdout.strip()
        ahead_text, behind_text = raw.split()
        return int(ahead_text), int(behind_text)

    def _ahead_count(self, repo_dir: Path) -> int:
        ahead, _behind = self._ahead_behind(repo_dir)
        return ahead

    def _ensure_not_behind(self, repo_dir: Path) -> None:
        _ahead, behind = self._ahead_behind(repo_dir)
        if behind:
            raise RuntimeError("github_reports_branch_behind_upstream")

    def _ensure_no_conflicts(self, repo_dir: Path) -> None:
        conflicted = self._git(repo_dir, "diff", "--name-only", "--diff-filter=U").stdout.strip()
        if conflicted:
            raise RuntimeError("github_reports_repo_has_conflicts")

    def _sync_upstream(self, repo_dir: Path, branch: str) -> None:
        upstream = self._upstream(repo_dir)
        if not upstream:
            return
        self._git(repo_dir, "fetch", "--quiet", self.config.remote, branch, env=self._push_env())
        _ahead, behind = self._ahead_behind(repo_dir)
        if not behind:
            return
        if self._git(repo_dir, "status", "--porcelain").stdout.strip():
            raise RuntimeError("github_reports_branch_behind_upstream")
        self._git(repo_dir, "merge", "--ff-only", "--quiet", upstream)

    def _push_if_needed(self, repo_dir: Path, branch: str) -> dict[str, Any]:
        self._ensure_not_behind(repo_dir)
        upstream = self._upstream(repo_dir)
        ahead, _behind = self._ahead_behind(repo_dir)
        if upstream and ahead <= 0:
            return {"pushed": False, "push_status": "up_to_date", "ahead_after": ahead}

        env = self._push_env()
        if upstream:
            self._git(repo_dir, "push", env=env)
        else:
            self._git(repo_dir, "push", "-u", self.config.remote, branch, env=env)
        ahead_after = self._ahead_count(repo_dir)
        return {"pushed": True, "push_status": "pushed", "ahead_after": ahead_after}

    def _push_env(self) -> dict[str, str]:
        env: dict[str, str] = {}
        if not self.config.github_token:
            return env
        askpass_dir = Path(tempfile.gettempdir()) / "xray-ops-git-askpass"
        askpass_dir.mkdir(mode=0o700, exist_ok=True)
        askpass = askpass_dir / "askpass.sh"
        askpass.write_text(
            "#!/bin/sh\n"
            "case \"$1\" in\n"
            "*Username*) printf '%s\\n' \"${OPS_GITHUB_REPORTS_USERNAME:-x-access-token}\" ;;\n"
            "*Password*) printf '%s\\n' \"$OPS_GITHUB_REPORTS_TOKEN\" ;;\n"
            "*) printf '\\n' ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        askpass.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        env.update(
            {
                "GIT_ASKPASS": str(askpass),
                "OPS_GITHUB_REPORTS_USERNAME": self.config.github_username,
                "OPS_GITHUB_REPORTS_TOKEN": self.config.github_token,
            }
        )
        return env
