# Velvet Relay gateway (Raspberry Pi)

*[Version française](README.fr.md)*

The server side of the **VPN mode** of the Velvet Relay iOS and macOS apps. A device sends all of its traffic over WireGuard to this gateway, which passes it through Tor:

```text
iPhone / Mac ── WireGuard ──> Raspberry (at home) ── Tor ──> Internet
```

Each device has its own key and its own address inside the tunnel (iPhone `10.66.0.2`, Mac `10.66.0.3`…). The exit country is shared by every connected device: the last choice made from any app applies to all of them.

The protocol between the apps and the gateway is a small HTTP API, documented in [docs/API.md](docs/API.md).

## Adding a device

Without Ansible or SSH, from a device that is already connected:

1. **New device**: Velvet Relay › VPN › Gateway shows its public key (QR code and text).
2. **Connected device**: VPN › Devices › "Add a device", then scan or paste that key and give it a name. The Raspberry authorises it immediately (without cutting the others off) and assigns it the next free address.
3. **New device**: Gateway › "Invitation code", then scan or paste the code shown. Everything is filled in; all that is left is to connect.

The same screen lists the devices (last connection) and lets you remove one: its access is cut immediately. A device cannot remove itself.

Over SSH on the Raspberry, `velvet-device list | add NAME KEY | invite KEY | remove KEY` does the same thing.

