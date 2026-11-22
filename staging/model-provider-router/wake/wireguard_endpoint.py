"""Refresh the home tunnel's dynamic IPv4 endpoint without exposing WG keys."""
from pathlib import Path
import socket
import subprocess
import time

INTERFACE = 'heretic-home'


def refresh(now=None, run=subprocess.check_output, resolve=socket.getaddrinfo,
            config_path=Path('/etc/wireguard/heretic-home.conf')):
    now = time.time() if now is None else now
    def query(field):
        return [line.split('\t', 1) for line in run(
            ['wg', 'show', INTERFACE, field], text=True).splitlines()]
    handshakes = query('latest-handshakes')
    if len(handshakes) != 1:
        raise RuntimeError('Expected exactly one home WireGuard peer')
    peer, stamp = handshakes[0]
    if int(stamp) and now - int(stamp) < 180:
        return False
    endpoints = query('endpoints')
    if len(endpoints) != 1 or endpoints[0][0] != peer:
        raise RuntimeError('Home WireGuard peer changed')
    configured = [line.split('=', 1)[1].strip() for line in config_path.read_text().splitlines()
                  if line.strip().split('=', 1)[0].strip() == 'Endpoint']
    if len(configured) != 1:
        raise RuntimeError('Expected one configured endpoint')
    host, port = configured[0].rsplit(':', 1)
    if not port.isdigit() or not 1 <= int(port) <= 65535:
        raise RuntimeError('Invalid configured endpoint port')
    addresses = sorted({row[4][0] for row in resolve(
        host, int(port), family=socket.AF_INET, type=socket.SOCK_DGRAM)})
    if not addresses:
        raise RuntimeError('Home endpoint DNS returned no IPv4 addresses')
    candidates = [ip + ':' + port for ip in addresses]
    if endpoints[0][1] in candidates:
        return False
    run(['wg', 'set', INTERFACE, 'peer', peer, 'endpoint', candidates[0]], text=True)
    return True


if __name__ == '__main__':
    if refresh():
        print('Updated home WireGuard endpoint from dynamic DNS')
