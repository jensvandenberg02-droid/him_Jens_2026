#!/usr/bin/env python3
"""
Garmin Connect Sync — HIM Knokke 2026
Exporteert alle toekomstige trainingen naar Garmin Connect.
Workouts verschijnen automatisch op de Forerunner 265S onder "Workouts".

Gebruik:
  python garmin_sync.py                  → alle toekomstige weken
  python garmin_sync.py --week 10        → alleen week 10
  python garmin_sync.py --dry-run        → test zonder iets te uploaden

GitHub Secrets vereist:
  GARMIN_EMAIL      jouw Garmin Connect e-mailadres
  GARMIN_PASSWORD   jouw Garmin Connect wachtwoord
"""

import os
import sys
import time
import argparse
from datetime import date, timedelta

try:
    import garminconnect
except ImportError:
    print("❌ garminconnect niet geïnstalleerd — run: pip install garminconnect")
    sys.exit(1)

# ── CONFIG ────────────────────────────────────────────────────────────────────

GARMIN_EMAIL    = os.environ.get("GARMIN_EMAIL", "")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD", "")

# Actuele fitnesswaarden — worden overschreven als STRAVA_DATA beschikbaar is
FTP       = 203   # Watt
WEIGHT    = 71    # kg
MAX_HR    = 191   # bpm
REST_HR   = 49    # bpm
CSS_SECS  = 100   # seconden per 100m (1:40/100m)

# HIM start datum (week 1 = 30 maart 2026)
PLAN_START = date(2026, 3, 30)

# ── ZONE BEREKENINGEN ─────────────────────────────────────────────────────────

def zones(ftp=FTP, max_hr=MAX_HR, rest_hr=REST_HR):
    """Bereken trainingszone grenzen vanuit FTP en hartslag."""
    hrr = max_hr - rest_hr
    return {
        # Fietswattage zones (% van FTP)
        "z2_lo":  round(ftp * 0.55),
        "z2_hi":  round(ftp * 0.75),
        "ss_lo":  round(ftp * 0.88),
        "ss_hi":  round(ftp * 0.93),
        "lt_lo":  round(ftp * 0.95),
        "lt_hi":  round(ftp * 1.05),
        "vo2_lo": round(ftp * 1.06),
        "vo2_hi": round(ftp * 1.20),
        "him_lo": round(ftp * 0.80),
        "him_hi": round(ftp * 0.83),
        # Hartslaggerenzen (Karvonen)
        "hr_z2_lo":  round(rest_hr + hrr * 0.60),
        "hr_z2_hi":  round(rest_hr + hrr * 0.70),
        "hr_lt_lo":  round(rest_hr + hrr * 0.80),
        "hr_lt_hi":  round(rest_hr + hrr * 0.90),
        "hr_him":    round(rest_hr + hrr * 0.75),
    }

Z = zones()

# ── GARMIN WORKOUT BUILDERS ───────────────────────────────────────────────────

