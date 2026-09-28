"""
Creates a Microsoft 365 Team or SharePoint group and populates it from a
text/CSV list, via the Microsoft Graph API (and, for the sharepoint mode,
the classic SharePoint REST API).

This script is independent of the RSC (Rubrik Security Cloud) GraphQL API
and its credentials -- it targets a separate Microsoft Entra ID app
registration (GRAPH_TENANT_ID / GRAPH_CLIENT_ID / GRAPH_CLIENT_SECRET in
.env). The project's Gaia read-only rule applies to RSC GraphQL mutations
only and does not apply here.

Usage:
    python3 createM365Group.py --type team --name "Project X" \\
        --owner admin@contoso.com --file members.txt

    python3 createM365Group.py --type sharepoint --name "Project X Access" \\
        --owner admin@contoso.com --file sites.csv --role member --dry-run
"""

import argparse
import csv
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

from graph_client import graph_get, graph_paginate, graph_post, graph_put, sp_rest_get, sp_rest_post

load_dotenv()

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_REQUIRED_ENV_VARS = ("GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "GRAPH_CLIENT_SECRET")


def _validate_env() -> None:
    missing = [var for var in _REQUIRED_ENV_VARS if not os.environ.get(var)]
    if missing:
        raise SystemExit(f"Fehlende .env-Variablen: {', '.join(missing)}")


def _odata_escape(value: str) -> str:
    return value.replace("'", "''")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--type", required=True, choices=["team", "sharepoint"], help="Zu erstellender Gruppentyp")
    parser.add_argument("--name", required=True, help="Anzeigename der neuen Gruppe")
    parser.add_argument("--file", required=True, help="Pfad zur Text- oder CSV-Liste der zu importierenden Objekte")
    parser.add_argument("--owner", required=True, help="UPN/E-Mail des initialen Gruppenbesitzers")
    parser.add_argument("--description", default="", help="Beschreibung der Gruppe")
    parser.add_argument("--mail-nickname", default=None, help="mailNickname (Standard: aus --name abgeleitet)")
    parser.add_argument(
        "--role",
        default="member",
        choices=["owner", "member", "visitor"],
        help="Nur --type sharepoint: Ziel-Berechtigungsgruppe auf der jeweiligen Site (Standard: member)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Nur auflösen/anzeigen, keine Schreiboperationen ausführen")
    return parser.parse_args()


def read_entries(file_path: Path) -> list[str]:
    entries: list[str] = []
    if file_path.suffix.lower() == ".csv":
        with file_path.open(newline="", encoding="utf-8") as fh:
            for row in csv.reader(fh):
                if not row:
                    continue
                value = row[0].strip()
                if value and not value.startswith("#"):
                    entries.append(value)
    else:
        for line in file_path.read_text(encoding="utf-8").splitlines():
            value = line.strip()
            if value and not value.startswith("#"):
                entries.append(value)
    return entries


def classify_entry(entry: str) -> str:
    """Returns "user", "site_url", or "name" (ambiguous, resolved via search)."""
    if entry.startswith("http://") or entry.startswith("https://"):
        return "site_url"
    if _EMAIL_RE.match(entry):
        return "user"
    return "name"


def slugify_mail_nickname(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "", name)
    if not slug:
        raise SystemExit("Konnte aus --name keinen gültigen mailNickname ableiten; bitte --mail-nickname angeben.")
    return slug


def resolve_user(entry: str, entry_type: str) -> dict | None:
    try:
        if entry_type == "user":
            return graph_get(f"/users/{entry}")
        matches = graph_paginate("/users", params={"$filter": f"startswith(displayName,'{_odata_escape(entry)}')"})
        if len(matches) == 1:
            return matches[0]
        if not matches:
            print(f"  [WARN] Kein Benutzer gefunden für '{entry}'.")
        else:
            print(f"  [WARN] Mehrdeutiger Name '{entry}' ({len(matches)} Treffer).")
        return None
    except Exception as exc:  # noqa: BLE001 -- resolution failures are reported, not fatal
        print(f"  [WARN] Fehler beim Auflösen von Benutzer '{entry}': {exc}")
        return None


def resolve_site(entry: str, entry_type: str) -> dict | None:
    try:
        if entry_type == "site_url":
            parsed = urlparse(entry)
            return graph_get(f"/sites/{parsed.netloc}:{parsed.path or '/'}")
        matches = graph_paginate("/sites", params={"search": entry})
        if len(matches) == 1:
            return matches[0]
        if not matches:
            print(f"  [WARN] Keine Site gefunden für '{entry}'.")
        else:
            print(f"  [WARN] Mehrdeutiger Site-Name '{entry}' ({len(matches)} Treffer).")
        return None
    except Exception as exc:  # noqa: BLE001 -- resolution failures are reported, not fatal
        print(f"  [WARN] Fehler beim Auflösen von Site '{entry}': {exc}")
        return None


def create_group(name: str, mail_nickname: str, description: str, owner_id: str) -> dict:
    body = {
        "displayName": name,
        "mailNickname": mail_nickname,
        "description": description,
        "mailEnabled": True,
        "securityEnabled": False,
        "groupTypes": ["Unified"],
        "owners@odata.bind": [f"https://graph.microsoft.com/v1.0/users/{owner_id}"],
    }
    return graph_post("/groups", body)


def provision_team(group_id: str, max_wait_seconds: int = 30) -> dict:
    """PUT /groups/{id}/team, retrying while the group is still provisioning."""
    delay = 2
    elapsed = 0
    last_error: Exception | None = None
    while elapsed < max_wait_seconds:
        try:
            return graph_put(f"/groups/{group_id}/team", {})
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status in (400, 404):
                last_error = exc
                time.sleep(delay)
                elapsed += delay
                delay = min(delay * 2, 10)
                continue
            raise
    raise RuntimeError(f"Timeout beim Warten auf Team-Provisionierung für Gruppe {group_id}: {last_error}")


def add_team_member(team_id: str, user_id: str) -> None:
    graph_post(
        f"/teams/{team_id}/members",
        {
            "@odata.type": "#microsoft.graph.aadUserConversationMember",
            "roles": [],
            "user@odata.bind": f"https://graph.microsoft.com/v1.0/users('{user_id}')",
        },
    )


def add_group_to_site(site_web_url: str, role: str, group_object_id: str) -> None:
    role_group_data = sp_rest_get(site_web_url, f"/_api/web/associated{role}group")
    group_title = role_group_data["d"]["Title"]
    login_name = f"c:0t.c|tenant|{group_object_id}"
    sp_rest_post(
        site_web_url,
        f"/_api/web/sitegroups/getbyname('{_odata_escape(group_title)}')/users",
        {"__metadata": {"type": "SP.User"}, "LoginName": login_name},
    )


def main() -> None:
    args = parse_args()
    _validate_env()

    file_path = Path(args.file)
    if not file_path.exists():
        raise SystemExit(f"Datei nicht gefunden: {file_path}")

    entries = read_entries(file_path)
    if not entries:
        raise SystemExit(f"Keine Einträge in {file_path} gefunden.")

    print(f"{len(entries)} Einträge aus {file_path.name} geladen.")
    classified = [(entry, classify_entry(entry)) for entry in entries]
    for entry, entry_type in classified:
        print(f"  - {entry}  [{entry_type}]")

    mail_nickname = args.mail_nickname or slugify_mail_nickname(args.name)

    print(f"\nLöse Owner '{args.owner}' auf...")
    owner = graph_get(f"/users/{args.owner}")
    print(f"-> Owner: {owner['displayName']} ({owner['id']})")

    group_id = "DRY-RUN-GROUP-ID"
    if args.dry_run:
        print("\n[DRY-RUN] Würde folgende Gruppe erstellen:")
        print(
            json.dumps(
                {
                    "displayName": args.name,
                    "mailNickname": mail_nickname,
                    "description": args.description,
                    "groupTypes": ["Unified"],
                    "owner": owner["userPrincipalName"],
                },
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        print(f"\nErstelle M365-Gruppe '{args.name}'...")
        group = create_group(args.name, mail_nickname, args.description, owner["id"])
        group_id = group["id"]
        print(f"-> Gruppe erstellt: {group_id}")

        if args.type == "team":
            print("Aktiviere Teams-Funktionalität für die Gruppe...")
            provision_team(group_id)
            print("-> Team bereit.")

    succeeded: list[str] = []
    skipped: list[tuple[str, str]] = []

    if args.type == "team":
        for entry, entry_type in classified:
            if entry_type == "site_url":
                print(f"  [WARN] '{entry}' sieht wie eine Site-URL aus und wird im team-Modus übersprungen.")
                skipped.append((entry, "Site-URL im team-Modus nicht unterstützt"))
                continue
            user = resolve_user(entry, entry_type)
            if not user:
                skipped.append((entry, "Benutzer nicht eindeutig auflösbar"))
                continue
            if args.dry_run:
                print(f"  [DRY-RUN] Würde '{user['displayName']}' ({user['id']}) als Mitglied hinzufügen.")
            else:
                add_team_member(group_id, user["id"])
                print(f"  -> Mitglied hinzugefügt: {user['displayName']} ({user['id']})")
            succeeded.append(entry)
    else:
        for entry, entry_type in classified:
            if entry_type == "user":
                print(f"  [WARN] '{entry}' sieht wie eine E-Mail-Adresse aus und wird im sharepoint-Modus übersprungen.")
                skipped.append((entry, "Benutzer-Eintrag im sharepoint-Modus nicht unterstützt"))
                continue
            site = resolve_site(entry, entry_type)
            if not site:
                skipped.append((entry, "Site nicht eindeutig auflösbar"))
                continue
            if args.dry_run:
                print(f"  [DRY-RUN] Würde Zugriff ({args.role}) auf Site '{site['displayName']}' gewähren.")
            else:
                add_group_to_site(site["webUrl"], args.role, group_id)
                print(f"  -> Zugriff gewährt: {site['displayName']} ({site['webUrl']}) als {args.role}")
            succeeded.append(entry)

    print("\n" + "=" * 60)
    print("ZUSAMMENFASSUNG")
    print("=" * 60)
    print(f"Gruppe        : {args.name} ({group_id})")
    print(f"Typ           : {args.type}")
    print(f"Erfolgreich   : {len(succeeded)}")
    print(f"Übersprungen  : {len(skipped)}")
    for entry, reason in skipped:
        print(f"  - {entry}: {reason}")


if __name__ == "__main__":
    main()
