#!/usr/bin/env python3
"""Assigns an existing RSC SLA domain to a list of M365 objects (Exchange, OneDrive, Teams, SharePoint)."""

import argparse
import csv
import os
from pathlib import Path

from rsc_client import gql, gql_vars

GAIA_MARKER = "gaia"

WORKLOAD_TYPES = {
    "exchange": {
        "label": "M365 Exchange Mailbox",
        "connection": "o365Mailboxes",
        "fields": ["id", "name", "userPrincipalName"],
        "match_fields": ["userPrincipalName"],
    },
    "onedrive": {
        "label": "M365 OneDrive",
        "connection": "o365Onedrives",
        "fields": ["id", "name", "userPrincipalName", "userName"],
        "match_fields": ["userPrincipalName", "userName"],
    },
    "teams": {
        "label": "M365 Teams",
        "connection": "o365Teams",
        "fields": ["id", "name"],
        "match_fields": ["name"],
    },
    "sharepoint": {
        "label": "M365 SharePoint Site",
        "connection": "o365Sites",
        "fields": ["id", "title", "url"],
        "match_fields": ["title", "url"],
    },
}

ORG_QUERY = """
{
  o365Orgs {
    nodes { id name }
  }
}
"""

SLA_LIST_QUERY = """
{
  slaDomains {
    nodes { id name }
  }
}
"""

ASSIGN_SLA_MUTATION = """
mutation AssignSla($input: AssignSlaInput!) {
  assignSla(input: $input) {
    success
  }
}
"""


