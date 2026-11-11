#!/usr/bin/env python3
"""velvet-ddns : garde à jour le nom public de la passerelle chez Cloudflare.

Met l'IPv4 publique de la box dans l'enregistrement A et l'IPv6 du Raspberry
dans l'enregistrement AAAA, toujours en « DNS only » (le proxy Cloudflare ne
transmet pas l'UDP de WireGuard). Ne supprime jamais rien : une adresse
introuvable laisse l'enregistrement tel quel.

Le jeton API (permission « Zone › DNS › Edit » sur la zone seulement) est lu
dans un fichier root:root 0600, jamais passé en argument.
"""
import argparse
import ipaddress
import json
import subprocess
import sys
import urllib.error
import urllib.request

API = "https://api.cloudflare.com/client/v4"
TIMEOUT = 20


def log(message):
    print(message, flush=True)


def cloudflare(token, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(API + path, data=data, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        try:
            payload = json.load(error)
        except ValueError:
            raise RuntimeError(f"Cloudflare {method} {path} : HTTP {error.code}") from None
    if not payload.get("success"):
        messages = "; ".join(e.get("message", "?") for e in payload.get("errors", []))
        raise RuntimeError(f"Cloudflare {method} {path} : {messages or 'échec'}")
    return payload["result"]


def public_ipv4():
    """IPv4 publique vue d'Internet (celle de la box) ; None si introuvable."""
    # curl -4 force l'IPv4 (urllib suivrait l'IPv6). Deux sources, au cas où.
    for url in ("https://api.ipify.org", "https://ipv4.icanhazip.com"):
        try:
            output = subprocess.run(["curl", "-4", "-fsS", "--max-time", str(TIMEOUT), url],
                                    check=True, capture_output=True, text=True).stdout
            address = ipaddress.ip_address(output.strip())
            if address.version == 4 and address.is_global:
                return str(address)
        except (OSError, subprocess.CalledProcessError, ValueError) as error:
            log(f"IPv4 publique introuvable via {url} : {getattr(error, 'stderr', '') or error}")
    return None


def local_ipv6(interface):
    """IPv6 globale stable du Raspberry (ni temporaire, ni obsolète, ni ULA)."""
    try:
        if interface == "auto":
            route = json.loads(subprocess.run(
                ["ip", "-6", "-j", "route", "show", "default"],
                check=True, capture_output=True, text=True).stdout)
            interface = route[0]["dev"]
        links = json.loads(subprocess.run(
            ["ip", "-6", "-j", "addr", "show", "dev", interface, "scope", "global"],
            check=True, capture_output=True, text=True).stdout)
    except (OSError, subprocess.CalledProcessError, ValueError, IndexError, KeyError) as error:
        log(f"IPv6 introuvable : {error}")
        return None
    for link in links:
        for info in link.get("addr_info", []):
            if info.get("family") != "inet6" or info.get("temporary") or info.get("deprecated"):
                continue
            address = ipaddress.ip_address(info["local"])
            if address.is_global:
                return str(address)
    log(f"Aucune IPv6 globale stable sur {interface}")
    return None


def sync(token, zone_id, name, record_type, content, ttl):
    records = cloudflare(token, "GET", f"/zones/{zone_id}/dns_records?type={record_type}&name={name}")
    wanted = {"type": record_type, "name": name, "content": content, "ttl": ttl, "proxied": False,
              "comment": "Géré par velvet-ddns (Raspberry)"}
    if not records:
        cloudflare(token, "POST", f"/zones/{zone_id}/dns_records", wanted)
        log(f"{record_type} {name} créé : {content}")
        return True
    record = records[0]
    if record["content"] == content and not record.get("proxied"):
        return False
    cloudflare(token, "PATCH", f"/zones/{zone_id}/dns_records/{record['id']}", wanted)
    log(f"{record_type} {name} : {record['content']} → {content}")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--record", required=True, help="nom complet, ex. vpn.example.net")
    parser.add_argument("--zone", required=True, help="zone Cloudflare, ex. example.net")
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--interface", default="auto")
    parser.add_argument("--ttl", type=int, default=60)
    parser.add_argument("--no-ipv4", action="store_true")
    parser.add_argument("--no-ipv6", action="store_true")
    args = parser.parse_args()

    with open(args.token_file) as handle:
        token = handle.read().strip()

    failures = 0
    try:
        zones = cloudflare(token, "GET", f"/zones?name={args.zone}")
        if not zones:
            raise RuntimeError(f"zone {args.zone} absente ou hors de portée du jeton")
        zone_id = zones[0]["id"]
    except (OSError, RuntimeError) as error:
        log(f"Erreur : {error}")
        return 1

    for record_type, enabled, finder in (
        ("A", not args.no_ipv4, public_ipv4),
        ("AAAA", not args.no_ipv6, lambda: local_ipv6(args.interface)),
    ):
        if not enabled:
            continue
        address = finder()
        if address is None:
            failures += 1
            continue
        try:
            sync(token, zone_id, args.record, record_type, address, args.ttl)
        except (OSError, RuntimeError) as error:
            log(f"Erreur {record_type} : {error}")
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
