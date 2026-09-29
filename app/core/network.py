"""Where the backoffice may be reached from."""
import ipaddress

from fastapi import Request

from app.core.config import settings
from app.core.permissions import forbidden


def enforce_admin_network(request: Request) -> None:
    """403 unless the caller's address is inside ADMIN_ALLOWED_CIDRS (no restriction when empty).

    Behind a proxy, run uvicorn with --proxy-headers and --forwarded-allow-ips so the address seen
    here is the real client and not the proxy's.
    """
    networks = settings.admin_networks
    if not networks:
        return
    host = request.client.host if request.client else ""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        raise forbidden("Acceso no permitido desde esta red", "admin_network_denied")
    if not any(address in network for network in networks):
        raise forbidden("Acceso no permitido desde esta red", "admin_network_denied")