Devices are stored in `/var/lib/velvet-exit/devices.json`, which is reloaded into WireGuard at boot. Only public keys travel: private keys never leave the devices. **Any connected device can add others** (see [What this does and does not protect](#what-this-does-and-does-not-protect)).

The gateway sends the tunnel's TCP to Tor's `TransPort` and its DNS to Tor's `DNSPort`. It routes nothing else: UDP and IPv6 from the tunnel are blocked, and if Tor stops, the tunnel's traffic is cut. The exit country is chosen from the app through a small API (`velvet-exit-api`) reachable only inside the tunnel.

Why a Raspberry: Tor needs about 90 MB of memory, while iOS limits a VPN extension to about 50 MB.

## Designed for a shared Raspberry

The playbook coexists with Docker, Home Assistant, nginx, and so on:

- **Tor runs in a Docker container** (`velvet-tor`, Debian 13, up-to-date Tor with GeoIP). The host system can therefore be older: Debian 11 only ships an unmaintained Tor 0.4.5, and the Tor Project repository no longer publishes for bullseye.
- **The firewall never flushes existing rules.** Velvet Relay uses its own tables (`velvet_filter`, `velvet_nat`), loaded by `velvet-firewall.service`. Docker's rules and the local network are left untouched.
- **Inbound IPv6 from the Internet is filtered.** With IPv6 the router does no NAT: without a filter, every service on the Raspberry is reachable from the Internet. Only the ports in `internet_ipv6_tcp_ports` (22 and 80 by default), WireGuard and ICMPv6 get through. The local network (IPv4 and the router's IPv6 prefix) is not filtered.
- **Automatic rollback**: on every firewall change, the new rules remove themselves after 3 minutes if Ansible can no longer connect.

## Requirements

- A Raspberry Pi running Debian or Raspberry Pi OS (11 or newer), **Docker installed**, SSH access.
- On the administration Mac: `brew install ansible`.
- **To reach the Raspberry** from the iPhone, either:
  - **IPv6, without touching the router**: the Raspberry's IPv6 address is directly reachable. The iPhone must have IPv6, which is the case on 4G/5G but not on every Wi-Fi network.
  - **IPv4**: forward **UDP port 51820** from the router to the Raspberry. If your connection is behind CGNAT (the router's WAN IP differs from your public IP), this is impossible without asking the ISP for a public IP.

## Deployment

1. **SSH key** (if you still log in with a password):

   ```sh
   ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -C "velvet-relay-admin"
   ssh-copy-id -i ~/.ssh/id_ed25519.pub root@RASPBERRY_ADDRESS
   ssh -o PasswordAuthentication=no root@RASPBERRY_ADDRESS true && echo "key OK"
   ```

2. **First device**: Velvet Relay › VPN › Gateway › "Copy public key".
3. **Variables**:

   ```sh
   cd ansible
   cp inventory.example.ini inventory.ini            # the Raspberry's address
   cp group_vars/all.example.yml group_vars/all.yml   # devices: public key + address
   chmod 600 group_vars/all.yml
   ```

   Put the first device in `wireguard_clients` (public key + `/32` address); later ones are added from the app (see "Adding a device"). `wireguard_clients` is only used for the first deployment: it seeds the device registry, which later deployments no longer touch. The old single-device format (`wireguard_client_public_key`) is still accepted.

4. **Dry run, then deploy**:

   ```sh
   ansible-playbook -i inventory.ini site.yml --check --diff
   ansible-playbook -i inventory.ini site.yml
   ```

   The end of the run prints the **Raspberry's public key**, its IPv6 address and the verified Tor exit. WireGuard's private key and Tor's control password are generated on the Raspberry and never leave it.
5. **First device**: in VPN › Gateway, enter the address (`[ipv6]:51820` or `public-ip:51820`) and the Raspberry's public key. Later devices receive an invitation code that fills everything in.
6. **SSH by key only**, once step 1 is verified: set `ssh_disable_password_auth: true` in `all.yml` and run the playbook again.

## Keep your configuration safe

`ansible/inventory.ini` and `ansible/group_vars/all.yml` are specific to your installation (the Raspberry's address, an optional Cloudflare token, your local IPv6 prefix). They are **ignored by git and must never be published**.

Keep a copy somewhere safe, preferably encrypted: a password manager, or `ansible-vault` (AES-256):

```sh
cd ansible
ansible-vault encrypt --output group_vars/all.yml.vault group_vars/all.yml
ansible-vault decrypt --output group_vars/all.yml group_vars/all.yml.vault   # to restore it
```

Do not commit those encrypted copies to a public repository. If you lose the vault password, start again from the `*.example` files (step 3 above).

## Checking that it works

On the Raspberry, **`velvet-diag`** summarises everything: services, the iPhone's last handshake, firewall counters, the verified Tor exit, errors from the last 24 hours, disk space. Its output contains no secret and no client IP address: it can be pasted as is when asking for help.

If the iPhone cannot connect, watch the `wireguard-entrant` counter during an attempt:

- it stays at **0**: packets are not arriving (address typed in the app, no IPv6 on the iPhone's network, router forwarding);
- it **increases without a handshake**: packets arrive but the key does not match (the iPhone's or the Raspberry's key was copied wrongly).

Detailed logs (persistent, kept across reboots):

```sh
journalctl -u velvet-tor -u velvet-exit-api -u wg-quick@wg0 -u velvet-firewall --since today
journalctl -f -u velvet-tor -u velvet-exit-api     # live
```

Counters restart from zero each time the firewall is reloaded. Tor's Docker logs are capped at 2 × 10 MB.

From the iPhone, with the VPN connected:

- the app shows "The whole phone goes through Tor", the exit IP and the requested country;
- Safari on https://check.torproject.org confirms Tor; https://browserleaks.com/ip shows neither your IP nor your ISP's DNS;
- `systemctl stop velvet-tor` on the Raspberry must **cut** the iPhone's browsing, not let it out directly;
- with the Raspberry switched off, the iPhone has no Internet as long as the VPN stays enabled (kill switch). Disconnecting the VPN restores normal access.

## What this does and does not protect

- **What it does:** a device's TCP traffic and DNS leave through Tor, so the sites you visit see a Tor exit instead of your home IP, and anyone watching your network sees only WireGuard traffic to your gateway.
- **What the gateway sees:** the device's IP address and the time of its connections, not the contents of HTTPS traffic.
- **Any connected device is an administrator.** The API has no authentication beyond being reachable only inside the tunnel, so any connected device can add or remove devices. Treat each device key as an admin key, and remove a lost or stolen device immediately (`velvet-device remove KEY`).
- **It is not a replacement for Tor Browser.** Apps can still identify you through logins, cookies or fingerprinting.
- **The gateway is only as trustworthy as the Raspberry it runs on.** Keep it updated.

## Limits

- Only TCP gets through: QUIC, video calls and UDP games are blocked. Most apps fall back to TCP on their own.
- The Raspberry sees the iPhone's IP and the time of connections, not the HTTPS contents.
- Updating Tor: `docker build --pull --tag velvet-tor:latest /etc/velvet/tor-image && systemctl restart velvet-tor`.

## Security

To report a vulnerability, see [SECURITY.md](SECURITY.md).

## License

Released under the [GNU Affero General Public License v3.0](LICENSE) (AGPL-3.0-or-later).

In short: you may use, study, modify and share this gateway, including to run your own. If you offer a **modified** version to other people as a network service, you must make your modified source code available to them under the same license. The Velvet Relay apps are separate programs that only talk to the gateway through its HTTP API ([docs/API.md](docs/API.md)); this license does not apply to them.

This is a summary, not legal advice: the [LICENSE](LICENSE) file is what counts.
