#!/usr/bin/env python3
"""Remove refused Xray entry sockets before Docker starts on the host."""

import argparse
import errno
import re
import socket
import stat
from pathlib import Path


ENTRY_SOCKET = re.compile(r"entry-(?:panel|unified)-[0-9]+\.sock")


def cleanup_stale_sockets(directory):
    directory = Path(directory)
    removed = []
    if not directory.exists():
        return removed
    for path in sorted(directory.iterdir()):
        if not ENTRY_SOCKET.fullmatch(path.name):
            continue
        before = path.lstat()
        if not stat.S_ISSOCK(before.st_mode):
            continue
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            probe.settimeout(1)
            try:
                probe.connect(str(path))
            except OSError as error:
                if error.errno != errno.ECONNREFUSED:
                    raise
            else:
                continue
        # Preserve a replacement created between inspection and removal.
        after = path.lstat()
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            continue
        path.unlink()
        removed.append(path.name)
    return removed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket-dir", required=True)
    args = parser.parse_args()
    for name in cleanup_stale_sockets(args.socket_dir):
        print(f"Removed stale Xray socket: {name}", flush=True)


if __name__ == "__main__":
    main()
