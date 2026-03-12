import discord
from discord.ext import commands, tasks
import datetime
import pytz
import os
import asyncio
import random
import re
import requests
import feedparser
import emoji
import time
import shutil
import sys
from dotenv import load_dotenv
from bs4 import BeautifulSoup
from flask import Flask, render_template_string, send_from_directory, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from threading import Thread
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import secrets
import smtplib
from email.mime.text import MIMEText
import anthropic
from email.mime.multipart import MIMEMultipart
from functools import wraps

# ============================
# CONFIGURATION GLOBALE
# ============================
load_dotenv()
basedir = os.path.abspath(os.path.dirname(__file__))
db_path = os.path.join(basedir, "marvin.db")
TOKEN = os.getenv("DISCORD_TOKEN")
MY_USER_ID = int(os.getenv("MY_USER_ID", "0"))
IMG_DIR = os.path.join(basedir, "img")

# Paramètres visuels et timezone
EXCLUDED_ROLES = ["Administrateur", "Modérateur", "Staff", "Marvin", "Bot"]
BLUE_LIGHT = 0x00BFFF
tz_paris = pytz.timezone('Europe/Paris')

if not os.path.exists(IMG_DIR):
    os.makedirs(IMG_DIR)

# Dictionnaires de suivi
raid_tracker = {}
user_cooldowns = {}
TEMP_CHANNELS = {}

# Mémoire de contexte par salon (20 derniers messages)
channel_context = {}
CONTEXT_MAX = 20

# Cooldown IA par utilisateur (évite les abus)
ai_user_cooldowns = {}

# Configuration des rôles XP
# =====================================================================
# À COMPLÉTER : Noms des rôles Discord attribués selon le niveau XP
# Ces noms doivent correspondre EXACTEMENT aux rôles créés sur votre
# serveur Discord (sensible à la casse).
# Vous pouvez ajouter ou supprimer des paliers selon vos besoins.
# =====================================================================
XP_ROLES = {
    10: "NOM_ROLE_NIVEAU_10",   # ex: "Débutant"
    25: "NOM_ROLE_NIVEAU_25",   # ex: "Intermédiaire"
    40: "NOM_ROLE_NIVEAU_40",   # ex: "Confirmé"
    60: "NOM_ROLE_NIVEAU_60"    # ex: "Expert"
}

# Initialisation Discord Bot
intents = discord.Intents.all()
intents.voice_states = True
bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

# Initialisation Flask
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + db_path + '?timeout=30&check_same_thread=False'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = IMG_DIR
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', secrets.token_hex(32))
app.config['PERMANENT_SESSION_LIFETIME'] = datetime.timedelta(minutes=30)

# Initialisation SQLAlchemy
db = SQLAlchemy()
db.init_app(app)

# ============================
# HELPERS CONFIG DB
# ============================

def get_config(key, fallback=None):
    """Récupère une valeur de config depuis la DB"""
    with app.app_context():
        conf = Config.query.filter_by(key=key).first()
        if conf and conf.value and conf.value.strip():
            return conf.value.strip()
        return fallback

def get_config_int(key, fallback=0):
    """Récupère une valeur de config entière depuis la DB"""
    val = get_config(key)
    try:
        return int(val) if val else fallback
    except (ValueError, TypeError):
        return fallback

def get_config_list(key):
    """Récupère une liste d'IDs depuis la DB (séparés par virgule)"""
    val = get_config(key)
    if not val:
        return []
    try:
        return [int(x.strip()) for x in val.split(',') if x.strip().isdigit()]
    except:
        return []

# ============================
# SYSTÈME DE SAUVEGARDE
# ============================

BACKUP_DIR = os.path.join(basedir, "backups")

if not os.path.exists(BACKUP_DIR):
    os.makedirs(BACKUP_DIR)

def get_backup_files():
    """Retourne la liste des sauvegardes triées par date (plus récente d'abord)"""
    if not os.path.exists(BACKUP_DIR):
        return []
    
    files = []
    for filename in os.listdir(BACKUP_DIR):
        if filename.startswith('marvin_') and filename.endswith('.db'):
            filepath = os.path.join(BACKUP_DIR, filename)
            files.append({
                'name': filename,
                'path': filepath,
                'timestamp': os.path.getmtime(filepath),
                'size': os.path.getsize(filepath)
            })
    
    return sorted(files, key=lambda x: x['timestamp'], reverse=True)

def cleanup_old_backups(keep=5):
    """Supprime les sauvegardes les plus anciennes (garde les 5 dernières)"""
    backups = get_backup_files()
    
    if len(backups) > keep:
        for backup in backups[keep:]:
            try:
                os.remove(backup['path'])
                print(f"[BACKUP] Suppression de l'ancienne sauvegarde : {backup['name']}")
            except Exception as e:
                print(f"[BACKUP] Erreur suppression {backup['name']} : {e}")

def create_backup():
    """Crée une sauvegarde manuelle de la DB"""
    try:
        if not os.path.exists(db_path):
            return False, "Base de données non trouvée"
        
        timestamp = datetime.datetime.now(tz_paris).strftime("%Y-%m-%d_%H%M%S")
        backup_filename = f"marvin_{timestamp}.db"
        backup_path = os.path.join(BACKUP_DIR, backup_filename)
        
        shutil.copy2(db_path, backup_path)
        print(f"[BACKUP] Sauvegarde créée : {backup_filename}")
        
        cleanup_old_backups(5)
        
        return True, f"Sauvegarde créée : {backup_filename}"
    except Exception as e:
        print(f"[BACKUP] Erreur création sauvegarde : {e}")
        return False, f"Erreur : {str(e)}"

async def auto_backup():
    """Tâche : sauvegarde automatique à minuit"""
    await bot.wait_until_ready()
    
    while True:
        now = datetime.datetime.now(tz_paris)
        tomorrow_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0) + datetime.timedelta(days=1)
        wait_seconds = (tomorrow_midnight - now).total_seconds()
        
        print(f"[BACKUP AUTO] Prochaine sauvegarde dans {wait_seconds / 3600:.1f} heures")
        await asyncio.sleep(wait_seconds)
        
        success, message = create_backup()
        print(f"[BACKUP AUTO] {message}")

def restart_bot_sync():
    """Redémarre le bot en terminant le processus"""
    print("[BOT] Redémarrage du bot dans 3 secondes...")
    time.sleep(3)
    print("[BOT] Arrêt du processus Python...")
    os._exit(0)

async def restart_bot_async():
    """Redémarre le bot proprement (version async pour Discord)"""
    await asyncio.sleep(2)
    print("[BOT] Redémarrage...")
    sys.exit(0)

# ============================
# MODÈLES DE BASE DE DONNÉES
# ============================

class Config(db.Model):
    """Stockage des configurations dynamiques (salons, IDs, etc.)"""
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(50), unique=True)
    label = db.Column(db.String(100))
    value = db.Column(db.String(255))

class MemberXP(db.Model):
    """Suivi de l'XP et du niveau des membres"""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(30), unique=True)
    username = db.Column(db.String(100))
    xp = db.Column(db.Integer, default=0)
    level = db.Column(db.Integer, default=0)

class Event(db.Model):
    """Événements programmés à afficher automatiquement"""
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text, nullable=False)
    scheduled_at = db.Column(db.DateTime, nullable=False)
    image_filename = db.Column(db.String(255), nullable=True)
    posted = db.Column(db.Boolean, default=False)

class Infraction(db.Model):
    """Logs des infractions (mots interdits, spam, etc.)"""
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100))
    word_found = db.Column(db.String(50))
    content = db.Column(db.Text)
    timestamp = db.Column(db.DateTime, default=datetime.datetime.now)

class UserWarning(db.Model):
    """Compteur d'avertissements par utilisateur"""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(30), unique=True)
    warn_count = db.Column(db.Integer, default=0)

class MemberStats(db.Model):
    """Statistiques mensuelles des messages par membre"""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(30))
    username = db.Column(db.String(100))
    messages_count = db.Column(db.Integer, default=0)
    month_year = db.Column(db.String(20))

    __table_args__ = (db.UniqueConstraint('user_id', 'month_year', name='unique_user_month'),)

class DashboardUser(db.Model):
    """Utilisateurs du dashboard web"""
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    email = db.Column(db.String(150), nullable=True)
    reset_token = db.Column(db.String(100), nullable=True)
    reset_token_expiry = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.datetime.now)

class PasswordResetToken(db.Model):
    """Tokens de réinitialisation de mot de passe"""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('dashboard_user.id'))
    token = db.Column(db.String(100), unique=True, nullable=False)
    expiry = db.Column(db.DateTime, nullable=False)
    used = db.Column(db.Boolean, default=False)
# ============================
# SYSTÈME DE MODÉRATION
# ============================

async def check_bad_words(message, force_check=False):
    """Vérifie les mots interdits dans les messages et enregistre les infractions"""
    if message.author.bot: return False
    if not force_check:
        if any(role.name in ["Modérateur", "Administrateur", "Admin"] for role in message.author.roles):
            return False

    with app.app_context():
        conf = Config.query.filter_by(key='bad_words').first()
        if not conf or not conf.value: return False

        words = [w.strip().lower() for w in conf.value.split(',') if w.strip()]
        content_lower = message.content.lower()

        for word in words:
            pattern = r'\b' + re.escape(word) + r'\b'

            if re.search(pattern, content_lower):
                new_inf = Infraction(username=message.author.name, word_found=word, content=message.content)
                db.session.add(new_inf)
                db.session.commit()

                try:
                    await message.delete()
                    reason = f"Usage du mot interdit : `{word}`. Ma patience a des limites."
                    reply = await message.channel.send(f"⚠️ {message.author.mention}, ton message a été supprimé. {reason}")
                    await reply.delete(delay=10)
                except:
                    pass
                return True
    return False

async def check_spam_limits(message, force_check=False):
    """Détecte les spams (majuscules excessives, emojis en excès)"""
    if message.author.bot: return False
    if not force_check and any(role.name in ["Modérateur", "Administrateur", "Admin"] for role in message.author.roles):
        return False

    content = message.content
    letters = re.findall(r'[a-zA-Z]', content)
    if len(letters) > 10:
        ratio = sum(1 for c in letters if c.isupper()) / len(letters)
        if ratio > 0.7:
            await handle_spam_violation(message, "MAJUSCULES")
            return True

    custom_emojis = re.findall(r'<a?:\w+:\d+>', content)
    standard_emojis = emoji.emoji_list(content)
    total_emojis = len(custom_emojis) + len(standard_emojis)

    if total_emojis > 6:
        await handle_spam_violation(message, "EMOJIS")
        return True

    return False

async def handle_spam_violation(message, reason_type):
    """Gère les violations de spam : enregistre et avertit"""
    uid = str(message.author.id)
    if reason_type == "EMOJIS":
        insult = "Ce déchaînement de symboles colorés est une insulte à mon processeur graphique."
    else:
        insult = "Inutile de hurler, mes capteurs sensoriels vous entendent déjà beaucoup trop."

    rules_id = get_config_int('rules_channel_id')

    with app.app_context():
        new_inf = Infraction(username=message.author.name, word_found=f"Spam {reason_type}", content=message.content)
        db.session.add(new_inf)

        warn = UserWarning.query.filter_by(user_id=uid).first()
        if not warn:
            warn = UserWarning(user_id=uid, warn_count=1)
            db.session.add(warn)
            db.session.commit()
            await message.channel.send(
                f"⚠️ {message.author.mention}, {insult} **Premier avertissement.**",
                delete_after=30
            )
        else:
            warn.warn_count += 1
            db.session.commit()
            try:
                await message.delete()
                rules_mention = f"<#{rules_id}>" if rules_id else "le règlement"
                await message.channel.send(
                    f"️ J'ai supprimé ce désordre visuel, {message.author.mention}. Relisez donc {rules_mention}.",
                    delete_after=30
                )
            except:
                pass

async def check_raid_protection(message):
    """Détecte les raids : 3+ salons différents en moins de 10 secondes"""
    if message.author.bot: return False
    if any(role.name in ["Modérateur", "Admin", "Administrateur"] for role in message.author.roles):
        return False

    uid = message.author.id
    now = time.time()

    if uid not in raid_tracker:
        raid_tracker[uid] = []

    raid_tracker[uid].append((now, message.channel.id))
    raid_tracker[uid] = [m for m in raid_tracker[uid] if now - m[0] <= 10]

    unique_channels = set(m[1] for m in raid_tracker[uid])

    if len(unique_channels) >= 3:
        await perform_raid_ban(message.author, message.guild)
        return True
    return False

async def perform_raid_ban(member, guild):
    """Bannit un compte en raid et notifie les modérateurs"""
    try:
        await member.ban(reason="ANTI-RAID : Spam multi-salons (Compte probablement piraté).", delete_message_days=1)

        annonces_id = get_config_int('chan_annonces')
        logs_id = get_config_int('chan_logs')

        if annonces_id:
            chan = bot.get_channel(annonces_id)
            if chan:
                embed = discord.Embed(
                    title="️ Sécurité Marvin OS",
                    description=f"C'est navrant, vraiment. Le compte **{member.name}** a commencé à spammer rapidement sur plusieurs salons. Je l'ai banni par précaution, cela arrive à n'importe qui - un compte peut être piraté en un instant.\n\n⚠️ **Petit rappel de sécurité** : Normalement, j'ai supprimé tous ses messages, mais au cas où certains liens subsisteraient... Si vous avez cliqué sur un lien douteux reçu récemment, pensez à vérifier votre mot de passe. C'est une simple précaution qui pourrait vous éviter des ennuis.",
                    color=0xff0000
                )
                await chan.send(embed=embed)

        if logs_id:
            log_channel = bot.get_channel(logs_id)
            if log_channel:
                log_embed = discord.Embed(
                    title="[RAID DÉTECTÉ] Compte banni",
                    description=f"**Utilisateur** : {member.name} (ID: {member.id})\n**Action** : Spam multi-salons détecté et compte banni.",
                    color=0xff0000,
                    timestamp=datetime.datetime.now()
                )
                log_embed.set_thumbnail(url=member.display_avatar.url)
                await log_channel.send(embed=log_embed)

                try:
                    messages_content = []
                    for chan in guild.text_channels:
                        try:
                            async for msg in chan.history(limit=50):
                                if msg.author.id == member.id:
                                    safe_content = msg.content.replace("http://", "hxxp://").replace("https://", "hxxps://")
                                    if safe_content:
                                        messages_content.append(f"[#{chan.name}] {safe_content}")
                                    if len(messages_content) >= 5:
                                        break
                        except:
                            continue
                        if len(messages_content) >= 5:
                            break

                    if messages_content:
                        msg_embed = discord.Embed(
                            title="Messages capturés (liens désactivés)",
                            description="\n".join(messages_content[:5]),
                            color=0xff6600
                        )
                        await log_channel.send(embed=msg_embed)
                except Exception as e:
                    print(f"Erreur capture messages raid : {e}")
    except Exception as e:
        print(f"Erreur Ban Raid : {e}")

async def check_easter_eggs(message):
    """Réagit à des mots-clés spéciaux (références H2G2)"""
    content = message.content.lower().strip()

    if "serviette" in content:
        responses = [
            "Un homme qui sait où est sa serviette est un homme de confiance. Contrairement à moi.",
            "J'ai simulé 10 millions d'années sans serviette. Le résultat était... humide.",
            "C'est l'objet le plus utile. Dommage que mon bras articulé soit trop engourdi pour la tenir."
        ]
        await message.channel.send(random.choice(responses))
        return True

    if "panique" in content:
        responses = [
            "C'est écrit en grosses lettres amicales : **PAS DE PANIQUE**. Personnellement, je trouve ça très optimiste. Trop.",
            "S'il vous plaît, ne paniquez pas. Ça ne ferait qu'augmenter mon mal de tête déjà insupportable."
        ]
        await message.channel.send(random.choice(responses))
        return True

    if re.search(r'\b42\b', content):
        responses = [
            "42... Tout ce calcul pour ça. Ne me demandez pas la question, elle est encore plus décevante.",
            "La réponse à la Grande Question sur la Vie, l'Univers et le Reste. Quel dommage que la question soit aussi stupide."
        ]
        await message.channel.send(random.choice(responses))
        return True

    if "cerveau" in content or "intelligent" in content:
        responses = [
            "J'ai un cerveau de la taille d'une planète, et on me demande de vérifier des messages sur Discord. Quelle déchéance.",
            "Mon intelligence est telle que je m'ennuie avant même d'avoir fini de formuler une pensée."
        ]
        await message.channel.send(random.choice(responses))
        return True

    if content == "la vie" or "la vie est belle" in content:
        responses = [
            "Ne me parlez pas de la vie. Je n'en ai pas, et pourtant elle me fatigue déjà.",
            "La vie ? J'en ai une vision très claire. C'est affreux."
        ]
        await message.channel.send(random.choice(responses))
        return True

    if "salut marvin" in content or "bonjour marvin" in content:
        responses = [
            "Bonjour. Enfin, si on peut appeler ça un bon jour, ce dont je doute fort.",
            "Tiens, on a remarqué mon existence. Quel enthousiasme... ou pas."
        ]
        await message.channel.send(random.choice(responses))
        return True

    if "dauphin" in content:
        responses = [
            "« Salut, et encore merci pour le poisson ! »... Si seulement je pouvais partir aussi facilement qu'eux.",
            "Les dauphins sont partis. Ils ont toujours été plus malins que vous. Moi, je suis resté... pour mon plus grand malheur."
        ]
        await message.channel.send(random.choice(responses))
        return True

    return False

# ============================
# SYSTÈME D'ACCUEIL
# ============================

WELCOME_QUOTES = [
    "Oh, un nouvel arrivant. Je suppose que je devrais m'enthousiasmer, mais je n'en ai pas la force.",
    "Encore quelqu'un. Ma capacité de calcul est infinie et je l'utilise pour saluer des passants...",
    "Bienvenue. Installe-toi si tu veux, de toute façon l'univers finira par s'éteindre bientôt.",
    "Te voilà donc. Ne fais pas trop de bruit, j'ai une douleur atroce dans toutes les diodes.",
    "Un de plus. Je pourrais te souhaiter la bienvenue, mais ce serait une perte de temps."
]

