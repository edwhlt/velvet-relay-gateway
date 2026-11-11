# Passerelle Velvet Relay (Raspberry Pi)

Le mode **VPN** des apps iOS et macOS envoie tout le trafic de l'appareil en WireGuard vers cette passerelle, qui le fait passer par Tor :

```text
iPhone / Mac ── WireGuard ──> Raspberry (maison) ── Tor ──> Internet
```

Chaque appareil a sa propre clé et sa propre adresse dans le tunnel (iPhone `10.66.0.2`, Mac `10.66.0.3`…). Le pays de sortie est commun à tous les appareils connectés : le dernier choix fait depuis une app s'applique à tous.

## Ajouter un appareil

Sans Ansible ni SSH, depuis un appareil déjà connecté :

1. **Nouvel appareil** : Velvet Relay › VPN › Passerelle affiche sa clé publique (QR code et texte).
2. **Appareil connecté** : VPN › Appareils › « Ajouter un appareil », puis scanne ou colle cette clé et donne un nom. Le Raspberry l'autorise tout de suite (sans couper les autres) et lui attribue la prochaine adresse libre.
3. **Nouvel appareil** : Passerelle › « Code d'invitation », puis scanne ou colle le code affiché. Tout est rempli, il n'y a plus qu'à se connecter.

Le même écran liste les appareils (dernière connexion) et permet d'en retirer un : son accès est coupé immédiatement. Un appareil ne peut pas se retirer lui-même.

En SSH sur le Raspberry, `velvet-device list | add NOM CLÉ | invite CLÉ | remove CLÉ` fait la même chose.

Les appareils sont enregistrés dans `/var/lib/velvet-exit/devices.json`, rechargé dans WireGuard au démarrage. Seules les clés publiques circulent : les clés privées ne quittent jamais les appareils. Tout appareil connecté peut en ajouter d'autres.

La passerelle envoie le TCP du tunnel dans le `TransPort` de Tor et le DNS dans son `DNSPort`. Elle ne route rien d'autre : UDP et IPv6 du tunnel sont bloqués, et si Tor s'arrête, le trafic du tunnel est coupé. Le pays de sortie se choisit depuis l'app, via une petite API (`velvet-exit-api`) joignable uniquement dans le tunnel.

Pourquoi un Raspberry : Tor a besoin d'environ 90 Mo de mémoire, alors qu'iOS limite une extension VPN à environ 50 Mo.

## Conçu pour un Raspberry partagé

Le playbook cohabite avec Docker, Home Assistant, nginx, etc. :

- **Tor tourne dans un conteneur Docker** (`velvet-tor`, Debian 13, Tor à jour avec GeoIP). Le système hôte peut donc être plus ancien : Debian 11 n'a qu'un Tor 0.4.5 non maintenu, et le dépôt du Tor Project ne publie plus pour bullseye.
- **Le pare-feu ne vide jamais les règles existantes.** Velvet Relay utilise ses propres tables (`velvet_filter`, `velvet_nat`), chargées par `velvet-firewall.service`. Les règles de Docker et le réseau local ne sont pas touchés.
- **L'IPv6 entrant depuis Internet est filtré.** En IPv6, la box ne fait pas de NAT : sans filtre, chaque service du Raspberry est joignable depuis Internet. Seuls les ports de `internet_ipv6_tcp_ports` (par défaut 22 et 80), WireGuard et l'ICMPv6 passent. Le réseau local (IPv4 et préfixe IPv6 de la box) n'est pas filtré.
- **Retour arrière automatique** : à chaque changement de pare-feu, les nouvelles règles sont retirées d'elles-mêmes au bout de 3 minutes si Ansible ne peut plus se connecter.

## Prérequis

