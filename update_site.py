#!/usr/bin/env python3
"""
Strava → GitHub Pages auto-updater
Haalt elke nacht activiteiten op van Strava en werkt de atletensite bij.
Haalt ook Garmin Connect gezondheidsdata op (slaap, HRV, stappen, rust HS).
"""

import os
import re
import json
import time
import requests
from datetime import datetime, timezone, timedelta
from collections import defaultdict

# ── STRAVA AUTH ──
CLIENT_ID     = os.environ["STRAVA_CLIENT_ID"]
CLIENT_SECRET = os.environ["STRAVA_CLIENT_SECRET"]
REFRESH_TOKEN = os.environ["STRAVA_REFRESH_TOKEN"]

# ── GARMIN CONNECT HEALTH DATA ──
def get_garmin_health_data():
    """
    Haalt slaap, HRV, stappen en rust-HS op via Garmin Connect.
    Gebruikt GARMIN_EMAIL / GARMIN_PASSWORD secrets (zelfde als garmin_sync.py).
    Geeft een dict terug met defaults bij elke fout zodat de rest van het
    script altijd kan doorlopen, ook zonder Garmin-credentials.
    """
    defaults = {
        "available":      False,
        "sleep_hours":    None,
        "sleep_score":    None,
        "hrv_status":     None,
        "hrv_value":      None,
        "resting_hr":     None,
        "steps":          None,
        "body_battery":   None,
        "vo2max":         None,
        "max_hr":         None,
        # Training Readiness
        "readiness_score":  None,
        "readiness_level":  None,
        "readiness_feedback": None,
        # Training Status
        "training_status":  None,
        "training_status_label": None,
        "acute_load":        None,
        "load_ratio":         None,
        # Race Predictor
        "predict_5k":      None,
        "predict_10k":     None,
        "predict_half":    None,
        "predict_marathon": None,
        # Dagelijkse stress
        "stress_avg":      None,
        "stress_rest_pct": None,
    }

    garmin_email    = os.environ.get("GARMIN_EMAIL", "")
    garmin_password = os.environ.get("GARMIN_PASSWORD", "")
    if not garmin_email or not garmin_password:
        print("  ⚠️ GARMIN_EMAIL/GARMIN_PASSWORD niet gevonden — health data overgeslagen")
        return defaults

    try:
        import garminconnect
    except ImportError:
        print("  ⚠️ garminconnect package niet geïnstalleerd — health data overgeslagen")
        return defaults

    try:
        client = garminconnect.Garmin(garmin_email, garmin_password)
        client.login()
        print("  ✅ Garmin Connect login geslaagd")
    except Exception as e:
        print(f"  ❌ Garmin login mislukt: {e}")
        return defaults

    today = datetime.now().strftime("%Y-%m-%d")
    result = dict(defaults)
    result["available"] = True

    # ── Slaap ──
    try:
        sleep = client.get_sleep_data(today)
        dto = sleep.get("dailySleepDTO", {}) if sleep else {}
        sleep_secs = dto.get("sleepTimeSeconds")
        if sleep_secs:
            result["sleep_hours"] = round(sleep_secs / 3600, 1)
        result["sleep_score"] = (dto.get("sleepScores") or {}).get("overall", {}).get("value")
        print(f"  💤 Slaap: {result['sleep_hours']}u · score {result['sleep_score']}")
    except Exception as e:
        print(f"  ⚠️ Slaapdata ophalen mislukt: {e}")

    # ── HRV ──
    try:
        hrv = client.get_hrv_data(today)
        if hrv:
            summary = hrv.get("hrvSummary", {})
            result["hrv_status"] = summary.get("status")
            result["hrv_value"]  = summary.get("lastNightAvg")
            print(f"  💓 HRV: {result['hrv_value']} ({result['hrv_status']})")
    except Exception as e:
        print(f"  ⚠️ HRV-data ophalen mislukt: {e}")

    # ── Stappen (laatste dag) ──
    try:
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        steps_data = client.get_daily_steps(yesterday, today)
        if steps_data:
            result["steps"] = steps_data[-1].get("totalSteps")
            print(f"  👟 Stappen: {result['steps']}")
    except Exception as e:
        print(f"  ⚠️ Stappendata ophalen mislukt: {e}")

    # ── Rust HS + Body Battery via get_stats ──
    try:
        stats = client.get_stats(today)
        if stats:
            result["resting_hr"]   = stats.get("restingHeartRate")
            result["body_battery"] = stats.get("bodyBatteryMostRecentValue")
            print(f"  ❤️ Rust HS: {result['resting_hr']} · Body Battery: {result['body_battery']}")
    except Exception as e:
        print(f"  ⚠️ Stats ophalen mislukt: {e}")

    # ── VO2max + Max HS via get_max_metrics (fallback: get_user_summary) ──
    try:
        max_metrics = client.get_max_metrics(today)
        if max_metrics:
            entry = max_metrics[0] if isinstance(max_metrics, list) else max_metrics
            generic = entry.get("generic", {}) if isinstance(entry, dict) else {}
            vo2 = generic.get("vo2MaxPreciseValue") or generic.get("vo2MaxValue")
            if vo2:
                result["vo2max"] = round(vo2, 1)
                print(f"  🫁 VO2max (Garmin): {result['vo2max']} ml/kg/min")
    except Exception as e:
        print(f"  ⚠️ Max metrics ophalen mislukt: {e}")

    if not result.get("vo2max"):
        try:
            summary = client.get_user_summary(today)
            if summary and summary.get("vo2Max"):
                result["vo2max"] = round(summary["vo2Max"], 1)
                print(f"  🫁 VO2max (via user summary): {result['vo2max']} ml/kg/min")
        except Exception as e:
            print(f"  ⚠️ User summary VO2max ophalen mislukt: {e}")

    try:
        summary = client.get_user_summary(today)
        if summary and summary.get("maxHeartRate"):
            result["max_hr"] = summary["maxHeartRate"]
            print(f"  💓 Max HS (Garmin, 24u): {result['max_hr']} bpm")
    except Exception as e:
        print(f"  ⚠️ Max HS ophalen mislukt: {e}")

    # ── Training Readiness — "ben je klaar voor een zware training?" ──
    try:
        readiness = client.get_training_readiness(today)
        if readiness:
            entry = readiness[0] if isinstance(readiness, list) else readiness
            if isinstance(entry, dict):
                result["readiness_score"]    = entry.get("score")
                result["readiness_level"]    = entry.get("level")
                result["readiness_feedback"] = entry.get("feedbackLong") or entry.get("feedbackShort")
                print(f"  🎯 Training Readiness: {result['readiness_score']}/100 ({result['readiness_level']})")
    except Exception as e:
        print(f"  ⚠️ Training Readiness ophalen mislukt: {e}")

    # ── Training Status — Productive / Peaking / Overreaching / Detraining / ... ──
    try:
        tstatus = client.get_training_status(today)
        if tstatus:
            # Structuur varieert per Garmin firmware-versie; meest gebruikte sleutel eerst proberen
            latest = (tstatus.get("mostRecentTrainingStatus") or {})
            dev_dict = latest.get("latestTrainingStatusData") if isinstance(latest, dict) else None
            if isinstance(dev_dict, dict) and dev_dict:
                first_device = next(iter(dev_dict.values()))
                status_code  = first_device.get("trainingStatus")
                STATUS_LABELS = {
                    0: "Geen status", 1: "Detraining", 2: "Herstel",
                    3: "Onderhoud", 4: "Productief", 5: "Piekvorm",
                    6: "Overbelasting", 7: "Onproductief", 8: "Geen data",
                }
                result["training_status"]       = status_code
                result["training_status_label"] = STATUS_LABELS.get(status_code, "Onbekend")
                result["acute_load"]            = first_device.get("loadLevelTrend")
            print(f"  📈 Training Status: {result['training_status_label']}")
    except Exception as e:
        print(f"  ⚠️ Training Status ophalen mislukt: {e}")

    # ── Acute:Chronic Load Ratio (trainingsbelasting-verhouding) ──
    try:
        load = client.get_training_status(today)
        acute = (load.get("mostRecentTrainingLoadBalance") or {}) if load else {}
        balance_dict = acute.get("metricsTrainingLoadBalanceDTOMap") if isinstance(acute, dict) else None
        if isinstance(balance_dict, dict) and balance_dict:
            first_balance = next(iter(balance_dict.values()))
            result["load_ratio"] = first_balance.get("trainingBalanceFeedbackPhrase")
            print(f"  ⚖️ Load balance: {result['load_ratio']}")
    except Exception as e:
        print(f"  ⚠️ Load balance ophalen mislukt: {e}")

    # ── Race Predictor — Garmin's eigen tijdschattingen ──
    try:
        predictions = client.get_race_predictions(startdate=today, enddate=today)
        if predictions:
            entry = predictions[-1] if isinstance(predictions, list) and predictions else predictions
            if isinstance(entry, dict):
                result["predict_5k"]       = entry.get("time5K")
                result["predict_10k"]      = entry.get("time10K")
                result["predict_half"]     = entry.get("timeHalfMarathon")
                result["predict_marathon"] = entry.get("timeMarathon")
                print(f"  🏃 Race predictor: 5K {result['predict_5k']}s · 10K {result['predict_10k']}s · Half {result['predict_half']}s")
    except Exception as e:
        print(f"  ⚠️ Race predictions ophalen mislukt: {e}")

    # ── Dagelijkse stress (los van slaap — overdag belasting) ──
    try:
        stress = client.get_all_day_stress(today)
        if stress:
            result["stress_avg"] = stress.get("avgStressLevel")
            rest_secs   = stress.get("restStressDuration") or 0
            total_secs  = (stress.get("totalStressDuration")
                           or stress.get("activeStressDuration") or 0) + rest_secs
            if total_secs:
                result["stress_rest_pct"] = round(rest_secs / total_secs * 100)
            print(f"  😌 Stress gem.: {result['stress_avg']} · rust {result['stress_rest_pct']}%")
    except Exception as e:
        print(f"  ⚠️ Stress data ophalen mislukt: {e}")

    return result


