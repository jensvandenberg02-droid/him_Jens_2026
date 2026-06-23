#!/usr/bin/env python3
"""
Push één specifieke trainingssessie naar Garmin Connect, op aanvraag.

Dit is GEEN automatische dagelijkse sync — dit script wordt alleen gestart
wanneer jij zelf op een "Push naar Garmin"-knop klikt op de site. De knop
stuurt de sessie-data (naam, dag, type, beschrijving, meta) rechtstreeks mee
als payload, zodat dit script altijd exact bouwt wat er op de site staat —
geen aparte, mogelijk verouderde kopie van de trainingsbibliotheek.

Gebruik (normaal via GitHub Action, maar ook handmatig mogelijk):
  python push_single_workout.py --week 13 --name "Loop — kort herstart" \\
      --day "Dinsdag" --color run --meta "5 km · HS 134–148 bpm" \\
      --desc "Volledig op hartslag 134-148 bpm. Tempo volgt vanzelf."

Vereist GARMIN_EMAIL en GARMIN_PASSWORD als omgevingsvariabelen.
"""

import os
import re
import sys
import json
import argparse
from datetime import date, timedelta

try:
    import garminconnect
except ImportError:
    print("❌ garminconnect niet geïnstalleerd — run: pip install garminconnect")
    sys.exit(1)

PLAN_START = date(2026, 3, 30)  # W1 maandag — zelfde anker als de site

DAY_NL_TO_OFFSET = {
    "maandag": 0, "dinsdag": 1, "woensdag": 2, "donderdag": 3,
    "vrijdag": 4, "zaterdag": 5, "zondag": 6,
}

# Sport-type per kleur-categorie zoals gebruikt in de site se sessie-bibliotheek
COLOR_TO_SPORT = {
    "run":  ("running", 1),
    "bike": ("cycling", 2),
    "swim": ("lap_swimming", 4),
    "str":  (None, None),   # kracht — geen Garmin structured workout mogelijk
    "rest": (None, None),
    "race": ("running", 1),  # race-dagen vaak loop-gerelateerd, beste benadering
}


def get_week_start(week_num):
    return PLAN_START + timedelta(weeks=week_num - 1)


def parse_day_offset(day_str):
    """Zet 'Dinsdag' of 'Donderdag + Vrijdag' (neemt eerste dag) om naar offset 0-6."""
    first_day = re.split(r"[+\s]", day_str.strip())[0].lower()
    return DAY_NL_TO_OFFSET.get(first_day, 0)


def parse_heart_rate_zone(text):
    """Zoekt een hartslagzone-patroon zoals 'HS 134–148 bpm' of '140-158 bpm' in tekst."""
    match = re.search(r"(\d{2,3})\s*[–\-]\s*(\d{2,3})\s*bpm", text)
    if match:
        return int(match.group(1)), int(match.group(2))
    return None, None


def parse_duration_minutes(text):
    """
    Zoekt een duur in minuten, bv. '~25 min', '55 min', '70-90 min'.
    Bij een range (zoals '70-90 min') wordt het EERSTE getal gebruikt — de
    conservatievere, kortere kant van de range — niet het getal dat toevallig
    direct voor 'min' staat. Geeft None als niet gevonden.
    """
    range_match = re.search(r"(\d+)\s*[–\-]\s*(\d+)\s*min", text)
    if range_match:
        return int(range_match.group(1))
    match = re.search(r"(\d+)\s*min", text)
    if match:
        return int(match.group(1))
    return None


def parse_distance_km(text):
    """
    Zoekt een afstand in km, bv. '5 km', '90 km', '10–13 km'. Bij een range
    wordt het EERSTE getal gebruikt (conservatiever, consistent met
    parse_duration_minutes) — niet het getal dat toevallig direct voor 'km'
    staat. Geeft None als niet gevonden.
    """
    range_match = re.search(r"(\d+(?:[.,]\d+)?)\s*[–\-]\s*(\d+(?:[.,]\d+)?)\s*km", text)
    if range_match:
        return float(range_match.group(1).replace(",", "."))
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*km", text)
    if match:
        return float(match.group(1).replace(",", "."))
    return None


def parse_distance_m_swim(text):
    """Zoekt een zwemafstand in meter, bv. '~2.400m', '1.200m'."""
    match = re.search(r"([\d.]+)\s*m\b", text)
    if match:
        try:
            return int(match.group(1).replace(".", ""))
        except ValueError:
            return None
    return None


# ── GARMIN STAP-BOUWBLOKKEN ───────────────────────────────────────────────────