def _gql_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def build_inventory_query(connection: str, fields: list[str]) -> str:
    field_block = "\n      ".join(fields)
    return f"""
query Inventory($o365OrgId: String!, $after: String) {{
  {connection}(
    o365OrgId: $o365OrgId
    after: $after
    filter: [{{field: IS_RELIC texts: "false"}}]
  ) {{
    nodes {{
      {field_block}
    }}
    pageInfo {{
      hasNextPage
      endCursor
    }}
  }}
}}
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, help="Pfad zur Liste der Objekt-Bezeichner (txt/csv, ein Eintrag pro Zeile)")
    parser.add_argument("--sla", default=None, help="Name der Ziel-SLA-Domain (ohne Angabe: interaktive Auswahl)")
    parser.add_argument(
        "--type",
        choices=list(WORKLOAD_TYPES),
        default=None,
        help="Objekttyp (ohne Angabe: interaktive Auswahl)",
    )
    parser.add_argument(
        "--org-id",
        default=None,
        help="RSC-ID der M365-Org (Standard: automatisch, wenn nur eine Org existiert)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Nur Zuordnung anzeigen, keine Schreiboperationen ausführen")
    return parser.parse_args()


def check_gaia_guard(dry_run: bool) -> None:
    fqdn = os.environ.get("RSC_FQDN", "")
    if not dry_run and GAIA_MARKER in fqdn.lower():
        raise SystemExit(
            "Abbruch: RSC_FQDN enthält 'gaia' -- dies ist die geschützte interne Rubrik-Demo-Umgebung.\n"
            "In dieser Umgebung sind keine RSC-Mutationen erlaubt (siehe CLAUDE.md).\n"
            "Nutze --dry-run, um das Skript trotzdem zu testen."
        )


def read_identifiers(file_path: Path) -> list[str]:
    identifiers: list[str] = []
    if file_path.suffix.lower() == ".csv":
        with file_path.open(newline="", encoding="utf-8") as fh:
            for row in csv.reader(fh):
                if not row:
                    continue
                value = row[0].strip()
                if value and not value.startswith("#"):
                    identifiers.append(value)
    else:
        for line in file_path.read_text(encoding="utf-8").splitlines():
            value = line.strip()
            if value and not value.startswith("#"):
                identifiers.append(value)
    return identifiers


def prompt_choice(labels: list[str], header: str) -> int:
    print(f"\n{header}")
    for i, label in enumerate(labels, start=1):
        print(f"  {i}. {label}")
    raw = input("Auswahl (Nummer): ").strip()
    if not raw.isdigit() or not (1 <= int(raw) <= len(labels)):
        raise SystemExit(f"Ungültige Auswahl. Bitte eine Zahl zwischen 1 und {len(labels)} eingeben.")
    return int(raw) - 1


def resolve_org_id(org_id: str | None) -> str:
    if org_id:
        return org_id

    data = gql(ORG_QUERY)
    orgs = data["data"]["o365Orgs"]["nodes"]
    if not orgs:
        raise SystemExit("Keine M365-Org in RSC gefunden.")
    if len(orgs) > 1:
        listing = "\n".join(f"  - {o['name']} ({o['id']})" for o in orgs)
        raise SystemExit(f"Mehrere M365-Orgs gefunden, bitte --org-id angeben:\n{listing}")

    print(f"-> Org: {orgs[0]['name']} ({orgs[0]['id']})")
    return orgs[0]["id"]


def resolve_sla(sla_name: str | None) -> tuple[str, str]:
    if sla_name:
        query = f'{{ slaDomains(filter: {{field: NAME text: "{_gql_escape(sla_name)}"}}) {{ nodes {{ id name }} }} }}'
        data = gql(query)
        matches = [n for n in data["data"]["slaDomains"]["nodes"] if n["name"] == sla_name]
        if not matches:
            raise SystemExit(f"SLA-Domain '{sla_name}' nicht gefunden.")
        if len(matches) > 1:
            raise SystemExit(f"Mehrdeutiger SLA-Name '{sla_name}' ({len(matches)} Treffer).")
        return matches[0]["id"], matches[0]["name"]

    data = gql(SLA_LIST_QUERY)
    slas = data["data"]["slaDomains"]["nodes"]
    if not slas:
        raise SystemExit("Keine SLA-Domains in RSC gefunden.")
    idx = prompt_choice([s["name"] for s in slas], "Verfügbare SLA-Domains:")
    return slas[idx]["id"], slas[idx]["name"]


def resolve_type(type_arg: str | None) -> str:
    if type_arg:
        return type_arg
    keys = list(WORKLOAD_TYPES)
    idx = prompt_choice([WORKLOAD_TYPES[k]["label"] for k in keys], "Objekttyp wählen:")
    return keys[idx]


def fetch_inventory(workload_key: str, org_id: str) -> list[dict]:
    spec = WORKLOAD_TYPES[workload_key]
    query = build_inventory_query(spec["connection"], spec["fields"])
    nodes: list[dict] = []
    cursor = None
    while True:
        data = gql_vars(query, {"o365OrgId": org_id, "after": cursor})
        connection_data = data["data"][spec["connection"]]
        nodes.extend(connection_data["nodes"])
        page_info = connection_data["pageInfo"]
        if not page_info["hasNextPage"]:
            break
        cursor = page_info["endCursor"]
    return nodes


def match_identifiers(
    identifiers: list[str], inventory: list[dict], match_fields: list[str]
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    matched: list[tuple[str, str]] = []
    skipped: list[tuple[str, str]] = []
    for identifier in identifiers:
        needle = identifier.strip().lower()
        hits = [node for node in inventory if any((node.get(f) or "").strip().lower() == needle for f in match_fields)]
        if not hits:
            skipped.append((identifier, "Kein passendes Objekt gefunden"))
        elif len(hits) > 1:
            skipped.append((identifier, f"Mehrdeutig ({len(hits)} Treffer)"))
        else:
            matched.append((identifier, hits[0]["id"]))
    return matched, skipped


def assign_sla(object_ids: list[str], sla_id: str) -> None:
    gql_vars(
        ASSIGN_SLA_MUTATION,
        {"input": {"slaDomainAssignType": "protectWithSlaId", "slaOptionalId": sla_id, "objectIds": object_ids}},
    )


def main() -> None:
    args = parse_args()
    check_gaia_guard(args.dry_run)

    file_path = Path(args.file)
    if not file_path.exists():
        raise SystemExit(f"Datei nicht gefunden: {file_path}")

    identifiers = read_identifiers(file_path)
    if not identifiers:
        raise SystemExit(f"Keine Einträge in {file_path} gefunden.")
    print(f"{len(identifiers)} Bezeichner aus {file_path.name} geladen.")

    print("\nAuthentifiziere mit RSC und löse M365-Org auf...")
    org_id = resolve_org_id(args.org_id)

    sla_id, sla_name = resolve_sla(args.sla)
    print(f"-> SLA: {sla_name} ({sla_id})")

    workload_key = resolve_type(args.type)
    spec = WORKLOAD_TYPES[workload_key]
    print(f"-> Objekttyp: {spec['label']}")

    print("\nLade Inventar aus RSC...")
    inventory = fetch_inventory(workload_key, org_id)
    print(f"-> {len(inventory)} Objekte gefunden (nach Relic-Filter).")

    matched, skipped = match_identifiers(identifiers, inventory, spec["match_fields"])

    print("\nZuordnung:")
    for identifier, object_id in matched:
        print(f"  [OK]   {identifier} -> {object_id}")
    for identifier, reason in skipped:
        print(f"  [WARN] {identifier}: {reason}")

    if args.dry_run:
        print(f"\n[DRY-RUN] Würde {len(matched)} Objekt(e) der SLA '{sla_name}' zuweisen.")
        return

    if not matched:
        raise SystemExit("Keine Objekte zum Zuweisen gefunden.")

    print(f"\nWeise SLA '{sla_name}' {len(matched)} Objekt(en) zu...")
    assign_sla([object_id for _, object_id in matched], sla_id)

    print("\n" + "=" * 60)
    print("ZUSAMMENFASSUNG")
    print("=" * 60)
    print(f"SLA           : {sla_name}")
    print(f"Objekttyp     : {spec['label']}")
    print(f"Zugewiesen    : {len(matched)}")
    print(f"Übersprungen  : {len(skipped)}")
    for identifier, reason in skipped:
        print(f"  - {identifier}: {reason}")


if __name__ == "__main__":
    main()
