#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Récupère les séances des cinémas et écrit data.json.
Lancé une fois par jour par GitHub. Rien à faire à la main.
"""

import json
import re
import sys
import unicodedata
from datetime import date, datetime

import requests
from bs4 import BeautifulSoup

ENTETE = {
    "User-Agent": "Programme perso cinemas quartier latin (usage prive, 1 visite/jour)"
}

MOIS = {
    "janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
    "juillet": 7, "aout": 8, "septembre": 9, "octobre": 10,
    "novembre": 11, "decembre": 12,
}


def sans_accents(t):
    return "".join(
        c for c in unicodedata.normalize("NFD", t.lower())
        if unicodedata.category(c) != "Mn"
    )


def telecharge(url):
    r = requests.get(url, headers=ENTETE, timeout=30)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or "utf-8"
    return r.text


# ---------------------------------------------------------------- Filmothèque

def lire_filmotheque(html):
    """La grille de la semaine : une fiche par film, avec salle, jours et horaires."""
    soup = BeautifulSoup(html, "html.parser")
    section = soup.select_one("#front-this_week")
    if section is None:
        raise ValueError("Filmothèque : section 'Cette semaine' introuvable")

    titre = section.select_one(".titles h2")
    debut, fin = bornes_semaine(titre.get_text(" ", strip=True) if titre else "")

    seances = []
    for fiche in section.select(".item"):
        infobulle = fiche.select_one(".tooltip")
        if infobulle is None:
            continue

        nom = infobulle.select_one(".movie-title")
        if nom is None:
            continue
        nom = re.sub(r"\s+", " ", nom.get_text(" ", strip=True))

        lien = fiche.select_one("a.more-link.for-desktop")
        lien = lien["href"] if lien and lien.get("href", "#") != "#" else ""

        affiche = fiche.select_one("figure.poster img")
        affiche = affiche["src"] if affiche else ""

        annee = infobulle.select_one(".generic .date")
        duree = infobulle.select_one(".generic .duration")
        cycle = infobulle.select_one(".event-badge")

        # La programmation alterne : un titre de salle, puis ses jours.
        salle = ""
        for bloc in infobulle.select(".programmation-title, .movie-timetable"):
            classes = bloc.get("class", [])
            if "programmation-title" in classes:
                salle = bloc.get_text(" ", strip=True)
                continue
            for jour in bloc.select(".day"):
                etiquette = jour.select_one(".day-title")
                if etiquette is None:
                    continue
                le_jour = jour_vers_date(etiquette.get_text(strip=True), debut, fin)
                if le_jour is None:
                    continue
                for h in jour.select(".day-hours div"):
                    heure = normalise_heure(h.get_text(strip=True))
                    if heure:
                        seances.append({
                            "cine": "Filmothèque",
                            "salle": salle,
                            "date": le_jour.isoformat(),
                            "heure": heure,
                            "titre": nom,
                            "annee": annee.get_text(strip=True) if annee else "",
                            "duree": jolie_duree(duree.get_text(strip=True) if duree else ""),
                            "cycle": cycle.get_text(" ", strip=True) if cycle else "",
                            "note": "",
                            "image": affiche,
                            "url": lien,
                        })
    return seances


def bornes_semaine(titre):
    """« Cette semaine du 02 au 08 septembre 2026 » -> (date début, date fin)."""
    t = sans_accents(titre)
    m = re.search(
        r"du\s+(\d{1,2})\s*([a-z]+)?\s*au\s+(\d{1,2})\s+([a-z]+)\s+(\d{4})", t)
    if not m:
        raise ValueError("Filmothèque : dates de la semaine illisibles (%r)" % titre)

    j1, mois1, j2, mois2, annee = m.groups()
    mois2 = MOIS[mois2]
    mois1 = MOIS[mois1] if mois1 in MOIS else mois2
    annee = int(annee)
    # Une semaine à cheval sur le nouvel an recule d'un an au début.
    annee1 = annee - 1 if mois1 == 12 and mois2 == 1 else annee
    return date(annee1, mois1, int(j1)), date(annee, mois2, int(j2))


def jour_vers_date(etiquette, debut, fin):
    """« Mercredi 02 » -> la vraie date, en s'appuyant sur les bornes de la semaine."""
    m = re.search(r"(\d{1,2})", etiquette)
    if not m:
        return None
    numero = int(m.group(1))
    for candidat in (debut, fin):
        try:
            essai = candidat.replace(day=numero)
        except ValueError:
            continue
        if debut <= essai <= fin:
            return essai
    return None


