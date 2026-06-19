# Mochi Agent 
![GitHub stars](https://img.shields.io/github/stars/Main-April/Mochi?style=social)
## Ne pas laisser les IA prendre le contrôle du code.

![Logo Mochi](https://raw.githubusercontent.com/Main-April/Mochi/refs/heads/main/logo.ico)

Cet assistant IA vous permet d'interagir avec les modèles OpenRouter que vous voulez. Il vous permet d'écrire du code, de le debugger ou de trouver
de la documentation. Cet outil ne vous permet donc pas de vibe coder, mais d'avoir un assistant directement intégré pour pouvoir augmenter votre 
productivité sans perdre le contrôle de votre code.

## Installation

```
# Installation rapide, dans votre dossier de projet ou non.
git clone "https://github.com/Main-April/Mochi"
```

## Fonctionnalités

| Fonction | Description |
|----------|-------------|
| Streaming SSE | Réponses affichées token par token |
| 4 modes | Work / Docs / Debug / Creative |
| Outils | write_file, read_file, list_files, run_command, web_fetch |
| Parseur réponses | Nettoie le superflu (tableaux, guides, intros) |
| Multi-key fallback | Tente toutes les clés API du `.env` en cascade |
| Mémoire persistante | Sauvegarde automatique dans `memory.json` |
| Stats temps réel | Tokens, messages, durée session, utilisation contexte |
| Journal conversation | Export `.txt` horodaté |
| Dark theme | Palette rose/rouge inspirée du logo |

## Démarrage rapide

```bash
pip install fastapi uvicorn httpx pydantic rich pywebview pillow

# Configurer les clés API
# Éditer .env avec tes clés OpenRouter

python server.py
```

Ouvrir `http://localhost:8000` dans le navigateur.

## Structure

```
Agent/
├── agent.py        # Cœur : Agent, OpenRouter, Memory
├── server.py       # Serveur FastAPI + endpoints REST/SSE
├── parser.py       # Parseur/nettoyeur de réponses IA
├── tools.py        # Outils (fichiers, commandes, fetch)
├── launcher.py     # Fenêtre native pywebview
├── config.json     # Configuration (modes, tokens, rate limits)
├── .env            # Clés API OpenRouter
├── memory.json     # Historique des conversations
├── frontend/
│   ├── index.html  # UI
│   ├── app.js      # Client SSE + rendu markdown
│   ├── style.css   # Thème sombre
│   └── agent-logo.png
└── README.md
```

## Modes

| Mode | Modèle par défaut | Outils | Usage |
|------|-------------------|--------|-------|
| Work | nex-agi/nex-n2-pro:free | Oui | Code, fichiers, commandes |
| Docs | google/gemma-4-31b-it:free | Non | Documentation, questions |
| Debug | openai/gpt-oss-20b:free | Lecture seule | Analyse et correction bugs |
| Creative | nvidia/nemotron-3-super-120b-a12b:free | Non | Génération créative |

Changement de mode dans le header ou par commande `/work`, `/docs`, `/debug`, `/creative`.

## API

| Endpoint | Méthode | Description |
|----------|---------|-------------|
| `/chat` | POST | Réponse complète (non-streaming) |
| `/chat/stream` | POST | Réponse en SSE streaming |
| `/stats` | GET | Statistiques détaillées |
| `/settings` | GET/POST | Lire/modifier configuration |
| `/mode` | POST | Changer de mode |
| `/reset` | POST | Réinitialiser la mémoire |
| `/conversation/log` | GET | Télécharger le journal `.txt` |

## Configuration

### `.env`

```env
OPENROUTER_API_KEY=sk-or-v1-...
WORK_API_KEY=sk-or-v1-...
DOCS_API_KEY=sk-or-v1-...
FALLBACK_KEY=sk-or-v1-...
```

Chaque clé est tentée en ordre si la précédente échoue (429, timeout, etc.).

### `config.json`

- `modes` : modèle, température, max_tokens, outils par mode
- `rate_limits` : RPM max, max_tokens_per_request, retries
- `context` : taille max du contexte, fichier mémoire

## Répertoire projet

Configurable dans le panneau Settings (champ "Répertoire du projet"). Tous les chemins d'outils sont relatifs à ce dossier.
