"""Resolve course node aliases with the native host resolver before proot.

The course runtime binds resolv.conf but not the host's /etc/hosts. Resolve
the trusted service address outside it; prompts and credentials stay intact.
"""
import ipaddress
import json
import re
from pathlib import Path
import socket
import sys
from urllib.parse import urlsplit, urlunsplit


def resolve_base_url(base):
    parts = urlsplit(base)
    if (parts.scheme != 'http' or parts.path != '/v1' or not parts.hostname
            or not parts.port or parts.username or parts.password or parts.query or parts.fragment):
        raise ValueError('Expected an internal course HTTP service /v1 address')
    try:
        address = ipaddress.ip_address(parts.hostname)
    except ValueError:
        try:
            address = ipaddress.ip_address(socket.gethostbyname(parts.hostname))
        except socket.gaierror:
            # Course Slurm nodes use private IPv4 names such as 10-0-1-10,
            # but those names need not have DNS records on compute nodes.
            if not re.fullmatch(r'\d{1,3}(?:-\d{1,3}){3}', parts.hostname):
                raise
            address = ipaddress.IPv4Address(parts.hostname.replace('-', '.'))
            if not address.is_private or address.is_loopback or address.is_link_local:
                raise ValueError('Encoded course node must be a private non-loopback IPv4 address')
    host = '[' + str(address) + ']' if address.version == 6 else str(address)
    return urlunsplit(('http', host + ':' + str(parts.port), '/v1', '', ''))


def main():
    path = Path(sys.argv[1])
    request = json.loads(path.read_text(encoding='utf-8'))
    request['base_url'] = resolve_base_url(request['base_url'])
    temporary = path.with_suffix('.resolved.tmp')
    temporary.write_text(json.dumps(request, ensure_ascii=True), encoding='utf-8')
    temporary.replace(path)
    print('CLAUSE_PROGRESS ' + json.dumps(['connection', 'Resolved model address before entering course runtime']), flush=True)


if __name__ == '__main__':
    main()