def make_running_step(step_order, step_type, duration_type, duration_value,
                      target_type=None, target_lo=None, target_hi=None,
                      description=""):
    """Bouw een enkele loopstap."""
    step = {
        "type":           "ExecutableStepDTO",
        "stepId":         None,
        "stepOrder":      step_order,
        "stepType":       {"stepTypeId": {"warmup": 1, "cooldown": 2, "interval": 3, "rest": 4, "other": 7}[step_type], "stepTypeKey": step_type},
        "childStepId":    None,
        "description":    description,
        "endCondition":   {"conditionTypeKey": duration_type, "conditionTypeId": {"time": 2, "distance": 3, "iterations": 7}[duration_type]},
        "endConditionValue": duration_value,
        "preferredEndConditionUnit": None,
        "endConditionCompare": None,
        "endConditionZone": None,
    }
    if target_type == "heart_rate" and target_lo and target_hi:
        step["targetType"]     = {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone"}
        step["targetValueOne"] = target_lo
        step["targetValueTwo"] = target_hi
    elif target_type == "pace" and target_lo and target_hi:
        step["targetType"]     = {"workoutTargetTypeId": 6, "workoutTargetTypeKey": "pace.zone"}
        step["targetValueOne"] = target_lo   # seconden per meter
        step["targetValueTwo"] = target_hi
    else:
        step["targetType"]     = {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"}
        step["targetValueOne"] = None
        step["targetValueTwo"] = None
    return step


def make_cycling_step(step_order, step_type, duration_type, duration_value,
                      target_type=None, target_lo=None, target_hi=None,
                      description=""):
    """Bouw een enkele fietsstap (power target in watt)."""
    step = {
        "type":           "ExecutableStepDTO",
        "stepId":         None,
        "stepOrder":      step_order,
        "stepType":       {"stepTypeId": {"warmup": 1, "cooldown": 2, "interval": 3, "rest": 4, "other": 7}[step_type], "stepTypeKey": step_type},
        "childStepId":    None,
        "description":    description,
        "endCondition":   {"conditionTypeKey": duration_type, "conditionTypeId": {"time": 2, "distance": 3, "iterations": 7}[duration_type]},
        "endConditionValue": duration_value,
        "preferredEndConditionUnit": None,
        "endConditionCompare": None,
        "endConditionZone": None,
    }
    if target_type == "power" and target_lo and target_hi:
        step["targetType"]     = {"workoutTargetTypeId": 2, "workoutTargetTypeKey": "power.zone"}
        step["targetValueOne"] = target_lo
        step["targetValueTwo"] = target_hi
    elif target_type == "heart_rate" and target_lo and target_hi:
        step["targetType"]     = {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone"}
        step["targetValueOne"] = target_lo
        step["targetValueTwo"] = target_hi
    else:
        step["targetType"]     = {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"}
        step["targetValueOne"] = None
        step["targetValueTwo"] = None
    return step


def make_repeat_step(step_order, iterations, child_steps):
    """Bouw een herhaalinstructie met kinderstappen."""
    return {
        "type":               "RepeatGroupDTO",
        "stepId":             None,
        "stepOrder":          step_order,
        "stepType":           {"stepTypeId": 6, "stepTypeKey": "repeat"},
        "childStepId":        1,
        "numberOfIterations": iterations,
        "smartRepeat":        False,
        "workoutSteps":       child_steps,
    }


def make_swimming_step(step_order, step_type, distance_m, target_type=None,
                       target_lo=None, target_hi=None, description=""):
    """Bouw een zwemstap."""
    step = {
        "type":           "ExecutableStepDTO",
        "stepId":         None,
        "stepOrder":      step_order,
        "stepType":       {"stepTypeId": {"warmup": 1, "cooldown": 2, "interval": 3, "rest": 4, "other": 7}[step_type], "stepTypeKey": step_type},
        "childStepId":    None,
        "description":    description,
        "endCondition":   {"conditionTypeKey": "distance", "conditionTypeId": 3},
        "endConditionValue": distance_m,
        "preferredEndConditionUnit": {"unitId": 2, "unitKey": "meter", "factor": 1.0},
        "endConditionCompare": None,
        "endConditionZone": None,
        "targetType":     {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"},
        "targetValueOne": None,
        "targetValueTwo": None,
    }
    return step


def workout_envelope(name, sport_type_key, sport_type_id, steps, description=""):
    """Bouw de Garmin workout wrapper."""
    return {
        "workoutId":          None,
        "ownerId":            None,
        "workoutName":        name,
        "description":        description,
        "updatedDate":        None,
        "createdDate":        None,
        "sportType":          {"sportTypeId": sport_type_id, "sportTypeKey": sport_type_key},
        "subSportType":       None,
        "estimatedDurationInSecs": None,
        "estimatedDistanceInMeters": None,
        "estimateType":       None,
        "workoutSegments": [
            {
                "segmentOrder": 1,
                "sportType":    {"sportTypeId": sport_type_id, "sportTypeKey": sport_type_key},
                "workoutSteps": steps,
            }
        ],
    }


# ── SESSIE DEFINITIES ─────────────────────────────────────────────────────────
# Elke functie retourneert een Garmin workout dict klaar voor upload.

def sessie_kracht_full_body(week):
    """Kracht full body — geen Garmin structured workout, sla over."""
    return None  # Kracht zit niet in Garmin workout structuur


def sessie_z2_loop(week, duur_min=30, fase=3):
    """Korte Z2 duurloop."""
    name = f"W{week} Z2 Loop {duur_min}min"
    pace_lo = 1000 / (Z["hr_z2_hi"] / 60 * 10)   # ruwe benadering
    steps = [
        make_running_step(1, "warmup",   "time", 300,
                          "heart_rate", Z["hr_z2_lo"] - 5, Z["hr_z2_lo"],
                          "Inlopen 5 min rustig"),
        make_running_step(2, "interval", "time", duur_min * 60 - 600,
                          "heart_rate", Z["hr_z2_lo"], Z["hr_z2_hi"],
                          f"Z2 {Z['hr_z2_lo']}–{Z['hr_z2_hi']} bpm"),
        make_running_step(3, "cooldown", "time", 300,
                          None, None, None,
                          "Uitlopen 5 min"),
    ]
    return workout_envelope(name, "running", 1, steps,
                            f"Zone 2 duurloop · HS {Z['hr_z2_lo']}–{Z['hr_z2_hi']} bpm · knie check")


def sessie_loop_lt(week, duur_min=50):
    """Loopkwaliteit — lactaatdrempel intervallen."""
    name = f"W{week} Loop LT Kwaliteit"
    steps = [
        make_running_step(1, "warmup",   "time", 600,
                          "heart_rate", Z["hr_z2_lo"], Z["hr_z2_hi"],
                          "Inlopen 10 min Z2"),
        make_repeat_step(2, 4, [
            make_running_step(1, "interval", "time", 480,
                              "heart_rate", Z["hr_lt_lo"], Z["hr_lt_hi"],
                              f"LT 8 min · {Z['hr_lt_lo']}–{Z['hr_lt_hi']} bpm"),
            make_running_step(2, "rest",     "time", 120,
                              None, None, None,
                              "Herstel 2 min rustig"),
        ]),
        make_running_step(3, "cooldown", "time", 600,
                          None, None, None,
                          "Uitlopen 10 min"),
    ]
    return workout_envelope(name, "running", 1, steps,
                            f"LT intervallen 4×8 min · HS {Z['hr_lt_lo']}–{Z['hr_lt_hi']} bpm")


def sessie_loop_vo2(week):
    """Loopkwaliteit — VO2max intervallen."""
    name = f"W{week} Loop VO2max"
    steps = [
        make_running_step(1, "warmup",   "time", 600,
                          "heart_rate", Z["hr_z2_lo"], Z["hr_z2_hi"],
                          "Inlopen 10 min Z2"),
        make_repeat_step(2, 5, [
            make_running_step(1, "interval", "time", 180,
                              "heart_rate", Z["hr_lt_hi"], MAX_HR - 5,
                              "3 min hard"),
            make_running_step(2, "rest",     "time", 180,
                              None, None, None,
                              "3 min rustig herstel"),
        ]),
        make_running_step(3, "cooldown", "time", 600,
                          None, None, None,
                          "Uitlopen 10 min"),
    ]
    return workout_envelope(name, "running", 1, steps,
                            "VO2max 5×3 min hard / 3 min herstel")


def sessie_loop_him_tempo(week, duur_min=60):
    """Lange duurloop op HIM-tempo."""
    name = f"W{week} Duurloop HIM-tempo {duur_min}min"
    steps = [
        make_running_step(1, "warmup",   "time", 600,
                          "heart_rate", Z["hr_z2_lo"], Z["hr_z2_hi"],
                          "Inlopen 10 min"),
        make_running_step(2, "interval", "time", (duur_min - 20) * 60,
                          "heart_rate", Z["hr_him"] - 5, Z["hr_him"] + 5,
                          f"HIM-tempo {Z['hr_him']}±5 bpm"),
        make_running_step(3, "cooldown", "time", 600,
                          None, None, None,
                          "Uitlopen 10 min"),
    ]
    return workout_envelope(name, "running", 1, steps,
                            f"Lange duurloop op HIM-tempo · HS ~{Z['hr_him']} bpm")


def sessie_duurloop_lang(week, duur_min=75):
    """Lange Z2 duurloop."""
    name = f"W{week} Lange Duurloop {duur_min}min"
    steps = [
        make_running_step(1, "warmup",   "time", 600,
                          "heart_rate", Z["hr_z2_lo"] - 5, Z["hr_z2_lo"],
                          "Inlopen 10 min"),
        make_running_step(2, "interval", "time", (duur_min - 20) * 60,
                          "heart_rate", Z["hr_z2_lo"], Z["hr_z2_hi"],
                          f"Z2 {Z['hr_z2_lo']}–{Z['hr_z2_hi']} bpm"),
        make_running_step(3, "cooldown", "time", 600,
                          None, None, None,
                          "Uitlopen 10 min"),
    ]
    return workout_envelope(name, "running", 1, steps,
                            f"Lange Z2 duurloop · {duur_min} min · aerobe basis")


def sessie_fiets_sweet_spot(week, sets=3, minuten=15):
    """Fiets sweet spot training."""
    name = f"W{week} Fiets Sweet Spot {sets}×{minuten}min"
    steps = [
        make_cycling_step(1, "warmup",   "time", 600,
                          "power", Z["z2_lo"], Z["z2_hi"],
                          "Inrijden 10 min Z2"),
        make_repeat_step(2, sets, [
            make_cycling_step(1, "interval", "time", minuten * 60,
                              "power", Z["ss_lo"], Z["ss_hi"],
                              f"Sweet Spot {Z['ss_lo']}–{Z['ss_hi']}W"),
            make_cycling_step(2, "rest",     "time", 300,
                              "power", Z["z2_lo"] - 20, Z["z2_lo"],
                              "Herstel 5 min"),
        ]),
        make_cycling_step(3, "cooldown", "time", 600,
                          "power", Z["z2_lo"] - 30, Z["z2_lo"] - 10,
                          "Uitrijden 10 min"),
    ]
    return workout_envelope(name, "cycling", 2, steps,
                            f"Sweet Spot {sets}×{minuten} min · {Z['ss_lo']}–{Z['ss_hi']}W · 88–93% FTP")


def sessie_fiets_lt(week):
    """Fiets lactaatdrempel."""
    name = f"W{week} Fiets LT"
    steps = [
        make_cycling_step(1, "warmup",   "time", 600,
                          "power", Z["z2_lo"], Z["z2_hi"],
                          "Inrijden 10 min"),
        make_repeat_step(2, 2, [
            make_cycling_step(1, "interval", "time", 1200,
                              "power", Z["lt_lo"], Z["lt_hi"],
                              f"LT 20 min {Z['lt_lo']}–{Z['lt_hi']}W"),
            make_cycling_step(2, "rest",     "time", 300,
                              "power", Z["z2_lo"] - 20, Z["z2_lo"],
                              "Herstel 5 min"),
        ]),
        make_cycling_step(3, "cooldown", "time", 600,
                          "power", Z["z2_lo"] - 30, Z["z2_lo"] - 10,
                          "Uitrijden 10 min"),
    ]
    return workout_envelope(name, "cycling", 2, steps,
                            f"LT 2×20 min · {Z['lt_lo']}–{Z['lt_hi']}W · 95–105% FTP")


def sessie_fiets_him(week, duur_min=90):
    """Lange HIM-specifieke fietsrit."""
    name = f"W{week} Fiets HIM-tempo {duur_min}min"
    steps = [
        make_cycling_step(1, "warmup",   "time", 900,
                          "power", Z["z2_lo"], Z["z2_hi"],
                          "Inrijden 15 min"),
        make_cycling_step(2, "interval", "time", (duur_min - 30) * 60,
                          "power", Z["him_lo"], Z["him_hi"],
                          f"HIM-tempo {Z['him_lo']}–{Z['him_hi']}W"),
        make_cycling_step(3, "cooldown", "time", 900,
                          "power", Z["z2_lo"] - 20, Z["z2_lo"],
                          "Uitrijden 15 min"),
    ]
    return workout_envelope(name, "cycling", 2, steps,
                            f"HIM-tempo {duur_min} min · {Z['him_lo']}–{Z['him_hi']}W · 80–83% FTP")


def sessie_fiets_lang(week, duur_min=120):
    """Lange Z2 fietsrit."""
    name = f"W{week} Lange Fietsrit {duur_min}min"
    steps = [
        make_cycling_step(1, "warmup",   "time", 600,
                          "power", Z["z2_lo"] - 20, Z["z2_lo"],
                          "Inrijden"),
        make_cycling_step(2, "interval", "time", (duur_min - 20) * 60,
                          "power", Z["z2_lo"], Z["z2_hi"],
                          f"Z2 {Z['z2_lo']}–{Z['z2_hi']}W"),
        make_cycling_step(3, "cooldown", "time", 600,
                          "power", Z["z2_lo"] - 30, Z["z2_lo"] - 10,
                          "Uitrijden"),
    ]
    return workout_envelope(name, "cycling", 2, steps,
                            f"Lange duurrit {duur_min} min · aerobe basis")


def sessie_zwem_css(week, sets_css=6, sets_max=8):
    """Zwemtraining op CSS tempo."""
    name = f"W{week} Zwemmen CSS"
    css = CSS_SECS  # seconden per 100m
    steps = [
        make_swimming_step(1, "warmup",   400, description="Warming-up 400m vrij"),
        make_repeat_step(2, sets_css, [
            make_swimming_step(1, "interval", 100,
                               description=f"CSS 100m · doel {css//60}:{css%60:02d}/100m"),
            make_swimming_step(2, "rest",     0,
                               description="20 sec rust"),
        ]),
        make_swimming_step(3, "interval", 200, description="200m pull buoy"),
        make_repeat_step(4, sets_max, [
            make_swimming_step(1, "interval", 50,
                               description="50m sprint MAX"),
            make_swimming_step(2, "rest",     0,
                               description="30 sec rust"),
        ]),
        make_swimming_step(5, "cooldown", 200, description="Cooling-down 200m"),
    ]
    total = 400 + sets_css * 100 + 200 + sets_max * 50 + 200
    return workout_envelope(name, "lap_swimming", 4, steps,
                            f"CSS {sets_css}×100m + {sets_max}×50m MAX · totaal ~{total}m")


def sessie_brick(week, fiets_km, loop_min):
    """Brick workout — fiets + loop. Garmin ondersteunt geen multi-sport in 1 workout,
    dus we maken twee aparte workouts die op dezelfde dag gepland worden."""
    fiets = sessie_fiets_him(week, duur_min=round(fiets_km / 30 * 60))
    fiets["workoutName"] = f"W{week} Brick Fiets {fiets_km}km"
    loop  = sessie_z2_loop(week, duur_min=loop_min)
    loop["workoutName"]  = f"W{week} Brick Loop {loop_min}min (na fiets)"
    return [fiets, loop]


# ── WEEKPLANNER ───────────────────────────────────────────────────────────────

def get_week_start(week_num):
    """Bereken de maandag van een gegeven weeknummer (W1 = 30 mrt 2026)."""
    return PLAN_START + timedelta(weeks=week_num - 1)


def get_week_sessions(week_num):
    """
    Retourneert lijst van (dag_offset, workout_dict) voor een week.
    dag_offset: 0=ma, 1=di, 2=wo, 3=do, 4=vr, 5=za, 6=zo
    """
    sessions = []
    w        = week_num

    # ── FASE 1: W1–W4 — Basis + HM Leuven ──
    if 1 <= w <= 4:
        sessions += [
            (1, sessie_z2_loop(w, 30)),                         # di: Z2 loop
            (2, sessie_fiets_sweet_spot(w, 3, 12)),             # wo: sweet spot
            (3, sessie_loop_lt(w, 50)),                         # do: loopkwaliteit
            (4, sessie_zwem_css(w, 5, 6)),                      # vr: zwemmen
            (5, sessie_duurloop_lang(w, 60 + w * 5)),           # za: lange duurloop
        ]
        if w == 4:  # HM Leuven week — geen lange duurloop
            sessions = [(d, s) for d, s in sessions if d != 5]

    # ── FASE 2: W5–W7 — Knie revalidatie ──
    elif 5 <= w <= 7:
        sessions += [
            (2, sessie_fiets_sweet_spot(w, 3, 15)),             # wo: fiets kwaliteit
            (4, sessie_zwem_css(w, 6, 8)),                      # vr: zwemmen
        ]
        if w >= 6:
            sessions.append((1, sessie_z2_loop(w, 20)))        # di: heel licht lopen
        if w == 7:  # mini brick als fiets in As
            brick = sessie_brick(w, 65, 15)
            sessions.append((6, brick[0]))                      # zo: fiets
            sessions.append((6, brick[1]))                      # zo: loop na fiets

    # ── FASE 3: W8–W14 — Loopherstart + Sprint opbouw ──
    elif 8 <= w <= 14:
        loopminuten = min(30 + (w - 8) * 5, 55)
        langeminuten = min(50 + (w - 8) * 8, 90)
        sessions += [
            (1, sessie_z2_loop(w, loopminuten)),                # di: Z2 loop
            (2, sessie_fiets_sweet_spot(w, 3, 15 + (w - 8))),  # wo: sweet spot opbouw
            (3, sessie_loop_lt(w) if w >= 10 else sessie_z2_loop(w, loopminuten)),  # do: kwaliteit
            (4, sessie_zwem_css(w, 6, 8)),                      # vr: zwemmen
            (5, sessie_duurloop_lang(w, langeminuten)),          # za: lange duurloop
        ]
        if w in [9, 14]:  # race weken — taper
            sessions = [(d, s) for d, s in sessions if d != 5]
        if w == 12:  # brick
            brick = sessie_brick(w, 80, 20)
            sessions.append((6, brick[0]))
            sessions.append((6, brick[1]))

    # ── FASE 4: W15–W17 — HIM Specificiteit ──
    elif 15 <= w <= 17:
        sessions += [
            (1, sessie_z2_loop(w, 40)),                         # di: Z2 loop
            (2, sessie_fiets_lt(w)),                            # wo: fiets LT
            (3, sessie_loop_him_tempo(w, 60)),                  # do: HIM-tempo loop
            (4, sessie_zwem_css(w, 8, 10)),                     # vr: zwemmen
            (5, sessie_duurloop_lang(w, 90)),                   # za: lange duurloop
            (6, sessie_fiets_him(w, 100)),                      # zo: lange HIM-fiets
        ]
        if w == 15:
            brick = sessie_brick(w, 90, 20)
            sessions = [(d, s) for d, s in sessions if d != 6]
            sessions.append((6, brick[0]))
            sessions.append((6, brick[1]))
        if w == 16:  # taper Cave
            sessions = [(d, s) for d, s in sessions if d not in [5, 6]]
        if w == 17:  # race week Cave
            sessions = [(d, s) for d, s in sessions if d in [1, 4]]

    # ── FASE 5: W18–W23 — HIM Piek + Taper ──
    elif 18 <= w <= 23:
        if w == 18:   # trainingsweekend zee
            sessions += [
                (1, sessie_z2_loop(w, 45)),
                (2, sessie_fiets_him(w, 90)),
                (3, sessie_loop_him_tempo(w, 65)),
                (4, sessie_zwem_css(w, 8, 10)),
                (5, sessie_duurloop_lang(w, 90)),
            ]
            brick = sessie_brick(w, 60, 25)
            sessions.append((6, brick[0]))
            sessions.append((6, brick[1]))
        elif w == 19:  # piekweek
            sessions += [
                (1, sessie_z2_loop(w, 45)),
                (2, sessie_fiets_him(w, 110)),
                (3, sessie_loop_him_tempo(w, 70)),
                (4, sessie_zwem_css(w, 10, 10)),
                (5, sessie_duurloop_lang(w, 100)),
            ]
            brick = sessie_brick(w, 110, 25)
            sessions.append((6, brick[0]))
            sessions.append((6, brick[1]))
        elif w == 20:  # taper -30%
            sessions += [
                (1, sessie_z2_loop(w, 35)),
                (2, sessie_fiets_sweet_spot(w, 2, 15)),
                (4, sessie_zwem_css(w, 6, 6)),
                (5, sessie_duurloop_lang(w, 65)),
            ]
        elif w == 21:  # taper -50%
            sessions += [
                (1, sessie_z2_loop(w, 30)),
                (2, sessie_fiets_sweet_spot(w, 2, 12)),
                (4, sessie_zwem_css(w, 5, 5)),
                (5, sessie_duurloop_lang(w, 50)),
            ]
        elif w == 22:  # minimale taper
            sessions += [
                (1, sessie_z2_loop(w, 25)),
                (2, sessie_fiets_sweet_spot(w, 1, 12)),
                (4, sessie_zwem_css(w, 4, 4)),
            ]
        elif w == 23:  # race week
            sessions += [
                (1, sessie_z2_loop(w, 20)),
                (2, sessie_fiets_sweet_spot(w, 1, 10)),
                (4, sessie_zwem_css(w, 3, 3)),
            ]

    # Filter None (kracht sessions)
    return [(d, s) for d, s in sessions if s is not None]


# ── GARMIN UPLOAD ─────────────────────────────────────────────────────────────

def upload_all(dry_run=False, only_week=None):
    today     = date.today()
    current_week = ((today - PLAN_START).days // 7) + 1

    if not GARMIN_EMAIL or not GARMIN_PASSWORD:
        print("❌ GARMIN_EMAIL of GARMIN_PASSWORD niet gevonden als environment variable")
        print("   Voeg toe als GitHub Secret of stel in als lokale omgevingsvariabele")
        return False

    print(f"🔑 Inloggen op Garmin Connect als {GARMIN_EMAIL}...")
    if not dry_run:
        try:
            client = garminconnect.Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
            client.login()
            print("✅ Ingelogd")
        except Exception as e:
            print(f"❌ Login mislukt: {e}")
            return False
    else:
        client = None
        print("🔍 DRY RUN — geen uploads")

    # Bepaal welke weken te verwerken
    if only_week:
        weeks_to_process = [only_week]
    else:
        weeks_to_process = range(max(current_week, 1), 24)  # huidige week t/m W23

    total_uploaded  = 0
    total_scheduled = 0
    total_skipped   = 0

    for week_num in weeks_to_process:
        week_start = get_week_start(week_num)
        sessions   = get_week_sessions(week_num)

        if not sessions:
            continue

        print(f"\n📅 Week {week_num} ({week_start.strftime('%d %b')})  —  {len(sessions)} sessies")

        for dag_offset, workout in sessions:
            session_date = week_start + timedelta(days=dag_offset)

            # Sla verlopen data over (tenzij --week opgegeven)
            if not only_week and session_date < today:
                print(f"   ⏭️  {session_date} — {workout['workoutName']} (verleden, overgeslagen)")
                total_skipped += 1
                continue

            if dry_run:
                print(f"   📋 {session_date} — {workout['workoutName']}")
                total_uploaded  += 1
                total_scheduled += 1
                continue

            try:
                # Upload workout
                result      = client.upload_workout(workout)
                workout_id  = result.get("workoutId")
                print(f"   ✅ Upload: {workout['workoutName']} (id: {workout_id})")
                total_uploaded += 1
                time.sleep(0.5)  # Garmin rate limit vermijden

                # Plan op datum
                if workout_id:
                    client.schedule_workout(workout_id, session_date.strftime("%Y-%m-%d"))
                    print(f"   📅 Gepland op {session_date}")
                    total_scheduled += 1
                    time.sleep(0.3)

            except Exception as e:
                print(f"   ❌ Fout bij {workout['workoutName']}: {e}")

    print(f"\n{'='*50}")
    print(f"✅ Klaar! {total_uploaded} workouts geüpload, {total_scheduled} ingepland, {total_skipped} overgeslagen")
    print(f"   Check je Forerunner 265S: Menu → Workouts → Gepland")
    return True


# ── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Sync HIM Knokke trainingsplan naar Garmin Connect")
    parser.add_argument("--dry-run", action="store_true",
                        help="Test zonder iets te uploaden")
    parser.add_argument("--week", type=int, default=None,
                        help="Exporteer alleen een specifieke week (bv. --week 10)")
    args = parser.parse_args()

    print("🏊🚴🏃 HIM Knokke 2026 — Garmin Connect Sync")
    print(f"   FTP: {FTP}W · Max HS: {MAX_HR} bpm · Rust HS: {REST_HR} bpm")
    print(f"   Sweet Spot: {Z['ss_lo']}–{Z['ss_hi']}W · LT: {Z['lt_lo']}–{Z['lt_hi']}W")
    print()

    success = upload_all(dry_run=args.dry_run, only_week=args.week)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
