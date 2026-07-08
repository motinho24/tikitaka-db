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
    # Italia
    "https://www.calciomercato.com/rss",
    "https://feeds.footballco.com/calcio/rss.xml",

    # Inghilterra
    "https://www.skysports.com/rss/12691",
    "https://www.skysports.com/rss/transfer-centre",
    "https://feeds.bbci.co.uk/sport/football/rss.xml",

    # Francia
    "https://www.footmercato.net/rss.xml",
    "https://dwh.lequipe.fr/api/edito/rss?path=/Football/Transferts-football/",
    "https://www.maxifoot.fr/rss-football.php",

    # Spagna
    "https://e00-marca.uecdn.es/rss/futbol/mercado-fichajes.xml",
    "https://www.mundodeportivo.com/feed/rss/es/futbol",
    "https://feeds.as.com/mrss-s/pages/as/site/as.com/section/futbol/subsection/portada",

    # Portogallo
    "https://www.record.pt/rss/rss.asp",
    "https://www.ojogo.pt/rss/Noticias.rss",

    # Turchia (feed generici del sito, contengono anche sport)
    "https://www.sabah.com.tr/rss/anasayfa.xml",

    # Argentina
    "https://www.tycsports.com/boca-juniors.html/rss.xml",
    "https://en.as.com/news/boca-juniors/rss.xml",

    # Olanda (fonte inglese specializzata sul calcio olandese)
    "https://www.dutchfootball.com/feed",
]

# NOTA IMPORTANTE:
# Non tutti questi URL sono stati verificati direttamente (alcuni siti bloccano
# il test automatico). Lo script prosegue comunque anche se un feed fallisce
# (vedi il blocco try/except in main()). Controlla i log della prima esecuzione
# su GitHub Actions per vedere quali feed funzionano davvero, e togli/sostituisci
# quelli che danno costantemente errore.

SEEN_FILE = "seen.json"
MAX_SEEN_STORED = 500  # evita che il file cresca all'infinito

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# Parole chiave che indicano un TRASFERIMENTO GIOCATORE in corso (calciomercato vero)
TRANSFER_KEYWORDS = [
    "here we go", "done deal", "medical completed", "signs for",
    "signs on", "sign on", "joins", "official transfer", "closing in on",
    "set to sign", "agrees to join", "ufficiale", "firma con", "trasferimento",
    "sign for", "loan move", "in advanced talks", "here we go confirmed",
    "completes move", "transfer news", "target", "eyeing",
    "interested in signing", "sign ", "on free", "free transfer",
    "plait a", "plaît à", "interesse", "interesado en", "ilgileniyor",
]

# Parole/frasi che escludono SEMPRE una notizia, anche se contiene una squadra
# di interesse. Coprono calcio femminile, allenatori/manager, mondiali, e
# argomenti non sportivi (politica, cultura, sicurezza).
EXCLUDE_KEYWORDS = [
    # Calcio femminile
    "women", "female", "lioness", "lionesses", "wsl", "nwsl", "femminile",
    "feminin", "femenino", "damen",
    # Allenatori / staff tecnico (non sono trasferimenti di giocatori)
    "manager", "head coach", "named coach", "new coach", "allenatore",
    "entraineur", "tecnico", "starts work", "appointed as", "boss named",
    # Mondiali/nazionali/politica/cultura (fuori scope, il progetto e' su club)
    "world cup", "mondiali", "mondial", "copa mundial", "trump", "infantino",
    "safety", "stabbing", "crush", "mural", "flags",
]

# Squadre che ti interessano davvero (dalla tua lista chiusa).
# Una notizia viene mandata SOLO se nel titolo compare almeno una di queste,
# cosi' scartiamo automaticamente notizie su squadre/allenatori che non ti servono.
TEAMS_OF_INTEREST = [
    "arsenal", "manchester city", "man city", "liverpool", "chelsea",
    "manchester united", "man utd", "man united", "tottenham", "spurs",
    "monaco", "marseille", "marsiglia", "olympique marsiglia", "psg",
    "paris saint-germain", "paris sg",
    "real madrid", "barcelona", "barca", "atletico madrid", "atletico de madrid",
    "atletico madryt", "juventus", "juve", "inter", "milan", "ac milan",
    "lazio", "roma", "as roma", "napoli",
    "sporting", "sporting lisbona", "sporting cp", "sporting lisboa",
    "benfica", "porto", "fc porto",
    "galatasaray", "gs ", "cimbom",
    "boca juniors", "boca", "river plate", "river",
    "psv", "psv eindhoven", "ajax",
]


def mentions_team_of_interest(title):
    t = title.lower()
    return any(team in t for team in TEAMS_OF_INTEREST)


def is_excluded(title):
    t = title.lower()
    return any(k in t for k in EXCLUDE_KEYWORDS)


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

    total_entries = 0
    scartate_gia_viste = 0
    scartate_no_team = 0
    scartate_excluded = 0
    scartate_no_transfer = 0

    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            print(f"Feed OK: {feed_url} -> {len(feed.entries)} elementi")
        except Exception as e:
            print(f"Errore leggendo il feed {feed_url}: {e}")
            continue

        for entry in feed.entries:
            total_entries += 1
            uid = normalize_id(entry)
            if not uid or uid in seen:
                scartate_gia_viste += 1
                continue

            title = entry.get("title", "(senza titolo)")
            link = entry.get("link", "")

            # Filtro combinato:
            # 1. Deve menzionare una delle tue squadre
            # 2. Non deve contenere parole escluse (calcio femminile, allenatori, mondiali...)
            # 3. Deve sembrare un trasferimento vero (contenere una parola chiave di mercato)
            if not mentions_team_of_interest(title):
                scartate_no_team += 1
                continue
            if is_excluded(title):
                scartate_excluded += 1
                continue
            if not is_probably_transfer(title):
                scartate_no_transfer += 1
                continue

            seen.add(uid)
            new_items.append({
                "uid": uid,
                "title": title,
                "link": link,
                "source": feed.feed.get("title", feed_url),
                "is_transfer": True,
            })

    print("--- Riepilogo diagnostico ---")
    print(f"Notizie totali lette dai feed: {total_entries}")
    print(f"Scartate perche' gia' viste in precedenza: {scartate_gia_viste}")
    print(f"Scartate: nessuna squadra di interesse nel titolo: {scartate_no_team}")
    print(f"Scartate: contenevano parola esclusa (donne/allenatori/mondiali): {scartate_excluded}")
    print(f"Scartate: nessuna parola di trasferimento riconosciuta: {scartate_no_transfer}")
    print(f"Notizie che hanno superato tutti i filtri: {len(new_items)}")
    print("-----------------------------")

    if not new_items:
        print("Nessuna notizia nuova trovata.")
        return

    for item in new_items[:15]:  # limite di sicurezza per non floodare Telegram
        message = (
            f"\u26bd <b>{item['title']}</b>\n"
            f"Fonte: {item['source']}\n"
            f"{item['link']}"
        )
        send_telegram_message(message)

    save_seen(seen)
    print(f"Inviate {min(len(new_items), 15)} notifiche. Totale nuove trovate: {len(new_items)}.")


if __name__ == "__main__":
    main()
