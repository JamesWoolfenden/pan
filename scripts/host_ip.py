"""Prints this machine's real LAN-facing IPv4 address, for the Makefile's dynamic HOST_IP
(see Makefile's own comment). Asking Windows for every adapter's address and filtering out
known VPN adapters by name is more reliable than the old "connect a UDP socket to 8.8.8.8"
trick: that trick assumed a VPN would never be the default route, which holds for a split-tunnel
setup but not a full-tunnel one (e.g. NordVPN's NordLynx) - there, the OS genuinely routes that
probe through the VPN, handing back the VPN's own IP instead of the LAN one.
"""

from __future__ import annotations

import socket
import subprocess

VPN_NAME_MARKERS = (
    "tailscale",
    "nordlynx",
    "nordvpn",
    "openvpn",
    "wireguard",
    "wintun",
    "vpn",
)


def _is_vpn_adapter(name: str) -> bool:
    name = name.lower()
    return any(marker in name for marker in VPN_NAME_MARKERS)


def _via_powershell() -> str | None:
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-NetIPAddress -AddressFamily IPv4 | "
                "Where-Object { $_.IPAddress -notlike '169.254.*' -and $_.IPAddress -ne '127.0.0.1' } | "
                'ForEach-Object { "$($_.InterfaceAlias)|$($_.IPAddress)" }',
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    for line in result.stdout.splitlines():
        alias, _, ip = line.strip().partition("|")
        if ip and not _is_vpn_adapter(alias):
            return ip
    return None


def _via_udp_probe() -> str:
    """Fallback for non-Windows or if PowerShell is unavailable - doesn't account for a
    full-tunnel VPN, but better than nothing."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]


def host_ip() -> str:
    return _via_powershell() or _via_udp_probe()


if __name__ == "__main__":
    print(host_ip())
