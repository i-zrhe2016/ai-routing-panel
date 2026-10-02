import errno
import socket

import pytest

from scripts.cleanup_xray_sockets import cleanup_stale_sockets


def test_removes_socket_left_by_abrupt_exit_and_can_bind_again(tmp_path):
    path = tmp_path / "entry-unified-443.sock"
    with socket.socket(socket.AF_UNIX) as listener:
        listener.bind(str(path))
        listener.listen()
    assert path.exists()
    assert cleanup_stale_sockets(tmp_path) == [path.name]
    with socket.socket(socket.AF_UNIX) as listener:
        listener.bind(str(path))
        listener.listen()
    assert cleanup_stale_sockets(tmp_path) == [path.name]
    assert cleanup_stale_sockets(tmp_path) == []


def test_keeps_live_socket_and_its_listener_usable(tmp_path):
    path = tmp_path / "entry-panel-31098.sock"
    with socket.socket(socket.AF_UNIX) as listener:
        listener.bind(str(path))
        listener.listen(5)
        assert cleanup_stale_sockets(tmp_path) == []
        assert path.exists()
        with socket.socket(socket.AF_UNIX) as client:
            client.connect(str(path))


def test_preserves_regular_files_symlinks_and_unrelated_sockets(tmp_path):
    regular = tmp_path / "entry-panel-31098.sock"
    regular.write_text("must remain")
    link = tmp_path / "entry-panel-31000.sock"
    link.symlink_to(regular)
    unrelated = tmp_path / "another-service.sock"
    with socket.socket(socket.AF_UNIX) as listener:
        listener.bind(str(unrelated))
    assert cleanup_stale_sockets(tmp_path) == []
    assert regular.read_text() == "must remain"
    assert link.is_symlink()
    assert unrelated.exists()


def test_missing_directory_is_noop(tmp_path):
    assert cleanup_stale_sockets(tmp_path / "missing") == []


def test_unexpected_connection_failure_never_deletes_socket(tmp_path, monkeypatch):
    path = tmp_path / "entry-panel-31098.sock"
    with socket.socket(socket.AF_UNIX) as listener:
        listener.bind(str(path))

    def denied(*args):
        raise PermissionError(errno.EACCES, "permission denied")

    monkeypatch.setattr(socket.socket, "connect", denied)
    with pytest.raises(PermissionError):
        cleanup_stale_sockets(tmp_path)
    assert path.exists()
