# Gateway API

`velvet_exit_api.py` is the small HTTP API the Velvet Relay apps use to choose the Tor exit country and to manage devices. It uses the Python standard library only.

## Access and trust model

- It listens **only on the gateway's address inside the WireGuard tunnel** (`10.66.0.1:8080` by default). Only devices already connected to the tunnel can reach it, and the traffic is encrypted by WireGuard.
- There is **no other authentication**: every connected device is trusted, including to add and remove other devices. WireGuard's allowed-IPs prevent a device from using another device's tunnel address, and the API identifies the caller by its tunnel address.
- Request bodies are JSON, at most 1024 bytes. Responses are JSON.
- Error messages in the `error` field are currently in French.
- The API is not versioned: the `Server` header starts with `VelvetExit/1`, and there is no version negotiation yet.

## Endpoints

| Method and path | Body | Success |
|---|---|---|
| `GET /status` | — | `200` with the current exit, verified through `check.torproject.org` |
| `POST /exit` | `{"country": "fr"}` or `{"country": null}` | `200`, same payload as `/status` |
| `GET /devices` | — | `200` `{"devices": [...]}` |
| `POST /devices` | `{"name": "iPad", "publicKey": "…"}` | `201` `{"device": {...}, "invitation": {...}}` |
| `POST /devices/invitation` | `{"publicKey": "…"}` | `200` `{"invitation": {...}}` |
| `POST /devices/remove` | `{"publicKey": "…"}` | `200` `{"devices": [...]}` |

Any other path returns `404`.

### `GET /status` and `POST /exit`

```json
{
  "isTor": true,
  "exitIP": "203.0.113.7",
  "country": "fr",
  "requestedCountry": "fr",
  "error": null
}
```

- `POST /exit` takes a lowercase two-letter country code, or `null` for automatic. It applies the country (`ExitNodes` with `StrictNodes`), asks Tor for a new circuit, and returns the verified status. Only one country change runs at a time.
- When the verification fails (the exit is not recognised as Tor, or it is outside the requested country), the status code is `502` and `error` explains why. The country is still applied.
- Errors: `400` for an invalid country code or invalid JSON; `500` when Tor's control port cannot be used.

### Devices

A device is:

```json
{
  "name": "iPad",
  "publicKey": "base64 WireGuard public key (44 characters)",
  "address": "10.66.0.4/32",
  "addedAt": 1767225600,
  "lastHandshake": 1767225700,
  "receivedBytes": 12345,
  "sentBytes": 67890,
  "isCurrent": false
}
```

- `GET /devices` returns every field above. `lastHandshake` is a Unix timestamp, or `null` if the device has never connected. `isCurrent` is `true` for the device making the request.
- `POST /devices` authorises a device and assigns the next free address. The name is 1 to 40 characters without control characters; the key is a base64-encoded 32-byte WireGuard public key. Errors: `400` invalid name or key, `409` already authorised, `507` no free address left in the tunnel.
- `POST /devices/remove` cuts the device's access immediately. A device cannot remove the one it is connected from (`409`); an unknown key returns `404`.

### Invitation

Returned by `POST /devices` and `POST /devices/invitation`. It carries everything a new device needs to connect:

```json
{
  "endpoint": "gateway.example.net:51820",
  "gatewayPublicKey": "base64 WireGuard public key of the gateway",
  "address": "10.66.0.4/32",
  "name": "iPad"
}
```

`endpoint` and `gatewayPublicKey` come from the `VELVET_PUBLIC_ENDPOINT` and `VELVET_GATEWAY_PUBLIC_KEY` environment variables, which the playbook sets.

## Configuration

Environment variables of the service (defaults in parentheses):

| Variable | Purpose |
|---|---|
| `VELVET_API_ADDRESS` (`10.66.0.1`), `VELVET_API_PORT` (`8080`) | Where the API listens |
| `VELVET_TOR_CONTROL` (`127.0.0.1:9051`) | Tor control port |
| `VELVET_TOR_PASSWORD_FILE` (`/etc/velvet/tor-control.pass`) | Tor control password (hexadecimal) |
| `VELVET_TOR_SOCKS` (`127.0.0.1:9050`) | Tor SOCKS port used for the exit verification |
| `VELVET_STATE_FILE` (`/var/lib/velvet-exit/country`) | Remembered exit country, restored after a restart |
| `VELVET_DEVICES_FILE` (`/var/lib/velvet-exit/devices.json`) | Device registry, the source of truth |
| `VELVET_WG_BIN` (`wg`), `VELVET_WG_INTERFACE` (`wg0`) | WireGuard command and interface |
| `VELVET_TUNNEL_NETWORK` (`10.66.0.0/24`) | Tunnel network; the first address belongs to the gateway |
| `VELVET_PUBLIC_ENDPOINT`, `VELVET_GATEWAY_PUBLIC_KEY` | Put in invitations |

At start-up the service reloads the registry into WireGuard and re-applies the remembered exit country. Request logs carry no client IP address and no headers.
