#!/usr/bin/env python3
"""Creates RSC-native O365 Configured Groups (Teams/SharePoint) from a CSV list and optionally assigns an SLA domain."""

import argparse
import csv
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from rsc_client import gql, gql_vars

GAIA_MARKER = "gaia"

WORKLOAD_BY_TYPE = {"team": "O365Teams", "sharepoint": "O365Site"}

ORG_QUERY = """
{
  o365Orgs {
    nodes { id name }
  }
}
"""

GROUPS_QUERY = """
query O365Groups($o365OrgId: String!) {
  o365Groups(o365OrgId: $o365OrgId) {
    nodes {
      id
      metadata {
        sharepointObjects
        teamsObjects
      }
    }
  }
}
"""

ADD_GROUP_MUTATION = """
mutation AddConfiguredGroupToHierarchy($input: AddConfiguredGroupToHierarchyInput!) {
  addConfiguredGroupToHierarchy(input: $input) {
    groupId
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


@dataclass(frozen=True)
class GroupSpec:
    name: str
    group_type: str
    expression: str
    pdls: list[str]
    sla_name: str | None


def _gql_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, help="Pfad zur CSV-Liste der anzulegenden Gruppen")
    parser.add_argument(
        "--org-id",
        default=None,
        help="RSC-ID der M365-Org (Standard: automatisch, wenn nur eine Org existiert)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Nur validieren/anzeigen, keine Schreiboperationen ausführen")
    return parser.parse_args()


def read_group_specs(file_path: Path) -> list[GroupSpec]:
    specs: list[GroupSpec] = []
    with file_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        required = {"name", "type", "expression"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise SystemExit(f"CSV fehlt Pflichtspalten: {', '.join(sorted(missing))}")

        for row in reader:
            name = (row.get("name") or "").strip()
            group_type = (row.get("type") or "").strip().lower()
            if not name or not group_type:
                continue
            if group_type not in WORKLOAD_BY_TYPE:
                raise SystemExit(f"Ungültiger type '{group_type}' für Gruppe '{name}' (erlaubt: team, sharepoint)")

            expression = (row.get("expression") or "").strip()
            pdls = [p.strip() for p in (row.get("pdls") or "").split(";") if p.strip()]
            sla_name = (row.get("sla") or "").strip() or None
            specs.append(GroupSpec(name, group_type, expression, pdls, sla_name))
    return specs


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


def resolve_sla_id(sla_name: str) -> str:
    query = f'{{ slaDomains(filter: {{field: NAME text: "{_gql_escape(sla_name)}"}}) {{ nodes {{ id name }} }} }}'
    data = gql(query)
    matches = [n for n in data["data"]["slaDomains"]["nodes"] if n["name"] == sla_name]
    if not matches:
        raise RuntimeError(f"SLA-Domain '{sla_name}' nicht gefunden.")
    if len(matches) > 1:
        raise RuntimeError(f"Mehrdeutiger SLA-Name '{sla_name}' ({len(matches)} Treffer).")
    return matches[0]["id"]


def create_group(org_id: str, spec: GroupSpec) -> str:
    variables = {
        "input": {
            "orgId": org_id,
            "displayName": spec.name,
            "wildcard": spec.expression,
            "pdls": spec.pdls,
            "workload": WORKLOAD_BY_TYPE[spec.group_type],
        }
    }
    result = gql_vars(ADD_GROUP_MUTATION, variables)
    return result["data"]["addConfiguredGroupToHierarchy"]["groupId"]


def fetch_group_object_counts(org_id: str, group_id: str) -> dict | None:
    data = gql_vars(GROUPS_QUERY, {"o365OrgId": org_id})
    for node in data["data"]["o365Groups"]["nodes"]:
        if node["id"] == group_id:
            return node["metadata"]
    return None


def assign_sla(group_id: str, sla_id: str) -> None:
    gql_vars(
        ASSIGN_SLA_MUTATION,
        {"input": {"slaDomainAssignType": "protectWithSlaId", "slaOptionalId": sla_id, "objectIds": [group_id]}},
    )


def check_gaia_guard(dry_run: bool) -> None:
    fqdn = os.environ.get("RSC_FQDN", "")
    if not dry_run and GAIA_MARKER in fqdn.lower():
        raise SystemExit(
            "Abbruch: RSC_FQDN enthält 'gaia' -- dies ist die geschützte interne Rubrik-Demo-Umgebung.\n"
            "In dieser Umgebung sind keine RSC-Mutationen erlaubt (siehe CLAUDE.md).\n"
            "Nutze --dry-run, um das Skript trotzdem zu testen."
        )


def process_spec(org_id: str, spec: GroupSpec, dry_run: bool) -> None:
    pdl_label = ";".join(spec.pdls) if spec.pdls else "Alle"
    print(f"\n[{spec.group_type}] '{spec.name}' — Expression: '{spec.expression or '*'}', PDLs: {pdl_label}")

    if dry_run:
        if spec.sla_name:
            print(f"  [DRY-RUN] Würde Gruppe erstellen und SLA '{spec.sla_name}' zuweisen.")
        else:
            print("  [DRY-RUN] Würde Gruppe erstellen (keine SLA-Zuweisung).")
        return

    group_id = create_group(org_id, spec)
    print(f"  -> Gruppe erstellt: {group_id}")

    counts = fetch_group_object_counts(org_id, group_id)
    if counts is not None:
        print(f"  -> Zugeordnete Objekte: {counts['sharepointObjects']} SharePoint, {counts['teamsObjects']} Teams")

    if spec.sla_name:
        sla_id = resolve_sla_id(spec.sla_name)
        assign_sla(group_id, sla_id)
        print(f"  -> SLA '{spec.sla_name}' zugewiesen.")


def main() -> None:
    args = parse_args()
    check_gaia_guard(args.dry_run)

    file_path = Path(args.file)
    if not file_path.exists():
        raise SystemExit(f"Datei nicht gefunden: {file_path}")

    specs = read_group_specs(file_path)
    if not specs:
        raise SystemExit(f"Keine gültigen Zeilen in {file_path} gefunden.")

    print(f"{len(specs)} Gruppe(n) aus {file_path.name} geladen.")
    print("\nAuthentifiziere mit RSC und löse M365-Org auf...")
    org_id = resolve_org_id(args.org_id)

    succeeded: list[str] = []
    skipped: list[tuple[str, str]] = []

    for spec in specs:
        try:
            process_spec(org_id, spec, args.dry_run)
            succeeded.append(spec.name)
        except Exception as exc:  # noqa: BLE001 -- Fehler pro Zeile melden, Bulk-Lauf fortsetzen
            print(f"  [FEHLER] {exc}", file=sys.stderr)
            skipped.append((spec.name, str(exc)))

    print("\n" + "=" * 60)
    print("ZUSAMMENFASSUNG")
    print("=" * 60)
    print(f"Erfolgreich   : {len(succeeded)}")
    print(f"Übersprungen  : {len(skipped)}")
    for name, reason in skipped:
        print(f"  - {name}: {reason}")


if __name__ == "__main__":
    main()
