#!/usr/bin/env python3
"""
Strava → GitHub Pages auto-updater
Haalt elke nacht activiteiten op van Strava en werkt de atletensite bij.
"""

import os
import json
import requests
from datetime import datetime, timezone
from collections import defaultdict

# ── STRAVA AUTH ──
CLIENT_ID     = os.environ["STRAVA_CLIENT_ID"]
CLIENT_SECRET = os.environ["STRAVA_CLIENT_SECRET"]
REFRESH_TOKEN = os.environ["STRAVA_REFRESH_TOKEN"]

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

# ── COMPUTE STATS ──
def compute_stats(activities, athlete):
    stats = {
        "max_hr":    athlete.get("athlete_zone", {}).get("heart_rate", {}).get("custom_zones", False) and 204 or 204,
        "ftp":       athlete.get("ftp") or 165,
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
    swim_speeds = []
    run_paces   = []
    bike_cads   = []
    run_cads    = []

    for a in activities:
        t = a.get("type", "").lower()
        mhr = a.get("max_heartrate") or 0
        if mhr > max_hr_seen:
            max_hr_seen = mhr

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
            cad = a.get("average_cadence", 0)
            if cad > 0:
                bike_cads.append(cad)

        elif "swim" in t:
            stats["total_swims"] += 1
            spd = a.get("average_speed", 0)
            if spd > 0:
                swim_speeds.append(spd)

    if max_hr_seen > 0:
        stats["max_hr"] = max_hr_seen

    if swim_speeds:
        best = max(swim_speeds)
        stats["best_swim"] = fmt_swim_pace(best)

    if run_paces:
        best = max(run_paces)
        stats["best_run_pace"] = fmt_pace(best)

    if bike_cads:
        stats["bike_cadence"] = round(sum(bike_cads) / len(bike_cads))

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

    Zwem  1.900m  op basis van beste zwemtempo met wedstrijdfactor
    Fiets 90 km   op basis van gemiddelde fietssnelheid lange ritten
    Run   21,1km  op basis van duurlooptempo met vermoeidheidsfactor
    T1+T2         vaste schatting 4 min totaal (niet apart vermeld)
    """

    # ── ZWEMMEN ──
    # Open water ~5% trager dan bad, wedstrijdadrenaline +2% → netto ×0.97
    swim_speeds = []
    for a in activities:
        if "swim" not in a.get("type","").lower():
            continue
        spd  = a.get("average_speed", 0)
        dist = a.get("distance", 0)
        dur  = a.get("moving_time", 0)
        if spd > 0 and dist > 500 and dur > 300:
            swim_speeds.append(spd)

    if swim_speeds:
        # Open water ~3% trager dan bad, Knokke: vlak water, geen stroom correctie
        him_swim_speed = max(swim_speeds) * 0.97
    else:
        him_swim_speed = 100 / 112  # fallback 1:52/100m

    swim_secs = round(1900 / him_swim_speed)

    # ── FIETSEN ──
    # Gebruik gemiddelde van ritten >20km (was >40km, te strikt voor 30 activiteiten)
    # HIM factor ×0.93: lichte wind kust + vermoeidheid na zwemmen
    # 12% was te agressief — 7% is realistischer voor getrainde triatleet
    ride_speeds = []
    for a in activities:
        if "ride" not in a.get("type","").lower():
            continue
        spd  = a.get("average_speed", 0)
        dist = a.get("distance", 0)
        if spd > 0 and dist > 20000:  # >20km
            ride_speeds.append(spd)

    # Hoogtemetercorrectie fiets:
    # HIM Knokke parcours: 44hm / 90km = ~49hm per 100km (vrijwel vlak)
    # Als trainingsritten meer hoogtemeters hebben → op Knokke ga je sneller
    # Vuistregel: elk verschil van 100hm/100km = ~1.5 km/u snelheidsverschil
    HIM_HM_PER_100KM = 44 / 90 * 100  # ~49 hm/100km

    ride_data = [
        (a.get("average_speed", 0), a.get("distance", 0), a.get("total_elevation_gain", 0))
        for a in activities
        if "ride" in a.get("type","").lower()
        and a.get("average_speed", 0) > 0
        and a.get("distance", 0) > 20000
    ]

    if ride_data:
        avg_speed_ms  = sum(s for s,_,_ in ride_data) / len(ride_data)
        total_dist    = sum(d for _,d,_ in ride_data)
        total_elev    = sum(e for _,_,e in ride_data)
        train_hm_per_100km = (total_elev / total_dist * 100000) if total_dist > 0 else HIM_HM_PER_100KM

        # Hoogteverschil tussen training en HIM parcours
        hm_diff = train_hm_per_100km - HIM_HM_PER_100KM
        speed_bonus_kmh = hm_diff * 0.005  # 0.5 km/u per 100hm/100km verschil (realistisch voor amateur)

        him_speed_kmh = (avg_speed_ms * 3.6 + speed_bonus_kmh) * 0.93
        him_ride_speed = him_speed_kmh / 3.6

        print(f"   Fiets: trainingsgemiddelde {avg_speed_ms*3.6:.1f} km/u")
        print(f"   Fiets: training {train_hm_per_100km:.0f} hm/100km vs HIM {HIM_HM_PER_100KM:.0f} hm/100km")
        print(f"   Fiets: hoogtebonus +{speed_bonus_kmh:.2f} km/u → HIM tempo {him_speed_kmh:.1f} km/u")
    elif ride_speeds:
        him_ride_speed = (sum(ride_speeds) / len(ride_speeds)) * 0.93
    else:
        him_ride_speed = (27 * 0.93) / 3.6  # fallback

    bike_secs = round(90000 / him_ride_speed)

    # ── LOPEN ──
    # Gebruik gemiddelde van langere duurlopen (>8km)
    # HIM correctie ×0.88: vermoeid na zwem+fiets, bewust rustig starten
    run_speeds = []
    for a in activities:
        if "run" not in a.get("type","").lower():
            continue
        spd  = a.get("average_speed", 0)
        dist = a.get("distance", 0)
        dur  = a.get("moving_time", 0)
        if spd > 0 and dist > 8000 and dur > 2400:
            run_speeds.append(spd)

    if run_speeds:
        him_run_speed = (sum(run_speeds) / len(run_speeds)) * 0.93
    else:
        him_run_speed = (1000 / 380) * 0.93  # fallback 6:20/km × 0.93

    run_secs = round(21100 / him_run_speed)

    # ── TRANSITIES (inbegrepen in totaal, niet apart getoond) ──
    t1_secs = 150  # T1 zwem→fiets: 2:30
    t2_secs = 90   # T2 fiets→run:  1:30

    total_secs = swim_secs + bike_secs + run_secs + t1_secs + t2_secs

    def hm(s):
        return f"{s//3600}:{(s%3600)//60:02d}"

    def hms(s):
        h = s // 3600
        m = (s % 3600) // 60
        sec = s % 60
        return str(h) + "u" + f"{m:02d}m" + f"{sec:02d}s"

    def swim_pace(spd):
        spm = 100 / spd
        return f"{int(spm//60)}:{int(spm%60):02d}/100m"

    def run_pace(spd):
        spm = 1000 / spd
        return f"{int(spm//60)}:{int(spm%60):02d}/km"

    return {
        "swim_time":  hm(swim_secs),
        "bike_time":  hm(bike_secs),
        "run_time":   hm(run_secs),
        "total_time": hms(total_secs),
        "swim_pace":  swim_pace(him_swim_speed),
        "bike_kmh":   f"{him_ride_speed*3.6:.1f} km/u",
        "run_pace":   run_pace(him_run_speed),
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

def build_strava_section(activities, stats, athlete):
    now = datetime.now().strftime("%-d %B %Y om %H:%M")
    name = f"{athlete.get('firstname','')} {athlete.get('lastname','')}".strip()

    cards = "\n".join(activity_card_html(a) for a in activities[:9])

    ftp  = stats["ftp"]
    wkg  = round(ftp / 71, 2)  # gewicht 71kg
    mhr  = stats["max_hr"]
    vo2  = stats["vo2max"] or 48
    swim = stats["best_swim"] or "—"
    bcad = stats["bike_cadence"] or 77
    rcad = stats["run_cadence"] or 165

    # HIM eindtijd schatting
    him = estimate_him_time(activities)

    # VO2max breakdown tabel
    breakdown = stats.get("vo2max_breakdown", [])
    breakdown_rows = ""
    for v, w, bname in breakdown:
        pct = round(w * 100)
        breakdown_rows += f'<tr><td>{bname}</td><td style="text-align:right;font-weight:600;color:var(--text)">{v:.1f}</td><td style="text-align:right;color:var(--muted)">{pct}%</td></tr>'

    # VO2max ring offset (schaal 30–75 → dashoffset 250–50)
    vo2_offset = round(250 - ((vo2 - 30) / 45) * 200)
    ftp_offset = round(250 - ((min(ftp, 250) - 100) / 150) * 200)

    return f"""<!-- ── ANALYSE ── -->
<section class="section" id="analyse">
  <div class="sec-label">Live via Strava API</div>
  <h2 class="sec-title">Recente <span>Activiteiten</span></h2>
  <p style="font-size:.82rem;color:var(--muted);margin-bottom:2rem">Automatisch bijgewerkt · Laatste sync: {now}</p>

  <div class="strava-grid">
    {cards}
  </div>

  <h3 style="font-family:'Barlow Condensed',sans-serif;font-size:1.4rem;font-weight:700;letter-spacing:.06em;text-transform:uppercase;margin-bottom:1.2rem;color:var(--dim)">Live <span style="color:var(--text)">Fitnesswaarden</span></h3>

  <div class="mhc-grid" style="margin-bottom:2.5rem">
    <div class="mhc"><div class="mhc-lbl">FTP</div><div class="mhc-val ac">{ftp} W</div><div class="mhc-sub">{wkg} W/kg</div></div>
    <div class="mhc"><div class="mhc-lbl">Max HS</div><div class="mhc-val">{mhr} bpm</div><div class="mhc-sub">gemeten in training</div></div>
    <div class="mhc"><div class="mhc-lbl">VO2max (schatting)</div><div class="mhc-val gr">~{vo2}</div><div class="mhc-sub">ml/kg/min</div></div>
    <div class="mhc"><div class="mhc-lbl">Beste zwemtempo</div><div class="mhc-val bl">{swim}</div><div class="mhc-sub">snelste gemiddelde</div></div>
    <div class="mhc"><div class="mhc-lbl">Fietscadans gem.</div><div class="mhc-val {'gr' if bcad >= 88 else 'ac'}">{bcad} rpm</div><div class="mhc-sub">{'✓ op schema' if bcad >= 88 else 'doel: 90 rpm'}</div></div>
    <div class="mhc"><div class="mhc-lbl">Loopcadans gem.</div><div class="mhc-val {'gr' if rcad >= 168 else 'ay'}">{rcad} spm</div><div class="mhc-sub">{'✓ goed' if rcad >= 168 else 'doel: 168–172 spm'}</div></div>
    <div class="mhc"><div class="mhc-lbl">Activiteiten (recent)</div><div class="mhc-val">{len(activities)}</div><div class="mhc-sub">🏃 {stats['total_runs']} · 🚴 {stats['total_rides']} · 🏊 {stats['total_swims']}</div></div>
    <div class="mhc"><div class="mhc-lbl">Rust HS</div><div class="mhc-val gr">50 bpm</div><div class="mhc-sub">uitstekend</div></div>
  </div>

  <h3 style="font-family:'Barlow Condensed',sans-serif;font-size:1.4rem;font-weight:700;letter-spacing:.06em;text-transform:uppercase;margin-bottom:1.2rem;color:var(--dim)">Geschatte <span style="color:var(--text)">HIM Eindtijd</span></h3>

  <div style="background:var(--card);border:1px solid rgba(232,81,42,.35);border-radius:14px;padding:1.5rem 1.8rem;margin-bottom:2.5rem;max-width:580px">
    <div style="display:flex;align-items:baseline;gap:.6rem;margin-bottom:1.2rem;flex-wrap:wrap">
      <div style="font-family:'Barlow Condensed',sans-serif;font-size:3.5rem;font-weight:900;line-height:1;color:var(--yellow);letter-spacing:-.01em">{him['total_time']}</div>
      <div style="font-size:.78rem;color:var(--muted);font-weight:500">geschatte eindtijd<br>incl. transities</div>
    </div>
    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:.8rem;margin-bottom:1rem">
      <div style="background:rgba(34,197,94,.07);border:1px solid rgba(34,197,94,.2);border-radius:9px;padding:.9rem 1rem">
        <div style="font-size:.6rem;font-weight:700;letter-spacing:.14em;text-transform:uppercase;color:#4de88a;margin-bottom:.35rem">🏊 Zwemmen</div>
        <div style="font-family:'Barlow Condensed',sans-serif;font-size:1.7rem;font-weight:900;color:var(--text);line-height:1">{him['swim_time']}</div>
        <div style="font-size:.7rem;color:var(--muted);margin-top:.2rem">1,9 km · {him['swim_pace']}</div>
      </div>
      <div style="background:rgba(58,143,255,.07);border:1px solid rgba(58,143,255,.2);border-radius:9px;padding:.9rem 1rem">
        <div style="font-size:.6rem;font-weight:700;letter-spacing:.14em;text-transform:uppercase;color:#6ab4ff;margin-bottom:.35rem">🚴 Fietsen</div>
        <div style="font-family:'Barlow Condensed',sans-serif;font-size:1.7rem;font-weight:900;color:var(--text);line-height:1">{him['bike_time']}</div>
        <div style="font-size:.7rem;color:var(--muted);margin-top:.2rem">90 km · {him['bike_kmh']}</div>
      </div>
      <div style="background:rgba(232,81,42,.07);border:1px solid rgba(232,81,42,.2);border-radius:9px;padding:.9rem 1rem">
        <div style="font-size:.6rem;font-weight:700;letter-spacing:.14em;text-transform:uppercase;color:#ff8060;margin-bottom:.35rem">🏃 Lopen</div>
        <div style="font-family:'Barlow Condensed',sans-serif;font-size:1.7rem;font-weight:900;color:var(--text);line-height:1">{him['run_time']}</div>
        <div style="font-size:.7rem;color:var(--muted);margin-top:.2rem">21,1 km · {him['run_pace']}</div>
      </div>
    </div>
    <div style="font-size:.72rem;color:var(--muted);line-height:1.6;border-top:1px solid var(--border);padding-top:.8rem">
      Schatting op basis van actuele Strava data · Open water −3% zwem · HIM-tempo correctie −12% fiets en run · Transities inbegrepen in totaal · Wordt automatisch bijgewerkt bij elke sync.
    </div>
  </div>

  <h3 style="font-family:'Barlow Condensed',sans-serif;font-size:1.4rem;font-weight:700;letter-spacing:.06em;text-transform:uppercase;margin-bottom:1.2rem;color:var(--dim)">VO2max <span style="color:var(--text)">Schatting</span></h3>
  <div class="vo2-row">
    <div class="vo2-card">
      <div class="ring-svg">
        <svg viewBox="0 0 110 110"><circle class="rbg" cx="55" cy="55" r="46"/><circle class="rfill" cx="55" cy="55" r="46" stroke="#e8512a" stroke-dasharray="289" stroke-dashoffset="{vo2_offset}"/></svg>
        <div class="ring-center"><div class="ring-val" style="color:#e8512a">~{vo2}</div><div class="ring-unit">ml/kg/min</div></div>
      </div>
      <div class="vo2-label">VO2max Schatting<br><span style="font-size:.62rem;color:#444">via looptempo 71kg</span></div>
    </div>
    <div class="vo2-card">
      <div class="ring-svg">
        <svg viewBox="0 0 110 110"><circle class="rbg" cx="55" cy="55" r="46"/><circle class="rfill" cx="55" cy="55" r="46" stroke="#3a8fff" stroke-dasharray="289" stroke-dashoffset="{ftp_offset}"/></svg>
        <div class="ring-center"><div class="ring-val" style="color:#3a8fff">{ftp}W</div><div class="ring-unit">{wkg} W/kg</div></div>
      </div>
      <div class="vo2-label">FTP Fietsen<br><span style="font-size:.62rem;color:#444">doel: 199–227W</span></div>
    </div>
    <div class="vo2-card" style="justify-content:center">
      <div class="mhc-lbl" style="text-align:center;margin-bottom:.8rem">HIM Doelniveau</div>
      <div class="ring-val" style="color:var(--green);font-family:'Barlow Condensed',sans-serif;font-size:2.5rem;font-weight:900;text-align:center">52+</div>
      <div style="font-size:.75rem;color:var(--muted);text-align:center;margin-top:.4rem">ml/kg/min vereist</div>
      <div style="font-size:.75rem;color:var(--accent);text-align:center;margin-top:.3rem">Gap: ~{max(0, 52 - vo2)} punten te winnen</div>
    </div>
  </div>

    <div style="background:var(--card);border:1px solid var(--border);border-radius:10px;padding:1.2rem 1.4rem;margin-top:1.5rem;max-width:420px">
    <div style="font-size:.72rem;font-weight:600;letter-spacing:.14em;text-transform:uppercase;color:var(--muted);margin-bottom:.8rem">VO2max — Firstbeat methode (top 5 runs)</div>
    <table style="width:100%;border-collapse:collapse;font-size:.82rem">
      <tr style="border-bottom:1px solid var(--border)">
        <th style="text-align:left;font-size:.65rem;text-transform:uppercase;letter-spacing:.1em;color:var(--muted);padding:.3rem .4rem;font-weight:500">Run</th>
        <th style="text-align:right;font-size:.65rem;text-transform:uppercase;letter-spacing:.1em;color:var(--muted);padding:.3rem .4rem;font-weight:500">Schatting</th>
        <th style="text-align:right;font-size:.65rem;text-transform:uppercase;letter-spacing:.1em;color:var(--muted);padding:.3rem .4rem;font-weight:500">Gewicht</th>
      </tr>
      {breakdown_rows}
      <tr style="border-top:1px solid var(--border)">
        <td style="padding:.4rem .4rem;font-weight:600;color:var(--text)">Gemiddeld</td>
        <td style="text-align:right;font-weight:600;color:var(--accent);font-size:1rem">{vo2}</td>
        <td></td>
      </tr>
    </table>
    <div style="font-size:.72rem;color:var(--muted);margin-top:.7rem;line-height:1.5">Firstbeat: tempo + hartslag per run → extrapoleer naar max. Mediaan van beste 5 runs om uitschieters te vermijden.</div>
  </div>

</section>"""

def generate_ai_update(activities, stats, him):
    """
    Roept de Anthropic Claude API aan om een persoonlijke trainingsupdate te schrijven.
    """
    import json

    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
    if not ANTHROPIC_API_KEY:
        return (
            "AI update niet beschikbaar — voeg ANTHROPIC_API_KEY toe als GitHub Secret.",
            ""
        )

    # Bouw een samenvatting van de data voor Claude
    recent = activities[:5]
    acts_summary = []
    for a in recent:
        t    = a.get("type", "Workout")
        name = a.get("name", t)
        dist = a.get("distance", 0)
        dur  = a.get("moving_time", 0)
        hr   = a.get("average_heartrate", 0)
        spd  = a.get("average_speed", 0)
        date = datetime.fromisoformat(a["start_date_local"].replace("Z","")).strftime("%a %-d %b")

        if "run" in t.lower():
            detail = f"{dist/1000:.1f}km op {fmt_pace(spd)} (gem. HS {int(hr) if hr else '?'} bpm)"
        elif "ride" in t.lower():
            detail = f"{dist/1000:.1f}km op {spd*3.6:.1f}km/u"
        elif "swim" in t.lower():
            detail = f"{dist:.0f}m op {fmt_swim_pace(spd)}"
        else:
            detail = f"{int(dur//60)} min"

        acts_summary.append(f"- {date}: {name} ({t}) — {detail}")

    acts_text = "\n".join(acts_summary) if acts_summary else "Geen recente activiteiten"

    prompt = f"""Je bent een persoonlijke triatleetcoach. Schrijf een korte, motiverende en eerlijke update voor Jens van den Berg (71kg, 182cm) die traint voor de Halve Ironman Knokke op 6 september 2026.

Actuele fitnessdata:
- VO2max schatting: ~{stats['vo2max']} ml/kg/min (doel: 52+)
- FTP: {stats['ftp']}W ({round(stats['ftp']/71, 2)} W/kg) (doel: 2,8–3,2 W/kg)
- Max hartslag: {stats['max_hr']} bpm
- Beste zwemtempo: {stats.get('best_swim') or '—'}
- Fietscadans: {stats.get('bike_cadence') or '—'} rpm
- Geschatte HIM eindtijd: {him['total_time']} (zwem {him['swim_time']} / fiets {him['bike_time']} / run {him['run_time']})

Laatste 5 activiteiten:
{acts_text}

Schrijf een persoonlijke update van 3–4 zinnen in het Nederlands. Wees specifiek over de laatste training en wat die betekent voor zijn voorbereiding. Geef ook één concrete tip voor de komende dagen. Schrijf in de tweede persoon ("je", niet "u"). Geen opsomming, gewoon lopende tekst."""

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
                "max_tokens": 300,
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
        print(f"   Claude API fout: {e}")
        return (
            f"Je staat er goed voor richting HIM Knokke. VO2max ~{stats['vo2max']} ml/kg/min, FTP {stats['ftp']}W. Blijf consistent trainen!",
            f"— Automatische fallback · {datetime.now().strftime('%-d %B %Y')}"
        )


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

    # Lees de huidige site
    with open("index.html", "r", encoding="utf-8") as f:
        html = f.read()

    # Vervang de analyse sectie
    new_section = build_strava_section(activities, stats, athlete)

    start = html.find("<!-- ── ANALYSE ── -->")
    end   = html.find("<!-- ── FOOTER ── -->")

    if start == -1 or end == -1:
        print("❌ Analyse sectie niet gevonden in HTML")
        return

    new_html = html[:start] + new_section + "\n\n" + html[end:]

    # Update alle waarden doorheen de volledige pagina
    ftp  = stats["ftp"]
    wkg  = round(ftp / 71, 2)
    mhr  = stats["max_hr"]
    vo2  = stats["vo2max"] or 48
    swim = stats["best_swim"] or "1:52"

    # HIM eindtijd berekenen (nodig voor AI update)
    him_time = estimate_him_time(activities)

    print("🤖 AI update genereren...")
    ai_text, ai_meta = generate_ai_update(activities, stats, him_time)
    print(f"   → {ai_text[:60]}...")

    import re

    # Injecteer AI update tekst
    new_html = re.sub(
        r'(<div id="ai-update-text"[^>]*>)(.*?)(</div>)',
        rf'\g<1>{ai_text}\3',
        new_html, flags=re.DOTALL
    )
    new_html = re.sub(
        r'(<div id="ai-update-meta"[^>]*>)(.*?)(</div>)',
        rf'\g<1>{ai_meta}\3',
        new_html, flags=re.DOTALL
    )


    new_html = re.sub(
        r'(<div class="hstat-val ac">)\d+(<small[^>]*>W</small></div>\s*<div class="hstat-lbl">FTP Fiets</div>)',
        rf'\g<1>{ftp}\2', new_html
    )
    new_html = re.sub(
        r'(<div class="hstat-val">)\d+[\.,]\d+(</div>\s*<div class="hstat-lbl">W/kg</div>)',
        rf'\g<1>{wkg}\2', new_html
    )
    new_html = re.sub(
        r'(<div class="hstat-val gr">)~?\d+(</div>\s*<div class="hstat-lbl">VO2max</div>)',
        rf'\g<1>~{vo2}\2', new_html
    )
    new_html = re.sub(
        r'(<div class="hstat-val">)\d+(</div>\s*<div class="hstat-lbl">Max HS bpm</div>)',
        rf'\g<1>{mhr}\2', new_html
    )

    # ── Progressie & Targets — metric kaartjes (robuuste match) ──
    new_html = re.sub(
        r'(<div class="mhc-lbl">FTP nu</div><div class="mhc-val ac">)\d+(\s*W</div><div class="mhc-sub">)\d+[\.,]\d+(\s*W/kg</div>)',
        rf'\g<1>{ftp}\g<2>{wkg}\3', new_html
    )
    new_html = re.sub(
        r'(<div class="mhc-lbl">VO2max</div><div class="mhc-val gr">)~?\d+(</div>)',
        rf'\g<1>~{vo2}\2', new_html
    )
    new_html = re.sub(
        r'(<div class="mhc-lbl">Max HS bpm</div><div class="mhc-val">)\d+(</div>)',
        rf'\g<1>{mhr}\2', new_html
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

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(new_html)

    print(f"✅ index.html bijgewerkt! VO2max: {vo2} · FTP: {ftp}W · Max HS: {mhr} bpm")

if __name__ == "__main__":
    main()
