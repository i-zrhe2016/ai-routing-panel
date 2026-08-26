import json
import os
import subprocess
from pathlib import Path

from components.xray_ops.github_reports import GitHubReportPublisher, GitHubReportPublisherConfig


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def _init_repo(repo: Path) -> None:
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "tester")
    _git(repo, "config", "user.email", "tester@example.com")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "base")


def _write_source_report(source: Path, report_date: str) -> tuple[Path, Path]:
    source.mkdir()
    json_path = source / f"{report_date}.json"
    markdown_path = source / f"{report_date}.md"
    json_path.write_text(json.dumps({"report_date": report_date}) + "\n", encoding="utf-8")
    markdown_path.write_text("# report\n", encoding="utf-8")
    return json_path, markdown_path


def test_github_report_publisher_commits_only_report_paths(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "unrelated.txt").write_text("keep staged\n", encoding="utf-8")
    _git(repo, "add", "unrelated.txt")

    json_path, markdown_path = _write_source_report(tmp_path / "source", "2030-01-02")

    status = GitHubReportPublisher(
        GitHubReportPublisherConfig(
            enabled=True,
            repo_dir=str(repo),
            push_enabled=False,
            author_name="i-zrhe2016",
            author_email="zrhe2016@gmail.com",
        )
    ).publish_report("2030-01-02", json_path, markdown_path)

    assert status["status"] == "published"
    assert status["commit_created"] is True
    assert status["push_status"] == "disabled"
    assert set(_git(repo, "show", "--name-only", "--format=", "HEAD").splitlines()) == {
        "ops-daily-reports/2030/2030-01-02.json",
        "ops-daily-reports/2030/2030-01-02.md",
        "ops-daily-reports/README.md",
    }
    assert _git(repo, "status", "--short", "--", "unrelated.txt") == "A  unrelated.txt"


def test_github_report_publisher_noops_when_archive_is_current(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    json_path, markdown_path = _write_source_report(tmp_path / "source", "2030-01-02")
    publisher = GitHubReportPublisher(
        GitHubReportPublisherConfig(enabled=True, repo_dir=str(repo), push_enabled=False)
    )

    publisher.publish_report("2030-01-02", json_path, markdown_path)
    status = publisher.publish_report("2030-01-02", json_path, markdown_path)

    assert status["status"] == "no_changes"
    assert status["commit_created"] is False


def test_github_report_publisher_fast_forwards_clean_repo_before_commit(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)

    seed = tmp_path / "seed"
    _init_repo(seed)
    branch = _git(seed, "branch", "--show-current")
    _git(seed, "remote", "add", "origin", str(remote))
    _git(seed, "push", "-q", "-u", "origin", branch)

    repo = tmp_path / "repo"
    subprocess.run(["git", "clone", "-q", str(remote), str(repo)], check=True)

    (seed / "remote.txt").write_text("advance\n", encoding="utf-8")
    _git(seed, "add", "remote.txt")
    _git(seed, "commit", "-q", "-m", "advance remote")
    _git(seed, "push", "-q")

    json_path, markdown_path = _write_source_report(tmp_path / "source", "2030-01-03")
    status = GitHubReportPublisher(
        GitHubReportPublisherConfig(enabled=True, repo_dir=str(repo), push_enabled=True)
    ).publish_report("2030-01-03", json_path, markdown_path)

    assert status["status"] == "published"
    assert status["commit_created"] is True
    assert status["push_status"] == "pushed"
    assert _git(repo, "rev-list", "--left-right", "--count", f"HEAD...origin/{branch}") == "0\t0"
    assert set(_git(repo, "show", "--name-only", "--format=", "HEAD").splitlines()) == {
        "ops-daily-reports/2030/2030-01-03.json",
        "ops-daily-reports/2030/2030-01-03.md",
        "ops-daily-reports/README.md",
    }
    assert _git(repo, "show", "--name-only", "--format=", "HEAD^").strip() == "remote.txt"


def test_github_report_publisher_askpass_uses_repo_git_directory(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    publisher = GitHubReportPublisher(
        GitHubReportPublisherConfig(enabled=True, repo_dir=str(repo), github_token="token")
    )

    env = publisher._push_env(repo)
    askpass = Path(env["GIT_ASKPASS"])

    assert askpass.parent == repo / ".git" / "xray-ops-askpass"
    assert os.access(askpass, os.X_OK)
