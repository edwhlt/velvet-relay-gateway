#!/usr/bin/env python3
"""API de la passerelle Velvet Relay : pays de sortie Tor et appareils.

N'écoute que sur l'adresse de la passerelle dans le tunnel WireGuard : seuls
les appareils déjà connectés peuvent la joindre, et le trafic est chiffré par
WireGuard.

    GET  /status             sortie actuelle, vérifiée via check.torproject.org
    POST /exit {"country": "fr" | null}
                             change le pays (null = automatique), puis vérifie

    GET  /devices            appareils autorisés et dernier handshake
    POST /devices {"name": "iPad", "publicKey": "…"}
                             autorise un appareil (adresse attribuée d'office)
                             et renvoie son code d'invitation
    POST /devices/invitation {"publicKey": "…"}
                             renvoie le code d'invitation d'un appareil existant
    POST /devices/remove {"publicKey": "…"}
                             retire un appareil (sauf celui qui fait la demande)

Le registre des appareils (VELVET_DEVICES_FILE) fait foi : au démarrage,
l'interface WireGuard est resynchronisée sur lui. Les clés privées ne passent
jamais par ici : seules les clés publiques sont échangées.

Bibliothèque standard uniquement. Parle au port de contrôle de Tor (conteneur
velvet-tor, 127.0.0.1:9051) avec un mot de passe lu dans un fichier.
"""

import base64
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LISTEN_ADDRESS = os.environ.get("VELVET_API_ADDRESS", "10.66.0.1")
LISTEN_PORT = int(os.environ.get("VELVET_API_PORT", "8080"))
CONTROL_ADDRESS = os.environ.get("VELVET_TOR_CONTROL", "127.0.0.1:9051")
CONTROL_PASSWORD_FILE = os.environ.get("VELVET_TOR_PASSWORD_FILE", "/etc/velvet/tor-control.pass")
SOCKS = os.environ.get("VELVET_TOR_SOCKS", "127.0.0.1:9050")
STATE_FILE = os.environ.get("VELVET_STATE_FILE", "/var/lib/velvet-exit/country")

DEVICES_FILE = os.environ.get("VELVET_DEVICES_FILE", "/var/lib/velvet-exit/devices.json")
WG_BIN = os.environ.get("VELVET_WG_BIN", "wg")
WG_INTERFACE = os.environ.get("VELVET_WG_INTERFACE", "wg0")
TUNNEL_NETWORK = ipaddress.ip_network(os.environ.get("VELVET_TUNNEL_NETWORK", "10.66.0.0/24"))
# Pour le code d'invitation : comment joindre la passerelle depuis Internet.
PUBLIC_ENDPOINT = os.environ.get("VELVET_PUBLIC_ENDPOINT", "")
GATEWAY_PUBLIC_KEY = os.environ.get("VELVET_GATEWAY_PUBLIC_KEY", "")

COUNTRY_RE = re.compile(r"^[a-z]{2}$")
NAME_RE = re.compile(r"^[^\x00-\x1f]{1,40}$")
# Un seul changement de pays à la fois, une seule modification d'appareils à la fois.
lock = threading.Lock()
devices_lock = threading.Lock()


class TorControlError(Exception):
    pass


def tor_control(*commands):
    """Envoie des commandes au port de contrôle ; renvoie les lignes de réponse."""
    host, port = CONTROL_ADDRESS.rsplit(":", 1)
    with open(CONTROL_PASSWORD_FILE) as password_file:
        password = password_file.read().strip()
    # Le mot de passe est hexadécimal (généré par Ansible) : rien à échapper.
    if not re.fullmatch(r"[0-9a-f]+", password):
        raise TorControlError("mot de passe de contrôle Tor invalide")

    with socket.create_connection((host, int(port)), timeout=15) as sock:
        stream = sock.makefile("rwb")
        replies = []
        for command in [f'AUTHENTICATE "{password}"', *commands]:
            stream.write(command.encode() + b"\r\n")
            stream.flush()
            lines = read_reply(stream)
            if not lines[-1].startswith("250"):
                raise TorControlError(f"{command.split()[0]} : {lines[-1]}")
            replies.extend(lines)
        stream.write(b"QUIT\r\n")
        stream.flush()
        return replies


def read_reply(stream):
    lines = []
    while True:
        raw = stream.readline()
        if not raw:
            raise TorControlError("connexion au contrôle Tor fermée")
        line = raw.decode(errors="replace").rstrip("\r\n")
        lines.append(line)
        # "250-…" = suite, "250 …" = dernière ligne.
        if len(line) >= 4 and line[3] == " ":
            return lines


def country_of(ip):
    for line in tor_control(f"GETINFO ip-to-country/{ip}"):
        if "=" in line and "ip-to-country" in line:
            value = line.split("=", 1)[1].strip().lower()
            return None if value in ("", "??") else value
    return None


