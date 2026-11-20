# Cartouche

Bibliothèque et lanceur de fichiers **PKG** PlayStation pour Windows :

- **PS4** : installe les PKG avec `pkg_extractor.exe` (fourni avec le shadPS4 Qt Launcher), puis lance les jeux dans **shadPS4**.
- **PS3** : confie les PKG à **RPCS3** (`--installpkg`), puis lance les jeux installés.

shadPS4 ne sait lancer qu'un jeu déjà extrait (`eboot.bin`). Cartouche fait tout le chemin pour toi :
fichier `.pkg` → extraction au bon endroit → bouton **Jouer**.

## Lancer

Double-clic sur **`Lancer Cartouche.bat`**.

L'interface s'ouvre dans sa propre fenêtre (Edge en mode application). Fermer la fenêtre arrête Cartouche
après quelques secondes. Le bouton **Quitter** l'arrête tout de suite.

Seul Python 3.10 ou plus récent est nécessaire, sans module à installer. Tout repose sur la bibliothèque
standard, pour rester léger.

## Ce que fait l'application

| Écran | Rôle |
|---|---|
| **Bibliothèque** | Les jeux installés, avec leur icône, leur version, la mise à jour et les DLC présents, et leur statut dans la base de compatibilité de shadPS4. Un clic sur **Jouer** lance le jeu. |
| **Fichiers PKG** | Tous les `.pkg` trouvés dans tes dossiers (Téléchargements par défaut, avec 3 niveaux de sous-dossiers) : jeu, mise à jour ou DLC, installé ou non, fichier incomplet. Un clic sur **Installer** les installe. |
| **Installations** | La file d'attente, avec la progression, l'annulation (le dossier partiel est supprimé) et le détail de `pkg_extractor`. |
| **Réglages** | Choix de la version de shadPS4, installation de la dernière version officielle en un clic, dossiers, options de lancement et réglages de performance de shadPS4. |
| **Diagnostic** | Vérifications (outils, place disque, réglages qui ralentissent, carte graphique) et analyse du dernier journal de shadPS4, avec des explications en français. |

### Préréglage léger

Le bouton **Préréglage léger** (Réglages › shadPS4 · performances) règle le fichier `config.json` de shadPS4
pour une petite carte graphique :

- il coupe tout ce qui sert au débogage ;
- il garde les shaders compilés, ce qui donne moins de saccades à partir du deuxième lancement ;
- il écrit le journal sans le ralentir ;
- il règle l'écran émulé en 1280 × 720.

Une copie de sauvegarde est faite au premier changement : `config.json.cartouche-backup`.

## Limites honnêtes

- Cartouche ne rend pas une carte graphique plus puissante. Sur une petite carte (GeForce MX, Intel Iris…),
  vise les jeux notés **Jouable** et légers.
- Les PKG chiffrés pour une vraie console ne s'extraient pas. Seuls les PKG que `pkg_extractor` accepte s'installent.
- Les polices système PS4 et le firmware PS3 ne sont pas fournis. Ils se récupèrent sur ta propre console, ou
  sur le site de PlayStation pour le firmware PS3.
- La partie PS3 n'a pas pu être essayée avec un vrai RPCS3 : il n'était pas installé sur la machine de développement.

## Données

Réglages, cache des PKG analysés, liste de compatibilité et journaux de Cartouche :
`%APPDATA%\Cartouche`. Cartouche ne modifie dans shadPS4 que `config.json`, et seulement quand tu le demandes.
Il ajoute aussi ton dossier de jeux à la liste de shadPS4.

Connexions Internet, toutes vers GitHub :

- la liste de compatibilité, une fois par jour ;
- la recherche de nouvelle version de shadPS4, toutes les 6 heures ;
- le téléchargement de shadPS4, seulement si tu cliques sur **Installer**.

## Tests

```bash
python -m unittest discover -s tests -v
```

Les tests fabriquent de faux PKG PS4 et PS3, un faux `pkg_extractor` et une fausse configuration de shadPS4.
Ils vérifient toute la chaîne : analyse, installation, état, lancement, serveur et mise à jour.
