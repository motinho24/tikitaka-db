"""
Bot di notifiche calciomercato via Telegram.

Cosa fa:
1. Legge una lista di feed RSS di news calcio.
2. Confronta i titoli con quelli già mandati in precedenza (salvati in un file
   "seen.json" dentro lo stesso repository, cosi' anche a distanza di giorni
   sa cosa ha gia' notificato).
3. Manda su Telegram solo le notizie NUOVE.
4. Aggiorna e salva seen.json nel repository (commit automatico).

Cosa NON fa (voluto):
- Non decide da solo se una notizia e' un trasferimento "ufficiale".
- Non scrive nulla nel database dei giocatori (tikitaka_database.json).
  Quella parte resta manuale/in chat con Claude, per evitare errori.
"""

import os
import json
import re
import sys
import feedparser
import requests

# --- Configurazione ---

RSS_FEEDS = [
    "https://www.calciomercato.com/rss",
    "https://www.footmercato.net/rss.xml",
    "https://www.skysports.com/rss/12691",  # Sky Sports football news
]

SEEN_FILE = "seen.json"
MAX_SEEN_STORED = 500  # evita che il file cresca all'infinito

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# Parole chiave specifiche di trasferimenti GIOCATORI (escluse parole generiche
# come "signs"/"agreement" che intercettano anche notizie su allenatori/staff)
TRANSFER_KEYWORDS = [
    "here we go", "done deal", "medical completed", "signs for",
    "joins", "official transfer", "ufficiale", "firma con", "trasferimento",
    "here we go confirmed",
]

# Squadre che ti interessano davvero (dalla tua lista chiusa).
# Una notizia viene mandata SOLO se nel titolo compare almeno una di queste,
# cosi' scartiamo automaticamente notizie su squadre/allenatori che non ti servono.
TEAMS_OF_INTEREST = [
    "arsenal", "manchester city", "man city", "liverpool", "chelsea",
    "manchester united", "man utd", "man united", "tottenham", "spurs",
    "monaco", "marseille", "marsiglia", "psg", "paris saint-germain",
    "real madrid", "barcelona", "barca", "atletico madrid", "atletico de madrid",
    "juventus", "juve", "inter", "milan", "ac milan", "lazio", "roma",
    "napoli", "sporting", "benfica", "porto", "galatasaray",
    "boca juniors", "river plate", "psv", "ajax",
]


def mentions_team_of_interest(title):
    t = title.lower()
    return any(team in t for team in TEAMS_OF_INTEREST)


def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_seen(seen_set):
    trimmed = list(seen_set)[-MAX_SEEN_STORED:]
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(trimmed, f, ensure_ascii=False, indent=2)


def normalize_id(entry):
    # Usa il link come identificatore univoco della notizia (piu' affidabile del titolo)
    return entry.get("link") or entry.get("id") or entry.get("title", "")


def is_probably_transfer(title):
    t = title.lower()
    return any(k in t for k in TRANSFER_KEYWORDS)


def send_telegram_message(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("ERRORE: TELEGRAM_TOKEN o TELEGRAM_CHAT_ID non impostati.")
        sys.exit(1)

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    resp = requests.post(url, data=payload, timeout=15)
    if resp.status_code != 200:
        print("Errore invio Telegram:", resp.status_code, resp.text)


def main():
    seen = load_seen()
    new_items = []

    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
        except Exception as e:
            print(f"Errore leggendo il feed {feed_url}: {e}")
            continue

        for entry in feed.entries:
            uid = normalize_id(entry)
            if not uid or uid in seen:
                continue

            title = entry.get("title", "(senza titolo)")
            link = entry.get("link", "")

            # Filtro principale: se il titolo non menziona nessuna delle tue
            # squadre di interesse, la notizia non ci serve. NON la segniamo
            # come "vista" cosi', se in futuro allarghiamo il filtro, potra'
            # comunque essere ripresa in considerazione.
            if not mentions_team_of_interest(title):
                continue

            seen.add(uid)
            new_items.append({
                "uid": uid,
                "title": title,
                "link": link,
                "source": feed.feed.get("title", feed_url),
                "is_transfer": is_probably_transfer(title),
            })

    if not new_items:
        print("Nessuna notizia nuova trovata.")
        return

    # Ordina mettendo prima le notizie che sembrano trasferimenti
    new_items.sort(key=lambda x: not x["is_transfer"])

    for item in new_items[:15]:  # limite di sicurezza per non floodare Telegram
        tag = "\u26bd TRASFERIMENTO?" if item["is_transfer"] else "\U0001F4F0 News"
        message = (
            f"{tag}\n"
            f"<b>{item['title']}</b>\n"
            f"Fonte: {item['source']}\n"
            f"{item['link']}"
        )
        send_telegram_message(message)

    save_seen(seen)
    print(f"Inviate {min(len(new_items), 15)} notifiche. Totale nuove trovate: {len(new_items)}.")


if __name__ == "__main__":
    main()