def requested_country():
    try:
        with open(STATE_FILE) as state:
            value = state.read().strip().lower()
            return value if COUNTRY_RE.match(value) else None
    except FileNotFoundError:
        return None


def apply_country(country):
    if country:
        tor_control(f"SETCONF ExitNodes={{{country.upper()}}} StrictNodes=1", "SIGNAL NEWNYM")
    else:
        tor_control("RESETCONF ExitNodes StrictNodes", "SIGNAL NEWNYM")
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as state:
        state.write(country or "")


def check_exit():
    """Requête réelle via Tor. Les clients sortent par d'autres circuits, mais
    toujours dans le pays imposé (StrictNodes)."""
    last_error = "vérification impossible"
    for _ in range(4):
        result = subprocess.run(
            ["curl", "--silent", "--fail", "--max-time", "30", "--socks5-hostname", SOCKS,
             "https://check.torproject.org/api/ip"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return data.get("IsTor", False), data.get("IP")
        last_error = f"curl a échoué (code {result.returncode})"
        time.sleep(3)
    raise TorControlError(last_error)


def status():
    requested = requested_country()
    try:
        is_tor, ip = check_exit()
        country = country_of(ip) if ip else None
        error = None
        if not is_tor:
            error = "la sortie n'est pas reconnue comme Tor"
        elif requested and country != requested:
            error = f"sortie hors du pays demandé ({(country or 'inconnu').upper()})"
        return {"isTor": is_tor, "exitIP": ip, "country": country,
                "requestedCountry": requested, "error": error}
    except (OSError, TorControlError, ValueError) as error:
        return {"isTor": False, "exitIP": None, "country": None,
                "requestedCountry": requested, "error": str(error)}


# MARK: - Appareils


class DeviceError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def is_public_key(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9+/]{43}=", value):
        return False
    try:
        return len(base64.b64decode(value)) == 32
    except ValueError:
        return False


def load_devices():
    try:
        with open(DEVICES_FILE) as registry:
            return json.load(registry).get("devices", [])
    except FileNotFoundError:
        return []


def save_devices(devices):
    # Écriture atomique : un registre à moitié écrit ferait perdre les appareils.
    os.makedirs(os.path.dirname(DEVICES_FILE), exist_ok=True)
    temporary = DEVICES_FILE + ".tmp"
    with open(temporary, "w") as registry:
        json.dump({"devices": devices}, registry, indent=2)
    os.replace(temporary, DEVICES_FILE)


def wg(*arguments):
    result = subprocess.run([WG_BIN, *arguments], capture_output=True, text=True)
    if result.returncode != 0:
        raise DeviceError(500, f"wg {arguments[0]} : {result.stderr.strip() or result.returncode}")
    return result.stdout


def wireguard_peers():
    """Pairs actuellement chargés : clé publique → (dernier handshake, reçu, envoyé)."""
    peers = {}
    for line in wg("show", WG_INTERFACE, "dump").splitlines()[1:]:
        fields = line.split("\t")
        if len(fields) >= 7:
            handshake = int(fields[4])
            peers[fields[0]] = (handshake or None, int(fields[5]), int(fields[6]))
    return peers


def sync_peers():
    """Aligne l'interface WireGuard sur le registre (ajouts et retraits)."""
    devices = load_devices()
    wanted = {device["publicKey"]: device["address"] for device in devices}
    for key in wireguard_peers():
        if key not in wanted:
            wg("set", WG_INTERFACE, "peer", key, "remove")
    for key, address in wanted.items():
        wg("set", WG_INTERFACE, "peer", key, "allowed-ips", address)


def next_free_address(devices):
    # La première adresse du réseau est celle de la passerelle (10.66.0.1) :
    # jamais attribuée, même si l'API écoute ailleurs.
    reserved = {next(TUNNEL_NETWORK.hosts()), ipaddress.ip_address(LISTEN_ADDRESS)}
    used = {ipaddress.ip_interface(device["address"]).ip for device in devices} | reserved
    for host in TUNNEL_NETWORK.hosts():
        if host not in used:
            return f"{host}/32"
    raise DeviceError(507, "plus aucune adresse libre dans le tunnel")


def invitation(device):
    return {"endpoint": PUBLIC_ENDPOINT, "gatewayPublicKey": GATEWAY_PUBLIC_KEY,
            "address": device["address"], "name": device["name"]}


def list_devices(caller):
    peers = wireguard_peers()
    result = []
    for device in load_devices():
        handshake, received, sent = peers.get(device["publicKey"], (None, 0, 0))
        result.append({
            "name": device["name"],
            "publicKey": device["publicKey"],
            "address": device["address"],
            "addedAt": device.get("addedAt"),
            "lastHandshake": handshake,
            "receivedBytes": received,
            "sentBytes": sent,
            "isCurrent": device["address"].split("/")[0] == caller,
        })
    return result


def add_device(name, public_key):
    name = (name or "").strip()
    if not NAME_RE.match(name):
        raise DeviceError(400, "nom d'appareil invalide (1 à 40 caractères)")
    if not is_public_key(public_key):
        raise DeviceError(400, "clé publique WireGuard invalide")
    devices = load_devices()
    if any(device["publicKey"] == public_key for device in devices):
        raise DeviceError(409, "cet appareil est déjà autorisé")
    device = {"name": name, "publicKey": public_key,
              "address": next_free_address(devices), "addedAt": int(time.time())}
    wg("set", WG_INTERFACE, "peer", public_key, "allowed-ips", device["address"])
    save_devices(devices + [device])
    return device


def find_device(public_key):
    for device in load_devices():
        if device["publicKey"] == public_key:
            return device
    raise DeviceError(404, "appareil inconnu")


def remove_device(public_key, caller):
    device = find_device(public_key)
    if device["address"].split("/")[0] == caller:
        raise DeviceError(409, "impossible de retirer l'appareil depuis lequel tu es connecté")
    wg("set", WG_INTERFACE, "peer", public_key, "remove")
    save_devices([d for d in load_devices() if d["publicKey"] != public_key])


class Handler(BaseHTTPRequestHandler):
    server_version = "VelvetExit/1"

    def do_GET(self):
        if self.path == "/devices":
            return self.devices_request(lambda body: (200, {"devices": list_devices(self.caller)}))
        if self.path != "/status":
            return self.reply(404, {"error": "inconnu"})
        result = status()
        self.reply(200 if result["error"] is None else 502, result)

    def do_POST(self):
        if self.path == "/devices":
            def add(body):
                device = add_device(body.get("name"), body.get("publicKey"))
                return 201, {"device": device, "invitation": invitation(device)}
            return self.devices_request(add)
        if self.path == "/devices/invitation":
            return self.devices_request(lambda body: (200, {"invitation": invitation(find_device(body.get("publicKey")))}))
        if self.path == "/devices/remove":
            def remove(body):
                remove_device(body.get("publicKey"), self.caller)
                return 200, {"devices": list_devices(self.caller)}
            return self.devices_request(remove)
        if self.path != "/exit":
            return self.reply(404, {"error": "inconnu"})
        try:
            body = self.read_body()
            country = body.get("country")
            if country is not None:
                country = str(country).lower()
                if not COUNTRY_RE.match(country):
                    return self.reply(400, {"error": "code pays invalide"})
        except (ValueError, AttributeError):
            return self.reply(400, {"error": "JSON invalide"})

        with lock:
            try:
                apply_country(country)
            except (OSError, TorControlError) as error:
                return self.reply(500, {"isTor": False, "exitIP": None, "country": None,
                                        "requestedCountry": country, "error": str(error)})
            result = status()
        self.reply(200 if result["error"] is None else 502, result)

    @property
    def caller(self):
        """Adresse de l'appareil dans le tunnel (10.66.0.x), ou la passerelle elle-même."""
        return self.client_address[0]

    def read_body(self):
        length = min(int(self.headers.get("Content-Length", "0")), 1024)
        body = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(body, dict):
            raise ValueError("objet JSON attendu")
        return body

    def devices_request(self, action):
        try:
            body = self.read_body() if self.command == "POST" else {}
        except ValueError:
            return self.reply(400, {"error": "JSON invalide"})
        with devices_lock:
            try:
                code, payload = action(body)
            except DeviceError as error:
                return self.reply(error.code, {"error": str(error)})
            except OSError as error:
                return self.reply(500, {"error": str(error)})
        self.reply(code, payload)

    def reply(self, code, payload):
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        # Journal minimal : ni IP cliente ni en-têtes.
        sys.stderr.write(f"{self.command} {self.path} → {args[1] if len(args) > 1 else ''}\n")


def restore_country():
    """Réapplique le pays mémorisé après un redémarrage (Tor peut être lent)."""
    country = requested_country()
    if not country:
        return
    for _ in range(30):
        try:
            apply_country(country)
            return
        except (OSError, TorControlError):
            time.sleep(2)


def restore_devices():
    """Recharge les appareils du registre dans WireGuard (après un redémarrage)."""
    for _ in range(30):
        try:
            sync_peers()
            return
        except (OSError, DeviceError) as error:
            sys.stderr.write(f"synchronisation des appareils : {error}\n")
            time.sleep(2)


if __name__ == "__main__":
    restore_devices()
    restore_country()
    server = ThreadingHTTPServer((LISTEN_ADDRESS, LISTEN_PORT), Handler)
    server.serve_forever()
