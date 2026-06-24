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
    cond_type_map = {"time": 2, "distance": 3, "lap": 1}
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


def make_repeat_group(step_order, iterations, workout_steps):
    """
    Bouwt een RepeatGroupDTO — een echt herhalingsblok zoals "2 keer" of
    "6 keer" in de Garmin Connect app, met de meegegeven stappen erin
    (bv. 1 werk-stap + 1 rust-stap, die samen 'iterations' keer herhaald
    worden).
    """
    return {
        "type":              "RepeatGroupDTO",
        "stepOrder":         step_order,
        "stepType":          {"stepTypeId": 6, "stepTypeKey": "repeat"},
        "numberOfIterations": iterations,
        "workoutSteps":      workout_steps,
        "endCondition":      {"conditionTypeKey": "iterations", "conditionTypeId": 7},
        "endConditionValue": float(iterations),
        "childStepId":       None,
        "smartRepeat":       False,
    }


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


# ── SEQUENTIËLE TEKST-TOKENIZER ───────────────────────────────────────────────
# In plaats van losse regex-matches die tussenliggende tekst negeren (en
# daardoor losse stappen zoals "200m easy" tussen twee herhalingsblokken
# verliezen), ontleedt deze tokenizer de hele tekst van links naar rechts in
# volgorde. Elk herkend stuk wordt een los token: een herhaling (REPEAT), een
# enkele afstand-stap (DIST_STEP) of een enkele tijd-stap (TIME_STEP). Tekst
# die niets van dit alles is (verbindingswoorden, vrije toelichting) wordt
# overgeslagen. Zo blijft de volgorde en volledigheid van de sessie behouden,
# voor een zo getrouw mogelijke overzetting naar Garmin.

# Zwem-herhaling: "6×100m CSS, 15s rust" — rust optioneel, mag verder weg staan
# door tussenliggende tempo-tekst (genegeerd tot de eerste komma/punt).
TOK_SWIM_REPEAT = re.compile(
    r"(\d+)\s*[×x]\s*(\d+)\s*m\s*([A-Za-z]*)"
    r"(?:(?!\s*[,·.]|\s*\+?\s*\d+\s*[×x]).)*"           # tussentekst, maar stop vóór een komma/punt of een volgend N×-patroon
    r"(?:,\s*(\d+)\s*s\s*rust)?",
    re.IGNORECASE
)

# Losse zwem-afstand zonder herhaling: "200m easy", "200m cool-down", "Warming-up 400m".
TOK_SWIM_SINGLE = re.compile(
    r"(?<![.\d~])(?:warming-?up\s+)?(\d+)\s*m\s*([A-Za-z\-]*)",
    re.IGNORECASE
)

# Tijd-herhaling: "3×8 min op 163-170 bpm, 5 min herstel" — rust optioneel.
TOK_TIME_REPEAT = re.compile(
    r"(\d+)\s*[×x]\s*(\d+)\s*min"
    r"[^,]*"
    r"(?:,\s*(\d+)\s*min\s*(?:actief\s*)?(?:herstel|rust)(?:stap)?)?",
    re.IGNORECASE
)

# Losse tijd-stap: "Warming-up 15 min", "cool-down 10 min".
TOK_TIME_SINGLE = re.compile(
    r"(warming-?up|cool-?down)\s+(\d+)\s*min",
    re.IGNORECASE
)


def tokenize_swim_text(text):
    """
    Ontleedt zwem-beschrijvingstekst sequentieel in een lijst van tokens, in
    de volgorde waarin ze in de tekst voorkomen:
      {"kind": "repeat", "reps": N, "distance_m": X, "label": L, "rest_s": R}
      {"kind": "single", "distance_m": X, "label": L}
    Voorkomt overlap: zodra een patroon ergens matcht, wordt die positie
    "verbruikt" en niet nogmaals door een ander patroon opnieuw gelezen.
    """
    text = re.sub(r"<[^>]+>", "", text)  # strip eventuele <strong> tags
    tokens = []
    pos = 0
    length = len(text)

    while pos < length:
        m_repeat = TOK_SWIM_REPEAT.match(text, pos)
        if m_repeat:
            reps, dist, label, rest = m_repeat.groups()
            tokens.append({
                "kind": "repeat", "reps": int(reps), "distance_m": int(dist),
                "label": (label or "").strip(), "rest_s": int(rest) if rest else 0,
            })
            pos = m_repeat.end()
            continue

        m_single = TOK_SWIM_SINGLE.match(text, pos)
        if m_single:
            dist, label = m_single.groups()
            tokens.append({
                "kind": "single", "distance_m": int(dist), "label": (label or "").strip(),
            })
            pos = m_single.end()
            continue

        pos += 1  # geen patroon op deze positie — 1 karakter opschuiven en opnieuw proberen

    return tokens


