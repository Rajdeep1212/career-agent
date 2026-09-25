"""Bounded public-page HTTP fetches, with DNS validation and connection pinning."""
import asyncio
import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import httpx


class UnsafeURLError(ValueError):
    """The destination or response cannot safely be used for verification."""


async def _resolve_host(host: str, port: int) -> list[str]:
    answers = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(answer[4][0]) for answer in answers))


def _public_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return _public_address(str(address.ipv4_mapped))
    return bool(address.is_global and not (address.is_reserved or address.is_multicast or address.is_loopback or address.is_link_local or address.is_unspecified))


async def _destination(url: str) -> tuple[httpx.URL, str, str]:
    if len(url) > 8192 or any(ord(char) < 32 for char in url) or "\\" in url:
        raise UnsafeURLError("Invalid destination URL.")
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower().rstrip(".")
        port = parts.port if parts.port is not None else (443 if parts.scheme == "https" else 80)
        if parts.scheme not in {"http", "https"} or parts.username is not None or parts.password is not None:
            raise UnsafeURLError("Only HTTP(S) URLs without credentials are allowed.")
        if port != (443 if parts.scheme == "https" else 80):
            raise UnsafeURLError("Nonstandard destination port.")
        if not host or "%" in host or host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            raise UnsafeURLError("Local destination blocked.")
        parsed = httpx.URL(url).copy_with(fragment=None)
        host = parsed.host.rstrip(".")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if "." not in host:
                raise UnsafeURLError("Local destination blocked.")
            addresses = await _resolve_host(host, port)
        else:
            addresses = [str(address)]
        if not addresses or not all(_public_address(address) for address in addresses):
            raise UnsafeURLError("Non-public destination blocked.")
        # The actual socket connects to this literal IP. Host and SNI retain the
        # original hostname, so TLS certificate verification still checks it.
        pinned = parsed.copy_with(host=addresses[0])
        authority = f"[{host}]" if ":" in host else host
        return pinned, authority, host
    except (ValueError, httpx.InvalidURL) as exc:
        if isinstance(exc, UnsafeURLError):
            raise
        raise UnsafeURLError("Invalid destination URL.") from exc


async def safe_get(url: str, *, timeout: float = 15.0, max_redirects: int = 4, max_bytes: int = 1_000_000) -> httpx.Response:
    """Fetch a public page; total deadline includes DNS, redirects and reading.

    No environment proxies, cookies shared across hops, TLS bypass, automatic
    redirects or unbounded response buffering are allowed. Compressed responses
    are rejected because hostile expansion would undermine the body-size limit.
    """
    async def fetch() -> httpx.Response:
        current = url
        for hop in range(max_redirects + 1):
            pinned, authority, tls_host = await _destination(current)
            async with httpx.AsyncClient(timeout=timeout, verify=True, trust_env=False, follow_redirects=False) as client:
                async with client.stream("GET", pinned, headers={"Host": authority, "User-Agent": "JobAgent/0.3", "Accept": "text/html,application/xhtml+xml,text/plain", "Accept-Encoding": "identity"}, extensions={"sni_hostname": tls_host}) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        target = response.headers.get("location")
                        if not target or hop == max_redirects:
                            raise UnsafeURLError("Redirect limit or invalid redirect.")
                        current = urljoin(current, target)
                        continue
                    if response.headers.get("content-encoding", "identity").lower() not in {"", "identity"}:
                        raise UnsafeURLError("Compressed response cannot be safely inspected.")
                    length = response.headers.get("content-length")
                    if length and (not length.isdigit() or int(length) > max_bytes):
                        raise UnsafeURLError("Response exceeds body limit.")
                    body = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=16384):
                        if len(body) + len(chunk) > max_bytes:
                            raise UnsafeURLError("Response exceeds body limit.")
                        body.extend(chunk)
                    return httpx.Response(response.status_code, headers=response.headers, content=bytes(body), request=httpx.Request("GET", current))
        raise UnsafeURLError("Redirect limit exceeded.")

    return await asyncio.wait_for(fetch(), timeout=max(0.1, min(float(timeout), 30.0)))
