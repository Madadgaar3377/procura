"""Trusted reverse-proxy client IP extraction."""
from __future__ import annotations

from functools import lru_cache
import ipaddress
from typing import Iterable

from fastapi import Request

from app.config import get_settings

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

# Official Cloudflare edge IP ranges (https://www.cloudflare.com/ips/).
CLOUDFLARE_PROXY_CIDRS: tuple[str, ...] = (
    '173.245.48.0/20',
    '103.21.244.0/22',
    '103.22.200.0/22',
    '103.31.4.0/22',
    '141.101.64.0/18',
    '108.162.192.0/18',
    '190.93.240.0/20',
    '188.114.96.0/20',
    '197.234.240.0/22',
    '198.41.128.0/17',
    '162.158.0.0/15',
    '104.16.0.0/13',
    '104.24.0.0/14',
    '172.64.0.0/13',
    '131.0.72.0/22',
    '2400:cb00::/32',
    '2606:4700::/32',
    '2803:f800::/32',
    '2405:b500::/32',
    '2405:8100::/32',
    '2a06:98c0::/29',
    '2c0f:f248::/32',
)


def _parse_ip(value: str) -> IPAddress | None:
    candidate = (value or '').strip()
    if not candidate:
        return None
    if candidate.startswith('[') and ']' in candidate:
        candidate = candidate[1:candidate.index(']')]
    elif candidate.count(':') == 1 and '.' in candidate:
        candidate = candidate.split(':', 1)[0]
    try:
        return ipaddress.ip_address(candidate)
    except ValueError:
        return None


@lru_cache(maxsize=16)
def _compile_networks(cidrs_csv: str) -> tuple[IPNetwork, ...]:
    networks: list[IPNetwork] = []
    for raw in cidrs_csv.split(','):
        item = raw.strip()
        if not item:
            continue
        networks.append(ipaddress.ip_network(item, strict=False))
    return tuple(networks)


def _in_networks(ip: IPAddress, networks: Iterable[IPNetwork]) -> bool:
    return any(ip in net for net in networks)


def get_client_ip(request: Request) -> str:
    """Resolve the real client IP only when the peer is a configured trusted proxy."""
    peer_raw = request.client.host if request.client else ''
    peer_ip = _parse_ip(peer_raw)
    if peer_ip is None:
        return peer_raw or 'unknown'

    settings = get_settings()
    trusted_nets = _compile_networks(settings.trusted_proxy_cidrs)
    if settings.cloudflare_proxy_cidrs.strip():
        cf_nets = _compile_networks(settings.cloudflare_proxy_cidrs)
    elif settings.trust_cloudflare_proxy:
        cf_nets = _compile_networks(','.join(CLOUDFLARE_PROXY_CIDRS))
    else:
        cf_nets = ()

    all_trusted = trusted_nets + cf_nets
    if not _in_networks(peer_ip, all_trusted):
        return str(peer_ip)

    saw_cloudflare = _in_networks(peer_ip, cf_nets)

    xff = request.headers.get('X-Forwarded-For', '')
    if xff:
        hops = [part.strip() for part in xff.split(',') if part.strip()]
        for hop in reversed(hops):
            hop_ip = _parse_ip(hop)
            if hop_ip is None:
                break
            if _in_networks(hop_ip, all_trusted):
                if _in_networks(hop_ip, cf_nets):
                    saw_cloudflare = True
                continue
            return str(hop_ip)

    if saw_cloudflare:
        for header in ('CF-Connecting-IP', 'True-Client-IP'):
            cf_ip = _parse_ip(request.headers.get(header, ''))
            if cf_ip is not None and not _in_networks(cf_ip, all_trusted):
                return str(cf_ip)

    real_ip = _parse_ip(request.headers.get('X-Real-IP', ''))
    if real_ip is not None and not _in_networks(real_ip, all_trusted):
        return str(real_ip)

    return str(peer_ip)
