# Tor de la passerelle Velvet Relay.
#
# En conteneur parce que le système hôte peut être plus ancien que Tor ne le
# permet : Debian 11 n'a qu'un Tor 0.4.5 non maintenu, et le dépôt du Tor
# Project ne publie plus pour bullseye. Debian 13 fournit un Tor suivi par
# l'équipe sécurité, avec ses fichiers GeoIP (indispensables à ExitNodes {fr}).
#
# Lancé en réseau hôte (velvet-tor.service) ; la configuration est montée en
# lecture seule depuis /etc/velvet/torrc.
FROM debian:trixie-slim

RUN apt-get update \
    && apt-get install --yes --no-install-recommends tor tor-geoipdb ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Démarré en root pour lier ses ports, Tor passe ensuite à debian-tor
# (directive User du torrc). Le circuit et le consensus persistent ici.
VOLUME /var/lib/tor

ENTRYPOINT ["tor", "-f", "/etc/tor/torrc"]
