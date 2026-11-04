import socket
import urllib.request

import duckdb
import pytest

from house_price_estimate.estimate import Estimator
from house_price_estimate.offline import NetworkForbiddenError, no_network


def test_no_network_blocks_sockets_and_dns():
    with no_network():
        with pytest.raises(NetworkForbiddenError):
            socket.create_connection(("example.com", 80), timeout=1)
        with pytest.raises(NetworkForbiddenError):
            socket.getaddrinfo("example.com", 443)
        with pytest.raises(NetworkForbiddenError):
            urllib.request.urlopen("https://example.com", timeout=1)
        s = socket.socket()
        with pytest.raises(NetworkForbiddenError):
            s.connect(("93.184.216.34", 80))
        s.close()
    # Restored afterwards.
    assert socket.getaddrinfo.__name__ != "_forbidden"


def test_duckdb_is_locked_down(paths):
    est = Estimator(paths.db)
    with pytest.raises(Exception, match="(?i)disabled|permission"):
        est.conn.execute("SELECT * FROM read_csv('https://example.com/x.csv')")
    with pytest.raises(duckdb.Error):
        est.conn.execute("SET enable_external_access = true")
    # A second Estimator on the same file shares the locked instance.
    est2 = Estimator(paths.db)
    with pytest.raises(Exception, match="(?i)disabled|permission"):
        est2.conn.execute("SELECT * FROM read_csv('https://example.com/x.csv')")
    est2.close()
    est.close()