def backfill_fitness_history(days=30):
    """
    Vult de FITNESS_HISTORY met terugwerkende kracht met data uit het verleden,
    zodat de dashboard-evolutiegrafiek niet pas na weken van dagelijkse syncs
    een zinvolle lijn toont. Wordt alleen aangeroepen als de bestaande
    geschiedenis nog kort is (zie main()).

    Haalt per historische dag bij Garmin op: rust-HS (get_stats) en VO2max
    (get_max_metrics, met get_user_summary als fallback). FTP wordt niet per
    dag opgevraagd (geen stabiele Garmin-bron hiervoor via deze library) —
    in plaats daarvan wordt voor het verleden lineair geïnterpoleerd tussen
    een lichtjes lager startpunt en de huidige FTP, wat een realistischer
    beeld geeft dan een volledig vlakke lijn. Gewicht krijgt een vergelijkbare
    lichte interpolatie indien geen historische metingen voorhanden zijn.

    Retourneert een lijst van dicts (date, ftp, vo2, rhr, weight), oudste eerst.
    Bij elke fout per dag wordt die dag overgeslagen zonder de hele backfill
    te laten mislukken.
    """
    garmin_email    = os.environ.get("GARMIN_EMAIL", "")
    garmin_password = os.environ.get("GARMIN_PASSWORD", "")
    if not garmin_email or not garmin_password:
        print("  ⚠️ Backfill overgeslagen — geen Garmin credentials")
        return []

    try:
        import garminconnect
        client = garminconnect.Garmin(garmin_email, garmin_password)
        client.login()
    except Exception as e:
        print(f"  ❌ Backfill: Garmin login mislukt: {e}")
        return []

    today = datetime.now().date()
    points = []
    errors_seen = 0

    for offset in range(days, -1, -3):  # elke 3 dagen i.p.v. elke dag — beperkt het aantal API-calls
        d = today - timedelta(days=offset)
        d_str = d.strftime("%Y-%m-%d")

        rhr = None
        vo2 = None

        try:
            stats = client.get_stats(d_str)
            if stats:
                rhr = stats.get("restingHeartRate")
        except Exception as e:
            errors_seen += 1
            if errors_seen <= 3:  # toon de eerste paar fouten, niet alle 11 herhalingen
                print(f"  ⚠️ get_stats({d_str}) mislukt: {type(e).__name__}: {e}")

        try:
            max_metrics = client.get_max_metrics(d_str)
            if max_metrics:
                entry = max_metrics[0] if isinstance(max_metrics, list) else max_metrics
                generic = entry.get("generic", {}) if isinstance(entry, dict) else {}
                v = generic.get("vo2MaxPreciseValue") or generic.get("vo2MaxValue")
                if v:
                    vo2 = round(v, 1)
        except Exception as e:
            errors_seen += 1
            if errors_seen <= 3:
                print(f"  ⚠️ get_max_metrics({d_str}) mislukt: {type(e).__name__}: {e}")

        if not vo2:
            try:
                summary = client.get_user_summary(d_str)
                if summary and summary.get("vo2Max"):
                    vo2 = round(summary["vo2Max"], 1)
            except Exception as e:
                errors_seen += 1
                if errors_seen <= 3:
                    print(f"  ⚠️ get_user_summary({d_str}) mislukt: {type(e).__name__}: {e}")

        if rhr or vo2:
            points.append({"date": d_str, "rhr": rhr, "vo2": vo2})
            print(f"  📅 Backfill {d_str}: rust HS={rhr} · VO2max={vo2}")
        else:
            print(f"  · Backfill {d_str}: geen data beschikbaar")

        time.sleep(0.3)  # vriendelijk blijven voor Garmin's onofficiële endpoints

    if errors_seen > 3:
        print(f"  ⚠️ Nog {errors_seen - 3} verdere fouten onderdrukt (zelfde patroon)")

    if not points:
        print("  ⚠️ Backfill leverde geen historische datapunten op — alle dagen gaven een fout of geen data")
        return []

    # Vul ontbrekende rhr/vo2 per punt op via de dichtstbijzijnde bekende waarde,
    # zodat de grafiek geen gaten toont.
    known_rhr = [p["rhr"] for p in points if p["rhr"]]
    known_vo2 = [p["vo2"] for p in points if p["vo2"]]
    default_rhr = known_rhr[-1] if known_rhr else 49
    default_vo2 = known_vo2[-1] if known_vo2 else 53
    for p in points:
        if not p["rhr"]:
            p["rhr"] = default_rhr
        if not p["vo2"]:
            p["vo2"] = default_vo2

    print(f"  ✅ Backfill voltooid: {len(points)} historische datapunten opgehaald")
    return points


def get_access_token():
    r = requests.post("https://www.strava.com/oauth/token", data={
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": REFRESH_TOKEN,
        "grant_type":    "refresh_token",
    })
    r.raise_for_status()
    return r.json()["access_token"]

def get_athlete(token):
    r = requests.get("https://www.strava.com/api/v3/athlete",
                     headers={"Authorization": f"Bearer {token}"})
    r.raise_for_status()
    return r.json()

def get_activities(token, per_page=30):
    r = requests.get("https://www.strava.com/api/v3/athlete/activities",
                     headers={"Authorization": f"Bearer {token}"},
                     params={"per_page": per_page, "page": 1})
    r.raise_for_status()
    return r.json()

# ── HELPERS ──
def fmt_pace(speed_ms):
    """m/s → mm:ss/km"""
    if not speed_ms or speed_ms == 0:
        return "—"
    secs = 1000 / speed_ms
    return f"{int(secs//60)}:{int(secs%60):02d}/km"

