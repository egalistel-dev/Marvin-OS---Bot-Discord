🤖 Marvin OS — Bot Discord pour communautés Makers

"J'ai un cerveau de la taille d'une planète, et on me demande de modérer un serveur Discord. Quelle déchéance."


📸 Aperçu / Screenshots

[Dashboard](screenshots/dashboard.png)[IA](screenshots/ia.png)[Evenements](screenshots/events.png) 


🇫🇷 Français
Présentation
Marvin OS est un bot Discord complet inspiré de Marvin l'androïde paranoïaque du roman H2G2 — Le Guide du Voyageur Galactique. Conçu pour les communautés makers (impression 3D, gravure laser, électronique DIY), il embarque un dashboard web d'administration, un système XP, de la modération automatique, et une IA conversationnelle basée sur Claude (Anthropic).
✨ Fonctionnalités

Système XP & niveaux — attribution automatique de rôles Discord selon les paliers
Modération automatique — anti-spam, anti-raid, mots interdits, logs d'infractions
Dashboard web — interface d'administration Flask avec authentification multi-utilisateurs
Intelligence Artificielle — réponses contextuelles via Claude (Anthropic), vision d'images, mémoire par salon
Événements programmés — annonces automatiques à date/heure définie avec image
Messages promotionnels — envoi conditionnel selon jours, fréquence et activité du salon
Hall of Fame — mise en avant automatique des créations populaires
Salons vocaux temporaires — création/suppression automatique d'ateliers privés
Surveillance YouTube — annonce automatique des nouvelles vidéos
Classement mensuel — top 5 le 1er du mois avec bonus XP
Sauvegarde automatique — backup de la base de données chaque nuit
Récupération de mot de passe — par email SMTP

📋 Prérequis

Python 3.10+
Docker (recommandé)
Un bot Discord (token sur discord.com/developers)
Une clé API Anthropic (optionnel, pour la fonction IA) sur platform.anthropic.com

🚀 Installation
Consultez le guide d'installation complet sur :
👉 https://egamaker.be/mon-bot-discord-guide-dinstallation-complet/
Démarrage rapide

Clonez le repo :

bashgit clone https://github.com/votre-username/marvin-os.git
cd marvin-os

Remplissez le fichier .env avec vos valeurs :

envDISCORD_TOKEN=votre_token_discord
MY_USER_ID=votre_id_discord
SECRET_KEY=une_clé_secrète_aléatoire

Lancez avec Docker Compose :

bashdocker compose up -d

Accédez au dashboard sur http://votre-ip:5050 et suivez le wizard de configuration.

⚙️ Configuration
Toute la configuration se fait depuis le dashboard web (port 5050) :

Salons Discord (annonces, logs, staff, HOF...)
Système XP et rôles
Intelligence Artificielle (clé API, personnalité, sujets interdits...)
Messages promotionnels
Email SMTP pour la récupération de mot de passe

☕ Soutenir le projet
Si Marvin OS vous est utile, vous pouvez offrir un café à son créateur :
<a href="https://www.buymeacoffee.com/egalistelw" target="_blank">
  <img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy Me A Coffee" height="45">
</a>

🇬🇧 English
Overview
Marvin OS is a full-featured Discord bot inspired by Marvin the Paranoid Android from The Hitchhiker's Guide to the Galaxy. Built for maker communities (3D printing, laser engraving, DIY electronics), it includes a web admin dashboard, XP system, automatic moderation, and AI-powered conversations using Claude (Anthropic).
✨ Features

XP & level system — automatic Discord role assignment based on activity
Auto moderation — anti-spam, anti-raid, forbidden words, infraction logs
Web dashboard — Flask admin interface with multi-user authentication
Artificial Intelligence — contextual replies via Claude (Anthropic), image vision, per-channel memory
Scheduled events — automatic announcements at a set date/time with image support
Promotional messages — conditional sending based on days, frequency and channel activity
Hall of Fame — automatic showcase of popular creations
Temporary voice channels — auto-create/delete private workshop channels
YouTube monitoring — automatic new video announcements
Monthly leaderboard — top 5 on the 1st of each month with XP bonus
Automatic backups — nightly database backup
Password recovery — via SMTP email

📋 Requirements

Python 3.10+
Docker (recommended)
A Discord bot token (discord.com/developers)
Anthropic API key (optional, for AI features) at platform.anthropic.com

🚀 Installation
See the full installation guide (in French) at:
👉 https://egamaker.be/mon-bot-discord-guide-dinstallation-complet/
Quick start

Clone the repo:

bashgit clone https://github.com/votre-username/marvin-os.git
cd marvin-os

Fill the .env file with your values:

envDISCORD_TOKEN=your_discord_token
MY_USER_ID=your_discord_user_id
SECRET_KEY=a_random_secret_key

Start with Docker Compose:

bashdocker compose up -d

Open the dashboard at http://your-ip:5050 and follow the setup wizard.

⚙️ Configuration
Everything is configured from the web dashboard (port 5050):

Discord channels (announcements, logs, staff, HOF...)
XP system and roles
AI settings (API key, personality, forbidden topics...)
Promotional messages
SMTP email for password recovery

☕ Support the project
If Marvin OS is useful to you, consider buying its creator a coffee:
<a href="https://www.buymeacoffee.com/egalistelw" target="_blank">
  <img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy Me A Coffee" height="45">
</a>

📁 Structure
marvin-os/
├── bot.py              # Code principal du bot / Main bot code
├── .env                # Variables d'environnement (à remplir, non inclus dans le repo)
├── compose.yaml        # Docker Compose
├── requirements.txt    # Dépendances Python
├── img/                # Images (avatar, classement...) — non inclus dans le repo
└── backups/            # Sauvegardes automatiques — non inclus dans le repo

📄 License
MIT License — voir / see LICENSE
Marvin OS est un projet open source. Les contributions sont les bienvenues !
Marvin OS is an open source project. Contributions are welcome!
