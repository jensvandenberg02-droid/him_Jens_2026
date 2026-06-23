#!/usr/bin/env python3
"""
Eenmalig opruimscript — verwijdert workouts uit Garmin Connect waarvan de
naam begint met "W" gevolgd door 2 cijfers (bv. "W13 Z2 Loop", "W08 Fiets
Sweet Spot") — exact het patroon dat garmin_sync.py gebruikte voor de
weeknummers van het HIM-trainingsschema.

Andere workouts in je account (bijvoorbeeld die je zelf handmatig hebt
aangemaakt, of die niet dit patroon volgen) worden NIET aangeraakt.

Dit verwijdert workouts uit je Garmin Connect account (en daarmee ook van je
Forerunner/Edge zodra die synct) — het raakt GEEN voltooide activiteiten aan,
enkel geplande/opgeslagen workouts.

Gebruik:
  python cleanup_garmin_workouts.py              → toont eerst welke workouts matchen, vraagt bevestiging
  python cleanup_garmin_workouts.py --confirm     → verwijdert direct zonder te vragen (voor automatisering)
  python cleanup_garmin_workouts.py --dry-run     → toont enkel wat verwijderd zou worden, verwijdert niets

Vereist GARMIN_EMAIL en GARMIN_PASSWORD als omgevingsvariabelen (zelfde als
de andere scripts).
"""

import os
import re
import sys
import time
import argparse

try:
    import garminconnect
except ImportError:
    print("❌ garminconnect niet geïnstalleerd — run: pip install garminconnect")
    sys.exit(1)


# Matcht "W" + exact 2 cijfers aan het begin van de naam, gevolgd door een
# spatie of einde van de string — bv. "W13 Z2 Loop" matcht, "Wandelen" niet,
# "W1 Z2 Loop" (1 cijfer) matcht niet, "W130 Iets" (3 cijfers) matcht niet.
WORKOUT_NAME_PATTERN = re.compile(r"^W\d{2}(\s|$)")


def main():
    parser = argparse.ArgumentParser(description='Verwijder workouts uit Garmin Connect die beginnen met "W" + 2 cijfers')
    parser.add_argument("--confirm", action="store_true", help="Verwijder direct zonder bevestiging te vragen")
    parser.add_argument("--dry-run", action="store_true", help="Toon enkel wat verwijderd zou worden")
    args = parser.parse_args()

    email    = os.environ.get("GARMIN_EMAIL", "")
    password = os.environ.get("GARMIN_PASSWORD", "")

    if not email or not password:
        print("❌ GARMIN_EMAIL of GARMIN_PASSWORD niet gevonden als environment variable.")
        print("   Stel ze lokaal in met:")
        print("     export GARMIN_EMAIL='jouw@email.com'")
        print("     export GARMIN_PASSWORD='jouwwachtwoord'")
        sys.exit(1)

    print(f"🔑 Inloggen op Garmin Connect als {email}...")
    try:
        client = garminconnect.Garmin(email, password)
        client.login()
        print("✅ Ingelogd")
    except Exception as e:
        print(f"❌ Login mislukt: {e}")
        sys.exit(1)

    # ── Alle workouts ophalen met paginatie ──
    print("📋 Workouts ophalen...")
    all_workouts = []
    start = 0
    limit = 100
    while True:
        batch = client.get_workouts(start=start, limit=limit)
        if not batch:
            break
        all_workouts.extend(batch)
        print(f"   → {len(all_workouts)} workouts gevonden tot nu toe...")
        if len(batch) < limit:
            break  # laatste pagina bereikt
        start += limit
        time.sleep(0.2)

    total = len(all_workouts)
    print(f"\n📊 Totaal aantal workouts in account: {total}")

    # ── Filter op naam-patroon "W" + 2 cijfers ──
    matching = [w for w in all_workouts if WORKOUT_NAME_PATTERN.match(w.get("workoutName", ""))]
    skipped  = total - len(matching)

    print(f"🎯 Workouts die matchen met patroon 'W## ...': {len(matching)}")
    print(f"⏭️  Workouts die NIET matchen (blijven staan): {skipped}")

    if not matching:
        print("✅ Niets te verwijderen — geen workouts gevonden die met het patroon matchen.")
        return

    if args.dry_run:
        print("\n🔍 DRY RUN — onderstaande workouts zouden verwijderd worden:")
        for w in matching[:30]:
            print(f"   - {w.get('workoutName', '(naamloos)')} (id: {w.get('workoutId')})")
        if len(matching) > 30:
            print(f"   ... en nog {len(matching) - 30} andere")
        print("\nGeen workouts verwijderd (dry-run).")
        return

    if not args.confirm:
        print("\nVoorbeeld van workouts die verwijderd worden:")
        for w in matching[:10]:
            print(f"   - {w.get('workoutName', '(naamloos)')}")
        if len(matching) > 10:
            print(f"   ... en nog {len(matching) - 10} andere")
        answer = input(f"\n⚠️  Dit verwijdert {len(matching)} workouts (patroon 'W## ...') uit je Garmin Connect account. "
                        f"Doorgaan? (typ 'ja' om te bevestigen): ")
        if answer.strip().lower() != "ja":
            print("Geannuleerd — er is niets verwijderd.")
            return

    print(f"\n🗑️  Verwijderen van {len(matching)} workouts...")
    deleted = 0
    failed = 0
    for i, w in enumerate(matching, 1):
        workout_id = w.get("workoutId")
        name = w.get("workoutName", "(naamloos)")
        if not workout_id:
            continue
        try:
            client.delete_workout(workout_id)
            deleted += 1
            if i % 20 == 0 or i == len(matching):
                print(f"   → {i}/{len(matching)} verwerkt ({deleted} verwijderd, {failed} mislukt)")
        except Exception as e:
            failed += 1
            print(f"   ⚠️ Mislukt: {name} (id: {workout_id}): {e}")
        time.sleep(0.25)  # vriendelijk blijven voor Garmin's onofficiële endpoints

    print(f"\n✅ Klaar! {deleted} workouts verwijderd, {failed} mislukt.")
    print(f"   {skipped} andere workouts (die niet matchten) zijn ongemoeid gelaten.")
    if failed > 0:
        print("   Run het script opnieuw om de mislukte verwijderingen alsnog te proberen.")


if __name__ == "__main__":
    main()