def make_step(sport, step_order, step_type, duration_type, duration_value,
              hr_lo=None, hr_hi=None, description=""):
    step_type_map = {"warmup": 1, "cooldown": 2, "interval": 3, "rest": 4, "other": 7}
    cond_type_map = {"time": 2, "distance": 3}
    step = {
        "type":           "ExecutableStepDTO",
        "stepId":         None,
        "stepOrder":      step_order,
        "stepType":       {"stepTypeId": step_type_map[step_type], "stepTypeKey": step_type},
        "childStepId":    None,
        "description":    description,
        "endCondition":   {"conditionTypeKey": duration_type, "conditionTypeId": cond_type_map[duration_type]},
        "endConditionValue": duration_value,
        "preferredEndConditionUnit": (
            {"unitId": 2, "unitKey": "meter", "factor": 1.0} if duration_type == "distance" else None
        ),
        "endConditionCompare": None,
        "endConditionZone": None,
    }
    if hr_lo and hr_hi:
        step["targetType"]     = {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone"}
        step["targetValueOne"] = hr_lo
        step["targetValueTwo"] = hr_hi
    else:
        step["targetType"]     = {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"}
        step["targetValueOne"] = None
        step["targetValueTwo"] = None
    return step


def workout_envelope(name, sport_type_key, sport_type_id, steps, description=""):
    return {
        "workoutId": None, "ownerId": None,
        "workoutName": name, "description": description,
        "updatedDate": None, "createdDate": None,
        "sportType": {"sportTypeId": sport_type_id, "sportTypeKey": sport_type_key},
        "subSportType": None,
        "estimatedDurationInSecs": None, "estimatedDistanceInMeters": None, "estimateType": None,
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": {"sportTypeId": sport_type_id, "sportTypeKey": sport_type_key},
            "workoutSteps": steps,
        }],
    }


def build_workout_from_payload(payload):
    """
    Bouwt een Garmin-workout-structuur op basis van de sessie-data zoals die
    op de site staat: name, day, color (bepaalt sporttype), desc, meta.

    Het hoofdblok wordt op AFSTAND gestuurd (km/m) wanneer de meta-tekst een
    afstand vermeldt — bewuste keuze: bij hartslag-gestuurde training duurt
    de sessie dan exact zo lang als nodig is om die afstand op de juiste
    hartslagzone af te leggen, in plaats van een vast tijdsblok dat de
    afstand kan laten variëren met het tempo van die dag.
    Warming-up en cooldown blijven kort en TIJD-gestuurd (vast, niet
    afstand-afhankelijk) — dat hoort gewoon bij elke sessie.
    Als er geen afstand in de meta staat (bv. een fiets-sessie in "~70-90
    min"), blijft het hoofdblok tijd-gestuurd — er is dan geen betrouwbare
    afstand om op te sturen.

    Voor zwemmen: geen hartslagzone-target (Garmin's swim-stappen
    ondersteunen dat niet op dezelfde manier), enkel de afstand.
    Voor kracht/rust: retourneert None — geen Garmin structured
    workout-equivalent, wordt overgeslagen.
    """
    name      = payload.get("name", "Training")
    color     = payload.get("color", "rest")
    desc      = payload.get("desc", "")
    meta      = payload.get("meta", "")
    full_text = f"{desc} {meta}"

    sport_key, sport_id = COLOR_TO_SPORT.get(color, (None, None))
    if sport_key is None:
        return None, f"Sessie-type '{color}' heeft geen Garmin structured workout-equivalent (kracht/rust)."

    # Brick-sessies (bv. "90 km + 20min run") combineren twee sporten in één
    # sessie. Garmin's structured workouts ondersteunen geen sport-wissel
    # binnen één workout via deze methode — een sessie pushen zou daardoor
    # altijd maar de helft van de training dekken (bv. enkel de fietsrit,
    # zonder het aansluitende loopstuk). Om geen misleidend incomplete
    # workout te pushen, wordt dit patroon herkend en de sessie overgeslagen.
    if re.search(r"\d+(?:[.,]\d+)?\s*(?:km|m)\s*\+\s*\d+\s*min", full_text):
        return None, (
            "Dit is een brick-sessie (combinatie van twee sporten in één training). "
            "Garmin ondersteunt geen sport-wissel binnen één structured workout — "
            "deze sessie kan niet volledig als één workout gepusht worden."
        )

    hr_lo, hr_hi = parse_heart_rate_zone(full_text)

    if sport_key == "lap_swimming":
        distance_m = parse_distance_m_swim(meta) or 2000
        steps = [
            make_step(sport_key, 1, "warmup",   "distance", 400,
                      description="Warming-up 400m"),
            make_step(sport_key, 2, "interval",  "distance", max(distance_m - 600, 400),
                      description=desc[:200]),
            make_step(sport_key, 3, "cooldown",  "distance", 200,
                      description="Cooling-down 200m"),
        ]
        workout = workout_envelope(name, sport_key, sport_id, steps, desc[:500])
        return workout, None

    # Lopen of fietsen — afstand wordt UITSLUITEND uit 'meta' gehaald, nooit
    # uit de vrije beschrijvingstekst (desc), die vaak een ander getal bevat
    # (bv. "warm op met 5 min stevig stappen") dat anders per ongeluk als
    # sessieduur/afstand gelezen wordt.
    distance_km  = parse_distance_km(meta)
    duration_min = parse_duration_minutes(meta)

    # Vaste, korte tijd-gestuurde warmup/cooldown — onafhankelijk van afstand.
    warmup_secs   = 300  # 5 min
    cooldown_secs = 300  # 5 min

    if distance_km:
        distance_m = round(distance_km * 1000)
        main_step = make_step(sport_key, 2, "interval", "distance", distance_m,
                               hr_lo=hr_lo, hr_hi=hr_hi,
                               description=desc[:200] or name)
    else:
        # Geen afstand vermeld (bv. fiets-sessie in "~70-90 min") — dan blijft
        # tijd de enige betrouwbare sturing voor het hoofdblok.
        if duration_min:
            main_secs = duration_min * 60
        else:
            main_secs = 30 * 60  # fallback: 30 minuten
        main_step = make_step(sport_key, 2, "interval", "time", main_secs,
                               hr_lo=hr_lo, hr_hi=hr_hi,
                               description=desc[:200] or name)

    steps = [
        make_step(sport_key, 1, "warmup", "time", warmup_secs,
                  description="Inwarmen"),
        main_step,
        make_step(sport_key, 3, "cooldown", "time", cooldown_secs,
                  description="Uitlopen/uitrijden"),
    ]
    workout = workout_envelope(name, sport_key, sport_id, steps, desc[:500])
    return workout, None