def tokenize_time_text(text):
    """
    Ontleedt loop/fiets-beschrijvingstekst sequentieel, analoog aan
    tokenize_swim_text maar dan op tijdseenheden:
      {"kind": "repeat", "reps": N, "duration_min": X, "rest_min": R}
      {"kind": "single", "role": "warmup"|"cooldown", "duration_min": X}
    """
    text = re.sub(r"<[^>]+>", "", text)
    tokens = []
    pos = 0
    length = len(text)

    while pos < length:
        m_repeat = TOK_TIME_REPEAT.match(text, pos)
        if m_repeat:
            reps, dur, rest = m_repeat.groups()
            tokens.append({
                "kind": "repeat", "reps": int(reps), "duration_min": int(dur),
                "rest_min": int(rest) if rest else 0,
            })
            pos = m_repeat.end()
            continue

        m_single = TOK_TIME_SINGLE.match(text, pos)
        if m_single:
            role_raw, dur = m_single.groups()
            role = "warmup" if "warm" in role_raw.lower() else "cooldown"
            tokens.append({"kind": "single", "role": role, "duration_min": int(dur)})
            pos = m_single.end()
            continue

        pos += 1

    return tokens


def build_workout_from_payload(payload):
    """
    Bouwt een Garmin-workout-structuur op basis van de sessie-data zoals die
    op de site staat: name, day, color (bepaalt sporttype), desc, meta.

    Gebruikt een sequentiële tokenizer (tokenize_swim_text / tokenize_time_text)
    die de hele beschrijvingstekst in volgorde ontleedt, zodat zowel
    herhalingsblokken (RepeatGroupDTO) als losse tussenstappen (bv. "200m
    easy" tussen twee zwem-herhalingen) correct en in de juiste volgorde
    worden meegenomen — in plaats van losse regex-matches die tussenliggende
    stukken zouden verliezen.

    Voor zwemmen: geen hartslagzone-target (Garmin's swim-stappen
    ondersteunen dat niet op dezelfde manier), enkel afstand.
    Voor kracht/rust: retourneert None — geen Garmin structured
    workout-equivalent, wordt overgeslagen.
    Voor brick-sessies (twee sporten in één training): retourneert None —
    Garmin ondersteunt geen sport-wissel binnen één structured workout.
    """
    name      = payload.get("name", "Training")
    color     = payload.get("color", "rest")
    desc      = payload.get("desc", "")
    meta      = payload.get("meta", "")
    full_text = f"{desc} {meta}"

    sport_key, sport_id = COLOR_TO_SPORT.get(color, (None, None))
    if sport_key is None:
        return None, f"Sessie-type '{color}' heeft geen Garmin structured workout-equivalent (kracht/rust)."

    if re.search(r"\d+(?:[.,]\d+)?\s*(?:km|m)\s*\+\s*\d+\s*min", full_text):
        return None, (
            "Dit is een brick-sessie (combinatie van twee sporten in één training). "
            "Garmin ondersteunt geen sport-wissel binnen één structured workout — "
            "deze sessie kan niet volledig als één workout gepusht worden."
        )

    hr_lo, hr_hi = parse_heart_rate_zone(full_text)

    # ── ZWEMMEN ──
    if sport_key == "lap_swimming":
        tokens = tokenize_swim_text(desc) or tokenize_swim_text(meta)

        if not tokens:
            # Geen enkel patroon herkend — val terug op 1 doorlopend blok
            # over de totale afstand uit de meta-tekst.
            distance_m = parse_distance_m_swim(meta) or 2000
            steps = [
                make_step(sport_key, 1, "warmup", "distance", 400, description="Warming-up 400m"),
                make_step(sport_key, 2, "interval", "distance", max(distance_m - 600, 400), description=desc[:200]),
                make_step(sport_key, 3, "cooldown", "distance", 200, description="Cooling-down 200m"),
            ]
            workout = workout_envelope(name, sport_key, sport_id, steps, desc[:500])
            return workout, None

        steps = []
        step_order = 1
        for i, tok in enumerate(tokens):
            is_first = (i == 0)
            is_last  = (i == len(tokens) - 1)

            if tok["kind"] == "repeat":
                work_step = make_step(
                    sport_key, 1, "interval", "distance", tok["distance_m"],
                    description=f"{tok['distance_m']}m {tok['label']}".strip()
                )
                if tok["rest_s"] > 0:
                    rest_step = make_step(sport_key, 2, "rest", "time", tok["rest_s"],
                                           description=f"{tok['rest_s']}s rust")
                    group_steps = [work_step, rest_step]
                else:
                    group_steps = [work_step]
                steps.append(make_repeat_group(step_order, tok["reps"], group_steps))
            else:
                # Los blok — eerste token wordt als warmup behandeld, laatste
                # als cooldown, alles ertussen als gewoon interval/herstel.
                if is_first:
                    step_type = "warmup"
                elif is_last:
                    step_type = "cooldown"
                else:
                    step_type = "rest"  # bv. "200m easy" tussen twee blokken — actief herstel
                label = f"{tok['distance_m']}m {tok['label']}".strip()
                steps.append(make_step(sport_key, step_order, step_type, "distance",
                                        tok["distance_m"], description=label))
            step_order += 1

        # Zorg dat er altijd een cooldown is, ook als de tekst er geen expliciete had.
        if tokens[-1]["kind"] == "repeat":
            steps.append(make_step(sport_key, step_order, "cooldown", "distance", 200,
                                    description="Cooling-down 200m"))

        workout = workout_envelope(name, sport_key, sport_id, steps, desc[:500])
        return workout, None

    # ── LOPEN OF FIETSEN ──
    tokens = tokenize_time_text(desc)

    if not tokens or not any(t["kind"] == "repeat" for t in tokens):
        # Geen interval-patroon herkend — val terug op het simpele gedrag:
        # afstand (uitsluitend uit 'meta') heeft voorrang boven tijd.
        distance_km  = parse_distance_km(meta)
        duration_min = parse_duration_minutes(meta)
        warmup_secs, cooldown_secs = 300, 300

        if distance_km:
            main_step = make_step(sport_key, 2, "interval", "distance", round(distance_km * 1000),
                                   hr_lo=hr_lo, hr_hi=hr_hi, description=desc[:200] or name)
        else:
            main_secs = (duration_min * 60) if duration_min else 30 * 60
            main_step = make_step(sport_key, 2, "interval", "time", main_secs,
                                   hr_lo=hr_lo, hr_hi=hr_hi, description=desc[:200] or name)

        steps = [
            make_step(sport_key, 1, "warmup", "time", warmup_secs, description="Inwarmen"),
            main_step,
            make_step(sport_key, 3, "cooldown", "time", cooldown_secs, description="Uitlopen/uitrijden"),
        ]
        workout = workout_envelope(name, sport_key, sport_id, steps, desc[:500])
        return workout, None

    # Interval-patroon gevonden — gebruik de expliciete warmup/cooldown-duur
    # uit de tekst zelf indien aanwezig, anders een vaste 5 minuten fallback.
    warmup_min  = next((t["duration_min"] for t in tokens if t["kind"] == "single" and t["role"] == "warmup"), 5)
    cooldown_min = next((t["duration_min"] for t in tokens if t["kind"] == "single" and t["role"] == "cooldown"), 5)

    steps = [make_step(sport_key, 1, "warmup", "time", warmup_min * 60, description="Inwarmen")]
    step_order = 2
    for tok in tokens:
        if tok["kind"] != "repeat":
            continue
        work_step = make_step(sport_key, 1, "interval", "time", tok["duration_min"] * 60,
                               hr_lo=hr_lo, hr_hi=hr_hi, description=desc[:200] or name)
        if tok["rest_min"] > 0:
            rest_step = make_step(sport_key, 2, "rest", "time", tok["rest_min"] * 60,
                                   description=f"{tok['rest_min']} min herstel")
            group_steps = [work_step, rest_step]
        else:
            group_steps = [work_step]
        steps.append(make_repeat_group(step_order, tok["reps"], group_steps))
        step_order += 1

    steps.append(make_step(sport_key, step_order, "cooldown", "time", cooldown_min * 60,
                            description="Uitlopen/uitrijden"))
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
