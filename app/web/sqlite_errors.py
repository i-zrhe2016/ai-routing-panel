def is_listen_port_conflict(exc):
    """Return whether SQLite identified the ports.listen_port unique key."""
    return "unique constraint failed: ports.listen_port" in str(exc).lower()
