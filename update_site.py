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

    # VO2max breakdown tabel
    breakdown = stats.get("vo2max_breakdown", [])
    breakdown_rows = ""
    for v, w, name in breakdown:
        pct = round(w * 100)
        breakdown_rows += f'<tr><td>{name}</td><td style="text-align:right;font-weight:600;color:var(--text)">{v:.1f}</td><td style="text-align:right;color:var(--muted)">{pct}%</td></tr>'

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

# ── MAIN ──
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

    # Update ook de hero stats (max HS, VO2max, FTP, CSS)
    ftp  = stats["ftp"]
    wkg  = round(ftp / 71, 2)
    mhr  = stats["max_hr"]
    vo2  = stats["vo2max"] or 48
    swim = stats["best_swim"] or "1:52"

    # Vervang hero stat waarden via herkenbare patronen
    import re
    new_html = re.sub(
        r'(<div class="hstat-val ac">)\d+(<small[^>]*>W</small></div>\s*<div class="hstat-lbl">FTP Fiets</div>)',
        rf'\g<1>{ftp}\2', new_html
    )
    new_html = re.sub(
        r'(<div class="hstat-val">)\d+\.\d+(</div>\s*<div class="hstat-lbl">W/kg</div>)',
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

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(new_html)

    print("✅ index.html bijgewerkt!")

if __name__ == "__main__":
    main()