def fmt_time(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h:
        return f"{h}u{m:02d}"
    return f"{m}:{s:02d}"

def fmt_swim_pace(speed_ms):
    """m/s → mm:ss/100m"""
    if not speed_ms or speed_ms == 0:
        return "—"
    secs = 100 / speed_ms
    return f"{int(secs//60)}:{int(secs%60):02d}/100m"

def sport_icon(t):
    t = t.lower()
    if "run" in t:   return "🏃"
    if "ride" in t or "cycling" in t: return "🚴"
    if "swim" in t:  return "🏊"
    if "weight" in t or "workout" in t: return "💪"
    return "🏋️"

def sport_class(t):
    t = t.lower()
    if "run" in t:   return "si-r"
    if "ride" in t:  return "si-b"
    if "swim" in t:  return "si-s"
    return "si-w"

def zone_bar(avg_hr, max_hr):
    if not avg_hr or not max_hr:
        return ""
    pct = avg_hr / max_hr
    if pct < 0.60:
        return '<div class="sact-zbar"><div class="szs sz1" style="width:80%"></div><div class="szs sz2" style="width:20%"></div></div>'
    elif pct < 0.70:
        return '<div class="sact-zbar"><div class="szs sz1" style="width:20%"></div><div class="szs sz2" style="width:75%"></div><div class="szs sz3" style="width:5%"></div></div>'
    elif pct < 0.80:
        return '<div class="sact-zbar"><div class="szs sz1" style="width:5%"></div><div class="szs sz2" style="width:70%"></div><div class="szs sz3" style="width:20%"></div><div class="szs sz4" style="width:5%"></div></div>'
    else:
        return '<div class="sact-zbar"><div class="szs sz2" style="width:30%"></div><div class="szs sz3" style="width:40%"></div><div class="szs sz4" style="width:30%"></div></div>'

# ── HERSTEL FILTER ──
def is_recovery(activity):
    """
    Geeft True terug als een activiteit niet moet meetellen in berekeningen.
    Filtert op:
    - Strava workout type: 11 = recovery run, 12 = workout (geen race)
    - Naam bevat 'herstel', 'recovery', 'rustig', 'easy', 'actief herstel'
    - Workout type string bevat 'recovery'
    """
    name = (activity.get("name") or "").lower()
    workout_type = activity.get("workout_type") or 0
    sport_type   = (activity.get("sport_type") or "").lower()

    # Strava workout types: 11 = recovery run
    if workout_type == 11:
        return True

    # Naam-gebaseerde filter
    recovery_keywords = ["herstel", "recovery", "actief herstel", "easy run", "rustig rondje"]
    if any(kw in name for kw in recovery_keywords):
        return True

    return False

# ── COMPUTE STATS ──
def compute_stats(activities, athlete):
    stats = {
        "max_hr":    204,
        "ftp":       athlete.get("ftp") or 165,
        "rest_hr":   athlete.get("measurement_preference") and 49 or 49,
        "vo2max":    None,
        "best_swim": None,
        "best_run_pace": None,
        "run_cadence": None,
        "bike_cadence": None,
        "total_runs": 0,
        "total_rides": 0,
        "total_swims": 0,
    }

    max_hr_seen = 0
    min_avg_hr  = 999  # laagste gemiddelde HS als benadering van rust HS
    swim_speeds = []
    run_paces   = []
    bike_cads   = []
    run_cads    = []

    for a in activities:
        # Herstelactiviteiten overslaan
        if is_recovery(a):
            print(f"   Herstel overgeslagen: {a.get('name', '?')}")
            continue

        t = a.get("type", "").lower()
        mhr = a.get("max_heartrate") or 0
        if mhr > max_hr_seen:
            max_hr_seen = mhr
        # Laagste gemiddelde HS (krachttraining/rust) als benadering rust HS
        avg_hr = a.get("average_heartrate", 0)
        if avg_hr and avg_hr > 30:
            min_avg_hr = min(min_avg_hr, avg_hr)

        if "run" in t:
            stats["total_runs"] += 1
            spd = a.get("average_speed", 0)
            if spd > 0:
                run_paces.append(spd)
            cad = a.get("average_cadence", 0)
            if cad > 0:
                run_cads.append(cad * 2)  # Strava geeft stappen per been

        elif "ride" in t:
            stats["total_rides"] += 1
            cad  = a.get("average_cadence", 0)
            dist = a.get("distance", 0)
            elev = a.get("total_elevation_gain", 0)
            if cad > 0 and dist > 0:
                dist_km    = dist / 1000
                hm_per_km  = elev / dist_km if dist_km > 0 else 0
                # Ritten met veel hoogtemeters hebben meer afdalen zonder trappen
                # Minimum drempel: cadans onder 55 rpm is te vertekend
                # Weeg cadans: vlakke ritten (< 5 hm/km) tellen volledig mee
                # Bergachtige ritten (> 15 hm/km) tellen voor 50%
                if cad >= 55:
                    weight = max(0.5, 1 - (hm_per_km - 5) * 0.025) if hm_per_km > 5 else 1.0
                    bike_cads.append((cad, weight))

        elif "swim" in t:
            stats["total_swims"] += 1
            spd = a.get("average_speed", 0)
            if spd > 0:
                swim_speeds.append(spd)

    if max_hr_seen > 0:
        stats["max_hr"] = max_hr_seen
    # Rust HS: gebruik vaste waarde 50 (Strava geeft dit niet terug via API)
    # Als min gemiddelde HS uit activiteiten lager dan 60 is, gebruik die als indicatie
    if min_avg_hr < 60:
        stats["rest_hr"] = round(min_avg_hr)
    else:
        stats["rest_hr"] = 49

    if swim_speeds:
        best = max(swim_speeds)
        stats["best_swim"] = fmt_swim_pace(best)

    if run_paces:
        best = max(run_paces)
        stats["best_run_pace"] = fmt_pace(best)

    if bike_cads:
        total_weight = sum(w for _, w in bike_cads)
        weighted_cad = sum(c * w for c, w in bike_cads) / total_weight
        stats["bike_cadence"] = round(weighted_cad)
        print(f"   Fietscadans: {len(bike_cads)} ritten · gewogen gemiddelde {stats['bike_cadence']} rpm")

    if run_cads:
        stats["run_cadence"] = round(sum(run_cads) / len(run_cads))

    # ── VO2MAX SCHATTING — FIRSTBEAT METHODE ──
    #
    # Enige beschikbare data: hartslag + looptempo bij runs
    # Methode: Firstbeat (dezelfde als Garmin/Polar)
    #
    # Per run: bereken VO2 bij die intensiteit op basis van
    # hartslag als fractie van hartslagreserve (Karvonen),
    # en extrapoleer naar VO2max.
    # Gebruik mediaan van de beste 5 runs om uitschieters te vermijden.

    REST_HR   = 50    # jouw rustpols
    MAX_HR    = stats["max_hr"] or 204
    WEIGHT_KG = 71

    firstbeat_scores = []

    for a in activities:
        if "run" not in a.get("type", "").lower():
            continue

        avg_hr = a.get("average_heartrate", 0)
        spd    = a.get("average_speed", 0)
        dur    = a.get("moving_time", 0)
        dist   = a.get("distance", 0)

        # Alleen runs met hartslag, minstens 10 min en 1 km
        if not avg_hr or not spd or dur < 600 or dist < 1000:
            continue

        # Hartslag als fractie van reserve (Karvonen)
        hrr      = MAX_HR - REST_HR
        hr_frac  = (avg_hr - REST_HR) / hrr if hrr > 0 else 0.70
        hr_frac  = max(0.40, min(0.98, hr_frac))

        # VO2 vereist bij dit looptempo (ACSM loopformule)
        # VO2 (ml/kg/min) = (spd_m_min × 0.2) + 3.5
        spd_m_min  = spd * 60
        vo2_at_pace = (spd_m_min * 0.2) + 3.5

        # Extrapoleer naar VO2max via hartslag fractie
        # Bij fractie f van HRR ≈ fractie f van VO2max (lineair)
        vo2_max_est = vo2_at_pace / hr_frac

        # Kleine correctie voor loopeconomie lichtgewicht loper
        eco = 1.02 if WEIGHT_KG < 75 else 1.0
        vo2_max_est *= eco

        firstbeat_scores.append(round(vo2_max_est, 1))

    if firstbeat_scores:
        firstbeat_scores.sort(reverse=True)
        top = firstbeat_scores[:5]
        final_vo2 = sum(top) / len(top)
        final_vo2 = round(min(max(final_vo2, 30), 70))

        print(f"   VO2max Firstbeat schattingen (top 5): {top}")
        print(f"   → Gemiddeld resultaat: {final_vo2}")

        stats["vo2max"] = final_vo2
        stats["vo2max_breakdown"] = [(v, 1/len(top), "Firstbeat") for v in top]
    else:
        # Geen runs met hartslag — gebruik veilige fallback
        stats["vo2max"] = 47
        stats["vo2max_breakdown"] = [( 47, 1.0, "Fallback (geen HR data)")]
        print("   VO2max: geen runs met hartslag gevonden, fallback 47")

    return stats


# ── HIM EINDTIJD SCHATTING ──
def estimate_him_time(activities):
    """
    Schat de HIM eindtijd op basis van actuele Strava data.
    Fiets: VAM-gebaseerde normalisatie per rit (hoogtemeters correct verwerkt)
    Run:   hartslag-gewogen gemiddelde van duurlopen
    Zwem:  beste zwemtempo x 0.97
    """

    HIM_HM_PER_100KM = 44 / 90 * 100  # ~49 hm/100km (Knokke, vrijwel vlak)
    MAX_HR  = 204
    REST_HR = 50

    # ── ZWEMMEN ──
    swim_speeds = []
    for a in activities:
        if "swim" not in a.get("type","").lower():
            continue
        if is_recovery(a):
            continue
        spd  = a.get("average_speed", 0)
        dist = a.get("distance", 0)
        dur  = a.get("moving_time", 0)
        if spd > 0 and dist > 500 and dur > 300:
            swim_speeds.append(spd)

    him_swim_speed = max(swim_speeds) * 0.97 if swim_speeds else 100 / 112
    swim_secs = round(1900 / him_swim_speed)

    # ── FIETSEN — VAM normalisatie per rit ──
    # Per rit: corrigeer snelheid naar vlak equivalent via hm/km
    # 1 hm/km kost ~2.5% snelheidsreductie tov vlak (amateur vuistregel)
    # Gewogen gemiddelde op ritafstand → meer gewicht aan langere ritten
    # Daarna: corrigeer terug naar HIM-parcours hoogteprofiel + wedstrijdfactor

    normalized_speeds = []  # (vlak_equiv_ms, dist_km)

    for a in activities:
        if "ride" not in a.get("type","").lower():
            continue
        if is_recovery(a):
            continue
        spd  = a.get("average_speed", 0)
        dist = a.get("distance", 0)
        elev = a.get("total_elevation_gain", 0)
        if not spd or dist < 20000:
            continue

        dist_km   = dist / 1000
        hm_per_km = elev / dist_km if dist_km > 0 else 0
        reduction = max(0.55, 1 - hm_per_km * 0.025)
        flat_equiv = spd / reduction
        normalized_speeds.append((flat_equiv, dist_km))
        print(f"   Fiets rit: {dist_km:.0f}km {spd*3.6:.1f}km/u {elev:.0f}hm ({hm_per_km:.1f}hm/km) → vlak equiv {flat_equiv*3.6:.1f}km/u")

    if normalized_speeds:
        total_w    = sum(w for _, w in normalized_speeds)
        weighted   = sum(s * w for s, w in normalized_speeds) / total_w
        # Corrigeer terug voor HIM-parcours
        him_hm_per_km  = HIM_HM_PER_100KM / 100
        him_reduction  = max(0.55, 1 - him_hm_per_km * 0.025)
        him_ride_speed = weighted * him_reduction * 0.93
        print(f"   Fiets: gewogen vlak equiv {weighted*3.6:.1f}km/u → HIM tempo {him_ride_speed*3.6:.1f}km/u")
    else:
        him_ride_speed = (27 * 0.93) / 3.6

    bike_secs = round(90000 / him_ride_speed)

    # ── LOPEN — hartslag-gewogen tempo ──
    # Normaliseer elk looptempo naar HIM-hartslag niveau (160 bpm ~75% HRR)
    # Zo tellen tempo runs en Z2 runs eerlijk mee
    run_data = []

    for a in activities:
        if "run" not in a.get("type","").lower():
            continue
        if is_recovery(a):
            continue
        spd    = a.get("average_speed", 0)
        dist   = a.get("distance", 0)
        dur    = a.get("moving_time", 0)
        avg_hr = a.get("average_heartrate", 0)
        if not spd or dist < 8000 or dur < 2400:
            continue

        dist_km = dist / 1000
        if avg_hr:
            hr_frac      = max(0.5, min(0.95, (avg_hr - REST_HR) / (MAX_HR - REST_HR)))
            him_hr_frac  = (160 - REST_HR) / (MAX_HR - REST_HR)
            him_spd      = spd * (him_hr_frac / hr_frac)
            run_data.append((him_spd, dist_km))
            print(f"   Run: {dist_km:.1f}km {fmt_pace(spd)} {avg_hr:.0f}bpm → HIM equiv {fmt_pace(him_spd)}")
        else:
            run_data.append((spd, dist_km))

    if run_data:
        total_w       = sum(w for _, w in run_data)
        weighted      = sum(s * w for s, w in run_data) / total_w
        him_run_speed = weighted * 0.93
        print(f"   Run: gewogen HIM equiv {fmt_pace(weighted)} → na vermoeidheid {fmt_pace(him_run_speed)}")
    else:
        him_run_speed = (1000 / 380) * 0.93

    run_secs = round(21100 / him_run_speed)

    # ── TRANSITIES ──
    total_secs = swim_secs + bike_secs + run_secs + 150 + 90

    def hm(s):
        return f"{s//3600}:{(s%3600)//60:02d}"

    def hms(s):
        h = s // 3600
        m = (s % 3600) // 60
        sec = s % 60
        return str(h) + "u" + f"{m:02d}m" + f"{sec:02d}s"

    print(f"   HIM: zwem {hm(swim_secs)} fiets {hm(bike_secs)} run {hm(run_secs)} totaal {hms(total_secs)}")

    return {
        "swim_time":  hm(swim_secs),
        "bike_time":  hm(bike_secs),
        "run_time":   hm(run_secs),
        "total_time": hms(total_secs),
        "swim_pace":  f"{int((100/him_swim_speed)//60)}:{int((100/him_swim_speed)%60):02d}/100m",
        "bike_kmh":   f"{him_ride_speed*3.6:.1f} km/u",
        "run_pace":   f"{int((1000/him_run_speed)//60)}:{int((1000/him_run_speed)%60):02d}/km",
        "total_secs": total_secs,
    }


# ── HTML GENERATORS ──
def activity_card_html(a):
    t     = a.get("type", "Workout")
    name  = a.get("name", t)
    date  = datetime.fromisoformat(a["start_date_local"].replace("Z","")).strftime("%a %-d %b")
    dist  = a.get("distance", 0)
    dur   = a.get("moving_time", 0)
    avg_hr = a.get("average_heartrate")
    max_hr = a.get("max_heartrate")
    avg_spd = a.get("average_speed", 0)
    elev  = a.get("total_elevation_gain", 0)
    cad   = a.get("average_cadence", 0)
    t_low = t.lower()

    metrics = []

    if "swim" in t_low:
        metrics.append(("Afstand", f"{dist:.0f}m"))
        metrics.append(("Tempo", fmt_swim_pace(avg_spd)))
        metrics.append(("Tijd", fmt_time(dur)))
    elif "run" in t_low:
        metrics.append(("Afstand", f"{dist/1000:.1f}km"))
        metrics.append(("Tempo", fmt_pace(avg_spd)))
        metrics.append(("Tijd", fmt_time(dur)))
        if cad: metrics.append(("Cadans", f"{int(cad*2)} spm"))
        if elev: metrics.append(("Hoogte", f"{elev:.0f}m"))
    elif "ride" in t_low:
        metrics.append(("Afstand", f"{dist/1000:.1f}km"))
        metrics.append(("Snelheid", f"{avg_spd*3.6:.1f}km/u"))
        metrics.append(("Tijd", fmt_time(dur)))
        if cad: metrics.append(("Cadans", f"{int(cad)} rpm"))
        if elev: metrics.append(("Hoogte", f"{elev:.0f}m"))
    else:
        metrics.append(("Tijd", fmt_time(dur)))

    if avg_hr: metrics.append(("Gem. HS", f"{int(avg_hr)} bpm"))
    if max_hr: metrics.append(("Max. HS", f"{int(max_hr)} bpm"))

    metrics_html = "\n".join(
        f'<div><div class="sm-lbl">{lbl}</div><div class="sm-val">{val}</div></div>'
        for lbl, val in metrics[:6]
    )

    zbar = zone_bar(avg_hr, max_hr or 204)

    return f"""
    <div class="sact">
      <div class="sact-hd">
        <div class="sact-icon {sport_class(t)}">{sport_icon(t)}</div>
        <div>
          <div class="sact-name">{name}</div>
          <div class="sact-date">{date}</div>
        </div>
      </div>
      <div class="sact-metrics">
        {metrics_html}
      </div>
      {zbar}
    </div>"""


def build_recovery_card_html(health):
    """
    Genereert de HTML-inhoud van de herstel-kaart (Training Readiness, slaap, HRV,
    rust-HS, Body Battery, stress, stappen). Wordt rechtstreeks binnen
    build_strava_section() gebruikt zodat de kaart elke sync meegegenereerd wordt
    in plaats van achteraf via regex te worden geïnjecteerd in mogelijk al
    overschreven HTML.
    """
    if health and health.get("available"):
        def fmt(val, suffix="", fallback="—"):
            return f"{val}{suffix}" if val is not None else fallback

        sleep_color = "var(--green)" if (health.get("sleep_hours") or 0) >= 7 else (
            "var(--yellow)" if (health.get("sleep_hours") or 0) >= 6 else "var(--accent)")
        bb_color = "var(--green)" if (health.get("body_battery") or 0) >= 60 else (
            "var(--yellow)" if (health.get("body_battery") or 0) >= 30 else "var(--accent)")
        readiness_color = "var(--green)" if (health.get("readiness_score") or 0) >= 70 else (
            "var(--yellow)" if (health.get("readiness_score") or 0) >= 40 else "var(--accent)")

        card_html = f"""<div class="recovery-grid">
        <div class="rec-stat">
          <div class="rec-lbl">🎯 Training Readiness</div>
          <div class="rec-val" style="color:{readiness_color}">{fmt(health.get('readiness_score'), '/100')}</div>
          <div class="rec-sub">{fmt(health.get('readiness_level'))}</div>
        </div>
        <div class="rec-stat">
          <div class="rec-lbl">💤 Slaap</div>
          <div class="rec-val" style="color:{sleep_color}">{fmt(health.get('sleep_hours'), 'u')}</div>
          <div class="rec-sub">{fmt(health.get('sleep_score'), '/100 score') if health.get('sleep_score') else 'geen score'}</div>
        </div>
        <div class="rec-stat">
          <div class="rec-lbl">💓 HRV</div>
          <div class="rec-val">{fmt(health.get('hrv_value'), 'ms')}</div>
          <div class="rec-sub">{fmt(health.get('hrv_status'))}</div>
        </div>
        <div class="rec-stat">
          <div class="rec-lbl">❤️ Rust HS</div>
          <div class="rec-val" style="color:var(--green)">{fmt(health.get('resting_hr'), ' bpm')}</div>
          <div class="rec-sub">vannacht</div>
        </div>
        <div class="rec-stat">
          <div class="rec-lbl">🔋 Body Battery</div>
          <div class="rec-val" style="color:{bb_color}">{fmt(health.get('body_battery'), '/100')}</div>
          <div class="rec-sub">huidig niveau</div>
        </div>
        <div class="rec-stat">
          <div class="rec-lbl">😌 Stress (rust %)</div>
          <div class="rec-val">{fmt(health.get('stress_rest_pct'), '%')}</div>
          <div class="rec-sub">{f"gem. {health.get('stress_avg')}" if health.get('stress_avg') is not None else 'geen data'}</div>
        </div>
        <div class="rec-stat">
          <div class="rec-lbl">👟 Stappen</div>
          <div class="rec-val">{fmt(health.get('steps'))}</div>
          <div class="rec-sub">gisteren</div>
        </div>
      </div>"""

        if health.get("readiness_feedback"):
            card_html += f"""
      <div class="rec-feedback">💬 {health['readiness_feedback']}</div>"""
        return card_html

    return """<div class="recovery-unavailable">
        ⚠️ Garmin gezondheidsdata niet beschikbaar — controleer of GARMIN_EMAIL en
        GARMIN_PASSWORD correct zijn ingesteld als GitHub Secrets.
      </div>"""


def build_performance_card_html(health):
    """
    Genereert de HTML-inhoud van de prestatie-kaart (Training Status + Race
    Predictor). Wordt rechtstreeks binnen build_strava_section() gebruikt,
    zelfde reden als build_recovery_card_html().
    """
    def fmt_race_time(seconds):
        if not seconds:
            return "—"
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        if h:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"

    if health and health.get("available") and health.get("training_status_label"):
        STATUS_COLORS = {
            "Productief": "var(--green)", "Piekvorm": "var(--green)",
            "Onderhoud": "var(--blue)", "Herstel": "var(--blue)",
            "Overbelasting": "var(--accent)", "Detraining": "var(--accent)",
            "Onproductief": "var(--yellow)",
        }
        status_color = STATUS_COLORS.get(health.get("training_status_label"), "var(--muted)")

        return f"""<div class="perf-status-row">
        <div class="perf-status-badge" style="color:{status_color};border-color:{status_color}">
          📈 {health['training_status_label']}
        </div>
        {f'<div class="perf-status-sub">{health["load_ratio"]}</div>' if health.get('load_ratio') else ''}
      </div>
      <div class="recovery-grid recovery-grid-4">
        <div class="rec-stat">
          <div class="rec-lbl">🏃 5K</div>
          <div class="rec-val" style="font-size:1.2rem">{fmt_race_time(health.get('predict_5k'))}</div>
          <div class="rec-sub">Garmin predictor</div>
        </div>
        <div class="rec-stat">
          <div class="rec-lbl">🏃 10K</div>
          <div class="rec-val" style="font-size:1.2rem">{fmt_race_time(health.get('predict_10k'))}</div>
          <div class="rec-sub">Garmin predictor</div>
        </div>
        <div class="rec-stat">
          <div class="rec-lbl">🏃 Halve marathon</div>
          <div class="rec-val" style="font-size:1.2rem">{fmt_race_time(health.get('predict_half'))}</div>
          <div class="rec-sub">Garmin predictor</div>
        </div>
        <div class="rec-stat">
          <div class="rec-lbl">🏃 Marathon</div>
          <div class="rec-val" style="font-size:1.2rem">{fmt_race_time(health.get('predict_marathon'))}</div>
          <div class="rec-sub">Garmin predictor</div>
        </div>
      </div>"""

    return """<div class="recovery-unavailable">
        ⚠️ Garmin trainingsstatus niet beschikbaar — komt beschikbaar zodra er genoeg
        trainingsdata is opgebouwd op je Forerunner 965 en Edge 840.
      </div>"""


def iso_week_label(dt):
    """Geeft een leesbaar (jaar, weeknummer) label terug voor groepering per week."""
    iso_year, iso_week, _ = dt.isocalendar()
    return iso_year, iso_week


def week_range_label(iso_year, iso_week):
    """Zet (jaar, weeknummer) om naar een leesbare 'ma–zo' datumrange string."""
    monday = datetime.fromisocalendar(iso_year, iso_week, 1)
    sunday = datetime.fromisocalendar(iso_year, iso_week, 7)
    if monday.month == sunday.month:
        return f"{monday.day}–{sunday.day} {MONTH_NL_SHORT[monday.month]}"
    return f"{monday.day} {MONTH_NL_SHORT[monday.month]} – {sunday.day} {MONTH_NL_SHORT[sunday.month]}"


MONTH_NL_SHORT = {
    1: "jan", 2: "feb", 3: "mrt", 4: "apr", 5: "mei", 6: "jun",
    7: "jul", 8: "aug", 9: "sep", 10: "okt", 11: "nov", 12: "dec",
}


def mini_week_chart_svg(day_durations):
    """
    Bouwt een kleine SVG-staafgrafiek (7 staven, ma t/m zo) van trainingsduur
    per dag in minuten, voor gebruik bovenaan elke weekgroep in de activiteiten-
    sectie. day_durations is een lijst van 7 waarden in minuten (0 als rustdag).
    """
    W, H = 280, 56
    bar_w = 28
    gap = 6
    max_val = max(day_durations) if max(day_durations) > 0 else 1
    bars = ""
    days_nl = ["M", "D", "W", "D", "V", "Z", "Z"]
    for i, val in enumerate(day_durations):
        x = i * (bar_w + gap)
        bar_h = round((val / max_val) * 38) if val > 0 else 0
        y = 40 - bar_h
        color = "var(--accent)" if val > 0 else "var(--border2)"
        bars += f'<rect x="{x}" y="{y}" width="{bar_w}" height="{max(bar_h, 2)}" rx="3" fill="{color}" />'
        bars += f'<text x="{x + bar_w/2}" y="52" text-anchor="middle" font-size="9" fill="var(--muted)">{days_nl[i]}</text>'
    return f'<svg viewBox="0 0 {W} {H}" style="width:100%;max-width:240px;height:56px;display:block">{bars}</svg>'


def build_activities_by_week_html(activities):
    """
    Groepeert activiteiten per ISO-week (meest recente week eerst) en geeft voor
    elke week een mini-staafgrafiek van trainingsduur per dag plus de individuele
    activiteit-kaarten. Toont maximaal de laatste 4 weken om de pagina behapbaar
    te houden.
    """
    if not activities:
        return '<div style="color:var(--muted);font-size:.85rem;padding:2rem 0">Geen recente activiteiten gevonden.</div>'

    weeks = {}  # (jaar, weeknum) -> list van activities
    for a in activities:
        try:
            dt = datetime.fromisoformat(a["start_date_local"].replace("Z", ""))
        except Exception:
            continue
        key = iso_week_label(dt)
        weeks.setdefault(key, []).append((dt, a))

    sorted_keys = sorted(weeks.keys(), reverse=True)[:4]  # laatste 4 weken met activiteit

    blocks = []
    for (iso_year, iso_week) in sorted_keys:
        entries = sorted(weeks[(iso_year, iso_week)], key=lambda x: x[0])

        # Duur per weekdag (ma=0 .. zo=6) in minuten, voor de mini-grafiek
        day_durations = [0.0] * 7
        for dt, a in entries:
            weekday = dt.weekday()
            day_durations[weekday] += (a.get("moving_time", 0) or 0) / 60

        total_minutes = sum(day_durations)
        total_h = int(total_minutes // 60)
        total_m = int(total_minutes % 60)

        cards = "\n".join(activity_card_html(a) for _, a in reversed(entries))  # meest recente eerst binnen de week

        blocks.append(f"""
    <div class="week-activity-group">
      <div class="wag-header">
        <div class="wag-title">Week {iso_week} <span class="wag-range">· {week_range_label(iso_year, iso_week)}</span></div>
        <div class="wag-total">{total_h}u{total_m:02d}m totaal · {len(entries)} sessies</div>
      </div>
      <div class="wag-chart">{mini_week_chart_svg(day_durations)}</div>
      <div class="strava-grid">
        {cards}
      </div>
    </div>""")

    return "\n".join(blocks)


def build_strava_section(activities, stats, athlete, health=None):
    """
    Genereert de drie HTML-blokken die bij elke sync vervangen worden:
      1. garmin_html       → herstel, prestatie, vormcurve, dashboard-grafiek
      2. activities_html   → recente activiteiten gegroepeerd per week
      3. fitness_html      → live fitnesswaarden, geschatte race tijden, VO2max
    Wordt teruggegeven als dict zodat main() elk blok naar zijn eigen plek in
    de HTML kan injecteren.
    """
    now = datetime.now().strftime("%-d %B %Y om %H:%M")

    ftp  = stats["ftp"]
    wkg  = round(ftp / 71, 2)  # gewicht 71kg
    mhr  = stats["max_hr"]
    # VO2max: uitsluitend via Garmin — geen eigen Firstbeat-berekening meer.
    vo2  = health.get("vo2max") if (health and health.get("vo2max")) else (stats["vo2max"] or 53)
    swim = stats["best_swim"] or "—"
    bcad = stats["bike_cadence"] or 77
    rcad = stats["run_cadence"] or 165

    # HIM eindtijd schatting
    him = estimate_him_time(activities)

    # VO2max ring offset (schaal 30–75 → dashoffset 250–50), uitsluitend Garmin-waarde
    vo2_offset = round(250 - ((vo2 - 30) / 45) * 200)

    # ── 1. GARMIN DATA SECTIE ──
    garmin_html = f"""<!-- ── GARMIN DATA ── -->
<section class="section" id="garmin-data">
  <div class="sec-label">⌚ Garmin Connect</div>
  <h2 class="sec-title">Herstel & <span>Trainingsstatus</span></h2>

  <!-- ── HERSTEL (GARMIN) ── -->
  <div class="recovery-card">
    <div class="recovery-label">⌚ Herstel vannacht · Garmin Connect</div>
    {build_recovery_card_html(health)}
  </div>

  <!-- ── PRESTATIE & TRAININGSSTATUS (GARMIN) ── -->
  <div class="recovery-card">
    <div class="recovery-label">📊 Trainingsstatus & Race Predictor · Forerunner 965</div>
    {build_performance_card_html(health)}
  </div>

  <!-- ── VORM-CURVE ── -->
  <div class="form-curve-card">
    <div class="form-curve-label">Trainingsbelasting · laatste 4 weken</div>
    <svg class="form-curve-svg" viewBox="0 0 600 140" preserveAspectRatio="none" id="form-curve-svg">
      <line x1="0" y1="115" x2="600" y2="115" stroke="var(--border)" stroke-width="1" />
    </svg>
    <div class="form-curve-legend">
      <span class="fcl-item"><span class="fcl-line fcl-load"></span>Belasting (sessies × volume)</span>
      <span class="fcl-item"><span class="fcl-line fcl-trend"></span>4-weken trend</span>
    </div>
  </div>

  <!-- ── FITNESS DASHBOARD GRAFIEK ── -->
  <div class="dashboard-card">
    <div class="dashboard-label">📈 Evolutie · FTP, VO2max, gewicht en hartslag over tijd</div>
    <svg class="dashboard-svg" viewBox="0 0 900 320" preserveAspectRatio="xMidYMid meet" id="dashboard-svg">
      <line x1="0" y1="280" x2="900" y2="280" stroke="var(--border)" stroke-width="1" />
    </svg>
    <div class="dashboard-legend" id="dashboard-legend">
      <span class="dl-item"><span class="dl-dot" style="background:var(--blue)"></span>FTP (W)</span>
      <span class="dl-item"><span class="dl-dot" style="background:var(--green)"></span>VO2max</span>
      <span class="dl-item"><span class="dl-dot" style="background:var(--accent)"></span>Rust HS</span>
      <span class="dl-item"><span class="dl-dot" style="background:var(--yellow)"></span>Gewicht</span>
    </div>
  </div>
</section>"""

    # ── 2. RECENTE ACTIVITEITEN SECTIE (PER WEEK GEGROEPEERD) ──
    activities_html = f"""<!-- ── RECENTE ACTIVITEITEN (PER WEEK GEGROEPEERD) ── -->
<section class="section" id="activiteiten">
  <div class="sec-label">Strava · automatisch bijgewerkt</div>
  <h2 class="sec-title">Recente <span>Activiteiten</span></h2>
  <p style="font-size:.82rem;color:var(--muted);margin-bottom:2rem">Automatisch bijgewerkt · Laatste sync: {now}</p>

  <div id="activities-by-week-container">
    {build_activities_by_week_html(activities)}
  </div>
</section>"""

    # ── 3. FITNESSWAARDEN & EINDTIJD SECTIE ──
    fitness_html = f"""<!-- ── FITNESSWAARDEN & EINDTIJD ── -->
<section class="section" id="fitnesswaarden">
  <div class="sec-label">Live via Strava + Garmin</div>
  <h2 class="sec-title">Fitnesswaarden & <span>Eindtijd</span></h2>

  <h3 style="font-family:'Barlow Condensed',sans-serif;font-size:1.4rem;font-weight:700;letter-spacing:.06em;text-transform:uppercase;margin-bottom:1.2rem;color:var(--dim)">Live <span style="color:var(--text)">Fitnesswaarden</span></h3>

  <div class="mhc-grid" style="margin-bottom:2.5rem" id="live-fitness-grid">
    <div class="mhc"><div class="mhc-lbl">FTP</div><div class="mhc-val ac" id="live-ftp">{ftp} W</div><div class="mhc-sub" id="live-wkg">{wkg} W/kg</div></div>
    <div class="mhc"><div class="mhc-lbl">Max HS</div><div class="mhc-val" id="live-mhr">{mhr} bpm</div><div class="mhc-sub">gemeten in training</div></div>
    <div class="mhc"><div class="mhc-lbl">VO2max (Garmin)</div><div class="mhc-val gr" id="live-vo2">{vo2}</div><div class="mhc-sub">ml/kg/min</div></div>
    <div class="mhc"><div class="mhc-lbl">Beste zwemtempo</div><div class="mhc-val bl" id="live-swim">{swim}</div><div class="mhc-sub">snelste gemiddelde</div></div>
    <div class="mhc"><div class="mhc-lbl">Fietscadans gem.</div><div class="mhc-val {'gr' if bcad >= 88 else 'ac'}" id="live-bcad">{bcad} rpm</div><div class="mhc-sub">{'✓ op schema' if bcad >= 88 else 'doel: 90 rpm'}</div></div>
    <div class="mhc"><div class="mhc-lbl">Loopcadans gem.</div><div class="mhc-val {'gr' if rcad >= 168 else 'ay'}" id="live-rcad">{rcad} spm</div><div class="mhc-sub">{'✓ goed' if rcad >= 168 else 'doel: 168–172 spm'}</div></div>
    <div class="mhc"><div class="mhc-lbl">Activiteiten (recent)</div><div class="mhc-val" id="live-activity-count">{len(activities)}</div><div class="mhc-sub" id="live-activity-breakdown">🏃 {stats['total_runs']} · 🚴 {stats['total_rides']} · 🏊 {stats['total_swims']}</div></div>
    <div class="mhc"><div class="mhc-lbl">Rust HS</div><div class="mhc-val gr" id="live-rhr">{stats.get('rest_hr', 49)} bpm</div><div class="mhc-sub">uitstekend</div></div>
  </div>

  <h3 style="font-family:'Barlow Condensed',sans-serif;font-size:1.4rem;font-weight:700;letter-spacing:.06em;text-transform:uppercase;margin-bottom:1.2rem;color:var(--dim)">Geschatte <span style="color:var(--text)">Race Tijden</span></h3>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin-bottom:2.5rem">

    <div style="background:var(--card);border:1px solid rgba(232,81,42,.35);border-radius:14px;padding:1.4rem">
      <div style="font-size:.6rem;font-weight:700;letter-spacing:.16em;text-transform:uppercase;color:var(--accent);margin-bottom:.5rem">🏊🚴🏃 Halve Ironman</div>
      <div style="font-family:'Barlow Condensed',sans-serif;font-size:2.8rem;font-weight:900;line-height:1;color:var(--yellow);letter-spacing:-.01em;margin-bottom:1rem" id="him-total-time">{him['total_time']}</div>
      <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:.5rem;margin-bottom:.8rem">
        <div style="background:rgba(34,197,94,.07);border:1px solid rgba(34,197,94,.2);border-radius:8px;padding:.6rem">
          <div style="font-size:.55rem;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:var(--green);margin-bottom:.25rem">ZWEM</div>
          <div style="font-family:'Barlow Condensed',sans-serif;font-size:1.3rem;font-weight:900;color:var(--text);line-height:1" id="him-swim-time">{him['swim_time']}</div>
          <div style="font-size:.65rem;color:var(--muted);margin-top:.2rem">1,9 km · doel &lt;32min</div>
        </div>
        <div style="background:rgba(58,143,255,.07);border:1px solid rgba(58,143,255,.2);border-radius:8px;padding:.6rem">
          <div style="font-size:.55rem;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:#3a8fff;margin-bottom:.25rem">FIETS</div>
          <div style="font-family:'Barlow Condensed',sans-serif;font-size:1.3rem;font-weight:900;color:var(--text);line-height:1" id="him-bike-time">{him['bike_time']}</div>
          <div style="font-size:.65rem;color:var(--muted);margin-top:.2rem">90 km · doel &lt;2u50</div>
        </div>
        <div style="background:rgba(232,81,42,.07);border:1px solid rgba(232,81,42,.2);border-radius:8px;padding:.6rem">
          <div style="font-size:.55rem;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:var(--accent);margin-bottom:.25rem">RUN</div>
          <div style="font-family:'Barlow Condensed',sans-serif;font-size:1.3rem;font-weight:900;color:var(--text);line-height:1" id="him-run-time">{him['run_time']}</div>
          <div style="font-size:.65rem;color:var(--muted);margin-top:.2rem">21,1 km · doel &lt;1u55</div>
        </div>
      </div>
      <div style="font-size:.68rem;color:var(--muted)">Knokke-Heist · 6 september 2026</div>
    </div>

    <div style="background:var(--card);border:1px solid rgba(160,80,255,.35);border-radius:14px;padding:1.4rem">
      <div style="font-size:.6rem;font-weight:700;letter-spacing:.16em;text-transform:uppercase;color:#a050ff;margin-bottom:.5rem">🏊🚴🏃 Volledige Ironman</div>
      <div style="font-family:'Barlow Condensed',sans-serif;font-size:2.8rem;font-weight:900;line-height:1;color:#a050ff;letter-spacing:-.01em;margin-bottom:1rem" id="im-total-time">{him['total_time']}</div>
      <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:.5rem;margin-bottom:.8rem">
        <div style="background:rgba(34,197,94,.07);border:1px solid rgba(34,197,94,.2);border-radius:8px;padding:.6rem">
          <div style="font-size:.55rem;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:var(--green);margin-bottom:.25rem">ZWEM</div>
          <div style="font-family:'Barlow Condensed',sans-serif;font-size:1.3rem;font-weight:900;color:var(--text);line-height:1" id="im-swim-time">{him['swim_time']}</div>
          <div style="font-size:.65rem;color:var(--muted);margin-top:.2rem">3,8 km</div>
        </div>
        <div style="background:rgba(58,143,255,.07);border:1px solid rgba(58,143,255,.2);border-radius:8px;padding:.6rem">
          <div style="font-size:.55rem;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:#3a8fff;margin-bottom:.25rem">FIETS</div>
          <div style="font-family:'Barlow Condensed',sans-serif;font-size:1.3rem;font-weight:900;color:var(--text);line-height:1" id="im-bike-time">{him['bike_time']}</div>
          <div style="font-size:.65rem;color:var(--muted);margin-top:.2rem">180 km</div>
        </div>
        <div style="background:rgba(160,80,255,.07);border:1px solid rgba(160,80,255,.2);border-radius:8px;padding:.6rem">
          <div style="font-size:.55rem;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:#a050ff;margin-bottom:.25rem">RUN</div>
          <div style="font-family:'Barlow Condensed',sans-serif;font-size:1.3rem;font-weight:900;color:var(--text);line-height:1" id="im-run-time">{him['run_time']}</div>
          <div style="font-size:.65rem;color:var(--muted);margin-top:.2rem">42,2 km</div>
        </div>
      </div>
      <div style="font-size:.68rem;color:var(--muted)">Extrapolatie · zwem +2% · fiets +6% · run +18%</div>
    </div>

  </div>

  <h3 style="font-family:'Barlow Condensed',sans-serif;font-size:1.4rem;font-weight:700;letter-spacing:.06em;text-transform:uppercase;margin-bottom:1.2rem;color:var(--dim)">VO2max <span style="color:var(--text)">— Garmin</span></h3>
  <div class="vo2-row vo2-row-single">
    <div class="vo2-card">
      <div class="ring-svg">
        <svg viewBox="0 0 110 110"><circle class="rbg" cx="55" cy="55" r="46"/><circle class="rfill" cx="55" cy="55" r="46" stroke="var(--green)" stroke-dasharray="289" stroke-dashoffset="{vo2_offset}"/></svg>
        <div class="ring-center"><div class="ring-val" id="ring-vo2-aw" style="color:var(--green)">{vo2}</div><div class="ring-unit">ml/kg/min</div></div>
      </div>
      <div class="vo2-label">Garmin<br><span style="font-size:.62rem;color:#666">directe schatting</span></div>
    </div>
  </div>

</section>"""

    return {
        "garmin": garmin_html,
        "activities": activities_html,
        "fitness": fitness_html,
    }

def generate_ai_update(activities, stats, him, health=None):
    """
    Roept de Anthropic Claude API aan om een persoonlijke trainingsupdate te schrijven.
    """

    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
    if not ANTHROPIC_API_KEY:
        return (
            "AI update niet beschikbaar — voeg ANTHROPIC_API_KEY toe als GitHub Secret.",
            ""
        )

    # Laatste activiteit volledig uitwerken
    last = activities[0] if activities else {}
    last_type  = last.get("type", "Workout")
    last_name  = last.get("name", last_type)
    last_dist  = last.get("distance", 0)
    last_dur   = last.get("moving_time", 0)
    last_hr    = last.get("average_heartrate", 0)
    last_maxhr = last.get("max_heartrate", 0)
    last_spd   = last.get("average_speed", 0)
    last_elev  = last.get("total_elevation_gain", 0)
    last_cad   = last.get("average_cadence", 0)
    last_date  = datetime.fromisoformat(last.get("start_date_local", "2026-01-01T00:00:00").replace("Z","")).strftime("%A %-d %B")

    if "run" in last_type.lower():
        last_detail = f"{last_dist/1000:.1f}km · tempo {fmt_pace(last_spd)} · gem. HS {int(last_hr) if last_hr else '?'} bpm · max HS {int(last_maxhr) if last_maxhr else '?'} bpm · cadans {int(last_cad*2) if last_cad else '?'} spm · hoogte {last_elev:.0f}m"
    elif "ride" in last_type.lower():
        last_detail = f"{last_dist/1000:.1f}km · {last_spd*3.6:.1f}km/u · gem. HS {int(last_hr) if last_hr else '?'} bpm · max HS {int(last_maxhr) if last_maxhr else '?'} bpm · cadans {int(last_cad) if last_cad else '?'} rpm · hoogte {last_elev:.0f}m"
    elif "swim" in last_type.lower():
        last_detail = f"{last_dist:.0f}m · tempo {fmt_swim_pace(last_spd)} · gem. HS {int(last_hr) if last_hr else '?'} bpm · duur {fmt_time(last_dur)}"
    else:
        last_detail = f"duur {fmt_time(last_dur)} · gem. HS {int(last_hr) if last_hr else '?'} bpm"

    # Overzicht laatste 5 activiteiten
    recent_lines = []
    for a in activities[1:6]:
        t    = a.get("type", "Workout")
        name = a.get("name", t)
        dist = a.get("distance", 0)
        dur  = a.get("moving_time", 0)
        hr   = a.get("average_heartrate", 0)
        spd  = a.get("average_speed", 0)
        date = datetime.fromisoformat(a["start_date_local"].replace("Z","")).strftime("%a %-d %b")
        if "run" in t.lower():
            detail = f"{dist/1000:.1f}km op {fmt_pace(spd)}, HS {int(hr) if hr else '?'} bpm"
        elif "ride" in t.lower():
            detail = f"{dist/1000:.1f}km op {spd*3.6:.1f}km/u, HS {int(hr) if hr else '?'} bpm"
        elif "swim" in t.lower():
            detail = f"{dist:.0f}m op {fmt_swim_pace(spd)}"
        else:
            detail = f"{int(dur//60)} min"
        recent_lines.append(f"- {date}: {name} ({t}) — {detail}")

    recent_text = "\n".join(recent_lines) if recent_lines else "Geen andere recente activiteiten"

    # ── Herstel-context uit Garmin health data ──
    health_text = ""
    if health and health.get("available"):
        lines = []
        if health.get("sleep_hours"):
            lines.append(f"- Slaap afgelopen nacht: {health['sleep_hours']}u" + (f" (score {health['sleep_score']}/100)" if health.get('sleep_score') else ""))
        if health.get("hrv_value"):
            lines.append(f"- HRV: {health['hrv_value']}ms ({health.get('hrv_status', 'onbekend')})")
        if health.get("resting_hr"):
            lines.append(f"- Rust hartslag vannacht: {health['resting_hr']} bpm")
        if health.get("body_battery") is not None:
            lines.append(f"- Body Battery: {health['body_battery']}/100")
        if health.get("steps"):
            lines.append(f"- Stappen gisteren: {health['steps']}")
        if lines:
            health_text = "\n\nHERSTEL- EN GEZONDHEIDSDATA (Garmin):\n" + "\n".join(lines)

    prompt = f"""Je bent een persoonlijke triatleetcoach van Jens van den Berg (71kg, 182cm), die traint voor de Halve Ironman Knokke op 6 september 2026. Schrijf een persoonlijke dagelijkse update in het Nederlands.

LAATSTE ACTIVITEIT ({last_date}):
Naam: {last_name}
Type: {last_type}
Data: {last_detail}

VORIGE ACTIVITEITEN (ter context):
{recent_text}

HUIDIGE FITNESSWAARDEN:
- VO2max: ~{stats['vo2max']} ml/kg/min (doel: 52+)
- FTP: {stats['ftp']}W ({round(stats['ftp']/71, 2)} W/kg)
- Max hartslag ooit gemeten: {stats['max_hr']} bpm
- Beste zwemtempo: {stats.get('best_swim') or '—'}
- Fietscadans gemiddeld: {stats.get('bike_cadence') or '—'} rpm
- Geschatte HIM eindtijd: {him['total_time']} (zwem {him['swim_time']} / fiets {him['bike_time']} / run {him['run_time']}){health_text}

SCHRIJF een persoonlijke update van MINIMUM 6 zinnen en MAXIMUM 8 zinnen. Structuur:
1. Analyseer de laatste activiteit CONCREET — noem de exacte cijfers (tempo, hartslag, cadans, hoogtemeters). Wat valt op? Is de hartslag lager dan verwacht bij dit tempo? Is de cadans verbeterd?
2. Vergelijk met de vorige activiteiten — zit hij in een goede lijn of is er iets dat opvalt?
3. Indien herstel-data beschikbaar is: koppel slaap/HRV/rust-HS aan trainingsbereidheid — is hij klaar voor een zware sessie, of moet hij voorzichtig zijn?
4. Koppel dit aan de HIM-voorbereiding — wat betekent dit concreet voor 6 september?
5. Geef één concrete, specifieke actietip voor de komende 2–3 dagen gebaseerd op de data.

Schrijf in vloeiende lopende tekst zonder opsomming of titels. Gebruik de exacte cijfers uit de data. Schrijf in de tweede persoon ("je")."""

    try:
        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 800,
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=30
        )
        response.raise_for_status()
        text = response.json()["content"][0]["text"].strip()
        now  = datetime.now().strftime("%-d %B %Y om %H:%M")
        meta = f"— Gegenereerd door Claude op {now} op basis van Strava data"
        return text, meta

    except Exception as e:
        print(f"   Claude API fout: {type(e).__name__}: {e}")
        # Log response if available
        try:
            print(f"   Response status: {response.status_code}")
            print(f"   Response body: {response.text[:200]}")
        except:
            pass
        return (
            f"Je staat er goed voor richting HIM Knokke. VO2max ~{stats['vo2max']} ml/kg/min, FTP {stats['ftp']}W. Blijf consistent trainen!",
            f"— Automatische fallback · {datetime.now().strftime('%-d %B %Y')}"
        )


def ai_update_already_done_today(html):
    """
    Checkt of de AI-coachingtekst vandaag al gegenereerd is, door de datum uit
    het bestaande 'ai-update-meta' veld te lezen. Voorkomt onnodige extra
    Anthropic API-aanroepen bij meerdere syncs per dag — de AI-tekst wordt
    bewust maar 1x per dag vernieuwd, de rest van de data (Strava, Garmin)
    elke sync.
    Retourneert (True, oude_tekst, oude_meta) als de tekst al van vandaag is,
    anders (False, None, None).
    """
    match = re.search(
        r'<div[^>]*id="ai-update-text"[^>]*>(.*?)</div>\s*<div[^>]*id="ai-update-meta"[^>]*>(.*?)</div>',
        html, flags=re.DOTALL
    )
    if not match:
        return False, None, None

    old_text, old_meta = match.group(1).strip(), match.group(2).strip()

    # Maandnamen NL → nummer, voor het parsen van "5 juli 2026 om 14:32"
    MONTHS_NL = {
        "januari": 1, "februari": 2, "maart": 3, "april": 4, "mei": 5, "juni": 6,
        "juli": 7, "augustus": 8, "september": 9, "oktober": 10, "november": 11, "december": 12,
    }
    date_match = re.search(r'(\d{1,2})\s+(\w+)\s+(\d{4})', old_meta)
    if not date_match:
        return False, None, None

    day, month_name, year = date_match.groups()
    month = MONTHS_NL.get(month_name.lower())
    if not month:
        return False, None, None

    try:
        old_date = datetime(int(year), month, int(day)).date()
    except ValueError:
        return False, None, None

    if old_date == datetime.now().date():
        return True, old_text, old_meta
    return False, None, None


def update_fitness_history(html, ftp, vo2, rhr, weight, backfill_points=None):
    """
    Werkt de FITNESS_HISTORY array in de HTML bij met een nieuw datapunt voor
    vandaag (of overschrijft het datapunt van vandaag als die er al is — bij
    meerdere syncs op dezelfde dag blijft er dus maar 1 datapunt per dag over).
    Voedt de dashboard-evolutiegrafiek (FTP, VO2max, rust-HS, gewicht over tijd).

    backfill_points (optioneel): lijst van {date, rhr, vo2} dicts uit
    backfill_fitness_history(), wordt alleen gebruikt als de bestaande
    geschiedenis nog kort is (zie main()). FTP en gewicht worden voor deze
    historische punten lineair geïnterpoleerd tussen een lichtjes lager
    startpunt en de huidige waarde, omdat daarvoor geen betrouwbare
    dag-per-dag Garmin-bron beschikbaar is via deze library.

    De array staat in de HTML als JavaScript-object-literal syntax (unquoted
    keys, single-quoted strings) — geen geldige JSON. We parsen elk object
    daarom met een gerichte regex in plaats van een JSON-parser te misbruiken.
    """
    today_iso = datetime.now().strftime("%Y-%m-%d")

    match = re.search(r'const FITNESS_HISTORY = (\[.*?\]);', html, flags=re.DOTALL)
    history = []
    if match:
        raw = match.group(1)
        entry_pattern = re.compile(
            r"date:\s*'([^']+)'\s*,\s*ftp:\s*([\d.]+)\s*,\s*vo2:\s*([\d.]+)\s*,\s*rhr:\s*([\d.]+)\s*,\s*weight:\s*([\d.]+)"
        )
        for d, f, v, r, w in entry_pattern.findall(raw):
            history.append({
                "date": d, "ftp": float(f), "vo2": float(v),
                "rhr": float(r), "weight": float(w),
            })
        if not history:
            print("  ⚠️ FITNESS_HISTORY array gevonden maar leeg of onverwacht formaat — start opnieuw")

    # ── Eenmalige backfill verwerken, indien meegegeven ──
    if backfill_points:
        existing_dates = {h["date"] for h in history}
        n = len(backfill_points)
        # Lichte interpolatie: FTP en gewicht starten ~3% resp. ~1.5% lager dan vandaag
        # en groeien lineair naar de huidige waarde toe — een realistischer beeld dan
        # een volledig vlakke lijn, zonder cijfers te verzinnen die niet onderbouwd zijn.
        ftp_start    = round(ftp * 0.94)
        weight_start = round(weight * 1.012, 1)
        for i, bp in enumerate(backfill_points):
            if bp["date"] in existing_dates or bp["date"] == today_iso:
                continue
            frac = i / max(n - 1, 1)
            interp_ftp    = round(ftp_start + (ftp - ftp_start) * frac)
            interp_weight = round(weight_start + (weight - weight_start) * frac, 1)
            history.append({
                "date":   bp["date"],
                "ftp":    interp_ftp,
                "vo2":    bp.get("vo2") or vo2,
                "rhr":    bp.get("rhr") or rhr,
                "weight": interp_weight,
            })

    # Verwijder een eventueel bestaand datapunt van vandaag, voeg het nieuwe toe
    history = [h for h in history if h.get("date") != today_iso]
    history.append({
        "date":   today_iso,
        "ftp":    ftp,
        "vo2":    vo2,
        "rhr":    rhr,
        "weight": weight,
    })
    history.sort(key=lambda h: h["date"])
    # Bewaar maximaal de laatste 60 datapunten zodat het bestand niet onbeperkt groeit
    history = history[-60:]

    def fmt_num(v):
        # Toon hele getallen zonder onnodige .0, decimalen blijven behouden
        return str(int(v)) if float(v) == int(v) else str(v)

    new_array = "[\n" + ",\n".join(
        f"  {{ date: '{h['date']}', ftp: {fmt_num(h['ftp'])}, vo2: {fmt_num(h['vo2'])}, "
        f"rhr: {fmt_num(h['rhr'])}, weight: {fmt_num(h['weight'])} }}"
        for h in history
    ) + "\n]"
    new_block = f"const FITNESS_HISTORY = {new_array};"

    result = re.sub(r'const FITNESS_HISTORY = \[.*?\];', new_block, html, count=1, flags=re.DOTALL)
    if result != html:
        print(f"  ✓ FITNESS_HISTORY bijgewerkt ({len(history)} datapunten, vandaag: ftp={ftp} vo2={vo2} rhr={rhr} gewicht={weight})")
    else:
        print(f"  ✗ FITNESS_HISTORY NIET GEVONDEN in HTML")
    return result


def main():
    print("🔄 Strava token ophalen...")
    token = get_access_token()

    print("👤 Atleet ophalen...")
    athlete = get_athlete(token)
    print(f"   → {athlete.get('firstname')} {athlete.get('lastname')}")

    print("🏃 Activiteiten ophalen...")
    activities = get_activities(token, per_page=30)
    print(f"   → {len(activities)} activiteiten gevonden")

    stats = compute_stats(activities, athlete)
    print(f"   → Max HS: {stats['max_hr']} · VO2max: {stats['vo2max']} · FTP: {stats['ftp']}W")

    print("⌚ Garmin health data ophalen...")
    health = get_garmin_health_data()

    # Lees de huidige site
    with open("index.html", "r", encoding="utf-8") as f:
        html = f.read()

    # Genereer de drie aparte secties (Garmin-data, Activiteiten, Fitnesswaarden)
    sections = build_strava_section(activities, stats, athlete, health)

    def replace_section(html_str, start_marker, end_marker, new_content, label):
        start = html_str.find(start_marker)
        end   = html_str.find(end_marker)
        if start == -1 or end == -1:
            print(f"❌ {label} sectie niet gevonden in HTML (marker '{start_marker}' of '{end_marker}' ontbreekt)")
            return html_str, False
        return html_str[:start] + new_content + "\n\n" + html_str[end:], True

    new_html = html
    new_html, ok1 = replace_section(
        new_html, "<!-- ── GARMIN DATA ── -->", '<div class="divider-line"></div>\n\n<!-- ── TRAININGSPLAN ── -->',
        sections["garmin"] + "\n\n", "Garmin data"
    )
    new_html, ok2 = replace_section(
        new_html, "<!-- ── RECENTE ACTIVITEITEN (PER WEEK GEGROEPEERD) ── -->", '<div class="divider-line"></div>\n\n<!-- ── FITNESSWAARDEN & EINDTIJD ── -->',
        sections["activities"] + "\n\n", "Recente activiteiten"
    )
    new_html, ok3 = replace_section(
        new_html, "<!-- ── FITNESSWAARDEN & EINDTIJD ── -->", "<!-- ── FOOTER ── -->",
        sections["fitness"], "Fitnesswaarden"
    )

    if not (ok1 and ok2 and ok3):
        print("❌ Niet alle secties konden bijgewerkt worden — controleer of index.html de juiste markers bevat")
        return

    # Update alle waarden doorheen de volledige pagina
    ftp  = stats["ftp"]
    wkg  = round(ftp / 71, 2)
    mhr  = stats["max_hr"]
    vo2  = stats["vo2max"] or 53
    swim = stats["best_swim"] or "1:40"

    # ── Garmin overschrijft VO2max (directe schatting) en max HS (alleen als hoger) ──
    # Max HS uit een racewedstrijd/test is betrouwbaarder dan een 24u-gemiddelde — daarom
    # nooit naar beneden bijstellen, alleen omhoog als Garmin een hogere piek meet.
    if health and health.get("vo2max"):
        vo2 = health["vo2max"]
        print(f"  🫁 VO2max overgenomen van Garmin: {vo2}")
    if health and health.get("max_hr") and health["max_hr"] > mhr:
        mhr = health["max_hr"]
        print(f"  💓 Max HS opgehoogd via Garmin: {mhr}")
    if health and health.get("resting_hr"):
        stats["rest_hr"] = health["resting_hr"]
        print(f"  ❤️ Rust HS overgenomen van Garmin: {stats['rest_hr']}")

    # HIM eindtijd berekenen (nodig voor AI update)
    him_time = estimate_him_time(activities)

    already_done, cached_text, cached_meta = ai_update_already_done_today(html)
    if already_done:
        print("🤖 AI update vandaag al gegenereerd — hergebruik bestaande tekst (geen extra API-kosten)")
        ai_text, ai_meta = cached_text, cached_meta
    else:
        print("🤖 AI update genereren...")
        ai_text, ai_meta = generate_ai_update(activities, stats, him_time, health)
        print(f"   → {ai_text[:60]}...")

    import re

    # Herstel- en prestatie-kaarten worden nu rechtstreeks binnen build_strava_section()
    # gegenereerd (zie new_section hierboven), dus geen losse injectie meer nodig.

    # ── Update STRAVA_DATA JS object zodat progressiebalken live werken ──
    swim_raw = stats.get("best_swim") or "1:40"
    swim_val = swim_raw.replace("/100m", "").strip()
    run_raw  = stats.get("best_run_pace") or "6:16"
    run_val  = run_raw.replace("/km", "").strip()
    rcad_val = stats.get("run_cadence") or 162
    bcad_val = stats.get("bike_cadence") or 77

    new_strava_data = f"""const STRAVA_DATA = {{
  ftp:       {ftp},
  wkg:       {wkg},
  bcad:      {bcad_val},
  rcad:      {rcad_val},
  runpace:   '{run_val}',
  swim:      '{swim_val}',
  vo2:       {vo2},
  max_hr:    {mhr},
}};"""

    new_html = re.sub(
        r'const STRAVA_DATA = \{[^}]+\};',
        new_strava_data,
        new_html,
        flags=re.DOTALL
    )

    # ── Fitness-geschiedenis bijwerken (voedt de dashboard-evolutiegrafiek) ──
    weight_for_history = stats.get("weight") or 71
    rhr_for_history     = stats.get("rest_hr") or 49

    # Eenmalige backfill: alleen als de bestaande geschiedenis nog kort is (≤2
    # datapunten), zodat dit niet bij elke sync opnieuw een hele reeks Garmin-
    # aanroepen doet. Zodra er voldoende natuurlijke geschiedenis is opgebouwd
    # via de dagelijkse syncs, slaat dit blok zichzelf vanzelf permanent over.
    existing_count = len(re.findall(r"date:\s*'[\d-]+'", new_html))
    backfill_points = None
    if existing_count <= 2:
        print("📅 Weinig fitness-geschiedenis gevonden — eenmalige backfill ophalen...")
        backfill_points = backfill_fitness_history(days=30)

    new_html = update_fitness_history(new_html, ftp, vo2, rhr_for_history, weight_for_history, backfill_points)

    # Injecteer AI update tekst — HTML-safe verwerken
    # Bij hergebruik (already_done) is ai_text al de kant-en-klare HTML uit een
    # vorige sync vandaag — die mag NIET opnieuw geëscaped worden, anders
    # ontstaat dubbele escaping (&amp;amp; etc.). Alleen verse tekst van de
    # Anthropic API moet door de escape + <p>-wrap stap.
    if already_done:
        ai_text_html = ai_text  # al volledig voorbereide HTML uit het bestaande bestand
    else:
        ai_text_html = ai_text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        ai_text_html = ai_text_html.replace('\n\n', '</p><p style="margin-top:.8rem">').replace('\n', ' ')
        ai_text_html = '<p>' + ai_text_html + '</p>'

    # ── AI update tekst injecteren ──
    # Robuuste aanpak: zoek op volledige openingstag inclusief newlines
    import re as _re

    def inject_div_content(html_str, div_id, new_content):
        """Vervang inhoud van een div met gegeven id."""
        pattern = rf'(<div[^>]*id="{div_id}"[^>]*>)(.*?)(</div>)'
        replacement = rf'\g<1>{new_content}\3'
        result = _re.sub(pattern, replacement, html_str, count=1, flags=_re.DOTALL)
        if result != html_str:
            print(f"   ✓ {div_id} bijgewerkt ({len(new_content)} chars)")
        else:
            print(f"   ✗ {div_id} NIET GEVONDEN")
        return result

    new_html = inject_div_content(new_html, 'ai-update-text', ai_text_html)
    new_html = inject_div_content(new_html, 'ai-update-meta', ai_meta)


    # ── Hero stats — ID-gebaseerde vervanging (betrouwbaar) ──
    new_html = re.sub(
        r'(<div class="hstat-val ac" id="hero-ftp">)\d+(<small)',
        rf'\g<1>{ftp}\2', new_html
    )
    new_html = re.sub(
        r'(<div class="hstat-val" id="hero-wkg">)[\d,\.]+(<)',
        rf'\g<1>{wkg}\2', new_html
    )
    new_html = re.sub(
        r'(<div class="hstat-val bl" id="hero-swim">)[^<]+(</div>)',
        rf'\g<1>{swim}\2', new_html
    )
    new_html = re.sub(
        r'(<div class="hstat-val gr" id="hero-vo2">)~?\d+(</div>)',
        rf'\g<1>~{vo2}\2', new_html
    )
    new_html = re.sub(
        r'(<div class="hstat-val" id="hero-mhr">)[\d\.]+(<\/div>)',
        rf'\g<1>{mhr}\2', new_html
    )
    # Rust HS — uit Strava athlete profiel indien beschikbaar, anders 50 bpm
    rhr = stats.get("rest_hr") or 49
    new_html = re.sub(
        r'(<div class="hstat-val gr" id="hero-rhr">)\d+(</div>)',
        rf'\g<1>{rhr}\2', new_html
    )

    # ── Progressie & Targets — metric kaartjes (ID-gebaseerd) ──
    new_html = re.sub(
        r'(id="mhc-ftp">)\d+(\s*W)',
        rf'\g<1>{ftp}\2', new_html
    )
    new_html = re.sub(
        r'(id="mhc-wkg">)[\d,\.]+(\s*W/kg)',
        rf'\g<1>{wkg}\2', new_html
    )
    new_html = re.sub(
        r'(id="mhc-vo2">)~?\d+(<)',
        rf'\g<1>{vo2}\2', new_html
    )
    new_html = re.sub(
        r'(id="mhc-bcad">)\d+(\s*rpm)',
        rf'\g<1>{stats.get("bike_cadence") or 77}\2', new_html
    )
    new_html = re.sub(
        r'(id="mhc-swim">)[^<]+(<)',
        rf'\g<1>{swim}\2', new_html
    )
    new_html = re.sub(
        r'(id="mhc-runpace">)[^<]+(<)',
        rf'\g<1>{stats.get("best_run_pace") or "6:16/km"}\2', new_html
    )
    new_html = re.sub(
        r'(id="mhc-mhr">)\d+(\.\d+)?( bpm)',
        rf'\g<1>{mhr}\3', new_html
    )
    new_html = re.sub(
        r'(id="mhc-rhr">)\d+( bpm)',
        rf'\g<1>{rhr}\2', new_html
    )
    rcad = stats.get("run_cadence") or 162
    new_html = re.sub(
        r'(id="mhc-rcad">)\d+( spm)',
        rf'\g<1>{rcad}\2', new_html
    )
    # ── VO2max ring — uitsluitend Garmin, geen eigen berekening meer ──
    new_html = re.sub(
        r'(id="ring-vo2-aw">)[\d,\.]+(<)',
        rf'\g<1>{vo2}\2', new_html
    )
    new_html = re.sub(
        r'(id="ring-vo2-aw-intro">)~?[\d,\.]+(<)',
        rf'\g<1>{vo2}\2', new_html
    )
    awn_offset = round(289 - (min(vo2, 70) / 70 * 289))
    new_html = re.sub(
        r'(id="ring-svg-aw"[^/]*)stroke-dashoffset="\d+"',
        rf'\g<1>stroke-dashoffset="{awn_offset}"', new_html
    )
    # ── Fitnesswaarden footer label ──
    weight_val = stats.get("weight") or 71
    new_html = re.sub(
        r'(id="disp-footer-info">)[^<]+(<)',
        rf'\g<1>{weight_val} kg · 182 cm · FTP {ftp}W ({wkg} W/kg) · Halve Ironman Knokke 6 september 2026\2', new_html
    )

    # ── Progressie balk VO2max huidige waarde ──
    new_html = re.sub(
        r'(<span class="goal-now">)~?\d+(</span><span class="goal-arrow">→</span><span class="goal-target">52\+)',
        rf'\g<1>~{vo2}\2', new_html
    )

    # ── Intro tekst ──
    new_html = re.sub(
        r'(VO2max van )~?\d+( ml/kg/min)',
        rf'\g<1>~{vo2}\2', new_html
    )

    # ── JSONBin credentials injecteren ──
    jsonbin_bin_id  = os.environ.get("JSONBIN_BIN_ID", "") or os.environ.get("JSON_BIN_ID", "")
    jsonbin_api_key = os.environ.get("JSONBIN_API_KEY", "")
    if jsonbin_bin_id and jsonbin_api_key:
        new_html = new_html.replace("'JSONBIN_BIN_ID_PLACEHOLDER'",  f"'{jsonbin_bin_id}'")
        new_html = new_html.replace("'JSONBIN_API_KEY_PLACEHOLDER'", f"'{jsonbin_api_key}'")
        print(f"   JSONBin credentials geïnjecteerd (bin: {jsonbin_bin_id[:8]}...)")
    else:
        print(f"   ⚠️  JSONBIN credentials niet gevonden — sync uitgeschakeld")

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(new_html)

    print(f"✅ index.html bijgewerkt! VO2max: {vo2} · FTP: {ftp}W · Max HS: {mhr} bpm")

if __name__ == "__main__":
    main()
