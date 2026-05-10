[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/)
![GitHub Release](https://img.shields.io/github/v/release/ghislaingaillot/savefamily-hacs?include_prereleases)
![GitHub Downloads (all assets, latest release)](https://img.shields.io/github/downloads/ghislaingaillot/savefamily-hacs/latest/total)

[![HACS Action](https://github.com/ghislaingaillot/savefamily-hacs/actions/workflows/validate.yaml/badge.svg)](https://github.com/ghislaingaillot/savefamily-hacs/actions/workflows/validate.yaml)
[![Hassfest](https://github.com/ghislaingaillot/savefamily-hacs/actions/workflows/hassfest.yaml/badge.svg)](https://github.com/ghislaingaillot/savefamily-hacs/actions/workflows/hassfest.yaml)

# SaveFamily — Home Assistant Integration

Intégration Home Assistant non officielle pour les montres connectées **SaveFamily** (montres GPS pour enfants).

Cette intégration expose la position GPS, le niveau de batterie, le compteur de pas et le statut de connexion de chaque montre dans Home Assistant.

> **Note technique** : SaveFamily utilise la plateforme backend partagée de 3G Electronics (`myaqsh.com`), identique à YQT Smart, SeTracker et CarePro+. Cette intégration est adaptée du projet open-source [yqt-smart-api](https://github.com/Niek/yqt-smart-api).

---

## Installation

### Via HACS (recommandé)

1. Ouvrir HACS dans Home Assistant
2. Aller dans **Intégrations** → ⋮ → **Dépôts personnalisés**
3. Ajouter l'URL : `https://github.com/ghislaingaillot/savefamily-hacs`
4. Catégorie : **Integration**
5. Rechercher **SaveFamily** et cliquer sur **Télécharger**
6. Redémarrer Home Assistant

### Installation manuelle

1. Télécharger la [dernière version](https://github.com/ghislaingaillot/savefamily-hacs/releases/latest)
2. Copier le dossier `custom_components/savefamily/` dans votre répertoire `config/custom_components/`
3. Redémarrer Home Assistant

---

## Configuration

1. Aller dans **Paramètres** → **Appareils et services** → **Ajouter une intégration**
2. Rechercher **SaveFamily**
3. Remplir le formulaire :

| Champ | Description |
|-------|-------------|
| **Région** | Serveur géographique. Choisir `europe` pour la France/Espagne |
| **Compte** | Email ou numéro de téléphone du compte SaveFamily |
| **Mot de passe** | Mot de passe du compte SaveFamily |
| **App ID** *(avancé)* | Identifiant d'application — laisser la valeur par défaut |

> **Problème de connexion ?** Si vous obtenez une erreur "compte non enregistré", l'App ID de votre version de SaveFamily est peut-être différent. Voir la section [Dépannage](#dépannage).

---

## Entités disponibles

L'intégration crée les entités suivantes pour **chaque montre** associée au compte :

### Device Tracker

| Entité | Description |
|--------|-------------|
| `device_tracker.<nom>_localisation` | Position GPS en temps réel sur la carte Home Assistant |

Attributs supplémentaires : `address`, `speed_kmh`, `direction_degrees`, `accuracy_m`, `position_timestamp`

### Capteurs (Sensors)

| Entité | Unité | Description |
|--------|-------|-------------|
| `sensor.<nom>_batterie` | % | Niveau de charge de la montre |
| `sensor.<nom>_derniere_position` | timestamp | Horodatage de la dernière mise à jour GPS |
| `sensor.<nom>_pas` | steps | Nombre de pas du jour |

### Capteurs binaires (Binary Sensors)

| Entité | Classe | Description |
|--------|--------|-------------|
| `binary_sensor.<nom>_en_ligne` | connectivity | `ON` si la montre a envoyé des données dans les 15 dernières minutes |
| `binary_sensor.<nom>_position_obsolete` | problem | `ON` si la dernière position a plus de 30 minutes |

### Boutons (Buttons)

| Entité | Description |
|--------|-------------|
| `button.<nom>_rafraichir_la_position` | Envoie une commande GPS à la montre pour forcer une mise à jour immédiate |

---

## Mise à jour des données

L'intégration interroge l'API toutes les **5 minutes**. Le bouton "Rafraîchir la position" envoie une commande asynchrone à la montre, puis relance une interrogation 20 secondes plus tard.

---

## Dépannage

### Erreur d'authentification — "account not registered"

La valeur par défaut de l'**App ID** (`aaagg11145`) est celle utilisée par la majorité des applications basées sur la plateforme 3G Electronics. Si elle ne fonctionne pas avec votre version de l'application, vous devrez trouver la valeur correcte en interceptant le trafic réseau de l'application mobile :

1. Installer [mitmproxy](https://mitmproxy.org/) sur votre ordinateur
2. Configurer votre téléphone pour utiliser le proxy
3. Se connecter dans l'application SaveFamily
4. Capturer la requête POST vers `/app/public/S10APP/v2_new_userLogin2`
5. Récupérer la valeur du paramètre `appid`
6. Modifier l'intégration dans Home Assistant → **Reconfigurer** → renseigner la valeur dans le champ "App ID"

### Activer les logs de debug

Ajouter dans `configuration.yaml` :

```yaml
logger:
  default: warning
  logs:
    custom_components.savefamily: debug
```

---

## Contribution

Les contributions sont les bienvenues ! Pour proposer une amélioration ou signaler un bug :

1. Ouvrir une [issue](https://github.com/ghislaingaillot/savefamily-hacs/issues) en utilisant le template approprié
2. Ou soumettre une Pull Request

---

## Crédits

- [Niek](https://github.com/Niek) pour le projet [yqt-smart-api](https://github.com/Niek/yqt-smart-api) et la documentation de reverse engineering de la plateforme 3G Electronics / myaqsh.com
- [SaveFamily](https://savefamily.es) pour les montres connectées

---

## Licence

Ce projet est sous licence [MIT](LICENSE).

---

## Avertissement

Cette intégration est un projet non officiel, sans lien avec SaveFamily. Elle est fournie "telle quelle", sans garantie d'aucune sorte. Utilisez-la à vos propres risques.
