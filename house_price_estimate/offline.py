"""Guards that stop address data leaving the machine.

``no_network`` blocks Python-level socket connections and DNS lookups.
``lock_down_duckdb`` stops DuckDB (which has its own C++ networking) from
auto-installing extensions or reading anything except the open database.
"""

import socket
from contextlib import contextmanager


class NetworkForbiddenError(RuntimeError):
    """Raised when code tries to use the network while estimating."""


def _forbidden(*args, **kwargs):
    raise NetworkForbiddenError("Network access is forbidden while handling addresses.")


_PATCHES = {
    (socket.socket, "connect"),
    (socket.socket, "connect_ex"),
    (socket.socket, "sendto"),
    (socket, "create_connection"),
    (socket, "getaddrinfo"),
    (socket, "gethostbyname"),
    (socket, "gethostbyname_ex"),
    (socket, "gethostbyaddr"),
}


@contextmanager
def no_network():
    """Block all Python socket connections and name lookups in this block."""
    saved = [(obj, name, getattr(obj, name)) for obj, name in _PATCHES]
    try:
        for obj, name, _ in saved:
            setattr(obj, name, _forbidden)
        yield
    finally:
        for obj, name, original in saved:
            setattr(obj, name, original)


def lock_down_duckdb(conn) -> None:
    """Disable extension autoloading and external file/HTTP access."""
    settings = dict(
        conn.execute(
            "SELECT name, value FROM duckdb_settings() WHERE name IN ("
            "'autoinstall_known_extensions', 'autoload_known_extensions', "
            "'enable_external_access', 'lock_configuration')"
        ).fetchall()
    )
    if settings.get("lock_configuration") == "true":
        # Connections to the same file in one process share an instance,
        # which an earlier Estimator may already have locked.
        unsafe = [
            k for k, v in settings.items() if k != "lock_configuration" and v != "false"
        ]
        if unsafe:
            raise RuntimeError(
                f"DuckDB configuration locked with unsafe settings: {unsafe}"
            )
        return
    conn.execute("SET autoinstall_known_extensions = false")
    conn.execute("SET autoload_known_extensions = false")
    conn.execute("SET enable_external_access = false")
    conn.execute("SET lock_configuration = true")