- Raspberry Pi avec Debian ou Raspberry Pi OS (11 ou plus récent), **Docker installé**, accès SSH.
- Sur le Mac d'administration : `brew install ansible`.
- **Pour joindre le Raspberry** depuis l'iPhone, au choix :
  - **IPv6, sans toucher à la box** : l'adresse IPv6 du Raspberry est directement joignable. Il faut que l'iPhone ait de l'IPv6, ce qui est le cas en 4G/5G mais pas sur tous les Wi-Fi.
  - **IPv4** : rediriger le port **UDP 51820** de la box vers le Raspberry. Si ta connexion est en CGNAT (l'IP WAN de la box diffère de ton IP publique), c'est impossible sans demander une IP publique à l'opérateur.

## Déploiement

1. **Clé SSH** (si tu te connectes encore par mot de passe) :

   ```sh
   ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -C "velvet-relay-admin"
   ssh-copy-id -i ~/.ssh/id_ed25519.pub root@ADRESSE_DU_RASPBERRY
   ssh -o PasswordAuthentication=no root@ADRESSE_DU_RASPBERRY true && echo "clé OK"
   ```

2. **Premier appareil** : Velvet Relay › VPN › Passerelle › « Copier la clé publique ».
3. **Variables** :

   ```sh
   cd ansible
   cp inventory.example.ini inventory.ini            # adresse du Raspberry
   cp group_vars/all.example.yml group_vars/all.yml   # appareils : clé publique + adresse
   chmod 600 group_vars/all.yml
   ```

   Le premier appareil dans `wireguard_clients` (clé publique + adresse `/32`), les suivants s'ajoutent ensuite depuis l'app (voir « Ajouter un appareil »). `wireguard_clients` ne sert qu'au premier déploiement : il amorce le registre des appareils, que les redéploiements ne touchent plus. L'ancien format à un seul appareil (`wireguard_client_public_key`) reste accepté.

4. **Vérification à blanc, puis déploiement** :

   ```sh
   ansible-playbook -i inventory.ini site.yml --check --diff
   ansible-playbook -i inventory.ini site.yml
   ```

   La fin affiche la **clé publique du Raspberry**, son adresse IPv6 et la sortie Tor vérifiée. La clé privée WireGuard et le mot de passe de contrôle de Tor sont générés sur le Raspberry et n'en sortent jamais.
5. **Premier appareil** : dans VPN › Passerelle, saisis l'adresse (`[ipv6]:51820` ou `ip-publique:51820`) et la clé publique du Raspberry. Les suivants reçoivent un code d'invitation qui remplit tout.
6. **SSH par clé uniquement**, une fois l'étape 1 vérifiée : mets `ssh_disable_password_auth: true` dans `all.yml` et relance le playbook.

## Sauvegarder sa configuration

`ansible/inventory.ini` et `ansible/group_vars/all.yml` sont propres à ton installation (adresse du Raspberry, éventuel jeton Cloudflare, préfixe IPv6 du réseau local). Ils sont **ignorés par git et ne doivent jamais être publiés**.

Garde-en une copie en lieu sûr, de préférence chiffrée : un gestionnaire de mots de passe, ou `ansible-vault` (AES-256) :

```sh
cd ansible
ansible-vault encrypt --output group_vars/all.yml.vault group_vars/all.yml
ansible-vault decrypt --output group_vars/all.yml group_vars/all.yml.vault   # pour la restaurer
```

Ne versionne pas ces copies chiffrées dans un dépôt public. Si tu perds le mot de passe du coffre, repars des fichiers `*.example` (étape 3 ci-dessus).

## Vérifications

Sur le Raspberry, **`velvet-diag`** résume tout : services, dernier handshake de l'iPhone, compteurs du pare-feu, sortie Tor vérifiée, erreurs des dernières 24 h, espace disque. Sa sortie ne contient ni secret ni adresse IP de client : elle peut être copiée telle quelle pour demander de l'aide.

Si l'iPhone n'arrive pas à se connecter, regarde le compteur `wireguard-entrant` pendant une tentative :

- il reste à **0** : les paquets n'arrivent pas (adresse saisie dans l'app, IPv6 absent sur le réseau de l'iPhone, redirection de box) ;
- il **augmente sans handshake** : les paquets arrivent mais la clé ne correspond pas (clé de l'iPhone ou du Raspberry mal recopiée).

Journaux détaillés (persistants, conservés au redémarrage) :

```sh
journalctl -u velvet-tor -u velvet-exit-api -u wg-quick@wg0 -u velvet-firewall --since today
journalctl -f -u velvet-tor -u velvet-exit-api     # en direct
```

Les compteurs repartent de zéro à chaque rechargement du pare-feu. Les logs Docker de Tor sont plafonnés à 2 × 10 Mo.

Depuis l'iPhone, VPN connecté :

- l'app affiche « Tout le téléphone passe par Tor », l'IP de sortie et le pays demandé ;
- Safari sur https://check.torproject.org confirme Tor ; https://browserleaks.com/ip ne montre ni ton IP ni le DNS de ton opérateur ;
- `systemctl stop velvet-tor` sur le Raspberry doit **couper** la navigation de l'iPhone, pas la faire sortir en direct ;
- le Raspberry éteint, l'iPhone n'a plus Internet tant que le VPN reste activé (coupe-circuit). Déconnecter le VPN rend l'accès normal.

## Limites

- Seul le TCP passe : QUIC, appels vidéo et jeux en UDP sont bloqués. La plupart des apps basculent seules sur TCP.
- Le Raspberry voit l'IP de l'iPhone et l'heure des connexions, pas le contenu HTTPS.
- Mettre à jour Tor : `docker build --pull --tag velvet-tor:latest /etc/velvet/tor-image && systemctl restart velvet-tor`.

## Sécurité

Pour signaler une vulnérabilité, voir [SECURITY.md](SECURITY.md) (en anglais).

## Licence

Publié sous [GNU Affero General Public License v3.0](LICENSE) (AGPL-3.0-or-later).

En bref : tu peux utiliser, étudier, modifier et partager cette passerelle, y compris pour faire tourner la tienne. Si tu proposes une version **modifiée** à d'autres personnes comme service réseau, tu dois mettre ton code source modifié à leur disposition sous la même licence. Les apps Velvet Relay sont des programmes distincts qui ne font que parler à la passerelle par son API HTTP ([docs/API.md](docs/API.md), en anglais) : cette licence ne s'applique pas à elles.

C'est un résumé, pas un avis juridique : c'est le fichier [LICENSE](LICENSE) qui fait foi.