def normalise_heure(texte):
    m = re.search(r"(\d{1,2})\s*[Hh:]\s*(\d{0,2})", texte)
    if not m:
        return ""
    return "%02d:%02d" % (int(m.group(1)), int(m.group(2) or 0))


def jolie_duree(texte):
    m = re.match(r"(\d{1,2})\s*H\s*(\d{2})", texte.strip(), re.I)
    return "%dh%s" % (int(m.group(1)), m.group(2)) if m else texte


# -------------------------------------------------------------------- La Clef

def lire_laclef(html):
    """Chaque séance est une fiche datée."""
    soup = BeautifulSoup(html, "html.parser")
    fiches = soup.select("article.entry-card")
    if not fiches:
        raise ValueError("La Clef : aucune séance trouvée")

    seances = []
    for fiche in fiches:
        quand = fiche.select_one("time.ct-meta-element-date")
        titre = fiche.select_one(".entry-title a")
        if quand is None or titre is None:
            continue

        m = re.search(r"(\d{2})/(\d{2})/(\d{4})\s*@\s*(\d{1,2})[:h](\d{2})",
                      quand.get_text(strip=True))
        if not m:
            continue
        j, mo, a, h, mi = (int(x) for x in m.groups())

        note = ""
        for p in fiche.select(".card-content p"):
            if p.find("small") is None:
                note = p.get_text(" ", strip=True)
                break

        img = fiche.select_one(".ct-media-container img")
        image = ""
        if img:
            image = img.get("data-src") or (img.get("src") or "")
            if image.startswith("data:"):
                image = ""

        seances.append({
            "cine": "La Clef",
            "salle": "",
            "date": date(a, mo, j).isoformat(),
            "heure": "%02d:%02d" % (h, mi),
            "titre": titre.get_text(" ", strip=True),
            "annee": "",
            "duree": "",
            "cycle": "",
            "note": note,
            "image": image,
            "url": titre.get("href", ""),
        })
    return seances


# ----------------------------------------------------------------------- main

SOURCES = [
    ("Filmothèque", "https://lafilmotheque.fr/", lire_filmotheque),
    ("La Clef", "https://laclefrevival.org/tout-le-programme/", lire_laclef),
]


def main():
    toutes = []
    problemes = []

    for nom, url, lecteur in SOURCES:
        try:
            trouvees = lecteur(telecharge(url))
            if not trouvees:
                raise ValueError("aucune séance récupérée")
            toutes.extend(trouvees)
            print("%-14s %3d séances" % (nom, len(trouvees)))
        except Exception as e:
            problemes.append("%s : %s" % (nom, e))
            print("%-14s ÉCHEC — %s" % (nom, e), file=sys.stderr)

    if not toutes:
        # On ne remplace jamais un bon fichier par un fichier vide.
        print("Rien récupéré du tout, data.json est laissé tel quel.", file=sys.stderr)
        return 1

    aujourdhui = date.today().isoformat()
    toutes = [s for s in toutes if s["date"] >= aujourdhui]
    toutes.sort(key=lambda s: (s["date"], s["heure"], s["cine"]))

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump({
            "maj": datetime.now().isoformat(timespec="minutes"),
            "problemes": problemes,
            "seances": toutes,
        }, f, ensure_ascii=False, indent=1)

    print("data.json écrit : %d séances." % len(toutes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
