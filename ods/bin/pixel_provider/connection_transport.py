"""Single-hop model metadata probe: no proxies, redirects or arbitrary paths."""
import http.client
import ipaddress
import socket
import ssl
from urllib.parse import urlsplit

from .connection import connection_url, normalize_connection, normalize_probe
from .store import MAX_BYTES, StoreError, decode_document


def _target(parts):
    port = parts.port or (443 if parts.scheme == 'https' else 80)
    candidates = socket.getaddrinfo(parts.hostname, port, type=socket.SOCK_STREAM)
    addresses = []
    for item in candidates:
        address = ipaddress.ip_address(item[4][0])
        checked = getattr(address, 'ipv4_mapped', None) or address
        if (checked.is_unspecified or checked.is_multicast or checked.is_link_local
                or parts.scheme == 'http' and not checked.is_loopback):
            raise StoreError('unsafe-connection-address')
        addresses.append(str(address))
    if not addresses:
        raise StoreError('connection-unavailable')
    return addresses[0], port


def probe_connection(connection, *, confirmed_endpoint):
    connection = normalize_connection(connection)
    if connection_url(confirmed_endpoint) != connection['baseUrl']:
        raise StoreError('connection-endpoint-not-confirmed')
    parts = urlsplit(connection['baseUrl'])
    sock = client = None
    try:
        address, port = _target(parts)
        # Connect to the exact checked address once; retain original TLS SNI and
        # hostname verification. HTTPConnection's automatic resolver is unused.
        sock = socket.create_connection((address,port), timeout=5)
        if parts.scheme == 'https':
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=parts.hostname)
        sock.settimeout(10)
        client = http.client.HTTPConnection(parts.hostname, port, timeout=10)
        client.sock = sock
        client.request('GET', '/v1/models', headers={
            'Authorization':'Bearer '+connection['credential']['apiKey'],
            'Accept':'application/json','Connection':'close'})
        response = client.getresponse()
        if response.status in (401,403):
            raise StoreError('connection-denied')
        if response.status != 200:
            raise StoreError('connection-unavailable')
        raw = response.read(MAX_BYTES+1)
        if len(raw) > MAX_BYTES:
            raise StoreError('invalid-probe')
        return normalize_probe(decode_document(raw), connection)
    except StoreError:
        raise
    except (OSError, ValueError, http.client.HTTPException):
        raise StoreError('connection-unavailable') from None
    finally:
        if client is not None:
            client.close()
        elif sock is not None:
            sock.close()
