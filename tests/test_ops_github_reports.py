import json
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


def test_github_report_publisher_commits_only_report_paths(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "unrelated.txt").write_text("keep staged\n", encoding="utf-8")
    _git(repo, "add", "unrelated.txt")

    source = tmp_path / "source"
    source.mkdir()
    json_path = source / "2030-01-02.json"
    markdown_path = source / "2030-01-02.md"
    json_path.write_text(json.dumps({"report_date": "2030-01-02"}) + "\n", encoding="utf-8")
    markdown_path.write_text("# report\n", encoding="utf-8")

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
    source = tmp_path / "source"
    source.mkdir()
    json_path = source / "2030-01-02.json"
    markdown_path = source / "2030-01-02.md"
    json_path.write_text(json.dumps({"report_date": "2030-01-02"}) + "\n", encoding="utf-8")
    markdown_path.write_text("# report\n", encoding="utf-8")
    publisher = GitHubReportPublisher(
        GitHubReportPublisherConfig(enabled=True, repo_dir=str(repo), push_enabled=False)
    )

    publisher.publish_report("2030-01-02", json_path, markdown_path)
    status = publisher.publish_report("2030-01-02", json_path, markdown_path)

    assert status["status"] == "no_changes"
    assert status["commit_created"] is False