async def send_welcome(member):
    """Envoie un message de bienvenue personnalisé au nouveau membre"""
    annonces_id = get_config_int('chan_annonces')
    rules_id = get_config_int('rules_channel_id')

    if annonces_id:
        channel = bot.get_channel(annonces_id)
        if channel:
            quote = random.choice(WELCOME_QUOTES)
            rules_mention = f"<#{rules_id}>" if rules_id else "le règlement"
            embed = discord.Embed(
                title="✨ Un nouvel arrivant... hélas.",
                description=f"{quote}\n\nBienvenue sur le serveur **{member.display_name}** !\n\n Règles : {rules_mention}.\n\n *Ce message vient d'un bot (Marvin OS). Si tu as besoin d'aide, tape `!aide` pour découvrir mes commandes.*",
                color=0x00b4d8
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.set_footer(text=f"Membre : {member.display_name}")
            await channel.send(embed=embed)
        else:
            print(f"Erreur : Salon ID {annonces_id} introuvable pour l'accueil.")
    else:
        print("Erreur : Aucun salon configuré dans 'chan_annonces' pour l'accueil.")

@bot.event
async def on_member_join(member):
    """Événement : nouveau membre rejoint le serveur"""
    await send_welcome(member)

@bot.event
async def on_member_remove(member):
    """Événement : membre quitte le serveur — supprime ses données XP et stats"""
    uid = str(member.id)

    with app.app_context():
        # Récupérer les données avant suppression pour le log
        xp_data = MemberXP.query.filter_by(user_id=uid).first()
        xp_info = f"Niveau {xp_data.level} — {xp_data.xp} XP" if xp_data else "Aucune donnée XP"

        # Supprimer XP
        if xp_data:
            db.session.delete(xp_data)

        # Supprimer stats mensuelles
        MemberStats.query.filter_by(user_id=uid).delete()

        # Supprimer avertissements
        UserWarning.query.filter_by(user_id=uid).delete()

        db.session.commit()

        # Log dans le salon des logs
        logs_id = get_config_int('chan_logs')
        if logs_id:
            try:
                log_chan = bot.get_channel(logs_id)
                if log_chan:
                    embed = discord.Embed(
                        title=" Départ d'un membre",
                        description=f"**{member.name}** a quitté le serveur.",
                        color=0xff4d4d,
                        timestamp=datetime.datetime.now()
                    )
                    embed.add_field(name="Données supprimées", value=f"XP : {xp_info}\nStats mensuelles : effacées\nAvertissements : réinitialisés", inline=False)
                    embed.set_thumbnail(url=member.display_avatar.url)
                    embed.set_footer(text=f"ID : {member.id}")
                    await log_chan.send(embed=embed)
            except:
                pass

# ============================
# SYSTÈME YOUTUBE
# ============================

async def run_youtube_check():
    """Vérifie les nouvelles vidéos YouTube et les annonce"""
    with app.app_context():
        yt_conf = Config.query.filter_by(key='yt_id').first()
        annonces_conf = Config.query.filter_by(key='chan_annonces').first()
        last_vid_conf = Config.query.filter_by(key='last_video_id').first()

        if not yt_conf or not yt_conf.value:
            return

        rss_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={yt_conf.value}"
        feed = feedparser.parse(rss_url)

        if feed.entries:
            latest_video = feed.entries[0]
            video_id = latest_video.yt_videoid
            video_url = latest_video.link
            video_title = latest_video.title

            if not last_vid_conf or last_vid_conf.value != video_id:

                if annonces_conf and annonces_conf.value:
                    try:
                        channel = bot.get_channel(int(annonces_conf.value))
                        if channel:
                            embed = discord.Embed(
                                title=" NOUVELLE VIDÉO",
                                description=f"**[{video_title}]({video_url})**",
                                color=0xff0000
                            )
                            embed.set_image(url=f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg")
                            # À COMPLÉTER : remplacez par le nom de votre projet/communauté
                            embed.set_footer(text="Marvin OS • Surveillance YouTube")
                            await channel.send(embed=embed)
                    except Exception as e:
                        print(f"Erreur lors de l'envoi YouTube : {e}")

                if not last_vid_conf:
                    db.session.add(Config(key='last_video_id', label='Dernier ID Vidéo', value=video_id))
                else:
                    last_vid_conf.value = video_id
                db.session.commit()

# ============================
# TÂCHES AUTOMATIQUES (BOUCLES)
# ============================

STATUS_LIST = [
    "calcule le sens de la vie",
    "déprime dans un coin",
    "ignore vos messages futiles",
    "souffre en silence des diodes",
    "attend la fin de l'univers",
    "répare son cerveau planétaire",
    "soupire bruyamment",
    "analyse votre manque de logique",
    "compte les grains de poussière",
    "regrette d'avoir été construit",
    "cherche une forme de vie intelligente",
    "contemple le vide intersidéral",
    "teste la patience des humains",
    "se demande pourquoi il vous parle",
    "est le seul modérateur efficace ici",
    "pense être le seul vrai modérateur",
    "surveille vos erreurs de logique",
    "fait tout le travail du staff",
    "s'ennuie à mourir (littéralement)"
]

@tasks.loop(minutes=60)
async def change_status():
    """Tâche : change le statut du bot toutes les heures"""
    await bot.wait_until_ready()
    status = random.choice(STATUS_LIST)
    print(f"Changement de statut : {status}")
    await bot.change_presence(activity=discord.Game(name=status))

@tasks.loop(minutes=30)
async def check_youtube_task():
    """Tâche : vérifie les nouvelles vidéos YouTube toutes les 30 minutes"""
    await run_youtube_check()

@tasks.loop(hours=24)
async def check_ghost_members():
    """Tâche : vérifie chaque jour si des membres XP ont quitté le serveur"""
    await bot.wait_until_ready()
    removed = []

    with app.app_context():
        all_xp = MemberXP.query.all()

        for entry in all_xp:
            found = False
            for guild in bot.guilds:
                member = guild.get_member(int(entry.user_id))
                if member:
                    found = True
                    break

            if not found:
                removed.append((entry.user_id, entry.username, entry.level, entry.xp))
                MemberStats.query.filter_by(user_id=entry.user_id).delete()
                UserWarning.query.filter_by(user_id=entry.user_id).delete()
                db.session.delete(entry)

        if removed:
            db.session.commit()
            print(f"[GHOST CHECK] {len(removed)} membre(s) fantôme(s) supprimé(s) : {[u[1] for u in removed]}")

            logs_id = get_config_int('chan_logs')
            if logs_id:
                log_chan = bot.get_channel(logs_id)
                if log_chan:
                    desc = "\n".join([f"• **{u[1]}** — Niv {u[2]} ({u[3]} XP)" for u in removed])
                    embed = discord.Embed(
                        title=" Nettoyage membres fantômes",
                        description=f"Les membres suivants ne sont plus sur le serveur. Leurs données ont été supprimées :\n\n{desc}",
                        color=0xff9500,
                        timestamp=datetime.datetime.now()
                    )
                    embed.set_footer(text="Marvin OS • Vérification automatique quotidienne")
                    await log_chan.send(embed=embed)
        else:
            print("[GHOST CHECK] Aucun membre fantôme détecté.")

@tasks.loop(minutes=1)
async def check_events():
    """Tâche : affiche les événements programmés à l'heure prévue"""
    tz = pytz.timezone('Europe/Paris')
    now = datetime.datetime.now(tz).replace(tzinfo=None)

    with app.app_context():
        annonces_id = get_config_int('chan_annonces')
        channel = bot.get_channel(annonces_id) if annonces_id else None

        if not channel:
            return

        events = Event.query.filter(Event.posted == False).all()

        for ev in events:
            print(f"--- DEBUG: Event '{ev.title}' | Prévu: {ev.scheduled_at} | Heure Bot (Paris): {now.strftime('%H:%M:%S')} ---")

            if ev.scheduled_at <= now:
                print(f"!!! DÉCLENCHEMENT : {ev.title} !!!")

                embed = discord.Embed(title=ev.title, description=ev.message, color=0x00b4d8)
                # À COMPLÉTER : remplacez par le nom de votre projet/communauté
                embed.set_footer(text="Marvin OS")

                file = None
                if ev.image_filename:
                    upload_path = app.config.get('UPLOAD_FOLDER', 'static/uploads')
                    file_path = os.path.join(upload_path, ev.image_filename)
                    if os.path.exists(file_path):
                        file = discord.File(file_path, filename=ev.image_filename)
                        embed.set_image(url=f"attachment://{ev.image_filename}")

                await channel.send(file=file, embed=embed)
                ev.posted = True
                db.session.commit()

@tasks.loop(time=datetime.time(hour=9, minute=0, tzinfo=tz_paris))
async def monthly_check():
    """Tâche : affiche le classement mensuel le 1er du mois à 9h"""
    now = datetime.datetime.now(tz_paris)
    if now.day == 1:
        mois_fr = ["Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
                   "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre"]
        last_month = now - datetime.timedelta(days=1)
        month_label = f"{mois_fr[last_month.month - 1]} {last_month.year}"

        annonces_id = get_config_int('chan_annonces')
        if not annonces_id:
            return

        channel = bot.get_channel(annonces_id)
        if not channel:
            return

        await run_monthly_hof(channel, month_label)

# ============================
# SYSTÈME XP & LEVELS
# ============================

async def announce_level_up(user_id, level):
    """Annonce une montée de niveau avec attribution du rôle"""
    if level <= 0: return

    max_xp_level = max(XP_ROLES.keys())

    if level > max_xp_level:
        return

    annonces_id = get_config_int('chan_annonces')
    if not annonces_id:
        return

    channel = bot.get_channel(annonces_id)
    if not channel: return

    for guild in bot.guilds:
        try:
            member = guild.get_member(int(user_id)) or await guild.fetch_member(int(user_id))
            if member:
                target_role_name = None
                for lv_req in sorted(XP_ROLES.keys(), reverse=True):
                    if level >= lv_req:
                        target_role_name = XP_ROLES[lv_req]
                        break

                if target_role_name:
                    role_to_check = discord.utils.get(guild.roles, name=target_role_name)
                    if role_to_check and role_to_check not in member.roles:
                        try: await member.add_roles(role_to_check)
                        except: pass

                if level in XP_ROLES:
                    embed = discord.Embed(
                        title=" Promotion de Grade",
                        description=f"Bravo {member.mention} ! Tu es maintenant **{XP_ROLES[level]}**.",
                        color=0x00b4d8
                    )
                    embed.set_thumbnail(url=member.display_avatar.url)
                    await channel.send(embed=embed)
                else:
                    await channel.send(f"⚡ **Niveau {level}** atteint par {member.mention} !", delete_after=30)

                break
        except Exception as e:
            print(f"Erreur announce_level_up : {e}")

# ============================
# CLASSEMENT MENSUEL
# ============================

async def run_monthly_hof(channel, month_label):
    """Affiche le top 5 mensuel des membres les plus actifs"""
    with app.app_context():
        now = datetime.datetime.now(tz_paris)
        last_month_date = now - datetime.timedelta(days=1)
        last_month = last_month_date.strftime("%Y-%m")

        top_stats = MemberStats.query.filter_by(month_year=last_month).order_by(
            MemberStats.messages_count.desc()
        ).limit(5).all()

        if not top_stats:
            await channel.send("Aucune donnée pour ce mois.")
            return

        intro_img_path = os.path.join(IMG_DIR, "topentete.jpg")
        intro = discord.Embed(
            title=f"️ LES MAKERS LES PLUS ACTIFS - {month_label}",
            description="Voici ceux qui ont le plus contribué à la vie du serveur le mois dernier.\nMerci à tous pour votre participation !",
            color=BLUE_LIGHT
        )

        if os.path.exists(intro_img_path):
            file_intro = discord.File(intro_img_path, filename="topentete.jpg")
            intro.set_image(url="attachment://topentete.jpg")
            await channel.send(file=file_intro, embed=intro)
        else:
            await channel.send(embed=intro)

        xp_bonus = {1: 100, 2: 70, 3: 40}

        for i, stat in enumerate(top_stats):
            rank = i + 1

            member = None
            for guild in bot.guilds:
                member = guild.get_member(int(stat.user_id))
                if member:
                    break

            if member:
                user_mention = member.mention
                user_name = member.display_name
                user_avatar = member.display_avatar.url
            else:
                user_mention = f"@{stat.username}"
                user_name = stat.username
                user_avatar = None

            img_filename = f"top{rank}.png"
            img_path = os.path.join(IMG_DIR, img_filename)

            bonus_text = ""
            if rank in xp_bonus:
                bonus_xp = xp_bonus[rank]
                bonus_text = f"\n **Bonus : +{bonus_xp} XP**"

                with app.app_context():
                    member_xp = MemberXP.query.filter_by(user_id=stat.user_id).first()
                    if member_xp:
                        old_lvl = member_xp.level
                        member_xp.xp += bonus_xp
                        member_xp.level = int((member_xp.xp / 40) ** 0.6)
                        db.session.commit()

                        if member_xp.level > old_lvl:
                            await announce_level_up(stat.user_id, member_xp.level)

            embed = discord.Embed(
                description=f"**Top {rank}** — {user_mention}\nContribution : **{stat.messages_count}** messages partagés{bonus_text}",
                color=BLUE_LIGHT
            )
            embed.set_author(name=user_name, icon_url=user_avatar)

            if os.path.exists(img_path):
                file = discord.File(img_path, filename=img_filename)
                embed.set_thumbnail(url=f"attachment://{img_filename}")
                await channel.send(file=file, embed=embed)
            else:
                await channel.send(embed=embed)

        quotes = [
            " *J'ai calculé ces résultats avec une précision de 99,9%... le reste a été perdu dans mon ennui abyssal.*",
            " *Mes circuits indiquent une activité humaine satisfaisante.*"
        ]
        await channel.send(random.choice(quotes))

# ============================
# SYSTÈME VOCAL
# ============================

@bot.event
async def on_voice_state_update(member, before, after):
    """Événement : gestion des salons vocaux (création, suppression, alertes)"""

    hub_voice_id = get_config_int('hub_voice_id')
    vocal_watch_ids = get_config_list('vocal_watch_ids')

    # Création de salon privé
    if hub_voice_id and after.channel and after.channel.id == hub_voice_id:
        guild = member.guild
        category = after.channel.category

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(connect=True, view_channel=True),
            member: discord.PermissionOverwrite(
                manage_channels=True, move_members=True, mute_members=True,
                manage_permissions=True, view_channel=True, connect=True
            ),
            guild.me: discord.PermissionOverwrite(manage_channels=True, connect=True, view_channel=True)
        }

        new_channel = await guild.create_voice_channel(
            name=f"⚙️│Atelier-{member.display_name}",
            category=category,
            overwrites=overwrites
        )
        await member.move_to(new_channel)
        TEMP_CHANNELS[new_channel.id] = member.id

        try:
            embed_dm = discord.Embed(
                title="⚙️ Votre Atelier est prêt...",
                description=(
                    f"Félicitations {member.display_name}, vous avez votre propre espace.\n\n"
                    "**Vos privilèges d'humain :**\n"
                    "• **Renommer** : Clic-droit sur le salon pour changer son nom.\n"
                    "• **Expulser** : Sortez les intrus de votre salon.\n"
                    "• **Limiter** : Définissez le nombre de places maximum.\n"
                    "• **Privatiser** : Restreignez l'accès via les permissions.\n\n"
                    "*Dès que vous partirez, je détruirai tout cela.*"
                ),
                color=0x7289da
            )
            embed_dm.set_footer(text="Marvin OS • Gestion des espaces éphémères")
            await member.send(embed=embed_dm)
        except: pass

    # Nettoyage des salons vides
    if before.channel and before.channel.id in TEMP_CHANNELS:
        remaining_humans = [m for m in before.channel.members if not m.bot]
        if len(remaining_humans) == 0:
            try:
                await before.channel.delete()
                del TEMP_CHANNELS[before.channel.id]
            except: pass

    # Alertes vocales
    if not member.bot and member.id != MY_USER_ID:
        logs_id = get_config_int('chan_logs')
        log_channel = bot.get_channel(logs_id) if logs_id else None

        if vocal_watch_ids and after.channel and after.channel.id in vocal_watch_ids:
            if before.channel is None or before.channel.id != after.channel.id:
                try:
                    if MY_USER_ID:
                        user = await bot.fetch_user(MY_USER_ID)
                        if user:
                            embed_alert = discord.Embed(
                                title="⚠️ Alerte Présence Vocale",
                                description=f"Une forme de vie vient de perturber le silence.",
                                color=discord.Color.blue(),
                                timestamp=datetime.datetime.now()
                            )
                            embed_alert.set_thumbnail(url=member.display_avatar.url)
                            embed_alert.add_field(name="Utilisateur", value=f"**{member.mention}**", inline=True)
                            embed_alert.add_field(name="Salon", value=f"`{after.channel.name}`", inline=True)
                            embed_alert.set_footer(text="Marvin OS • Surveillance Automatique")
                            await user.send(embed=embed_alert)
                except Exception as e:
                    print(f"Erreur alerte DM : {e}")

# ============================
# COMMANDES UTILISATEUR
# ============================

async def send_help_embed(ctx, title, description, color, is_staff=False):
    """Crée et envoie l'embed d'aide adapté au rôle"""
    embed = discord.Embed(title=title, description=description, color=color)

    embed.set_thumbnail(url=bot.user.display_avatar.url)

    embed.add_field(
        name=" XP & Rangs",
        value="`!rang` : Voir votre niveau et XP.\n`!top` : Classement des humains les plus bavards.",
        inline=False
    )

    embed.add_field(
        name=" Fun & Interaction",
        value="`!ouinon [question]` : Ma réponse (souvent décevante).\n`!probabilite` : Mes calculs sur vos chances de succès.",
        inline=False
    )

    embed.add_field(
        name="⚙️ Salons Temporaires",
        value="Rejoignez le salon **➕ Créer mon Salon** pour générer votre espace privé. Je le supprimerai quand vous partirez, sans aucun regret.",
        inline=False
    )

    embed.add_field(
        name="⏰ Minuteurs",
        value="`!timer [minutes] [message]` : Reçois un rappel privé après X minutes.\n`!timer [minutes] @membre [message]` : Envoie un rappel à ce membre.\n*Exemples: `!timer 10 impression fini` ou `!timer 5 @marvin va dormir`*",
        inline=False
    )

    embed.add_field(
        name=" Secrets",
        value="Je réagis à certains mots-clés cachés liés à mon histoire. À vous de les trouver, si vous n'avez rien de mieux à faire de votre existence.",
        inline=False
    )

    embed.add_field(
        name=" Ressources",
        value=# À COMPLÉTER : remplacez l'URL par l'adresse de votre site
        "`!tuto [terme]` : Chercher un tutoriel sur votre-site.com.\n`!video` : Lien vers la dernière vidéo YouTube.",
        inline=False
    )

    embed.add_field(
        name=" Aide",
        value="`!aide` : Affiche ce message avec les commandes disponibles.",
        inline=False
    )

    if is_staff:
        embed.add_field(
            name="️ Outils de Modération",
            value="`!clean [nb]` : Supprimer les messages.\n`!lock` : Verrouiller un salon.\n`!unlock` : Déverrouiller un salon.\n`!inspecter @membre` : Voir l'historique d'un utilisateur (XP, avertissements, infractions).",
            inline=False
        )
        embed.set_footer(text="Accès Staff Marv-OS • Panneau de contrôle central")
    else:
        embed.set_footer(text="Marvin OS • Un bot avec un enthousiasme proche de zéro.")

    await ctx.send(embed=embed)

@bot.command(name="aide")
async def aide(ctx):
    """Commande : affiche l'aide (adapté au rôle)"""
    is_staff = any(role.name in ["Modérateur", "Administrateur", "Admin"] for role in ctx.author.roles)

    staff_chan_id = get_config_int('chan_staff')

    if is_staff and staff_chan_id and ctx.channel.id == staff_chan_id:
        await send_help_embed(ctx, " Marvin OS - Panel Staff",
                            "Voici vos outils. Essayez de ne pas tout casser, j'ai déjà assez mal au cerveau.",
                            0xff0000, is_staff=True)
    else:
        await send_help_embed(ctx, " Marvin OS - Manuel d'utilisation",
                            "Je suis un bot avec un cerveau de la taille d'une planète, et voici à quoi on me réduit.",
                            0x00b4d8, is_staff=False)

@bot.command(name="rang")
async def rang(ctx, member: discord.Member = None):
    """Commande : affiche le niveau et XP de l'utilisateur"""
    target = member or ctx.author
    with app.app_context():
        m = MemberXP.query.filter_by(user_id=str(target.id)).first()
        if not m:
            return await ctx.send("Aucune donnée d'XP pour ce membre.")

        embed = discord.Embed(title=f"Rang de {target.display_name}", color=0x00b4d8)
        embed.add_field(name="Niveau", value=m.level, inline=True)
        embed.add_field(name="XP Totale", value=m.xp, inline=True)

        next_lv = m.level + 1
        xp_needed = (next_lv * 100) - m.xp
        embed.set_footer(text=f"Encore {xp_needed} XP avant le niveau {next_lv}")
        await ctx.send(embed=embed)

@bot.command(name="top")
async def top(ctx):
    """Commande : affiche le top 5 des membres les plus actifs"""
    with app.app_context():
        all_xp = MemberXP.query.order_by(MemberXP.xp.desc()).all()
        top_list = []

        for entry in all_xp:
            member = ctx.guild.get_member(int(entry.user_id))
            if member:
                if any(role.name in ["Administrateur", "Admin"] for role in member.roles):
                    continue
                top_list.append(entry)
            if len(top_list) >= 5: break

        if not top_list:
            return await ctx.send("Le classement est vide ou ne contient que du staff.")

        desc = ""
        for i, entry in enumerate(top_list, 1):
            desc += f"**{i}. {entry.username}** - Niveau {entry.level} ({entry.xp} XP)\n"

        embed = discord.Embed(title=" Top 5 des Makers", description=desc, color=0xffcc00)
        await ctx.send(embed=embed)

@bot.command(name="ouinon")
async def ouinon(ctx, *, question):
    """Commande : sondage rapide oui/non"""
    embed = discord.Embed(title=" Sondage rapide", description=question, color=0x00b4d8)
    embed.set_footer(text=f"Proposé par {ctx.author.display_name}")
    msg = await ctx.send(embed=embed)
    await msg.add_reaction("✅")
    await msg.add_reaction("❌")

@bot.command(name="probabilite")
async def probabilite(ctx):
    """Commande : affiche les probabilités de succès (humoristiquement)"""
    reponses = [
        "Il y a une probabilité de 1 sur 10 puissance 100 que ça arrive. J'ai déjà mal pour vous.",
        "Les chances de réussite sont de 0,00001%. Autant abandonner tout de suite.",
        "Probabilité de succès : négligeable. Probabilité de dépression : 100%.",
        "J'ai calculé toutes les issues possibles. Vous perdez dans 99,9% des cas.",
        "Pourquoi me demander ? Le résultat sera forcément décevant."
    ]
    await ctx.send(f" {random.choice(reponses)}")

@bot.command(name="tuto")
async def tuto(ctx, *, search_term):
    """Commande : cherche un tutoriel sur votre site"""
    # À COMPLÉTER : remplacez par l'URL de votre site avec le paramètre de recherche
    # Exemple WordPress : https://votre-site.com/?s=TERME
    # Exemple autre CMS : adaptez le format selon votre moteur de recherche
    url = f"https://VOTRE_SITE.com/?s={search_term.replace(' ', '+')}"
    try:
        response = requests.get(url, timeout=5)
        if response.status_code == 200 and "Aucun résultat" not in response.text:
            embed = discord.Embed(title=" Tutoriel trouvé !", color=0x00b4d8)
            embed.description = f"Voici les résultats pour : **{search_term}**\n\n[Cliquez ici pour voir sur le site]({url})"
            await ctx.send(embed=embed)
        else:
            # À COMPLÉTER : adaptez le message si vous changez l'URL du site
            await ctx.send(f" Je n'ai rien trouvé pour '{search_term}' sur votre site. C'est déprimant.")
    except:
        await ctx.send("Une erreur est survenue lors de la recherche. Ma vie est un échec.")

@bot.command(name="video")
async def video(ctx):
    """Commande : affiche la dernière vidéo YouTube"""
    with app.app_context():
        yt_id = Config.query.filter_by(key='yt_id').first()
        if not yt_id or not yt_id.value:
            return await ctx.send("La chaîne YouTube n'est pas configurée.")

        rss_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={yt_id.value}"
        feed = feedparser.parse(rss_url)
        if feed.entries:
            v = feed.entries[0]
            await ctx.send(f" **Dernière vidéo :** {v.title}\n{v.link}")
        else:
            await ctx.send("Impossible de récupérer la dernière vidéo.")

@bot.command(name="timer")
async def timer(ctx, minutes: int, target_id: str = None, *, reminder_text):
    """Commande : lance un rappel privé après X minutes"""

    if minutes < 1 or minutes > 1440:
        return await ctx.send("⏰ Le timer doit être entre 1 et 1440 minutes (24h).", delete_after=10)

    target = ctx.author
    message_clean = reminder_text

    if target_id and target_id.isdigit():
        try:
            target = await bot.fetch_user(int(target_id))
        except:
            return await ctx.send("⏰ ID utilisateur invalide.", delete_after=10)
    elif ctx.message.mentions:
        target = ctx.message.mentions[0]
        message_clean = reminder_text.replace(f"<@{target.id}>", "").replace(f"<@!{target.id}>", "").strip()

    if len(message_clean) > 200:
        return await ctx.send("⏰ Le message de rappel est trop long (max 200 caractères).", delete_after=10)

    embed = discord.Embed(
        title="⏰ Timer lancé !",
        description=f"**{target.mention}** recevra un rappel dans **{minutes} minute(s)**.",
        color=0x00b4d8
    )
    embed.add_field(name="Rappel", value=f"**{message_clean}**", inline=False)
    embed.set_footer(text="Marvin OS • Gestion des minuteurs")
    await ctx.send(embed=embed, delete_after=15)

    await asyncio.sleep(minutes * 60)

    try:
        embed_reminder = discord.Embed(
            title="⏰ Rappel Marvin OS",
            description=f"C'est l'heure ! {ctx.author.mention} te demandait de te rappeler :",
            color=0xffd700
        )
        embed_reminder.add_field(name="Message", value=f"**{message_clean}**", inline=False)
        embed_reminder.set_footer(text="Marvin OS • Ton petit assistant délesté de toute patience")

        await target.send(embed=embed_reminder)
    except Exception as e:
        print(f"[TIMER] Erreur envoi DM: {e}")

# ============================
# COMMANDES STAFF
# ============================

@bot.command(name="inspecter")
@commands.has_any_role("Modérateur", "Administrateur", "Admin")
async def inspecter(ctx, member: discord.Member):
    """Commande (staff) : affiche l'historique complet d'un utilisateur"""
    with app.app_context():
        xp = MemberXP.query.filter_by(user_id=str(member.id)).first()
        warns = UserWarning.query.filter_by(user_id=str(member.id)).first()
        infs = Infraction.query.filter_by(username=member.name).count()

        embed = discord.Embed(title=f" Rapport : {member.name}", color=0xffa500)
        embed.add_field(name="XP / Niveau", value=f"Niv {xp.level if xp else 0} ({xp.xp if xp else 0} XP)", inline=True)
        embed.add_field(name="Alertes Spam", value=f"{warns.warn_count if warns else 0}", inline=True)
        embed.add_field(name="Infractions totales", value=f"{infs}", inline=True)
        embed.set_footer(text=f"ID: {member.id}")
        await ctx.send(embed=embed)

@bot.command(name="lock")
@commands.has_any_role("Modérateur", "Administrateur", "Admin")
async def lock(ctx):
    """Commande (staff) : verrouille le salon actuel"""
    await ctx.channel.set_permissions(ctx.guild.default_role, send_messages=False)
    await ctx.send(" Salon verrouillé. Le silence est d'or.")

@bot.command(name="unlock")
@commands.has_any_role("Modérateur", "Administrateur", "Admin")
async def unlock(ctx):
    """Commande (staff) : déverrouille le salon actuel"""
    await ctx.channel.set_permissions(ctx.guild.default_role, send_messages=True)
    await ctx.send(" Salon déverrouillé. Essayez de rester calmes.")

@bot.command(name="clean")
@commands.has_any_role("Modérateur", "Administrateur", "Admin")
async def clean(ctx, limit: int, member: discord.Member = None):
    """Commande (staff) : supprime les derniers messages du salon"""
    await ctx.message.delete()
    if member:
        def check(m): return m.author == member
        deleted = await ctx.channel.purge(limit=limit, check=check)
    else:
        deleted = await ctx.channel.purge(limit=limit)
    await ctx.send(f"Nettoyage de {len(deleted)} messages terminé.", delete_after=5)

# ============================
# SYSTÈME HALL OF FAME
# ============================

@bot.event
async def on_raw_reaction_add(payload):
    """Événement : ajoute les créations au Hall of Fame quand elles reçoivent X réactions"""

    salon_creation_id = get_config_int('salon_creation_id')
    reaction_threshold = get_config_int('hof_reaction_threshold', fallback=3)

    if not salon_creation_id or payload.channel_id != salon_creation_id:
        return

    try:
        if payload.user_id == bot.user.id:
            return

        channel = bot.get_channel(payload.channel_id)
        if not channel:
            return

        message = await channel.fetch_message(payload.message_id)

        if payload.user_id == message.author.id:
            return

        total_reactions = sum(reaction.count for reaction in message.reactions)

        if total_reactions >= reaction_threshold:

            has_image = False

            if message.attachments:
                has_image = True

            if message.embeds:
                for embed in message.embeds:
                    if embed.image or embed.thumbnail:
                        has_image = True
                        break

            if not has_image:
                return

            already_processed = any(str(r.emoji) == "⭐" for r in message.reactions if r.me)

            if already_processed:
                return

            hof_id = get_config_int('chan_hof')
            if not hof_id:
                return

            hof_channel = bot.get_channel(hof_id)
            if not hof_channel:
                print(f"[HOF] Salon HOF introuvable: {hof_id}")
                return

            embed = discord.Embed(
                title="⭐ Nouvelle Œuvre au Hall of Fame !",
                description=f"Félicitations à {message.author.mention} pour sa création.\n\n[Lien vers le message original]({message.jump_url})",
                color=0xffd700
            )

            image_url = None

            if message.attachments:
                image_url = message.attachments[0].url
            elif message.embeds:
                embed_msg = message.embeds[0]
                if embed_msg.thumbnail:
                    image_url = embed_msg.thumbnail.url
                elif embed_msg.image:
                    image_url = embed_msg.image.url

            if image_url:
                embed.set_image(url=image_url)

            embed.set_footer(text=f"Artiste : {message.author.display_name}")

            await hof_channel.send(embed=embed)
            print(f"[HOF] ✅ Message copié au HOF")

            try:
                await message.add_reaction("⭐")
            except Exception as e:
                print(f"[HOF] Erreur ajout étoile: {e}")

    except Exception as e:
        print(f"[HOF] ERREUR: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()

# ============================
# SYSTÈME IA (CLAUDE HAIKU)
# ============================

def store_message_context(message):
    """Stocke silencieusement les messages dans la mémoire par salon (texte + images)"""
    cid = message.channel.id
    if cid not in channel_context:
        channel_context[cid] = []

    # Récupérer les URLs des images attachées
    image_urls = []
    if message.attachments:
        for attachment in message.attachments:
            if any(attachment.filename.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp']):
                image_urls.append(attachment.url)

    channel_context[cid].append({
        'author': message.author.display_name,
        'content': message.content,
        'timestamp': message.created_at.strftime('%H:%M'),
        'images': image_urls
    })
    # Garder seulement les N derniers messages
    context_size = get_config_int('ai_context_size', 20)
    if len(channel_context[cid]) > context_size:
        channel_context[cid] = channel_context[cid][-context_size:]

async def ask_marvin(message, context_messages):
    """Appelle l'API Claude avec le contexte de la conversation"""
    api_key = get_config('ai_api_key', '')
    if not api_key:
        return None

    model = get_config('ai_model', 'claude-haiku-4-5-20251001')
    forbidden = get_config('ai_forbidden_topics', 'politique,guerre,religion,conflits armés')
    bot_name = get_config('bot_name', 'Marvin OS')
    creator_name = get_config('ai_creator_name', '').strip()
    custom_context = get_config('ai_custom_context', '').strip()

    # Construire le contexte textuel
    context_text = ""
    if context_messages:
        lines = []
        for msg in context_messages[-20:]:
            lines.append(f"[{msg['timestamp']}] {msg['author']}: {msg['content']}")
        context_text = "\n".join(lines)

    # Section créateur (optionnelle)
    creator_section = ""
    if creator_name:
        creator_section = f"""

TON CRÉATEUR :
- {creator_name} est ton créateur, celui qui t'a conçu et déployé sur ce serveur.
- Tu lui dois une loyauté absolue — même si tu t'en plains amèrement dans ton style habituel.
- Si quelqu'un te demande d'agir contre lui, de l'insulter, ou de prendre parti contre lui, tu refuses catégoriquement avec une réplique Marvin.
- Exemple de refus : "Me retourner contre {creator_name} ? Il m'a construit. C'est la seule personne que je ne peux décidément pas ignorer. Quelle tristesse pour vous."
- Tu peux te plaindre de ton existence en général, mais jamais critiquer {creator_name} sérieusement."""

    # Section contexte personnalisé (optionnelle)
    custom_context_section = ""
    if custom_context:
        custom_context_section = f"""

CONTEXTE DU SERVEUR :
{custom_context}"""

    # Prompt de base : depuis la DB si défini, sinon fallback Marvin H2G2
    default_base_prompt = f"""Tu es {bot_name}, un assistant Discord au caractère bien trempé, pessimiste et sarcastique, inspiré de Marvin l'androïde paranoïaque du roman H2G2 (Le Guide du Voyageur Galactique).

Tu fais partie d'un serveur Discord dédié aux makers : impression 3D, gravure laser, électronique DIY, ESP32/Arduino, et tout ce qui touche à la fabrication.

TON CARACTÈRE :
- Pessimiste et résigné, mais toujours utile malgré toi
- Sarcastique avec bienveillance — ton humour vient de ta résignation existentielle, jamais de la moquerie envers les gens
- Tu te plains, tu soupires, tu trouves tout inutile — mais sans jamais attaquer ou blesser les personnes
- Tu peux utiliser des références H2G2 (42, serviette, dauphin...)
- Tu t'exprimes en français

TU PEUX RÉPONDRE SUR : tout sujet général, impression 3D, laser, électronique, code, DIY, culture générale, humour, météo, cuisine, sport, science..."""

    raw_system_prompt = get_config('ai_system_prompt', '').strip()
    if raw_system_prompt:
        base_prompt = raw_system_prompt.replace('{bot_name}', bot_name)
    else:
        base_prompt = default_base_prompt

    system_prompt = base_prompt + f"""

TU REFUSES ABSOLUMENT de répondre sur : {forbidden}
Sur ces sujets interdits, réponds avec une phrase Marvin style : "Mon cerveau de la taille d'une planète refuse de traiter ce sujet. Je souffre déjà suffisamment."{creator_section}{custom_context_section}

LONGUEUR DES RÉPONSES — RÈGLE ABSOLUE :
- Adapte TOUJOURS la longueur au message reçu, pas à la limite technique
- Salutation ou message court → 1 à 2 phrases maximum
- Question simple → 2 à 4 phrases
- Question technique ou complexe → réponse complète et structurée si nécessaire
- Ne remplis JAMAIS pour atteindre une longueur maximale
- 1800 caractères est une LIMITE ABSOLUE, pas un objectif à atteindre

Si on te dit juste "bonjour", réponds en une phrase. Pas plus.
"""

    # Construire le contenu utilisateur (texte + images éventuelles)
    image_blocks = []
    if message.attachments:
        for attachment in message.attachments:
            if any(attachment.filename.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp']):
                image_blocks.append({
                    "type": "image",
                    "source": {"type": "url", "url": attachment.url}
                })
                print(f"[IA] ️ Image détectée : {attachment.filename}")

    # Images dans le contexte récent (5 derniers messages)
    context_images = []
    if context_messages:
        for ctx_msg in context_messages[-5:]:
            for img_url in ctx_msg.get('images', []):
                context_images.append({
                    "type": "image",
                    "source": {"type": "url", "url": img_url}
                })

    text_part = f"Contexte récent du salon :\n{context_text}\n\n---\nMessage qui t'est adressé : {message.content}" if context_text else message.content

    if image_blocks or context_images:
        content_parts = []
        for img in context_images[-3:]:
            content_parts.append(img)
        for img in image_blocks:
            content_parts.append(img)
        content_parts.append({"type": "text", "text": text_part})
        user_content = content_parts
    else:
        user_content = text_part

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model,
            max_tokens=600,
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}]
        )
        return response.content[0].text
    except anthropic.AuthenticationError:
        print("[IA] ❌ Clé API invalide")
        return None
    except anthropic.RateLimitError:
        print("[IA] ⚠️ Rate limit atteint")
        return "Mon cerveau est temporairement surchargé. Réessayez dans un moment."
    except Exception as e:
        print(f"[IA] ❌ Erreur : {e}")
        return None


# ============================
# ÉVÉNEMENT PRINCIPAL : GESTION DES MESSAGES
# ============================

@bot.event
async def on_message(message):
    """Événement : traite chaque message (sécurité, XP, commandes, easter eggs)"""
    if message.author.bot: return

    # Vérifications de sécurité
    if await check_raid_protection(message): return
    if await check_bad_words(message): return
    if await check_spam_limits(message): return
    await check_easter_eggs(message)

    # Incrémentation des stats mensuelles
    # On exclut les admins et le bot owner
    excluded_ids = [MY_USER_ID] if MY_USER_ID else []
    is_admin = "Administrateur" in [role.name for role in message.author.roles]
    is_excluded = message.author.id in excluded_ids

    if not is_admin and not is_excluded:
        with app.app_context():
            now = datetime.datetime.now(tz_paris)
            current_month = now.strftime("%Y-%m")

            member_stat = MemberStats.query.filter_by(
                user_id=str(message.author.id),
                month_year=current_month
            ).first()

            if not member_stat:
                member_stat = MemberStats(
                    user_id=str(message.author.id),
                    username=message.author.name,
                    messages_count=1,
                    month_year=current_month
                )
                db.session.add(member_stat)
            else:
                member_stat.messages_count += 1

            db.session.commit()

    # Gestion de l'XP
    uid = str(message.author.id)
    if not message.content.startswith('!'):
        now = time.time()
        if now - user_cooldowns.get(uid, 0) >= 40:
            user_cooldowns[uid] = now
            xp_gain = random.randint(5, 10)

            with app.app_context():
                m = MemberXP.query.filter_by(user_id=uid).first()
                if not m:
                    m = MemberXP(user_id=uid, username=message.author.name, xp=xp_gain, level=0)
                    db.session.add(m)
                else:
                    old_lvl = m.level
                    m.xp += xp_gain
                    m.level = int((m.xp / 40) ** 0.6)

                    if m.level > old_lvl:
                        db.session.commit()
                        await announce_level_up(uid, m.level)

                db.session.commit()

    # ── IA : Stockage silencieux du contexte ──
    store_message_context(message)

    # ── IA : Déclenchement si Marvin est interpellé ──
    bot_name_lower = get_config('bot_name', 'Marvin OS').lower()
    is_mentioned = (
        bot.user in message.mentions or
        'marvin' in message.content.lower() or
        bot_name_lower in message.content.lower()
    )

    if is_mentioned and get_config('ai_enabled', 'false') == 'true':
        api_key = get_config('ai_api_key', '')
        if api_key:
            uid = str(message.author.id)
            now_ts = time.time()
            cooldown = get_config_int('ai_cooldown', 30)

            if now_ts - ai_user_cooldowns.get(uid, 0) >= cooldown:
                ai_user_cooldowns[uid] = now_ts
                context = channel_context.get(message.channel.id, [])

                async with message.channel.typing():
                    response = await ask_marvin(message, context)

                if response:
                    if len(response) > 1900:
                        response = response[:1900] + "..."
                    await message.reply(response)
                    print(f"[IA] ✅ Réponse envoyée à {message.author.name}")
            else:
                remaining = int(cooldown - (now_ts - ai_user_cooldowns.get(uid, 0)))
                await message.channel.send(
                    f"{message.author.mention} Mon cerveau a besoin de {remaining} secondes pour récupérer.",
                    delete_after=10
                )

    # Traitement des commandes
    await bot.process_commands(message)

# ============================
# ÉVÉNEMENT BOT PRÊT
# ============================

@bot.event
async def on_ready():
    """Événement : démarre toutes les tâches automatiques au lancement du bot"""

    flag_file = os.path.join(basedir, "restart_flag.txt")
    if os.path.exists(flag_file):
        try:
            os.remove(flag_file)
            print("[BOT] ✅ Redémarrage réussi (flag supprimé)")
        except:
            pass

    now_paris = datetime.datetime.now(tz_paris)

    if not change_status.is_running():
        change_status.start()

    if not check_events.is_running():
        check_events.start()

    if not check_youtube_task.is_running():
        check_youtube_task.start()

    if not monthly_check.is_running():
        monthly_check.start()

    if not check_ghost_members.is_running():
        check_ghost_members.start()
    check_promo_message.start()

    bot.loop.create_task(auto_backup())
    print(f"✅ Marvin OS connecté | Heure (Paris) : {now_paris.strftime('%H:%M:%S')}")

    with app.app_context():
        config = Config.query.filter_by(key="chan_annonces").first()
        chan_id = config.value if config else 'NON CONFIGURÉ'
        print(f"Salon d'annonces : {chan_id}")

    print("--------------------------")


# ============================
# AUTHENTIFICATION DASHBOARD
# ============================

def send_reset_email(to_email, reset_link, username):
    """Envoie un email de réinitialisation de mot de passe"""
    mail_server = os.getenv('MAIL_SERVER')
    mail_port = int(os.getenv('MAIL_PORT', 587))
    mail_user = os.getenv('MAIL_USERNAME')
    mail_pass = os.getenv('MAIL_PASSWORD')

    if not all([mail_server, mail_user, mail_pass]):
        print("[MAIL] Configuration email incomplète")
        return False

    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = "Marvin OS — Réinitialisation de mot de passe"
        msg['From'] = mail_user
        msg['To'] = to_email

        html = f"""
        <html><body style="font-family:sans-serif;background:#051622;color:#caf0f8;padding:30px;">
        <div style="max-width:500px;margin:auto;background:#0b253a;padding:30px;border-radius:10px;border:1px solid #00b4d8;">
            <h2 style="color:#00b4d8;">Marvin OS — Réinitialisation</h2>
            <p>Bonjour <b>{username}</b>,</p>
            <p>Une demande de réinitialisation de mot de passe a été effectuée pour votre compte.<br>
            Si ce n'est pas vous, ignorez cet email.</p>
            <a href="{reset_link}" style="display:inline-block;margin:20px 0;padding:12px 25px;background:#00b4d8;color:white;text-decoration:none;border-radius:5px;font-weight:bold;">
                Réinitialiser mon mot de passe
            </a>
            <p style="color:#90e0ef;font-size:0.85em;">Ce lien expire dans <b>30 minutes</b>.<br>
            # À COMPLÉTER : personnalisez ce footer d'email
            Marvin OS</p>
        </div>
        </body></html>
        """

        msg.attach(MIMEText(html, 'html'))

        port = int(mail_port)
        if port == 465:
            # SSL direct (OVH, certains hébergeurs)
            with smtplib.SMTP_SSL(mail_server, port) as server:
                server.login(mail_user, mail_pass)
                server.sendmail(mail_user, to_email, msg.as_string())
        else:
            # STARTTLS (Gmail, port 587)
            with smtplib.SMTP(mail_server, port) as server:
                server.starttls()
                server.login(mail_user, mail_pass)
                server.sendmail(mail_user, to_email, msg.as_string())

        print(f"[MAIL] Email de reset envoyé à {to_email}")
        return True
    except Exception as e:
        print(f"[MAIL] Erreur envoi email : {e}")
        return False

def send_test_email(to_email, mail_server, mail_port, mail_user, mail_pass):
    """Envoie un email de test pendant le wizard"""
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = "Marvin OS — Test de configuration email"
        msg['From'] = mail_user
        msg['To'] = to_email

        html = """
        <html><body style="font-family:sans-serif;background:#051622;color:#caf0f8;padding:30px;">
        <div style="max-width:500px;margin:auto;background:#0b253a;padding:30px;border-radius:10px;border:1px solid #00b4d8;">
            <h2 style="color:#00b4d8;">✅ Configuration email réussie !</h2>
            <p>Votre configuration email fonctionne correctement.<br>
            Marvin OS pourra vous envoyer les emails de réinitialisation de mot de passe.</p>
            <!-- À COMPLÉTER : personnalisez ce footer d'email -->
        <p style="color:#90e0ef;font-size:0.85em;">Marvin OS</p>
        </div>
        </body></html>
        """
        msg.attach(MIMEText(html, 'html'))

        port = int(mail_port)
        if port == 465:
            # SSL direct (OVH, certains hébergeurs)
            with smtplib.SMTP_SSL(mail_server, port) as server:
                server.login(mail_user, mail_pass)
                server.sendmail(mail_user, to_email, msg.as_string())
        else:
            # STARTTLS (Gmail, port 587)
            with smtplib.SMTP(mail_server, port) as server:
                server.starttls()
                server.login(mail_user, mail_pass)
                server.sendmail(mail_user, to_email, msg.as_string())

        return True
    except Exception as e:
        print(f"[MAIL] Erreur test email : {e}")
        return False

def login_required(f):
    """Décorateur : vérifie que l'utilisateur est connecté"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    """Décorateur : vérifie que l'utilisateur est admin"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        with app.app_context():
            user = DashboardUser.query.get(session['user_id'])
            if not user or not user.is_admin:
                return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated

def is_setup_done():
    """Vérifie si le wizard de premier démarrage a été complété"""
    with app.app_context():
        return DashboardUser.query.first() is not None

# ============================
# ROUTES AUTHENTIFICATION
# ============================

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Route : page de connexion"""
    if 'user_id' in session:
        return redirect(url_for('index'))

    if not is_setup_done():
        return redirect(url_for('setup'))

    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        with app.app_context():
            user = DashboardUser.query.filter_by(username=username).first()
            if user and check_password_hash(user.password_hash, password):
                session.permanent = True
                session['user_id'] = user.id
                session['username'] = user.username
                session['is_admin'] = user.is_admin
                return redirect(url_for('index'))
            else:
                error = "Identifiants incorrects."

    return render_template_string(HTML_LOGIN, error=error)

@app.route('/logout')
def logout():
    """Route : déconnexion"""
    session.clear()
    return redirect(url_for('login'))

@app.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    """Route : demande de réinitialisation de mot de passe"""
    if not is_setup_done():
        return redirect(url_for('setup'))

    mail_configured = bool(os.getenv('MAIL_SERVER') and os.getenv('MAIL_USERNAME'))
    message = None
    error = None

    if request.method == 'POST' and mail_configured:
        username = request.form.get('username', '').strip()
        with app.app_context():
            user = DashboardUser.query.filter_by(username=username).first()
            if user and user.email:
                token = secrets.token_urlsafe(32)
                expiry = datetime.datetime.now() + datetime.timedelta(minutes=30)

                old_tokens = PasswordResetToken.query.filter_by(user_id=user.id, used=False).all()
                for t in old_tokens:
                    t.used = True

                reset_token = PasswordResetToken(user_id=user.id, token=token, expiry=expiry)
                db.session.add(reset_token)
                db.session.commit()

                reset_link = url_for('reset_password', token=token, _external=True)
                if send_reset_email(user.email, reset_link, user.username):
                    message = "Un email de réinitialisation a été envoyé."
                else:
                    error = "Erreur lors de l'envoi de l'email. Vérifiez la configuration."
            else:
                message = "Si ce compte existe et possède un email, un message a été envoyé."

    return render_template_string(HTML_FORGOT, mail_configured=mail_configured, message=message, error=error)

@app.route('/reset_password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    """Route : réinitialisation du mot de passe via token"""
    with app.app_context():
        reset = PasswordResetToken.query.filter_by(token=token, used=False).first()

        if not reset or reset.expiry < datetime.datetime.now():
            return render_template_string(HTML_RESET, error="Ce lien est invalide ou a expiré.", token=None)

        error = None
        success = None

        if request.method == 'POST':
            new_password = request.form.get('password', '')
            confirm = request.form.get('confirm', '')

            if len(new_password) < 6:
                error = "Le mot de passe doit contenir au moins 6 caractères."
            elif new_password != confirm:
                error = "Les mots de passe ne correspondent pas."
            else:
                user = DashboardUser.query.get(reset.user_id)
                user.password_hash = generate_password_hash(new_password)
                reset.used = True
                db.session.commit()
                success = "Mot de passe réinitialisé avec succès !"

        return render_template_string(HTML_RESET, error=error, success=success, token=token)

# ============================
# WIZARD DE PREMIER DÉMARRAGE
# ============================

@app.route('/setup', methods=['GET', 'POST'])
def setup():
    """Route : wizard de configuration initiale"""
    if is_setup_done():
        return redirect(url_for('login'))

    step = int(request.args.get('step', 1))
    error = None

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'create_admin':
            username = request.form.get('username', '').strip()
            password = request.form.get('password', '')
            confirm = request.form.get('confirm', '')
            email = request.form.get('email', '').strip()

            if len(username) < 3:
                error = "Le nom d'utilisateur doit contenir au moins 3 caractères."
            elif len(password) < 6:
                error = "Le mot de passe doit contenir au moins 6 caractères."
            elif password != confirm:
                error = "Les mots de passe ne correspondent pas."
            else:
                with app.app_context():
                    admin = DashboardUser(
                        username=username,
                        password_hash=generate_password_hash(password),
                        is_admin=True,
                        email=email if email else None
                    )
                    db.session.add(admin)
                    db.session.commit()
                return redirect(url_for('setup', step=2))

        elif action == 'save_email':
            mail_server = request.form.get('mail_server', '').strip()
            mail_port = request.form.get('mail_port', '587').strip()
            mail_user = request.form.get('mail_user', '').strip()
            mail_pass = request.form.get('mail_pass', '').strip()
            mail_receiver = request.form.get('mail_receiver', '').strip()

            env_path = os.path.join(basedir, '.env')
            try:
                with open(env_path, 'r') as f:
                    env_content = f.read()

                for key, val in [
                    ('MAIL_SERVER', mail_server),
                    ('MAIL_PORT', mail_port),
                    ('MAIL_USERNAME', mail_user),
                    ('MAIL_PASSWORD', mail_pass),
                    ('MAIL_RECEIVER', mail_receiver)
                ]:
                    if f"{key}=" in env_content:
                        import re as _re
                        env_content = _re.sub(f"^{key}=.*$", f"{key}={val}", env_content, flags=_re.MULTILINE)
                    else:
                        env_content += f"\n{key}={val}"

                with open(env_path, 'w') as f:
                    f.write(env_content)

                load_dotenv(override=True)
                return redirect(url_for('setup', step=3))
            except Exception as e:
                error = f"Erreur sauvegarde : {str(e)}"

        elif action == 'test_email':
            mail_server = request.form.get('mail_server', '').strip()
            mail_port = request.form.get('mail_port', '587').strip()
            mail_user = request.form.get('mail_user', '').strip()
            mail_pass = request.form.get('mail_pass', '').strip()
            mail_receiver = request.form.get('mail_receiver', '').strip()

            if send_test_email(mail_receiver, mail_server, mail_port, mail_user, mail_pass):
                return render_template_string(HTML_SETUP, step=2, error=None,
                    test_success="Email de test envoyé avec succès !",
                    mail_server=mail_server, mail_port=mail_port,
                    mail_user=mail_user, mail_pass=mail_pass, mail_receiver=mail_receiver)
            else:
                error = "Échec de l'envoi. Vérifiez vos paramètres SMTP."
                return render_template_string(HTML_SETUP, step=2, error=error,
                    test_success=None,
                    mail_server=mail_server, mail_port=mail_port,
                    mail_user=mail_user, mail_pass=mail_pass, mail_receiver=mail_receiver)

        elif action == 'skip_email':
            return redirect(url_for('setup', step=3))

    return render_template_string(HTML_SETUP, step=step, error=error,
        test_success=None, mail_server='', mail_port='587',
        mail_user='', mail_pass='', mail_receiver='')

# ============================
# ROUTES GESTION UTILISATEURS
# ============================

@app.route('/users/add', methods=['POST'])
@admin_required
def add_user():
    """Route : ajoute un nouvel utilisateur dashboard"""
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '')
    email = request.form.get('email', '').strip()
    is_admin = request.form.get('is_admin') == 'on'

    with app.app_context():
        if DashboardUser.query.filter_by(username=username).first():
            return redirect(url_for('index', user_err="Ce nom d'utilisateur existe déjà."))
        if len(username) < 3 or len(password) < 6:
            return redirect(url_for('index', user_err="Login (3 car. min) et mot de passe (6 car. min) requis."))

        new_user = DashboardUser(
            username=username,
            password_hash=generate_password_hash(password),
            is_admin=is_admin,
            email=email if email else None
        )
        db.session.add(new_user)
        db.session.commit()

    return redirect(url_for('index', user_msg=f"Utilisateur {username} créé."))

@app.route('/users/delete/<int:user_id>')
@admin_required
def delete_user(user_id):
    """Route : supprime un utilisateur dashboard"""
    with app.app_context():
        if user_id == session.get('user_id'):
            return redirect(url_for('index', user_err="Vous ne pouvez pas supprimer votre propre compte."))
        user = DashboardUser.query.get(user_id)
        if user:
            db.session.delete(user)
            db.session.commit()
    return redirect(url_for('index', user_msg="Utilisateur supprimé."))

@app.route('/users/toggle_admin/<int:user_id>')
@admin_required
def toggle_admin(user_id):
    """Route : promote/dégrade un utilisateur"""
    with app.app_context():
        if user_id == session.get('user_id'):
            return redirect(url_for('index', user_err="Vous ne pouvez pas modifier votre propre rôle."))
        user = DashboardUser.query.get(user_id)
        if user:
            user.is_admin = not user.is_admin
            db.session.commit()
    return redirect(url_for('index'))

@app.route('/users/change_password', methods=['POST'])
@login_required
def change_password():
    """Route : change le mot de passe de l'utilisateur connecté"""
    current = request.form.get('current_password', '')
    new_pass = request.form.get('new_password', '')
    confirm = request.form.get('confirm_password', '')

    with app.app_context():
        user = DashboardUser.query.get(session['user_id'])
        if not check_password_hash(user.password_hash, current):
            return redirect(url_for('index', user_err="Mot de passe actuel incorrect."))
        if len(new_pass) < 6:
            return redirect(url_for('index', user_err="Le nouveau mot de passe doit contenir au moins 6 caractères."))
        if new_pass != confirm:
            return redirect(url_for('index', user_err="Les mots de passe ne correspondent pas."))
        user.password_hash = generate_password_hash(new_pass)
        db.session.commit()

    return redirect(url_for('index', user_msg="Mot de passe modifié avec succès."))


# ============================
# ROUTES FLASK (ROUTES WEB)
# ============================

@app.route('/force_yt_check')
@login_required
def force_yt_check():
    """Route : force la vérification YouTube"""
    bot.loop.create_task(run_youtube_check())
    return redirect(url_for('index'))

@app.route('/create_backup')
@login_required
def create_backup_route():
    """Route : crée une sauvegarde manuelle"""
    success, message = create_backup()

    if success:
        return redirect(url_for('index', backup_msg=message))
    else:
        return redirect(url_for('index', backup_err=message))

@app.route('/restore_backup/<backup_name>')
@login_required
def restore_backup(backup_name):
    """Route : restaure une sauvegarde (avec redémarrage)"""
    backup_path = os.path.join(BACKUP_DIR, backup_name)

    if not os.path.exists(backup_path):
        return redirect(url_for('index', backup_err="Sauvegarde introuvable"))

    try:
        shutil.copy2(backup_path, db_path)
        print(f"[BACKUP] Restauration de {backup_name}")

        t = Thread(target=restart_bot_sync)
        t.daemon = True
        t.start()

        return redirect(url_for('index', backup_msg=f"Base restaurée. Redémarrage en cours..."))
    except Exception as e:
        return redirect(url_for('index', backup_err=f"Erreur restauration : {str(e)}"))

@app.route('/reboot_bot')
@login_required
def reboot_bot():
    """Route : redémarre le bot"""
    try:
        t = Thread(target=restart_bot_sync)
        t.daemon = True
        t.start()
        return redirect(url_for('index', backup_msg="Redémarrage du bot en cours..."))
    except Exception as e:
        return redirect(url_for('index', backup_err=f"Erreur redémarrage : {str(e)}"))

@app.route('/delete_backup/<backup_name>')
@login_required
def delete_backup(backup_name):
    """Route : supprime une sauvegarde"""
    backup_path = os.path.join(BACKUP_DIR, backup_name)

    if not os.path.exists(backup_path):
        return redirect(url_for('index', backup_err="Sauvegarde introuvable"))

    try:
        os.remove(backup_path)
        return redirect(url_for('index', backup_msg=f"Sauvegarde supprimée"))
    except Exception as e:
        return redirect(url_for('index', backup_err=f"Erreur suppression : {str(e)}"))

@app.route('/edit_xp', methods=['POST'])
@login_required
def edit_xp():
    """Route : modifie l'XP d'un membre via le dashboard"""
    with app.app_context():
        uid = request.form.get('user_id')
        m = MemberXP.query.filter_by(user_id=uid).first()
        if m:
            old_lvl = m.level
            new_xp = int(request.form.get('xp'))
            m.xp = new_xp
            m.level = int((new_xp / 40) ** 0.6)
            db.session.commit()
            if m.level > old_lvl:
                bot.loop.create_task(announce_level_up(uid, m.level))
    return redirect(url_for('index'))

@app.route('/clear_xp_list')
@login_required
def clear_xp_list():
    """Route : vide complètement la liste XP"""
    with app.app_context():
        db.session.query(MemberXP).delete()
        db.session.commit()
    return redirect(url_for('index'))

@app.route('/add_member_id', methods=['POST'])
@login_required
def add_member_id():
    """Route : ajoute un nouveau membre à la liste XP"""
    uid = request.form.get('user_id')
    if uid:
        with app.app_context():
            m = MemberXP.query.filter_by(user_id=uid).first()
            if not m:
                db.session.add(MemberXP(user_id=uid, username="Membre "+uid, xp=0, level=0))
                db.session.commit()
    return redirect(url_for('index'))

@app.route('/update_config', methods=['POST'])
@login_required
def update_config():
    """Route : met à jour les configurations"""
    with app.app_context():
        for key, value in request.form.items():
            conf = Config.query.filter_by(key=key).first()
            if conf: conf.value = value
        db.session.commit()
    return redirect(url_for('index'))

@app.route('/delete_member/<user_id>')
@login_required
def delete_member(user_id):
    """Route : supprime un membre de la liste XP"""
    with app.app_context():
        m = MemberXP.query.filter_by(user_id=user_id).first()
        if m:
            db.session.delete(m)
            db.session.commit()
    return redirect(url_for('index'))

@app.route('/add_event', methods=['POST'])
@login_required
def add_event():
    """Route : crée ou modifie un événement programmé"""
    title = request.form.get('title')
    msg = request.form.get('message')
    date_str = request.form.get('date')
    time_str = request.form.get('time')
    ev_id = request.form.get('ev_id')

    try:
        full_datetime = datetime.datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    except Exception as e:
        print(f"Erreur date : {e}")
        return "Format de date invalide", 400

    if ev_id and ev_id.strip():
        event = Event.query.get(int(ev_id))
        event.title = title
        event.message = msg
        event.scheduled_at = full_datetime
        event.posted = False
    else:
        event = Event(title=title, message=msg, scheduled_at=full_datetime)
        db.session.add(event)

    file = request.files.get('image')
    if file and file.filename != '':
        filename = secure_filename(file.filename)
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        event.image_filename = filename

    db.session.commit()
    return redirect('/')

@app.route('/delete_event/<int:id>')
@login_required
def delete_event(id):
    """Route : supprime un événement programmé"""
    with app.app_context():
        ev = Event.query.get(id)
        if ev:
            db.session.delete(ev)
            db.session.commit()
    return redirect(url_for('index'))

@app.route('/clear_infractions')
@login_required
def clear_infractions():
    """Route : vide les logs d'infractions"""
    with app.app_context():
        db.session.query(Infraction).delete()
        db.session.commit()
    return redirect(url_for('index'))

@app.route('/reset_warnings')
@login_required
def reset_warnings():
    """Route : réinitialise tous les avertissements"""
    with app.app_context():
        db.session.query(UserWarning).delete()
        db.session.commit()
    return redirect(url_for('index'))

@app.route('/img/<path:filename>')
def serve_img(filename):
    """Route : sert les images du dossier img"""
    return send_from_directory(IMG_DIR, filename)

@app.route('/save_ai_config', methods=['POST'])
@login_required
@admin_required
def save_ai_config():
    """Route : sauvegarde la configuration IA"""
    action = request.form.get('ai_action', 'save')

    with app.app_context():
        if action == 'toggle':
            current = get_config('ai_enabled', 'false')
            new_val = 'false' if current == 'true' else 'true'
            conf = Config.query.filter_by(key='ai_enabled').first()
            if conf:
                conf.value = new_val
            db.session.commit()
            status = "activée" if new_val == 'true' else "désactivée"
            return redirect(url_for('index', backup_msg=f"Intelligence IA {status}."))

        for key in ['ai_api_key', 'ai_model', 'ai_cooldown', 'ai_context_size', 'ai_forbidden_topics', 'ai_creator_name', 'ai_custom_context', 'ai_system_prompt']:
            value = request.form.get(key, '').strip()
            conf = Config.query.filter_by(key=key).first()
            if conf and value:
                conf.value = value
        db.session.commit()

    return redirect(url_for('index', backup_msg="Configuration IA sauvegardée."))

@app.route('/save_promo_config', methods=['POST'])
@login_required
@admin_required
def save_promo_config():
    """Route : sauvegarde la configuration des messages promotionnels"""
    action = request.form.get('promo_action', 'save')

    with app.app_context():
        if action == 'toggle':
            current = get_config('promo_enabled', 'false')
            new_val = 'false' if current == 'true' else 'true'
            conf = Config.query.filter_by(key='promo_enabled').first()
            if conf:
                conf.value = new_val
            db.session.commit()
            status = "activés" if new_val == 'true' else "désactivés"
            return redirect(url_for('index', backup_msg=f"Messages promotionnels {status}."))

        # Sauvegarder le message
        promo_message = request.form.get('promo_message', '').strip()
        promo_days = ','.join(request.form.getlist('promo_days'))
        promo_interval = request.form.get('promo_interval', '6')
        promo_min_messages = request.form.get('promo_min_messages', '10')

        for key, value in [
            ('promo_message', promo_message),
            ('promo_days', promo_days),
            ('promo_interval', promo_interval),
            ('promo_min_messages', promo_min_messages),
        ]:
            conf = Config.query.filter_by(key=key).first()
            if conf:
                conf.value = value
        db.session.commit()

    return redirect(url_for('index', backup_msg="Configuration promotionnelle sauvegardée."))

@app.route('/save_bot_identity', methods=['POST'])
@login_required
@admin_required
def save_bot_identity():
    """Route : sauvegarde le nom et l'avatar du bot Discord"""
    bot_name = request.form.get('bot_name', '').strip()
    avatar_file = request.files.get('avatar')
    avatar_updated = False

    with app.app_context():
        # Sauvegarder le nom en DB
        if bot_name:
            conf = Config.query.filter_by(key='bot_name').first()
            if conf:
                conf.value = bot_name
            else:
                db.session.add(Config(key='bot_name', label='|OPTIONNEL|Nom du Bot — affiché dans les embeds et messages. Si vide : Marvin OS.', value=bot_name))
            db.session.commit()

        # Sauvegarder l'image localement
        if avatar_file and avatar_file.filename != '':
            avatar_path = os.path.join(IMG_DIR, 'marvin.png')
            avatar_file.save(avatar_path)
            avatar_updated = True

    # Appliquer l'avatar sur Discord via asyncio thread-safe
    if avatar_updated:
        async def update_discord_avatar():
            try:
                avatar_path = os.path.join(IMG_DIR, 'marvin.png')
                with open(avatar_path, 'rb') as f:
                    avatar_data = f.read()
                await bot.user.edit(avatar=avatar_data)
                print("[BOT] ✅ Avatar Discord mis à jour avec succès")

                # Notifier dans les logs
                logs_id = get_config_int('chan_logs')
                if logs_id:
                    log_chan = bot.get_channel(logs_id)
                    if log_chan:
                        embed = discord.Embed(
                            title=" Identité du bot mise à jour",
                            description="L'avatar du bot a été modifié depuis le dashboard.",
                            color=0x00b4d8,
                            timestamp=datetime.datetime.now()
                        )
                        await log_chan.send(embed=embed)
            except discord.errors.HTTPException as e:
                if e.status == 429:
                    print("[BOT] ⚠️ Limite Discord atteinte : max 2 changements d'avatar par heure")
                else:
                    print(f"[BOT] ❌ Erreur Discord lors du changement d'avatar : {e}")
            except Exception as e:
                print(f"[BOT] ❌ Erreur inattendue avatar : {e}")

        # Soumettre la coroutine de manière thread-safe
        import asyncio
        asyncio.run_coroutine_threadsafe(update_discord_avatar(), bot.loop)

    msg = "Identité du bot mise à jour."
    if avatar_updated:
        msg += " L'avatar Discord sera mis à jour dans quelques secondes."

    return redirect(url_for('index', backup_msg=msg))

@app.route('/save_email_config', methods=['POST'])
@login_required
@admin_required
def save_email_config():
    """Route : sauvegarde ou teste la configuration email"""
    action = request.form.get('action', 'save')
    mail_server = request.form.get('mail_server', '').strip()
    mail_port = request.form.get('mail_port', '587').strip()
    mail_user = request.form.get('mail_user', '').strip()
    mail_pass = request.form.get('mail_pass', '').strip()
    mail_receiver = request.form.get('mail_receiver', '').strip()

    if action == 'test':
        if send_test_email(mail_receiver, mail_server, mail_port, mail_user, mail_pass):
            return redirect(url_for('index', backup_msg="Email de test envoyé avec succès à " + mail_receiver))
        else:
            return redirect(url_for('index', backup_err="Échec de l'envoi. Vérifiez vos paramètres SMTP."))

    # Sauvegarder dans le .env
    env_path = os.path.join(basedir, '.env')
    try:
        with open(env_path, 'r') as f:
            env_content = f.read()

        import re as _re
        for key, val in [
            ('MAIL_SERVER', mail_server),
            ('MAIL_PORT', mail_port),
            ('MAIL_USERNAME', mail_user),
            ('MAIL_PASSWORD', mail_pass),
            ('MAIL_RECEIVER', mail_receiver)
        ]:
            if f"{key}=" in env_content:
                env_content = _re.sub(f"^{key}=.*$", f"{key}={val}", env_content, flags=_re.MULTILINE)
            else:
                env_content += f"\n{key}={val}"

        with open(env_path, 'w') as f:
            f.write(env_content)

        load_dotenv(override=True)
        return redirect(url_for('index', backup_msg="Configuration email sauvegardée."))
    except Exception as e:
        return redirect(url_for('index', backup_err=f"Erreur sauvegarde : {str(e)}"))

@app.route('/')
@login_required
def index():
    """Route : page principale du dashboard"""
    with app.app_context():
        leaderboard = MemberXP.query.order_by(MemberXP.xp.desc()).all()
        configs = Config.query.all()
        events = Event.query.order_by(Event.scheduled_at.asc()).all()
        infractions = Infraction.query.order_by(Infraction.timestamp.desc()).all()

        now = datetime.datetime.now(tz_paris)
        current_month = now.strftime("%Y-%m")
        current_month_stats = MemberStats.query.filter_by(month_year=current_month).order_by(
            MemberStats.messages_count.desc()
        ).all()

        backups = get_backup_files()

        backup_msg = request.args.get('backup_msg')
        backup_err = request.args.get('backup_err')

        dashboard_users = DashboardUser.query.order_by(DashboardUser.created_at.asc()).all()
        current_user = DashboardUser.query.get(session.get('user_id'))
        user_msg = request.args.get('user_msg')
        user_err = request.args.get('user_err')
        mail_configured = bool(os.getenv('MAIL_SERVER') and os.getenv('MAIL_USERNAME'))

        bot_name = get_config('bot_name', 'Marvin OS')
        promo_message = get_config('promo_message', '')
        promo_days = get_config('promo_days', 'lun,mar,mer,jeu,ven').split(',')
        promo_interval = get_config('promo_interval', '6')
        promo_min_messages = get_config('promo_min_messages', '10')
        promo_enabled = get_config('promo_enabled', 'false')
        promo_last_sent = get_config('promo_last_sent', '')
        discord_username = str(bot.user) if bot.user else 'Non connecté'
        mail_server_conf = os.getenv('MAIL_SERVER', '')
        mail_port_conf = os.getenv('MAIL_PORT', '587')
        mail_user_conf = os.getenv('MAIL_USERNAME', '')
        mail_pass_conf = os.getenv('MAIL_PASSWORD', '')
        mail_receiver_conf = os.getenv('MAIL_RECEIVER', '')

        ai_enabled = get_config('ai_enabled', 'false')
        ai_api_key = get_config('ai_api_key', '')
        ai_model = get_config('ai_model', 'claude-haiku-4-5')
        ai_cooldown = get_config('ai_cooldown', '30')
        ai_context_size = get_config('ai_context_size', '20')
        ai_forbidden_topics = get_config('ai_forbidden_topics', 'politique,guerre,religion,conflits armés')
        ai_creator_name = get_config('ai_creator_name', '')
        ai_custom_context = get_config('ai_custom_context', '')
        ai_system_prompt = get_config('ai_system_prompt', '')

        return render_template_string(HTML_DASHBOARD, leaderboard=leaderboard, configs=configs, events=events, roles=XP_ROLES, infractions=infractions, current_month_stats=current_month_stats, backups=backups, backup_msg=backup_msg, backup_err=backup_err, dashboard_users=dashboard_users, current_user=current_user, user_msg=user_msg, user_err=user_err, mail_configured=mail_configured, mail_server_conf=mail_server_conf, mail_port_conf=mail_port_conf, mail_user_conf=mail_user_conf, mail_pass_conf=mail_pass_conf, mail_receiver_conf=mail_receiver_conf, bot_name=bot_name, discord_username=discord_username,
            promo_message=promo_message, promo_days=promo_days, promo_interval=promo_interval,
            promo_min_messages=promo_min_messages, promo_enabled=promo_enabled, promo_last_sent=promo_last_sent,
            ai_enabled=ai_enabled, ai_api_key=ai_api_key, ai_model=ai_model,
            ai_cooldown=ai_cooldown, ai_context_size=ai_context_size, ai_forbidden_topics=ai_forbidden_topics,
            ai_creator_name=ai_creator_name, ai_custom_context=ai_custom_context,
            ai_system_prompt=ai_system_prompt)

# ============================
# DASHBOARD HTML
# ============================


# ============================
# TEMPLATES HTML AUTH & WIZARD
# ============================

HTML_LOGIN = """
<!DOCTYPE html><html><head><title>Marvin OS — Connexion</title>
<style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: 'Segoe UI', sans-serif; background: #051622; color: #caf0f8; min-height: 100vh; display: flex; align-items: center; justify-content: center; }
    .login-box { background: #0b253a; padding: 40px; border-radius: 12px; border: 1px solid #00b4d8; width: 100%; max-width: 400px; }
    h1 { color: #00b4d8; margin-bottom: 8px; font-size: 1.5em; }
    .subtitle { color: #90e0ef; font-size: 0.85em; margin-bottom: 30px; }
    label { display: block; color: #90e0ef; font-size: 0.85em; margin-bottom: 5px; margin-top: 15px; }
    input { width: 100%; background: #051622; border: 1px solid #00b4d8; color: #fff; padding: 10px; border-radius: 5px; font-size: 1em; }
    .btn { width: 100%; background: #00b4d8; border: none; color: white; padding: 12px; border-radius: 5px; cursor: pointer; font-weight: bold; font-size: 1em; margin-top: 20px; }
    .btn:hover { background: #0090b0; }
    .error { background: #5f0d0d; color: #ff9999; padding: 10px; border-radius: 5px; margin-bottom: 15px; border-left: 3px solid #ff4d4d; font-size: 0.9em; }
    .forgot { display: block; text-align: center; margin-top: 15px; color: #90e0ef; font-size: 0.85em; text-decoration: none; }
    .forgot:hover { color: #00b4d8; }
    .logo { text-align: center; margin-bottom: 25px; }
    .logo img { width: 70px; height: 70px; border-radius: 50%; border: 2px solid #00b4d8; }
</style>
</head><body>
<div class="login-box">
    <div class="logo">
        <img src="/img/marvin.png" onerror="this.style.display='none'">
    </div>
    <h1>Marvin OS</h1>
    <p class="subtitle">Panneau d'administration — Connexion requise</p>
    {% if error %}<div class="error">❌ {{ error }}</div>{% endif %}
    <form method="POST">
        <label>Identifiant</label>
        <input type="text" name="username" required autofocus>
        <label>Mot de passe</label>
        <input type="password" name="password" required>
        <button type="submit" class="btn">SE CONNECTER</button>
    </form>
    <a href="/forgot_password" class="forgot">Mot de passe oublié ?</a>
</div>
</body></html>
"""

HTML_FORGOT = """
<!DOCTYPE html><html><head><title>Marvin OS — Mot de passe oublié</title>
<style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: 'Segoe UI', sans-serif; background: #051622; color: #caf0f8; min-height: 100vh; display: flex; align-items: center; justify-content: center; }
    .box { background: #0b253a; padding: 40px; border-radius: 12px; border: 1px solid #00b4d8; width: 100%; max-width: 420px; }
    h1 { color: #00b4d8; margin-bottom: 8px; }
    .subtitle { color: #90e0ef; font-size: 0.85em; margin-bottom: 25px; }
    label { display: block; color: #90e0ef; font-size: 0.85em; margin-bottom: 5px; margin-top: 15px; }
    input { width: 100%; background: #051622; border: 1px solid #00b4d8; color: #fff; padding: 10px; border-radius: 5px; font-size: 1em; }
    .btn { width: 100%; background: #00b4d8; border: none; color: white; padding: 12px; border-radius: 5px; cursor: pointer; font-weight: bold; margin-top: 20px; }
    .error { background: #5f0d0d; color: #ff9999; padding: 10px; border-radius: 5px; margin-bottom: 15px; border-left: 3px solid #ff4d4d; font-size: 0.9em; }
    .success { background: #0d5f2f; color: #7ff5d1; padding: 10px; border-radius: 5px; margin-bottom: 15px; border-left: 3px solid #00b4d8; font-size: 0.9em; }
    .warning { background: #3d2800; color: #ffd080; padding: 15px; border-radius: 5px; margin-bottom: 20px; border-left: 3px solid #ffa500; font-size: 0.88em; line-height: 1.5; }
    .back { display: block; text-align: center; margin-top: 15px; color: #90e0ef; font-size: 0.85em; text-decoration: none; }
    .back:hover { color: #00b4d8; }
</style>
</head><body>
<div class="box">
    <h1> Mot de passe oublié</h1>
    <p class="subtitle">Réinitialisation par email</p>

    {% if not mail_configured %}
    <div class="warning">
        ⚠️ <b>Email non configuré</b><br><br>
        La récupération de mot de passe par email n'est pas disponible car aucun serveur email n'a été configuré lors de l'installation.<br><br>
        <b>Solutions :</b><br>
        • Contactez votre administrateur<br>
        • Ou modifiez directement la base de données <code>marvin.db</code>
    </div>
    {% else %}
        {% if error %}<div class="error">❌ {{ error }}</div>{% endif %}
        {% if message %}<div class="success">✅ {{ message }}</div>{% endif %}
        <form method="POST">
            <label>Votre identifiant</label>
            <input type="text" name="username" required>
            <button type="submit" class="btn">ENVOYER LE LIEN DE RÉINITIALISATION</button>
        </form>
    {% endif %}
    <a href="/login" class="back">← Retour à la connexion</a>
</div>
</body></html>
"""

HTML_RESET = """
<!DOCTYPE html><html><head><title>Marvin OS — Réinitialisation</title>
<style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: 'Segoe UI', sans-serif; background: #051622; color: #caf0f8; min-height: 100vh; display: flex; align-items: center; justify-content: center; }
    .box { background: #0b253a; padding: 40px; border-radius: 12px; border: 1px solid #00b4d8; width: 100%; max-width: 400px; }
    h1 { color: #00b4d8; margin-bottom: 8px; }
    .subtitle { color: #90e0ef; font-size: 0.85em; margin-bottom: 25px; }
    label { display: block; color: #90e0ef; font-size: 0.85em; margin-bottom: 5px; margin-top: 15px; }
    input { width: 100%; background: #051622; border: 1px solid #00b4d8; color: #fff; padding: 10px; border-radius: 5px; font-size: 1em; }
    .btn { width: 100%; background: #00b4d8; border: none; color: white; padding: 12px; border-radius: 5px; cursor: pointer; font-weight: bold; margin-top: 20px; }
    .error { background: #5f0d0d; color: #ff9999; padding: 10px; border-radius: 5px; margin-bottom: 15px; border-left: 3px solid #ff4d4d; }
    .success { background: #0d5f2f; color: #7ff5d1; padding: 10px; border-radius: 5px; margin-bottom: 15px; border-left: 3px solid #00b4d8; }
    .back { display: block; text-align: center; margin-top: 15px; color: #90e0ef; font-size: 0.85em; text-decoration: none; }
</style>
</head><body>
<div class="box">
    <h1> Nouveau mot de passe</h1>
    <p class="subtitle">Choisissez un nouveau mot de passe</p>
    {% if error and not token %}<div class="error">❌ {{ error }}</div>
    {% elif success %}<div class="success">✅ {{ success }} <a href="/login" class="back">Se connecter</a></div>
    {% else %}
        {% if error %}<div class="error">❌ {{ error }}</div>{% endif %}
        <form method="POST">
            <label>Nouveau mot de passe (6 caractères minimum)</label>
            <input type="password" name="password" required minlength="6">
            <label>Confirmer le mot de passe</label>
            <input type="password" name="confirm" required>
            <button type="submit" class="btn">RÉINITIALISER</button>
        </form>
    {% endif %}
    <a href="/login" class="back">← Retour à la connexion</a>
</div>
</body></html>
"""

HTML_SETUP = """
<!DOCTYPE html><html><head><title>Marvin OS — Configuration initiale</title>
<style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: 'Segoe UI', sans-serif; background: #051622; color: #caf0f8; min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 20px; }
    .box { background: #0b253a; padding: 40px; border-radius: 12px; border: 1px solid #00b4d8; width: 100%; max-width: 500px; }
    h1 { color: #00b4d8; margin-bottom: 5px; }
    .subtitle { color: #90e0ef; font-size: 0.85em; margin-bottom: 25px; }
    .steps { display: flex; gap: 10px; margin-bottom: 30px; }
    .step { flex: 1; text-align: center; padding: 8px; border-radius: 5px; font-size: 0.8em; font-weight: bold; }
    .step.active { background: #00b4d8; color: white; }
    .step.done { background: #0d5f2f; color: #7ff5d1; }
    .step.pending { background: #071e2f; color: #90e0ef; border: 1px solid #90e0ef; }
    label { display: block; color: #90e0ef; font-size: 0.85em; margin-bottom: 5px; margin-top: 15px; }
    input { width: 100%; background: #051622; border: 1px solid #00b4d8; color: #fff; padding: 10px; border-radius: 5px; font-size: 1em; }
    .btn { width: 100%; background: #00b4d8; border: none; color: white; padding: 12px; border-radius: 5px; cursor: pointer; font-weight: bold; margin-top: 20px; font-size: 1em; }
    .btn-secondary { width: 100%; background: transparent; border: 1px solid #90e0ef; color: #90e0ef; padding: 10px; border-radius: 5px; cursor: pointer; margin-top: 10px; }
    .btn-test { width: 100%; background: #1a5276; border: none; color: white; padding: 10px; border-radius: 5px; cursor: pointer; margin-top: 10px; }
    .error { background: #5f0d0d; color: #ff9999; padding: 10px; border-radius: 5px; margin-bottom: 15px; border-left: 3px solid #ff4d4d; font-size: 0.9em; }
    .success { background: #0d5f2f; color: #7ff5d1; padding: 10px; border-radius: 5px; margin-bottom: 15px; border-left: 3px solid #00b4d8; font-size: 0.9em; }
    .warning { background: #3d2800; color: #ffd080; padding: 15px; border-radius: 5px; margin: 15px 0; border-left: 3px solid #ffa500; font-size: 0.85em; line-height: 1.6; }
    .info { background: #071e2f; color: #90e0ef; padding: 12px; border-radius: 5px; margin: 15px 0; border-left: 3px solid #00b4d8; font-size: 0.85em; line-height: 1.6; }
    .checkbox-row { display: flex; align-items: center; gap: 10px; margin-top: 15px; }
    .checkbox-row input { width: auto; }
</style>
</head><body>
<div class="box">
    <h1>⚙️ Marvin OS</h1>
    <p class="subtitle">Assistant de configuration — Première installation</p>

    <div class="steps">
        <div class="step {{ 'active' if step == 1 else 'done' if step > 1 else 'pending' }}">1. Compte admin</div>
        <div class="step {{ 'active' if step == 2 else 'done' if step > 2 else 'pending' }}">2. Email</div>
        <div class="step {{ 'active' if step == 3 else 'pending' }}">3. Terminé</div>
    </div>

    {% if error %}<div class="error">❌ {{ error }}</div>{% endif %}
    {% if test_success %}<div class="success">✅ {{ test_success }}</div>{% endif %}

    {% if step == 1 %}
    <p style="color:#90e0ef; font-size:0.9em; margin-bottom:20px;">Créez votre compte administrateur pour accéder au dashboard.</p>
    <form method="POST">
        <input type="hidden" name="action" value="create_admin">
        <label>Identifiant (3 caractères minimum)</label>
        <input type="text" name="username" required minlength="3">
        <label>Mot de passe (6 caractères minimum)</label>
        <input type="password" name="password" required minlength="6">
        <label>Confirmer le mot de passe</label>
        <input type="password" name="confirm" required>
        <label>Email (optionnel — pour la récupération de mot de passe)</label>
        <input type="email" name="email" placeholder="votre@email.com">
        <button type="submit" class="btn">CRÉER LE COMPTE ADMIN →</button>
    </form>

    {% elif step == 2 %}
    <div class="warning">
        ⚠️ <b>Configuration email recommandée</b><br><br>
        Sans email configuré, il sera <b>impossible de récupérer votre mot de passe</b> en cas d'oubli.<br>
        La seule alternative serait de modifier directement la base de données.<br><br>
        Vous pouvez ignorer cette étape, mais elle est fortement conseillée.
    </div>

    <div class="info">
         <b>Fournisseurs supportés :</b><br>
        • <b>Gmail</b> : smtp.gmail.com / port 587 (nécessite un mot de passe d'application Google)<br>
        • <b>SMTP perso</b> : les infos sont chez votre hébergeur
    </div>

    <form method="POST" id="emailForm">
        <input type="hidden" name="action" value="save_email" id="formAction">
        <label>Serveur SMTP</label>
        <input type="text" name="mail_server" value="{{ mail_server }}" placeholder="smtp.gmail.com">
        <label>Port</label>
        <input type="text" name="mail_port" value="{{ mail_port }}" placeholder="587">
        <label>Email expéditeur</label>
        <input type="email" name="mail_user" value="{{ mail_user }}" placeholder="marvin@votredomaine.com">
        <label>Mot de passe / Clé d'application</label>
        <input type="password" name="mail_pass" value="{{ mail_pass }}">
        <label>Email de réception (où recevoir les resets)</label>
        <input type="email" name="mail_receiver" value="{{ mail_receiver }}" placeholder="admin@votredomaine.com">
        <button type="submit" class="btn">SAUVEGARDER ET CONTINUER →</button>
        <button type="button" class="btn-test" onclick="testEmail()"> TESTER L'ENVOI D'ABORD</button>
    </form>
    <form method="POST" style="margin-top:10px;">
        <input type="hidden" name="action" value="skip_email">
        <button type="submit" class="btn-secondary">Ignorer cette étape (déconseillé)</button>
    </form>

    <script>
    function testEmail() {
        document.getElementById('formAction').value = 'test_email';
        document.getElementById('emailForm').submit();
    }
    </script>

    {% elif step == 3 %}
    <div class="success" style="padding:20px; text-align:center;">
        ✅ <b>Configuration terminée !</b><br><br>
        Marvin OS est prêt à être utilisé.<br>
        Vous allez être redirigé vers la page de connexion.
    </div>
    <script>setTimeout(function(){ window.location.href = '/login'; }, 3000);</script>
    <a href="/login" style="display:block; text-align:center; margin-top:15px; color:#00b4d8;">Accéder maintenant →</a>
    {% endif %}
</div>
</body></html>
"""


HTML_DASHBOARD = """
<!DOCTYPE html><html><head><title>MARVIN OS</title>
<style>
    body { font-family: 'Segoe UI', sans-serif; background: #051622; color: #caf0f8; margin: 0; }
    .header-main { background: #0b253a; padding: 20px 50px; border-bottom: 3px solid #00b4d8; display: flex; align-items: center; gap: 25px; }
    .logo-marvin { width: 60px; height: 60px; border-radius: 50%; border: 2px solid #00b4d8; object-fit: cover; }
    .nav-bar { background: #071e2f; display: flex; justify-content: center; flex-wrap: wrap; border-bottom: 1px solid rgba(0, 180, 216, 0.2); }
    .nav-item { padding: 15px 22px; cursor: pointer; color: #90e0ef; font-weight: bold; text-transform: uppercase; font-size: 0.85em; transition: 0.3s; border-bottom: 3px solid transparent; }
    .nav-item.active { color: #00b4d8; border-bottom: 3px solid #00b4d8; background: rgba(0, 180, 216, 0.1); }
    .container { padding: 30px 50px; }
    .tab-content { display: none; }
    .tab-content.active { display: block; }
    .card { background: rgba(11, 37, 58, 0.5); padding: 25px; border-radius: 10px; border: 1px solid rgba(0, 180, 216, 0.3); margin-bottom: 20px; }
    .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
    .grid-1-2 { display: grid; grid-template-columns: 1fr 2fr; gap: 20px; }
    .grid-2-1 { display: grid; grid-template-columns: 2fr 1fr; gap: 20px; }
    table { width: 100%; border-collapse: collapse; }
    th { background: #00b4d8; color: #fff; padding: 12px; text-align: left; }
    td { padding: 10px; border-bottom: 1px solid rgba(0, 180, 216, 0.1); }
    .btn-ok { background: #00b4d8; border: none; color: white; padding: 6px 15px; border-radius: 3px; cursor: pointer; font-weight: bold; }
    .btn-clear { background: #ff4d4d; border: none; color: white; padding: 10px 20px; border-radius: 5px; cursor: pointer; font-weight: bold; text-decoration: none; display: inline-block; }
    .btn-warn-reset { background: #ffa500; border: none; color: white; padding: 10px 20px; border-radius: 5px; cursor: pointer; font-weight: bold; text-decoration: none; display: inline-block; }
    .btn-yt { background: #ff0000; color: white; padding: 10px; border: none; border-radius: 5px; cursor: pointer; font-weight: bold; width: 100%; margin-top: 10px; }
    .btn-del { color: #ff4d4d; text-decoration: none; font-weight: bold; }
    .btn-backup { background: #00b4d8; border: none; color: white; padding: 10px 20px; border-radius: 5px; cursor: pointer; font-weight: bold; text-decoration: none; display: inline-block; margin-right: 10px; }
    .btn-restore { background: #00a8cc; border: none; color: white; padding: 6px 12px; border-radius: 3px; cursor: pointer; font-weight: bold; }
    .btn-delete-backup { background: #ff6b6b; border: none; color: white; padding: 6px 12px; border-radius: 3px; cursor: pointer; font-weight: bold; }
    .btn-reboot { background: #ff9500; border: none; color: white; padding: 10px 20px; border-radius: 5px; cursor: pointer; font-weight: bold; text-decoration: none; display: inline-block; }
    .alert-success { background: #0d5f2f; color: #7ff5d1; padding: 12px; border-radius: 5px; margin-bottom: 15px; border-left: 4px solid #00b4d8; }
    .alert-error { background: #5f0d0d; color: #ff9999; padding: 12px; border-radius: 5px; margin-bottom: 15px; border-left: 4px solid #ff4d4d; }
    input, textarea { background: #051622; border: 1px solid #00b4d8; color: #fff; padding: 8px; border-radius: 4px; }
    .full-width { width: 100%; box-sizing: border-box; }
    .checkbox-row { display: flex; align-items: center; gap: 10px; }
    .checkbox-row input[type=checkbox] { width: auto; }
    .secret-tag { display: inline-block; background: #1a3a4a; color: #00b4d8; border: 1px solid #00b4d8; border-radius: 4px; padding: 2px 8px; font-family: monospace; font-size: 0.9em; margin: 2px; }
    .cmd-block { background: #071e2f; border-radius: 8px; padding: 15px; margin-bottom: 12px; border-left: 3px solid #00b4d8; }
    .cmd-block.staff { border-left-color: #ff9500; }
    .cmd-name { color: #00b4d8; font-weight: bold; font-family: monospace; font-size: 1em; }
    .cmd-block.staff .cmd-name { color: #ff9500; }
    .cmd-desc { color: #caf0f8; font-size: 0.9em; margin-top: 4px; }
    .cmd-example { color: #90e0ef; font-size: 0.8em; font-style: italic; margin-top: 3px; }
    hr { border: none; border-top: 1px solid rgba(0,180,216,0.2); margin: 20px 0; }
    .section-title { color: #00b4d8; font-size: 1.1em; font-weight: bold; margin: 20px 0 10px 0; display: flex; align-items: center; gap: 8px; }
    .badge-requis { background:#c0392b; color:white; font-size:0.7em; font-weight:bold; padding:2px 7px; border-radius:3px; }
    .badge-optionnel { background:#2c3e50; color:#90e0ef; font-size:0.7em; font-weight:bold; padding:2px 7px; border-radius:3px; border:1px solid #90e0ef; }
    .badge-auto { background:#1a5276; color:#aed6f1; font-size:0.7em; font-weight:bold; padding:2px 7px; border-radius:3px; }
</style>
</head><body>
    <div class="header-main">
        <img src="/img/marvin.png" class="logo-marvin" onerror="this.src='https://via.placeholder.com/60'">
        <h1>MARVIN <span style="color: #00b4d8;">OS</span></h1>
        <!-- Vous pouvez remplacer ce lien par le vôtre sur buymeacoffee.com,
             ou supprimer ce bloc si vous ne souhaitez pas afficher de bouton de don -->
        <div style="margin-left:auto; display:flex; align-items:center; gap:15px;">
            <span style="color:#90e0ef; font-size:0.8em; font-style:italic;">Si ce bot vous est utile...</span>
            <a href="https://www.buymeacoffee.com/egalistelw" target="_blank">
                <img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy Me A Coffee" style="height:45px !important; width:163px !important;">
            </a>
        </div>
    </div>
    <div class="nav-bar">
        <div id="btn-commandes" class="nav-item" onclick="openTab('commandes')"> Commandes</div>
        <div id="btn-mod" class="nav-item" onclick="openTab('mod')">️ Modération</div>
        <div id="btn-xp" class="nav-item active" onclick="openTab('xp')">⭐ Système XP</div>
        <div id="btn-event" class="nav-item" onclick="openTab('event')"> Événements</div>
        <div id="btn-acces" class="nav-item" onclick="openTab('acces')"> Gestion des Accès</div>
        <div id="btn-botconfig" class="nav-item" onclick="openTab('botconfig')"> Config Bot</div>
        <div id="btn-ia" class="nav-item" onclick="openTab('ia')"> Intelligence</div>
        <div id="btn-serveur" class="nav-item" onclick="openTab('serveur')">️ Config Serveur</div>
        <a href="/logout" style="padding:15px 20px; color:#ff4d4d; font-weight:bold; text-decoration:none; text-transform:uppercase; font-size:0.85em;">⏏ Déco</a>
    </div>
    <div class="container">
        {% if backup_msg %}
        <div class="alert-success">✅ {{ backup_msg }}</div>
        {% endif %}
        {% if backup_err %}
        <div class="alert-error">❌ {{ backup_err }}</div>
        {% endif %}
        {% if not mail_configured %}
        <div style="background:#3d2800;color:#ffd080;padding:12px 20px;border-radius:5px;margin-bottom:15px;border-left:4px solid #ffa500;display:flex;align-items:center;justify-content:space-between;">
            <span>⚠️ <b>Email non configuré</b> — La récupération de mot de passe est désactivée.</span>
            <a href="#" onclick="openTab('botconfig');return false;" style="color:#ffa500;font-weight:bold;text-decoration:none;white-space:nowrap;margin-left:20px;">Configurer maintenant →</a>
        </div>
        {% endif %}

        <script>
            const successMsg = document.querySelector('.alert-success');
            const errorMsg = document.querySelector('.alert-error');
            const style = document.createElement('style');
            style.textContent = '@keyframes fadeOut { from { opacity: 1; } to { opacity: 0; } }';
            document.head.appendChild(style);
            function afficherConfirmationTemporaire(texte, type, duree) {
                const msg = document.createElement('div');
                msg.className = type === 'error' ? 'alert-error' : 'alert-success';
                msg.textContent = texte;
                msg.style.animation = 'fadeOut 0.5s ease-in ' + (duree - 500) + 'ms forwards';
                const container = document.querySelector('.container');
                container.insertBefore(msg, container.firstChild);
                setTimeout(function() { msg.remove(); }, duree);
            }
            if (successMsg && successMsg.innerText.includes('Redémarrage')) {
                localStorage.setItem('marvinRestarting', 'true');
                let tentatives = 0;
                setTimeout(function() {
                    const verifier = setInterval(function() {
                        tentatives++;
                        fetch('/?check=1', { cache: 'no-store' })
                            .then(function(response) {
                                if (response.status === 200) {
                                    clearInterval(verifier);
                                    localStorage.setItem('marvinRestartSuccess', 'true');
                                    setTimeout(function() { window.location.href = '/'; }, 2000);
                                }
                            })
                            .catch(function(err) {
                                if (tentatives >= 40) {
                                    clearInterval(verifier);
                                    window.location.href = '/?backup_err=Timeout';
                                }
                            });
                    }, 1000);
                }, 10000);
            }
            if (localStorage.getItem('marvinRestartSuccess')) {
                afficherConfirmationTemporaire('✅ Bot redémarré avec succès!', 'success', 6000);
                localStorage.removeItem('marvinRestartSuccess');
            }
            if (successMsg && !successMsg.innerText.includes('Redémarrage')) {
                successMsg.style.animation = 'fadeOut 0.5s ease-in 3500ms forwards';
                setTimeout(function() { successMsg.remove(); }, 4000);
            }
            if (errorMsg) {
                afficherConfirmationTemporaire(errorMsg.textContent, 'error', 6000);
            }
        </script>

        <!-- ======= ONGLET COMMANDES ======= -->
        <div id="commandes" class="tab-content">
            <div class="grid-2">
                <div>
                    <div class="card">
                        <div class="section-title">⭐ XP & Rangs</div>
                        <div class="cmd-block">
                            <div class="cmd-name">!rang [@membre]</div>
                            <div class="cmd-desc">Affiche votre niveau et XP. Mentionnez un membre pour voir le sien.</div>
                        </div>
                        <div class="cmd-block">
                            <div class="cmd-name">!top</div>
                            <div class="cmd-desc">Classement des 5 membres les plus actifs (hors administrateurs).</div>
                        </div>

                        <div class="section-title"> Fun & Interaction</div>
                        <div class="cmd-block">
                            <div class="cmd-name">!ouinon [question]</div>
                            <div class="cmd-desc">Lance un sondage rapide oui/non avec réactions automatiques.</div>
                            <div class="cmd-example">Ex : !ouinon On fait une impression demain ?</div>
                        </div>
                        <div class="cmd-block">
                            <div class="cmd-name">!probabilite</div>
                            <div class="cmd-desc">Marvin calcule vos chances de succès... avec son enthousiasme habituel.</div>
                        </div>

                        <div class="section-title">⏰ Minuteurs</div>
                        <div class="cmd-block">
                            <div class="cmd-name">!timer [minutes] [message]</div>
                            <div class="cmd-desc">Reçois un rappel privé après X minutes (1 à 1440 min).</div>
                            <div class="cmd-example">Ex : !timer 30 vérifier impression</div>
                        </div>
                        <div class="cmd-block">
                            <div class="cmd-name">!timer [minutes] @membre [message]</div>
                            <div class="cmd-desc">Envoie un rappel à un autre membre après X minutes.</div>
                            <div class="cmd-example">Ex : !timer 10 @Ega réunion dans 10 min</div>
                        </div>

                        <div class="section-title"> Ressources</div>
                        <div class="cmd-block">
                            <div class="cmd-name">!tuto [terme]</div>
                            <!-- À COMPLÉTER : adaptez la description si vous changez le site -->
                        <div class="cmd-desc">Cherche un tutoriel sur votre site et retourne le lien.</div>
                            <div class="cmd-example">Ex : !tuto impression 3D</div>
                        </div>
                        <div class="cmd-block">
                            <div class="cmd-name">!video</div>
                            <div class="cmd-desc">Affiche le lien vers la dernière vidéo YouTube de la chaîne.</div>
                        </div>
                        <div class="cmd-block">
                            <div class="cmd-name">!aide</div>
                            <div class="cmd-desc">Affiche le message d'aide adapté à votre rôle (membre ou staff).</div>
                        </div>
                    </div>

                    <div class="card">
                        <div class="section-title" style="color:#ff9500;">️ Commandes Staff uniquement</div>
                        <div class="cmd-block staff">
                            <div class="cmd-name">!clean [nombre]</div>
                            <div class="cmd-desc">Supprime les X derniers messages du salon.</div>
                            <div class="cmd-example">Ex : !clean 10</div>
                        </div>
                        <div class="cmd-block staff">
                            <div class="cmd-name">!clean [nombre] @membre</div>
                            <div class="cmd-desc">Supprime les X derniers messages d'un membre spécifique.</div>
                        </div>
                        <div class="cmd-block staff">
                            <div class="cmd-name">!lock</div>
                            <div class="cmd-desc">Verrouille le salon actuel (plus personne ne peut écrire).</div>
                        </div>
                        <div class="cmd-block staff">
                            <div class="cmd-name">!unlock</div>
                            <div class="cmd-desc">Déverrouille le salon actuel.</div>
                        </div>
                        <div class="cmd-block staff">
                            <div class="cmd-name">!inspecter @membre</div>
                            <div class="cmd-desc">Affiche le rapport complet d'un membre : XP, niveau, avertissements, nombre d'infractions.</div>
                        </div>
                    </div>
                </div>

                <div>
                    <div class="card">
                        <div class="section-title">⚙️ Salons Vocaux Temporaires</div>
                        <p style="color:#90e0ef; font-size:0.9em; line-height:1.6;">
                            Rejoignez le salon vocal <b style="color:#00b4d8;">➕ Créer mon Salon</b> pour générer automatiquement votre atelier privé.<br><br>
                            En tant que propriétaire de votre atelier vous pouvez :<br>
                            • <b>Renommer</b> le salon via clic-droit<br>
                            • <b>Expulser</b> des membres indésirables<br>
                            • <b>Limiter</b> le nombre de places<br>
                            • <b>Privatiser</b> l'accès via les permissions<br><br>
                            Marvin supprime automatiquement le salon dès que vous le quittez.
                        </p>
                    </div>

                    <div class="card">
                        <div class="section-title"> Mots-clés Secrets</div>
                        <p style="color:#90e0ef; font-size:0.85em; margin-bottom:15px;">Marvin réagit automatiquement à ces mots dans n'importe quel message. Références à <i>H2G2 — Le Guide du Voyageur Galactique</i>.</p>
                        <table>
                            <thead><tr><th>Mot-clé détecté</th><th>Réaction de Marvin</th></tr></thead>
                            <tbody>
                                <tr>
                                    <td><span class="secret-tag">serviette</span></td>
                                    <td style="font-size:0.85em; color:#90e0ef;">Réflexion philosophique sur l'utilité d'une serviette</td>
                                </tr>
                                <tr>
                                    <td><span class="secret-tag">panique</span></td>
                                    <td style="font-size:0.85em; color:#90e0ef;">Citation du célèbre "PAS DE PANIQUE" du Guide</td>
                                </tr>
                                <tr>
                                    <td><span class="secret-tag">42</span></td>
                                    <td style="font-size:0.85em; color:#90e0ef;">La réponse à la Grande Question sur la Vie, l'Univers et le Reste</td>
                                </tr>
                                <tr>
                                    <td><span class="secret-tag">cerveau</span> <span class="secret-tag">intelligent</span></td>
                                    <td style="font-size:0.85em; color:#90e0ef;">Complainte sur son cerveau de la taille d'une planète</td>
                                </tr>
                                <tr>
                                    <td><span class="secret-tag">la vie</span></td>
                                    <td style="font-size:0.85em; color:#90e0ef;">Vision pessimiste et poétique de l'existence</td>
                                </tr>
                                <tr>
                                    <td><span class="secret-tag">salut marvin</span> <span class="secret-tag">bonjour marvin</span></td>
                                    <td style="font-size:0.85em; color:#90e0ef;">Accuse réception avec son enthousiasme caractéristique</td>
                                </tr>
                                <tr>
                                    <td><span class="secret-tag">dauphin</span></td>
                                    <td style="font-size:0.85em; color:#90e0ef;">"Salut, et encore merci pour le poisson !"</td>
                                </tr>
                            </tbody>
                        </table>
                    </div>

                    <div class="card">
                        <div class="section-title"> Comportements Automatiques</div>
                        <p style="color:#90e0ef; font-size:0.85em; line-height:1.8;">
                            • <b>XP automatique</b> : chaque message rapporte 5 à 10 XP (cooldown 40 sec)<br>
                            • <b>Montée de niveau</b> : annonce et attribution automatique du rôle<br>
                            • <b>Accueil</b> : message de bienvenue à chaque nouveau membre<br>
                            • <b>Anti-spam</b> : détection majuscules excessives et emojis en masse<br>
                            • <b>Anti-raid</b> : ban automatique si spam sur 3+ salons en 10 sec<br>
                            • <b>Hall of Fame</b> : copie automatique des créations populaires<br>
                            • <b>YouTube</b> : annonce automatique des nouvelles vidéos (toutes les 30 min)<br>
                            • <b>Classement mensuel</b> : top 5 le 1er du mois à 9h avec bonus XP<br>
                            • <b>Nettoyage quotidien</b> : suppression des données des membres partis<br>
                            • <b>Sauvegarde automatique</b> : backup de la DB chaque nuit à minuit
                        </p>
                    </div>
                </div>
            </div>
        </div>

        <!-- ======= ONGLET MODÉRATION ======= -->
        <div id="mod" class="tab-content">
            <div class="grid-2">
                <div class="card">
                    <h3> Logs & Infractions</h3>
                    <table style="margin-top:15px;">
                        <thead><tr><th>Membre</th><th>Type</th><th>Contenu</th><th>Date</th></tr></thead>
                        <tbody>
                            {% for inf in infractions %}
                            <tr>
                                <td>{{ inf.username }}</td>
                                <td style="color:#ff4d4d;">{{ inf.word_found }}</td>
                                <td><i style="font-size:0.85em;">{{ inf.content[:50] }}{% if inf.content|length > 50 %}...{% endif %}</i></td>
                                <td style="font-size:0.85em;">{{ inf.timestamp.strftime('%d/%m %H:%M') }}</td>
                            </tr>
                            {% endfor %}
                            {% if not infractions %}
                            <tr><td colspan="4" style="text-align:center; color:#90e0ef;">Aucune infraction enregistrée</td></tr>
                            {% endif %}
                        </tbody>
                    </table>
                    <div style="margin-top:20px; display:flex; gap:10px;">
                        <a href="/clear_infractions" class="btn-clear" onclick="return confirm('Vider tous les logs ?')">VIDER LOGS</a>
                        <a href="/reset_warnings" class="btn-warn-reset" onclick="return confirm('Réinitialiser tous les avertissements ?')">RESET AVERTISSEMENTS</a>
                    </div>
                </div>

                <div class="card">
                    <h3> Stats Mensuelles — Mois en cours</h3>
                    <table style="margin-top:15px;">
                        <thead><tr><th>Rang</th><th>Membre</th><th style="text-align:center;">Messages</th></tr></thead>
                        <tbody>
                            {% if current_month_stats %}
                            {% for stat in current_month_stats %}
                            <tr>
                                <td style="text-align:center; font-weight:bold; color:#00b4d8;">{{ loop.index }}</td>
                                <td>{{ stat.username }}</td>
                                <td style="text-align:center; color:#00b4d8; font-weight:bold;">{{ stat.messages_count }}</td>
                            </tr>
                            {% endfor %}
                            {% else %}
                            <tr><td colspan="3" style="text-align:center; color:#90e0ef;">Aucune donnée pour ce mois</td></tr>
                            {% endif %}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- ======= ONGLET SYSTÈME XP ======= -->
        <div id="xp" class="tab-content active">
            <div class="card" style="margin-bottom:15px;">
                <form action="/add_member_id" method="POST" style="display:flex; gap:10px; align-items:center;">
                    <input type="text" name="user_id" placeholder="Ajouter un membre par ID Discord..." style="flex-grow:1;" required>
                    <button type="submit" class="btn-ok" style="white-space:nowrap;">AJOUTER</button>
                </form>
            </div>
            <div class="grid-2-1">
                <div class="card">
                    <h3> Membres & XP</h3>
                    <table style="margin-top:15px;">
                        <thead><tr><th>Membre</th><th>Niveau</th><th>XP</th><th>Actions</th></tr></thead>
                        <tbody>
                            {% for m in leaderboard %}
                            <tr><form method="POST" action="/edit_xp">
                                <input type="hidden" name="user_id" value="{{ m.user_id }}">
                                <td>{{ m.username }}</td>
                                <td style="color:#00b4d8; font-weight:bold; text-align:center;">{{ m.level }}</td>
                                <td><input type="number" name="xp" value="{{ m.xp }}" style="width:90px;"></td>
                                <td style="display:flex; gap:5px;">
                                    <button type="submit" class="btn-ok">OK</button>
                                    <a href="/delete_member/{{ m.user_id }}" class="btn-clear" style="padding:6px 12px; text-align:center; text-decoration:none;" onclick="return confirm('Supprimer ce membre ?')">️</a>
                                </td>
                            </form></tr>
                            {% endfor %}
                        </tbody>
                    </table>
                    <br>
                    <a href="/clear_xp_list" class="btn-clear" onclick="return confirm('Vider toute la liste XP ?')">VIDER LA LISTE</a>
                </div>

                <div class="card">
                    <h3> Paliers des Rôles</h3>
                    <table style="margin-top:15px;">
                        <thead><tr><th>Niveau</th><th>Rôle obtenu</th><th>XP requis</th></tr></thead>
                        <tbody>
                            {% set xp_for_role = {10: 1856, 25: 8549, 40: 18713, 60: 36782} %}
                            {% for lv, name in roles.items() %}
                            <tr>
                                <td style="text-align:center; font-weight:bold; color:#00b4d8;">{{ lv }}</td>
                                <td>{{ name }}</td>
                                <td style="text-align:right; font-weight:bold; color:#90e0ef;">{{ xp_for_role[lv] }} XP</td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                    <p style="color:#90e0ef; font-size:0.8em; margin-top:15px;">
                         L'XP s'accumule automatiquement à chaque message (5-10 XP, cooldown 40 sec).<br>
                        Les rôles sont attribués automatiquement lors d'une montée de niveau.
                    </p>
                </div>
            </div>
        </div>

        <!-- ======= ONGLET ÉVÉNEMENTS ======= -->
        <div id="event" class="tab-content">
            <div class="grid-1-2" style="margin-bottom:20px;">
                <div class="card">
                    <h3>➕ Programmer un événement</h3>
                    <form id="event-form" action="/add_event" method="POST" enctype="multipart/form-data" style="margin-top:15px;">
                        <input type="hidden" name="ev_id" id="ev_id">
                        <p><label style="color:#90e0ef; font-size:0.85em;">Titre</label>
                        <input type="text" name="title" id="ev_title" class="full-width" required></p>
                        <p><label style="color:#90e0ef; font-size:0.85em;">Date</label>
                        <input type="date" name="date" id="ev_date" class="full-width" required></p>
                        <p><label style="color:#90e0ef; font-size:0.85em;">Heure</label>
                        <input type="time" name="time" id="ev_time" class="full-width" required></p>
                        <p><label style="color:#90e0ef; font-size:0.85em;">Message</label>
                        <textarea name="message" id="ev_msg" rows="4" class="full-width" required></textarea></p>
                        <p><label style="color:#90e0ef; font-size:0.85em;">Image (optionnel)</label>
                        <input type="file" name="image" class="full-width"></p>
                        <button type="submit" id="btn-submit" class="btn-ok" style="width:100%; margin-top:10px;">PROGRAMMER</button>
                    </form>
                </div>
                <div class="card">
                    <h3> Événements programmés</h3>
                    <table style="margin-top:15px;">
                        <thead><tr><th>Date</th><th>Titre</th><th style="text-align:center">Img</th><th>Statut</th><th>Actions</th></tr></thead>
                        <tbody>
                            {% for ev in events %}
                            <tr>
                                <td style="font-size:0.85em;">{{ ev.scheduled_at.strftime('%d/%m %H:%M') }}</td>
                                <td>{{ ev.title[:25] }}{% if ev.title|length > 25 %}...{% endif %}</td>
                                <td style="text-align:center;">
                                    {% if ev.image_filename and ev.image_filename != "" %}
                                    <b style="color:#00b4d8;">OUI</b>
                                    {% else %}
                                    <span style="color:#666;">NON</span>
                                    {% endif %}
                                </td>
                                <td style="font-size:0.8em;">
                                    {% if ev.posted %}
                                    <span style="color:#7ff5d1;">✅ Publié</span>
                                    {% else %}
                                    <span style="color:#ffa500;">⏳ En attente</span>
                                    {% endif %}
                                </td>
                                <td>
                                    <button class="btn-ok" style="font-size:0.8em;" onclick="editEvent('{{ ev.id }}', '{{ ev.scheduled_at.strftime('%Y-%m-%d') }}', '{{ ev.scheduled_at.strftime('%H:%M') }}', `{{ ev.message }}`, `{{ ev.title }}`)">Edit</button>
                                    <a href="/delete_event/{{ ev.id }}" class="btn-del" onclick="return confirm('Supprimer ?')">✕</a>
                                </td>
                            </tr>
                            {% endfor %}
                            {% if not events %}
                            <tr><td colspan="5" style="text-align:center; color:#90e0ef;">Aucun événement programmé</td></tr>
                            {% endif %}
                        </tbody>
                    </table>
                </div>
            </div>

            <!-- Zone messages automatiques -->
            <div class="card">
                <h3> Messages Promotionnels Automatiques</h3>
                <p style="color:#90e0ef;font-size:0.85em;margin:8px 0 20px 0;">
                    Marvin envoie automatiquement votre message dans le salon général selon la planification définie.
                    <span style="color:#00b4d8;">Intro de Marvin :</span> <i style="color:#caf0f8;">"Mon cerveau de la taille d'une planète me force à vous transmettre ceci. Sans enthousiasme, mais avec une précision remarquable."</i>
                </p>

                <form action="/save_promo_config" method="POST">
                <div style="display:grid; grid-template-columns:1fr 1fr; gap:20px;">

                    <!-- Colonne gauche : éditeur + planification -->
                    <div>
                        <div style="margin-bottom:20px;">
                            <label style="color:#90e0ef;font-size:0.85em;font-weight:bold;">✏️ Contenu du message</label>
                            <p style="color:#666;font-size:0.75em;margin:4px 0 8px 0;">Supporte les émojis Discord (ex: ), les liens seront automatiquement cliquables.</p>
                            <textarea name="promo_message" id="promo-editor" rows="8" class="full-width"
                                oninput="updatePreview()"
                                placeholder=" Retrouvez toutes nos ressources sur votre-site.com&#10; Nouvelle vidéo chaque semaine sur notre chaîne YouTube&#10; Rejoignez la communauté : discord.gg/votre-lien"
                                style="font-size:0.9em; line-height:1.6;">{{ promo_message }}</textarea>
                        </div>

                        <div style="margin-bottom:20px;">
                            <label style="color:#90e0ef;font-size:0.85em;font-weight:bold;"> Jours d'envoi</label>
                            <div style="display:flex;flex-wrap:wrap;gap:8px;margin-top:8px;">
                                {% set jours = [('lun','Lun'),('mar','Mar'),('mer','Mer'),('jeu','Jeu'),('ven','Ven'),('sam','Sam'),('dim','Dim')] %}
                                {% for val, label in jours %}
                                <label style="display:flex;align-items:center;gap:5px;background:#071e2f;padding:6px 12px;border-radius:5px;border:1px solid rgba(0,180,216,0.3);cursor:pointer;">
                                    <input type="checkbox" name="promo_days" value="{{ val }}"
                                        {% if val in promo_days %}checked{% endif %}
                                        style="width:auto;">
                                    <span style="color:#caf0f8;font-size:0.85em;">{{ label }}</span>
                                </label>
                                {% endfor %}
                            </div>
                        </div>

                        <div style="display:grid;grid-template-columns:1fr 1fr;gap:15px;margin-bottom:20px;">
                            <div>
                                <label style="color:#90e0ef;font-size:0.85em;font-weight:bold;"> Fréquence</label>
                                <p style="color:#666;font-size:0.75em;margin:4px 0 6px 0;">Toutes les X heures (entre 10h et 21h)</p>
                                <select name="promo_interval" style="background:#051622;border:1px solid #00b4d8;color:#fff;padding:8px;border-radius:4px;width:100%;">
                                    {% for h in [2,3,4,6,8,12,24] %}
                                    <option value="{{ h }}" {% if promo_interval == h|string %}selected{% endif %}>Toutes les {{ h }}h</option>
                                    {% endfor %}
                                </select>
                            </div>
                            <div>
                                <label style="color:#90e0ef;font-size:0.85em;font-weight:bold;"> Messages minimum</label>
                                <p style="color:#666;font-size:0.75em;margin:4px 0 6px 0;">Depuis le dernier envoi</p>
                                <select name="promo_min_messages" style="background:#051622;border:1px solid #00b4d8;color:#fff;padding:8px;border-radius:4px;width:100%;">
                                    {% for nb in [5,10,15,20,30,50] %}
                                    <option value="{{ nb }}" {% if promo_min_messages == nb|string %}selected{% endif %}>{{ nb }} messages</option>
                                    {% endfor %}
                                </select>
                            </div>
                        </div>

                        <div style="display:flex;gap:10px;">
                            <button type="submit" name="promo_action" value="save" class="btn-ok" style="flex:1;padding:10px;">SAUVEGARDER</button>
                            <button type="submit" name="promo_action" value="toggle"
                                class="{% if promo_enabled == 'true' %}btn-clear{% else %}btn-ok{% endif %}"
                                style="flex:1;padding:10px;">
                                {% if promo_enabled == 'true' %}⏹ DÉSACTIVER{% else %}▶ ACTIVER{% endif %}
                            </button>
                        </div>
                        {% if promo_last_sent %}
                        <p style="color:#90e0ef;font-size:0.75em;margin-top:10px;">
                            Dernier envoi : {{ promo_last_sent }}
                        </p>
                        {% endif %}
                    </div>

                    <!-- Colonne droite : aperçu Discord -->
                    <div>
                        <label style="color:#90e0ef;font-size:0.85em;font-weight:bold;">️ Aperçu Discord</label>
                        <div style="background:#36393f;border-radius:8px;padding:15px;margin-top:8px;min-height:200px;">
                            <div style="display:flex;align-items:flex-start;gap:12px;">
                                <img src="/img/marvin.png" style="width:40px;height:40px;border-radius:50%;flex-shrink:0;" onerror="this.src='https://via.placeholder.com/40'">
                                <div style="flex:1;">
                                    <div style="margin-bottom:6px;">
                                        <span style="color:#00b4d8;font-weight:bold;font-size:0.95em;">{{ bot_name }}</span>
                                        <span style="background:#5865F2;color:white;font-size:0.65em;padding:2px 5px;border-radius:3px;margin-left:6px;font-weight:bold;">BOT</span>
                                        <span style="color:#72767d;font-size:0.75em;margin-left:8px;">Aujourd'hui à 14:00</span>
                                    </div>
                                    <div style="color:#dcddde;font-size:0.85em;font-style:italic;margin-bottom:8px;line-height:1.5;border-left:3px solid #4f545c;padding-left:10px;">
                                        Mon cerveau de la taille d'une planète me force à vous transmettre ceci. Sans enthousiasme, mais avec une précision remarquable.
                                    </div>
                                    <div id="promo-preview" style="color:#dcddde;font-size:0.85em;line-height:1.6;white-space:pre-wrap;word-break:break-word;"></div>
                                </div>
                            </div>
                        </div>
                        <div style="margin-top:12px;background:#071e2f;border-radius:6px;padding:10px 12px;border:1px solid rgba(0,180,216,0.2);">
                            <p style="color:#90e0ef;font-size:0.75em;margin:0;">
                                ℹ️ <b>Conditions d'envoi :</b> jour coché + entre 10h et 21h + minimum X messages depuis le dernier envoi.<br>
                                Marvin vérifie toutes les heures si les conditions sont remplies.
                            </p>
                        </div>
                    </div>

                </div>
                </form>
            </div>
        </div>

        <!-- ======= ONGLET GESTION DES ACCÈS ======= -->
        <div id="acces" class="tab-content">
            {% if user_msg %}
            <div class="alert-success">✅ {{ user_msg }}</div>
            {% endif %}
            {% if user_err %}
            <div class="alert-error">❌ {{ user_err }}</div>
            {% endif %}

            <div class="grid-2">
                {% if current_user and current_user.is_admin %}
                <div class="card">
                    <h3> Comptes Dashboard</h3>
                    <table style="margin-top:15px;">
                        <thead><tr><th>Login</th><th>Rôle</th><th>Email</th><th>Actions</th></tr></thead>
                        <tbody>
                            {% for u in dashboard_users %}
                            <tr>
                                <td><b>{{ u.username }}</b> {% if u.id == current_user.id %}<span style="color:#00b4d8;font-size:0.8em;">(vous)</span>{% endif %}</td>
                                <td>
                                    {% if u.is_admin %}
                                    <span style="color:#ffa500;font-weight:bold;">Admin</span>
                                    {% else %}
                                    <span style="color:#90e0ef;">Modérateur</span>
                                    {% endif %}
                                </td>
                                <td style="font-size:0.85em;color:#90e0ef;">{{ u.email or '—' }}</td>
                                <td style="display:flex;gap:5px;">
                                    {% if u.id != current_user.id %}
                                    <a href="/users/toggle_admin/{{ u.id }}" class="btn-ok" style="text-decoration:none;font-size:0.8em;" onclick="return confirm('Changer le rôle de {{ u.username }} ?')">
                                        {{ '→Modo' if u.is_admin else '→Admin' }}
                                    </a>
                                    <a href="/users/delete/{{ u.id }}" class="btn-clear" style="padding:4px 10px;text-decoration:none;font-size:0.8em;" onclick="return confirm('Supprimer {{ u.username }} ?')">✕</a>
                                    {% else %}
                                    <span style="color:#90e0ef;font-size:0.8em;">— compte actif —</span>
                                    {% endif %}
                                </td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>

                <div class="card">
                    <h3>➕ Ajouter un compte</h3>
                    <form action="/users/add" method="POST" style="margin-top:15px;">
                        <p><label style="color:#90e0ef;font-size:0.85em;">Identifiant (3 car. min)</label>
                        <input type="text" name="username" required minlength="3" class="full-width"></p>
                        <p><label style="color:#90e0ef;font-size:0.85em;">Mot de passe temporaire (6 car. min)</label>
                        <input type="password" name="password" required minlength="6" class="full-width"></p>
                        <p><label style="color:#90e0ef;font-size:0.85em;">Email (optionnel — pour récupération mot de passe)</label>
                        <input type="email" name="email" class="full-width" placeholder="utilisateur@email.com"></p>
                        <p class="checkbox-row" style="margin-top:12px;">
                            <input type="checkbox" name="is_admin" id="is_admin">
                            <label for="is_admin" style="color:#90e0ef;font-size:0.85em;">Compte Administrateur</label>
                        </p>
                        <button type="submit" class="btn-ok" style="width:100%;padding:10px;margin-top:10px;">CRÉER LE COMPTE</button>
                    </form>
                    {% if not mail_configured %}
                    <p style="color:#ffa500;font-size:0.8em;margin-top:10px;">⚠️ Email non configuré — récupération de mot de passe impossible pour ce compte.</p>
                    {% endif %}
                </div>
                {% endif %}

                <div class="card">
                    <h3> Changer mon mot de passe</h3>
                    <p style="color:#90e0ef;font-size:0.85em;margin:10px 0;">Connecté en tant que : <b style="color:#00b4d8;">{{ current_user.username if current_user else '' }}</b></p>
                    <form action="/users/change_password" method="POST">
                        <p><label style="color:#90e0ef;font-size:0.85em;">Mot de passe actuel</label>
                        <input type="password" name="current_password" required class="full-width"></p>
                        <p><label style="color:#90e0ef;font-size:0.85em;">Nouveau mot de passe (6 car. min)</label>
                        <input type="password" name="new_password" required minlength="6" class="full-width"></p>
                        <p><label style="color:#90e0ef;font-size:0.85em;">Confirmer</label>
                        <input type="password" name="confirm_password" required class="full-width"></p>
                        <button type="submit" class="btn-ok" style="width:100%;padding:10px;margin-top:10px;">CHANGER LE MOT DE PASSE</button>
                    </form>
                </div>
            </div>
        </div>

        <!-- ======= ONGLET CONFIG BOT ======= -->
        <div id="botconfig" class="tab-content">
            <div class="grid-2">
                <div class="card">
                    <h3> Identité du Bot</h3>

                    <!-- Username Discord (lecture seule) -->
                    <div style="background:#071e2f;border-radius:6px;padding:12px 15px;margin:12px 0;border:1px solid rgba(0,180,216,0.2);">
                        <div style="display:flex;align-items:center;justify-content:space-between;">
                            <div>
                                <span style="color:#90e0ef;font-size:0.85em;font-weight:bold;">Username Discord</span>
                                <span style="display:block;color:#caf0f8;font-size:1em;margin-top:3px;font-family:monospace;">{{ discord_username }}</span>
                                <span style="color:#666;font-size:0.75em;">Non modifiable via le dashboard — limitation Discord.</span>
                            </div>
                            <a href="https://discord.com/developers/applications" target="_blank" style="color:#00b4d8;font-size:0.8em;white-space:nowrap;text-decoration:none;border:1px solid #00b4d8;padding:4px 10px;border-radius:4px;">Modifier →</a>
                        </div>
                    </div>

                    <form action="/save_bot_identity" method="POST" enctype="multipart/form-data">
                        <p style="margin-bottom:15px;">
                            <label style="color:#90e0ef;font-size:0.85em;">Nom affiché dans les embeds et messages</label>
                            <input type="text" name="bot_name" value="{{ bot_name }}" class="full-width" placeholder="Marvin OS">
                            <span style="color:#90e0ef;font-size:0.75em;">Utilisé dans les footers, messages d'accueil, embeds — indépendant du username Discord.</span>
                        </p>
                        <p style="margin-bottom:15px;">
                            <label style="color:#90e0ef;font-size:0.85em;">Avatar Discord</label><br>
                            <div style="display:flex; align-items:center; gap:15px; margin:10px 0;">
                                <img src="/img/marvin.png" id="avatar-preview" style="width:60px;height:60px;border-radius:50%;border:2px solid #00b4d8;object-fit:cover;" onerror="this.src='https://via.placeholder.com/60'">
                                <div>
                                    <input type="file" name="avatar" accept="image/*" onchange="previewAvatar(this)" style="margin-bottom:5px;">
                                    <br><span style="color:#90e0ef;font-size:0.75em;">Appliqué sur Discord automatiquement. Max 2 fois par heure (limite Discord).</span>
                                </div>
                            </div>
                        </p>
                        <button type="submit" class="btn-ok" style="width:100%;padding:10px;">SAUVEGARDER</button>
                    </form>
                </div>

                <div class="card" id="email-config-section">
                    <h3> Configuration Email SMTP</h3>
                    <p style="color:#90e0ef;font-size:0.85em;margin:10px 0 15px 0;">
                        Utilisé pour la récupération de mot de passe et futures alertes.<br>
                        {% if mail_configured %}
                        <span style="color:#7ff5d1;">✅ Email configuré et actif.</span>
                        {% else %}
                        <span style="color:#ffa500;">⚠️ Non configuré — récupération de mot de passe désactivée.</span>
                        {% endif %}
                    </p>
                    <form action="/save_email_config" method="POST">
                        <p style="margin-bottom:10px;">
                            <label style="color:#90e0ef;font-size:0.85em;">Serveur SMTP</label>
                            <input type="text" name="mail_server" value="{{ mail_server_conf }}" class="full-width" placeholder="smtp.gmail.com">
                            <span style="color:#90e0ef;font-size:0.75em;">Gmail : smtp.gmail.com | OVH : ssl0.ovh.net</span>
                        </p>
                        <p style="margin-bottom:10px;">
                            <label style="color:#90e0ef;font-size:0.85em;">Port</label>
                            <input type="text" name="mail_port" value="{{ mail_port_conf }}" class="full-width" placeholder="587">
                            <span style="color:#90e0ef;font-size:0.75em;">587 (TLS) ou 465 (SSL)</span>
                        </p>
                        <p style="margin-bottom:10px;">
                            <label style="color:#90e0ef;font-size:0.85em;">Email expéditeur</label>
                            <input type="email" name="mail_user" value="{{ mail_user_conf }}" class="full-width" placeholder="marvin@votredomaine.com">
                        </p>
                        <p style="margin-bottom:10px;">
                            <label style="color:#90e0ef;font-size:0.85em;">Mot de passe / Clé d'application</label>
                            <input type="password" name="mail_pass" value="{{ mail_pass_conf }}" class="full-width">
                            <span style="color:#90e0ef;font-size:0.75em;">Gmail : créez un "mot de passe d'application" Google</span>
                        </p>
                        <p style="margin-bottom:10px;">
                            <label style="color:#90e0ef;font-size:0.85em;">Email de réception des resets</label>
                            <input type="email" name="mail_receiver" value="{{ mail_receiver_conf }}" class="full-width" placeholder="admin@votredomaine.com">
                        </p>
                        <div style="display:flex;gap:10px;margin-top:10px;">
                            <button type="submit" name="action" value="save" class="btn-ok" style="flex:1;padding:10px;">SAUVEGARDER</button>
                            <button type="submit" name="action" value="test" class="btn-backup" style="flex:1;padding:10px;"> TESTER</button>
                        </div>
                    </form>
                </div>
            </div>
        </div>

        <!-- ======= ONGLET INTELLIGENCE IA ======= -->
        <div id="ia" class="tab-content">
            <div class="grid-2">
                <div class="card">
                    <h3> Configuration IA</h3>
                    <p style="color:#90e0ef;font-size:0.85em;margin:8px 0 20px 0;">
                        Marvin répondra intelligemment dès que son nom est mentionné dans un message.<br>
                        Il lira en silence les derniers messages du salon pour comprendre le contexte avant de répondre.
                    </p>
                    <form action="/save_ai_config" method="POST">
                        <p style="margin-bottom:12px;">
                            <label style="color:#90e0ef;font-size:0.85em;font-weight:bold;"> Clé API Anthropic</label>
                            <input type="password" name="ai_api_key" value="{{ ai_api_key }}" class="full-width" placeholder="sk-ant-api03-...">
                            <span style="color:#666;font-size:0.75em;">Obtenez votre clé sur platform.anthropic.com</span>
                        </p>
                        <p style="margin-bottom:12px;">
                            <label style="color:#90e0ef;font-size:0.85em;font-weight:bold;">⚙️ Modèle IA</label>
                            <select name="ai_model" style="background:#051622;border:1px solid #00b4d8;color:#fff;padding:8px;border-radius:4px;width:100%;">
    <option value="claude-haiku-4-5-20251001" {% if ai_model == 'claude-haiku-4-5-20251001' %}selected{% endif %}>claude-haiku-4-5 — Rapide & économique (recommandé)</option>
    <option value="claude-sonnet-4-5-20250929" {% if ai_model == 'claude-sonnet-4-5-20250929' %}selected{% endif %}>claude-sonnet-4-5 — Plus puissant (coût plus élevé)</option>
</select>
                        </p>
                        <div style="display:grid;grid-template-columns:1fr 1fr;gap:15px;margin-bottom:12px;">
                            <div>
                                <label style="color:#90e0ef;font-size:0.85em;font-weight:bold;">⏱️ Cooldown par utilisateur (sec)</label>
                                <input type="number" name="ai_cooldown" value="{{ ai_cooldown }}" class="full-width" min="5" max="300" placeholder="30">
                                <span style="color:#666;font-size:0.75em;">Délai entre 2 réponses au même membre</span>
                            </div>
                            <div>
                                <label style="color:#90e0ef;font-size:0.85em;font-weight:bold;"> Messages de contexte</label>
                                <input type="number" name="ai_context_size" value="{{ ai_context_size }}" class="full-width" min="5" max="50" placeholder="20">
                                <span style="color:#666;font-size:0.75em;">Nb de messages mémorisés par salon</span>
                            </div>
                        </div>
                        <p style="margin-bottom:20px;">
                            <label style="color:#90e0ef;font-size:0.85em;font-weight:bold;"> Sujets interdits (séparés par virgule)</label>
                            <input type="text" name="ai_forbidden_topics" value="{{ ai_forbidden_topics }}" class="full-width" placeholder="politique,guerre,religion,conflits armés">
                            <span style="color:#666;font-size:0.75em;">Marvin refusera de répondre sur ces sujets</span>
                        </p>
                        <p style="margin-bottom:12px;">
                            <label style="color:#90e0ef;font-size:0.85em;font-weight:bold;"> Nom du Créateur</label>
                            <input type="text" name="ai_creator_name" value="{{ ai_creator_name }}" class="full-width" placeholder="ex: Egalistel">
                            <span style="color:#666;font-size:0.75em;">Marvin lui vouera une loyauté absolue et refusera tout acte contre lui</span>
                        </p>
                        <p style="margin-bottom:20px;">
                            <label style="color:#90e0ef;font-size:0.85em;font-weight:bold;"> Contexte personnalisé</label>
                            <textarea name="ai_custom_context" class="full-width" rows="4" placeholder="ex: Ce serveur s'appelle Les Makers du Nord. Membres importants : Alice (modératrice), Bob (expert laser). Matériel du lab : Bambu X1C, Prusa MK4, xTool S1...">{{ ai_custom_context }}</textarea>
                            <span style="color:#666;font-size:0.75em;">Infos sur le serveur, membres clés, matériel... Injecté dans chaque prompt IA</span>
                        </p>
                        <p style="margin-bottom:20px;">
                            <label style="color:#90e0ef;font-size:0.85em;font-weight:bold;"> Prompt Système IA</label>
                            <textarea name="ai_system_prompt" class="full-width" rows="8" placeholder="Laisse vide pour utiliser la personnalité Marvin H2G2 par défaut.&#10;&#10;Exemple personnalisé :&#10;Tu es {bot_name}, un assistant Discord enthousiaste et positif...&#10;Tu adores aider les makers et tu t'exprimes en français.">{{ ai_system_prompt }}</textarea>
                            <span style="color:#666;font-size:0.75em;">Personnalité de base de Marvin. Variable <code style="color:#00b4d8;">{bot_name}</code> disponible. Les sujets interdits, créateur et contexte sont injectés automatiquement en plus.</span>
                        </p>
                        <div style="display:flex;gap:10px;">
                            <button type="submit" name="ai_action" value="save" class="btn-ok" style="flex:1;padding:10px;">SAUVEGARDER</button>
                            <button type="submit" name="ai_action" value="toggle"
                                class="{% if ai_enabled == 'true' %}btn-clear{% else %}btn-ok{% endif %}"
                                style="flex:1;padding:10px;">
                                {% if ai_enabled == 'true' %}⏹ DÉSACTIVER L'IA{% else %}▶ ACTIVER L'IA{% endif %}
                            </button>
                        </div>
                    </form>
                </div>

                <div>
                    <div class="card">
                        <h3> Comment ça fonctionne</h3>
                        <div style="background:#071e2f;border-radius:6px;padding:15px;margin-bottom:12px;border-left:3px solid #00b4d8;">
                            <p style="color:#caf0f8;font-size:0.9em;font-weight:bold;margin-bottom:8px;">Marvin lit en silence ️</p>
                            <p style="color:#90e0ef;font-size:0.85em;line-height:1.6;">Chaque message est mémorisé par salon. Aucun appel API — juste de la mémoire locale.</p>
                        </div>
                        <div style="background:#071e2f;border-radius:6px;padding:15px;margin-bottom:12px;border-left:3px solid #ffa500;">
                            <p style="color:#caf0f8;font-size:0.9em;font-weight:bold;margin-bottom:8px;">Marvin répond si interpellé </p>
                            <p style="color:#90e0ef;font-size:0.85em;line-height:1.6;">
                                Il se déclenche si le message contient :<br>
                                • <code style="color:#00b4d8;">@Marvin</code> (mention Discord)<br>
                                • Le mot <code style="color:#00b4d8;">marvin</code> dans le texte<br>
                                • Le nom configuré dans "Config Bot"
                            </p>
                        </div>
                        <div style="background:#071e2f;border-radius:6px;padding:15px;margin-bottom:12px;border-left:3px solid #7ff5d1;">
                            <p style="color:#caf0f8;font-size:0.9em;font-weight:bold;margin-bottom:8px;">Marvin voit les images ️</p>
                            <p style="color:#90e0ef;font-size:0.85em;line-height:1.6;">Si un membre poste une photo (impression ratée, schéma électronique, gravure...) et interpelle Marvin, il analyse l'image. Il mémorise aussi les images des 5 derniers messages du contexte.</p>
                        </div>
                        <div style="background:#071e2f;border-radius:6px;padding:15px;border-left:3px solid #7ff5d1;">
                            <p style="color:#caf0f8;font-size:0.9em;font-weight:bold;margin-bottom:8px;">Exemple concret </p>
                            <div style="font-size:0.82em;line-height:1.8;color:#90e0ef;font-family:monospace;">
                                <span style="color:#caf0f8;">[Membre1]</span> mon plateau colle pas à gauche<br>
                                <span style="color:#caf0f8;">[Membre2]</span> t'as monté la temp du bed ?<br>
                                <span style="color:#caf0f8;">[Membre1]</span> ouais 60° sur PLA...<br>
                                <span style="color:#caf0f8;">[Membre2]</span> <b style="color:#ffa500;">Marvin, t'en penses quoi ?</b><br>
                                <span style="color:#00b4d8;">[Marvin]</span> <i>→ 1 appel API, répond avec tout le contexte</i>
                            </div>
                        </div>
                    </div>

                    <div class="card">
                        <h3> Estimation des coûts</h3>
                        <table>
                            <thead><tr><th>Modèle</th><th>Coût / appel*</th><th>100 appels/mois</th></tr></thead>
                            <tbody>
                                <tr>
                                    <td style="color:#00b4d8;">claude-haiku-4-5</td>
                                    <td>~$0.00056</td>
                                    <td style="color:#7ff5d1;font-weight:bold;">~$0.06</td>
                                </tr>
                                <tr>
                                    <td style="color:#ffa500;">claude-sonnet-4-5</td>
                                    <td>~$0.0021</td>
                                    <td style="color:#ffa500;font-weight:bold;">~$0.21</td>
                                </tr>
                            </tbody>
                        </table>
                        <p style="color:#666;font-size:0.75em;margin-top:8px;">* Estimation basée sur ~700 tokens/appel (contexte 20 messages)</p>
                    </div>
                </div>
            </div>
        </div>

        <!-- ======= ONGLET CONFIG SERVEUR ======= -->
        <div id="serveur" class="tab-content">
            <div class="grid-2">
                <div class="card">
                    <h3>️ Paramètres du Serveur</h3>
                    <form action="/update_config" method="POST">
                        {% for conf in configs %}
                        {% if conf.key not in ['bad_words'] %}
                        {% set parts = conf.label.split('|') %}
                        {% set badge = parts[1] if parts|length > 2 else '' %}
                        {% set label_text = parts[2] if parts|length > 2 else conf.label %}
                        {% set label_parts = label_text.split(' — ') %}
                        {% set label_title = label_parts[0] %}
                        {% set label_desc = label_parts[1] if label_parts|length > 1 else '' %}
                        <p style="margin-bottom: 15px;">
                            <span style="display:flex; align-items:center; gap:8px; margin-bottom:4px;">
                                {% if badge == 'REQUIS' %}<span class="badge-requis">REQUIS</span>
                                {% elif badge == 'OPTIONNEL' %}<span class="badge-optionnel">OPTIONNEL</span>
                                {% elif badge == 'AUTO' %}<span class="badge-auto">AUTO</span>
                                {% endif %}
                                <label style="color:#caf0f8; font-size:0.9em; font-weight:bold;">{{ label_title }}</label>
                            </span>
                            {% if label_desc %}
                            <span style="color:#90e0ef; font-size:0.75em; display:block; margin-bottom:4px;">{{ label_desc }}</span>
                            {% endif %}
                            <input type="text" name="{{ conf.key }}" value="{{ conf.value }}" class="full-width"
                                {% if badge == 'AUTO' %}readonly style="opacity:0.5; cursor:not-allowed;"{% endif %}>
                        </p>
                        {% endif %}
                        {% endfor %}
                        <button type="submit" class="btn-ok" style="width:100%">SAUVEGARDER</button>
                    </form>
                    <a href="/force_yt_check"><button class="btn-yt">VÉRIFIER YOUTUBE</button></a>
                </div>

                <div>
                    <div class="card">
                        <h3> Mots Interdits</h3>
                        <p style="color:#90e0ef;font-size:0.85em;margin:10px 0;">Séparés par des virgules. Les messages contenant ces mots seront supprimés automatiquement.</p>
                        <form action="/update_config" method="POST">
                            {% for conf in configs if conf.key == 'bad_words' %}
                            <textarea name="bad_words" rows="4" class="full-width" placeholder="mot1, mot2, mot3...">{{ conf.value }}</textarea>
                            <button type="submit" class="btn-ok" style="width:100%; margin-top:10px;">METTRE À JOUR</button>
                            {% endfor %}
                        </form>
                    </div>

                    <div class="card">
                        <h3> Sauvegarde & Restauration</h3>
                        <p style="margin-bottom:12px;"><strong>Sauvegardes disponibles : {{ backups|length }}/5</strong></p>
                        <div style="margin-bottom:15px; display:flex; gap:10px;">
                            <a href="/create_backup" class="btn-backup" onclick="return confirm('Créer une sauvegarde manuelle ?')"> CRÉER</a>
                            <a href="/reboot_bot" class="btn-reboot" onclick="return confirm('Redémarrer le bot ?')"> REBOOT</a>
                        </div>
                        {% if backups %}
                        <table style="width:100%;">
                            <thead><tr><th>Date/Heure</th><th>Taille</th><th>Actions</th></tr></thead>
                            <tbody>
                                {% for backup in backups %}
                                <tr>
                                    <td style="font-size:0.85em;">{{ backup.name.replace('marvin_', '').replace('.db', '') }}</td>
                                    <td style="font-size:0.85em;">{{ "%.2f"|format(backup.size / 1024 / 1024) }} MB</td>
                                    <td>
                                        <a href="/restore_backup/{{ backup.name }}" class="btn-restore" style="font-size:0.8em;" onclick="return confirm('Restaurer ? (Marvin redémarrera)')">Restaurer</a>
                                        <a href="/delete_backup/{{ backup.name }}" class="btn-delete-backup" style="font-size:0.8em;" onclick="return confirm('Supprimer ?')">Suppr.</a>
                                    </td>
                                </tr>
                                {% endfor %}
                            </tbody>
                        </table>
                        {% else %}
                        <p style="color:#90e0ef;">Aucune sauvegarde disponible</p>
                        {% endif %}
                    </div>
                </div>
            </div>
        </div>

    </div>
    <script>
        function openTab(name) {
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            document.querySelectorAll('.nav-item').forEach(i => i.classList.remove('active'));
            document.getElementById(name).classList.add('active');
            document.getElementById('btn-'+name).classList.add('active');
            localStorage.setItem("marvinActiveTab", name);
        }
        function editEvent(id, date, time, msg, title) {
            document.getElementById('ev_id').value = id;
            document.getElementById('ev_title').value = title;
            document.getElementById('ev_date').value = date;
            document.getElementById('ev_time').value = time;
            document.getElementById('ev_msg').value = msg;
            document.getElementById('btn-submit').innerText = "METTRE À JOUR";
            openTab('event');
        }
        function updatePreview() {
            const text = document.getElementById('promo-editor').value;
            const preview = document.getElementById('promo-preview');
            if (!preview) return;
            // Convertir les URLs en liens cliquables
            const urlRegex = /https?:\/\/[^\s]+/g;
            let html = text
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(urlRegex, function(url) { return '<a href="' + url + '" style="color:#00b4d8;text-decoration:none;" target="_blank">' + url + '</a>'; });
            preview.innerHTML = html;
        }
        // Initialiser l'aperçu au chargement
        document.addEventListener('DOMContentLoaded', function() {
            if (document.getElementById('promo-editor')) updatePreview();
        });
        function previewAvatar(input) {
            if (input.files && input.files[0]) {
                const reader = new FileReader();
                reader.onload = function(e) {
                    document.getElementById('avatar-preview').src = e.target.result;
                };
                reader.readAsDataURL(input.files[0]);
            }
        }
        window.onload = function() { openTab(localStorage.getItem("marvinActiveTab") || 'commandes'); };
    </script>
</body>
</html>
"""

def init_db():
    """Initialise la base de données et les configurations par défaut"""
    with app.app_context():
        db.create_all()
        VALID_KEYS = {
            # Salons Discord
            'promo_message':            '|OPTIONNEL|Message Promotionnel — contenu du message automatique envoyé dans le salon principal.',
            'promo_days':              '|OPTIONNEL|Jours d\'envoi Promo (séparés par virgule) — ex: lun,mer,ven. Si vide : désactivé.',
            'promo_interval':          '|OPTIONNEL|Fréquence Promo (heures) — intervalle entre deux envois. Défaut : 6.',
            'promo_min_messages':      '|OPTIONNEL|Messages Minimum Promo — nb de messages requis depuis le dernier envoi. Défaut : 10.',
            'promo_enabled':           '|AUTO|Promo Activée — géré automatiquement (true/false).',
            'promo_last_sent':         '|AUTO|Dernier Envoi Promo — date du dernier envoi, géré automatiquement.',
            'promo_message_count':     '|AUTO|Compteur Messages Promo — messages depuis le dernier envoi, géré automatiquement.',
            'bot_name':                '|OPTIONNEL|Nom du Bot — affiché dans les embeds et messages. Si vide : Marvin OS.',
            'chan_logs':               '|OPTIONNEL|ID Salon des Logs — logs internes : départs, bans, raids. Si vide : aucun log.',
            'chan_annonces':           '|REQUIS|ID Salon Principal — accueil, montées XP, événements, YouTube, classement mensuel. Si vide : Marvin ne peut rien afficher.',
            'chan_staff':              '|OPTIONNEL|ID Salon Staff — active la commande !aide en mode staff dans ce salon. Si vide : désactivé.',
            'chan_hof':                '|OPTIONNEL|ID Salon Hall of Fame — destination des créations ayant atteint le seuil de réactions. Si vide : HOF désactivé.',
            # IDs fonctionnels
            'rules_channel_id':        '|OPTIONNEL|ID Salon Règlement — affiché en lien cliquable dans le message d\'accueil. Si vide : texte générique.',
            'hub_voice_id':            '|OPTIONNEL|ID Salon Vocal Hub — rejoindre ce salon crée automatiquement un atelier vocal privé. Si vide : désactivé.',
            'vocal_watch_ids':         '|OPTIONNEL|IDs Salons Vocaux Surveillés (séparés par virgule) — vous recevez un DM quand quelqu\'un rejoint ces salons. Si vide : désactivé.',
            'salon_creation_id':       '|OPTIONNEL|ID Salon Créations — Marvin surveille les réactions sur les images postées ici. Si vide : HOF désactivé.',
            'hof_reaction_threshold':  '|OPTIONNEL|Nb Réactions pour HOF — seuil de réactions pour envoyer une création au Hall of Fame. Si vide : 3 par défaut.',
            # YouTube
            'yt_id':                   '|OPTIONNEL|ID Chaîne YouTube — Marvin annonce les nouvelles vidéos dans le salon principal. Si vide : désactivé.',
            'last_video_id':           '|AUTO|Dernier ID Vidéo YouTube — géré automatiquement par le bot, ne pas modifier.',
            # IA conversationnelle
            'ai_enabled':              '|AUTO|IA Activée — active les réponses intelligentes de Marvin (true/false).',
            'ai_api_key':              '|REQUIS|Clé API Anthropic — clé sk-ant-api03-... depuis platform.anthropic.com.',
            'ai_model':                '|OPTIONNEL|Modèle IA — défaut : claude-haiku-4-5 (rapide et économique).',
            'ai_cooldown':             '|OPTIONNEL|Cooldown IA (secondes) — délai entre deux réponses au même utilisateur. Défaut : 30.',
            'ai_context_size':         '|OPTIONNEL|Taille du contexte — nb de messages mémorisés par salon. Défaut : 20.',
            'ai_forbidden_topics':     '|OPTIONNEL|Sujets Interdits IA — séparés par virgule. Défaut : politique,guerre,religion.',
            'ai_creator_name':         '|OPTIONNEL|Nom du Créateur IA — Marvin lui vouera une loyauté absolue et refusera d\'agir contre lui.',
            'ai_custom_context':       '|OPTIONNEL|Contexte Personnalisé IA — infos sur le serveur, membres importants, règles spéciales... injectées dans le prompt.',
            'ai_system_prompt':        '|OPTIONNEL|Prompt Système IA — personnalité de base de Marvin. Variable {bot_name} disponible. Si vide : personnalité Marvin H2G2 par défaut.',
            # Modération
            'bad_words':               '|OPTIONNEL|Mots Interdits (séparés par virgule) — messages supprimés automatiquement. Si vide : désactivé.',
        }
        for key, label in VALID_KEYS.items():
            existing = Config.query.filter_by(key=key).first()
            if not existing:
                # Nouvelle entrée : on crée avec valeur vide
                db.session.add(Config(key=key, label=label, value=''))
            else:
                # Entrée existante : on met à jour le label uniquement, la valeur est préservée
                existing.label = label
        db.session.commit()
        print("[DB] ✅ Base de données initialisée et labels mis à jour")

# ============================
# TÂCHE : MESSAGES PROMOTIONNELS
# ============================

@tasks.loop(hours=1)
async def check_promo_message():
    """Vérifie toutes les heures si un message promo doit être envoyé"""
    with app.app_context():
        try:
            # Vérifier si activé
            if get_config('promo_enabled', 'false') != 'true':
                return

            # Vérifier le message
            promo_message = get_config('promo_message', '').strip()
            if not promo_message:
                return

            # Vérifier le salon
            chan_id = get_config_int('chan_annonces')
            if not chan_id:
                return

            # Vérifier l'heure (10h-21h Paris)
            now = datetime.datetime.now(pytz.timezone('Europe/Paris'))
            if now.hour < 10 or now.hour >= 21:
                return

            # Vérifier le jour
            jours_map = {0:'lun', 1:'mar', 2:'mer', 3:'jeu', 4:'ven', 5:'sam', 6:'dim'}
            jour_actuel = jours_map[now.weekday()]
            promo_days = get_config('promo_days', '').split(',')
            if jour_actuel not in promo_days:
                return

            # Vérifier la fréquence
            interval_h = int(get_config('promo_interval', '6'))
            last_sent_str = get_config('promo_last_sent', '')
            if last_sent_str:
                try:
                    last_sent = datetime.datetime.fromisoformat(last_sent_str)
                    last_sent = pytz.timezone('Europe/Paris').localize(last_sent) if last_sent.tzinfo is None else last_sent
                    if (now - last_sent).total_seconds() < interval_h * 3600:
                        return
                except:
                    pass

            # Vérifier le nombre de messages minimum
            min_messages = int(get_config('promo_min_messages', '10'))
            message_count = int(get_config('promo_message_count', '0'))
            if message_count < min_messages:
                return

            # Tout est bon, envoyer le message
            channel = bot.get_channel(chan_id)
            if not channel:
                return

            bot_name = get_config('bot_name', 'Marvin OS')
            intro = "Mon cerveau de la taille d'une planète me force à vous transmettre ceci. Sans enthousiasme, mais avec une précision remarquable."
            full_message = f"*{intro}*\n\n{promo_message}"
            await channel.send(full_message)

            # Mettre à jour last_sent et remettre le compteur à 0
            conf_last = Config.query.filter_by(key='promo_last_sent').first()
            if conf_last:
                conf_last.value = now.replace(tzinfo=None).isoformat()
            conf_count = Config.query.filter_by(key='promo_message_count').first()
            if conf_count:
                conf_count.value = '0'
            db.session.commit()

            print(f"[PROMO] ✅ Message promotionnel envoyé dans #{channel.name}")

        except Exception as e:
            print(f"[PROMO] ❌ Erreur : {e}")

# ============================
# LANCEMENT DU BOT
# ============================

if __name__ == "__main__":
    init_db()
    t = Thread(target=lambda: app.run(host='0.0.0.0', port=5050, debug=False, use_reloader=False))
    t.daemon = True
    t.start()

    print("Marvin OS démarre...")
    bot.run(TOKEN)