def main():
    parser = argparse.ArgumentParser(description="Push één specifieke sessie naar Garmin Connect")
    parser.add_argument("--week", type=int, required=True)
    parser.add_argument("--name", type=str, required=True)
    parser.add_argument("--day",  type=str, required=True)
    parser.add_argument("--color", type=str, default="run")
    parser.add_argument("--desc", type=str, default="")
    parser.add_argument("--meta", type=str, default="")
    args = parser.parse_args()

    garmin_email    = os.environ.get("GARMIN_EMAIL", "")
    garmin_password = os.environ.get("GARMIN_PASSWORD", "")
    if not garmin_email or not garmin_password:
        print("❌ GARMIN_EMAIL of GARMIN_PASSWORD niet gevonden")
        sys.exit(1)

    payload = {
        "name": args.name, "day": args.day, "color": args.color,
        "desc": args.desc, "meta": args.meta,
    }
    print(f"🔎 Sessie: {json.dumps(payload, ensure_ascii=False)}")

    workout, skip_reason = build_workout_from_payload(payload)
    if skip_reason:
        print(f"⏭️  {skip_reason}")
        print("   Deze sessie kan niet als Garmin-workout gepusht worden.")
        sys.exit(0)

    dag_offset   = parse_day_offset(args.day)
    week_start   = get_week_start(args.week)
    session_date = week_start + timedelta(days=dag_offset)
    print(f"✅ Workout opgebouwd: {workout['workoutName']} → datum {session_date}")

    print(f"🔑 Inloggen op Garmin Connect als {garmin_email}...")
    try:
        client = garminconnect.Garmin(garmin_email, garmin_password)
        client.login()
        print("✅ Ingelogd")
    except Exception as e:
        print(f"❌ Login mislukt: {e}")
        sys.exit(1)

    try:
        result     = client.upload_workout(workout)
        workout_id = result.get("workoutId")
        print(f"✅ Workout geüpload: {workout['workoutName']} (id: {workout_id})")

        if workout_id:
            client.schedule_workout(workout_id, session_date.strftime("%Y-%m-%d"))
            print(f"📅 Gepland op {session_date}")
            print("\n🎉 Klaar! Check je Forerunner 965: Menu → Training → Workouts → Gepland")
        else:
            print("⚠️ Geen workout_id teruggekregen — sessie is mogelijk geüpload maar niet ingepland")

    except Exception as e:
        print(f"❌ Upload/plannen mislukt: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
