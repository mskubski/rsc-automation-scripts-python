# RSC Automation Scripts — Python

Python scripts for automating operations against the **Rubrik Security Cloud (RSC) GraphQL API**.  
All scripts use [`requests`](https://docs.python-requests.org/) and share a common authentication and GraphQL client layer.

This repo also includes `createM365Group.py`, which is **independent of RSC**: it automates Microsoft 365 Team / SharePoint group creation via the Microsoft Graph API, using its own credentials and client layer (`graph_auth.py`, `graph_client.py`). See the "M365 Gruppen-Import" section below.

---

## Prerequisites

- **Python 3.10+** and **pip3**
- A Rubrik Security Cloud **Service Account** with the required permissions

Install dependencies:

```bash
pip3 install -r requirements.txt
```

---

## Credentials — `.env` file

All scripts load credentials from a `.env` file in the project root. Create it before running any script.

```bash
RSC_CLIENT_ID="client|xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
RSC_CLIENT_SECRET="your-client-secret"
RSC_NAME="your-service-account-name"
RSC_TOKEN_URI="https://<tenant>.my.rubrik.com/api/client_token"
RSC_FQDN="<tenant>.my.rubrik.com"
```

| Variable | Description |
|---|---|
| `RSC_CLIENT_ID` | Service account client ID (starts with `client\|`) |
| `RSC_CLIENT_SECRET` | Service account secret |
| `RSC_NAME` | Friendly name for this service account (informational) |
| `RSC_TOKEN_URI` | Full URL of the OAuth2 token endpoint |
| `RSC_FQDN` | Hostname of the RSC tenant (used for all API calls) |

> The `.env` file contains sensitive credentials — it is excluded from version control via `.gitignore`.

---

## Token caching — `rsc_auth.py`

Rubrik limits each service account to **10 active tokens** at a time. All scripts share a token cache to avoid exhausting this limit.

**How it works:**

1. On first run, a token is requested from RSC and written to `.rsc_token_cache` with permissions `600` (owner-only).
2. On subsequent runs, the cached token's expiry is decoded from the JWT — no API call is made.
3. The cached token is reused as long as it has more than **5 minutes** remaining (configurable via `TOKEN_BUFFER_SECONDS`).
4. When the token is near expiry, a new one is requested and the cache is updated.

```
-> Using cached token (expires in 43199s).
-> New token obtained (expires in 43200s, cached to .rsc_token_cache).
```

To override the buffer window (e.g. 10 minutes):

```bash
TOKEN_BUFFER_SECONDS=600 python3 startVMbackup.py
```

---

## Shared modules

### `rsc_auth.py`

Token cache helper. Import and call `get_token()` — returns a valid bearer token, fetching a new one only when needed.

```python
from rsc_auth import get_token
token = get_token()
```

### `rsc_client.py`

GraphQL client built on top of `rsc_auth`. Provides three helpers:

```python
from rsc_client import gql, gql_vars, gql_vars_raw

# Simple query — raises RuntimeError on GraphQL errors
result = gql("{ vSphereVmNewConnection { nodes { id name } } }")

# Query or mutation with variables — raises RuntimeError on GraphQL errors
result = gql_vars("""
  mutation TriggerBackup($input: VsphereBulkOnDemandSnapshotInput!) {
    vsphereBulkOnDemandSnapshot(input: $input) { responses { id } }
  }
""", {"input": {"config": {"vms": ["<id>"], "slaId": "<id>"}}})

# Raw — no error raise, use for retry logic
result = gql_vars_raw(query, variables)
```

---

## Scripts

### `startVMbackup.py`

Lists all non-relic, non-replicated vSphere VMs with their assigned SLA, lets the user select one by number, and triggers an immediate on-demand backup using the object's own effective SLA.

```bash
python3 startVMbackup.py
```

**Example interaction:**

```
Authenticating with RSC...
-> Using cached token (expires in 43100s).

Fetching VM inventory...

Available VMs:
--------------------------------------------------------------
  No.  VM Name                                  Assigned SLA
--------------------------------------------------------------
  1    my-vm-01                                 Platinum
  2    my-vm-02                                 Gold
--------------------------------------------------------------

Enter the number of the VM to back up: 1

Selected VM : my-vm-01
Using SLA   : Platinum

Triggering on-demand backup...

SUCCESS! On-demand backup started.
  VM      : my-vm-01
  SLA     : Platinum
  Job ID  : ONDEMAND_SNAPSHOT_VSPHERE_VIRTUAL_MACHINE_...
```

---

## M365 Gruppen-Import (`createM365Group.py`)

Erstellt ein **Microsoft 365 Team** oder eine **SharePoint-Gruppe** über die Microsoft Graph API und befüllt sie mit Objekten aus einer Text- oder CSV-Liste. Dieses Skript ist **komplett unabhängig von RSC** — es nutzt eine eigene Azure-AD-App-Registrierung und eigene `.env`-Variablen, keine RSC-Credentials.

> Die Gaia-Read-Only-Regel aus `CLAUDE.md` gilt nur für Mutationen in der RSC-GraphQL-API und ist auf dieses Skript **nicht anwendbar**.

### Zusätzliche `.env`-Variablen

```env
GRAPH_TENANT_ID=00000000-0000-0000-0000-000000000000
GRAPH_CLIENT_ID=11111111-1111-1111-1111-111111111111
GRAPH_CLIENT_SECRET=your-app-registration-secret
```

| Variable | Beschreibung |
|---|---|
| `GRAPH_TENANT_ID` | Microsoft Entra ID Tenant-ID |
| `GRAPH_CLIENT_ID` | App-Registrierung (Client ID) |
| `GRAPH_CLIENT_SECRET` | App-Registrierung Client Secret |

Token-Caching läuft analog zu `rsc_auth.py`, aber über zwei getrennte Cache-Dateien (`.graph_token_cache` für Microsoft Graph, `.graph_sp_token_cache` für die klassische SharePoint-REST-API), da beide Scopes unterschiedliche Audiences haben.

### Benötigte Azure-AD-App-Berechtigungen (Application, Admin Consent erforderlich)

- **Microsoft Graph**: `Group.ReadWrite.All`, `User.Read.All`, `Team.Create`, `TeamMember.ReadWrite.All`, `Sites.Read.All`
- **SharePoint** (klassische API-Berechtigung im Entra-Portal, separat von Graph): `Sites.FullControl.All` — nur für `--type sharepoint` benötigt.

> ⚠️ `Sites.FullControl.All` ist eine tenant-weit mächtige Berechtigung. Die App-Registrierung entsprechend absichern (Secret-Rotation, restriktive Owner-Liste).

**Technischer Hintergrund:** Der naheliegende Graph-Endpunkt `POST /sites/{id}/permissions` kann laut [Microsoft-Doku](https://learn.microsoft.com/en-us/graph/api/site-post-permissions?view=graph-rest-1.0) **nur Anwendungsberechtigungen** vergeben, keine Gruppen-/Benutzerberechtigungen auf einer Site. Um die neu erstellte M365-Gruppe tatsächlich einer bestehenden SharePoint-Site hinzuzufügen, nutzt das Skript im `sharepoint`-Modus daher zusätzlich die klassische SharePoint-REST-API (`_api/web/sitegroups/...`) mit claims-codiertem Gruppennamen.

### Listenformat & Auto-Erkennung

Die Datei kann `.csv` (erste Spalte pro Zeile) oder reiner Text sein (eine Zeile = ein Eintrag). Leerzeilen und Zeilen mit `#` werden ignoriert. **CSV-Dateien dürfen keine Header-Zeile enthalten** — jede Zeile wird 1:1 als Eintrag interpretiert. Jede Zeile wird automatisch klassifiziert:

| Muster | Erkannt als | Verwendung |
|---|---|---|
| enthält `@` und sieht wie eine E-Mail aus | Benutzer | `--type team`: wird als Teammitglied aufgelöst und hinzugefügt |
| beginnt mit `http://` oder `https://` | Site-URL | `--type sharepoint`: wird als Site aufgelöst |
| alles andere | Name | wird per Graph-Suche aufgelöst (Displayname- bzw. Site-Suche) |

Einträge, die nicht zum gewählten `--type` passen (z. B. eine E-Mail-Adresse im `sharepoint`-Modus), werden übersprungen und in der Zusammenfassung als Warnung ausgegeben.

### Verwendung

```bash
# Team erstellen und Mitglieder aus einer Liste hinzufügen
python3 createM365Group.py --type team \
  --name "Project X" \
  --owner admin@contoso.com \
  --file members.txt

# SharePoint: Gruppe erstellen und ihr Zugriff auf bestehende Sites geben
python3 createM365Group.py --type sharepoint \
  --name "Project X Access" \
  --owner admin@contoso.com \
  --file sites.csv \
  --role member

# Testlauf ohne Schreibzugriffe (Auflösung/Klassifizierung nur anzeigen)
python3 createM365Group.py --type team --name "Project X" \
  --owner admin@contoso.com --file members.txt --dry-run
```

| Argument | Pflicht | Beschreibung |
|---|---|---|
| `--type {team,sharepoint}` | ja | Zu erstellender Gruppentyp |
| `--name` | ja | Anzeigename der neuen Gruppe |
| `--file` | ja | Pfad zur `.txt`/`.csv`-Liste |
| `--owner` | ja | UPN des initialen Gruppenbesitzers |
| `--description` | nein | Gruppenbeschreibung |
| `--mail-nickname` | nein | Standard: aus `--name` abgeleiteter Slug |
| `--role {owner,member,visitor}` | nein (Standard `member`) | nur `--type sharepoint`: Ziel-Berechtigungsgruppe je Site |
| `--dry-run` | nein | Nur auflösen/anzeigen, keine Schreiboperationen |

---

## Project structure

```
.
├── rsc_auth.py          # Shared token cache helper (RSC)
├── rsc_client.py        # Shared GraphQL client (gql, gql_vars, gql_vars_raw)
├── startVMbackup.py     # On-demand VM backup
├── graph_auth.py        # Shared token cache helper (Microsoft Graph / SharePoint)
├── graph_client.py      # Shared Graph + SharePoint REST client
├── createM365Group.py   # M365 Team/SharePoint group creation + import
├── requirements.txt     # Python dependencies
├── .env                 # Credentials (not committed)
└── .gitignore
```

---

## Related

- [rsc-automation-scripts-shell](https://github.com/mskubski/rsc-automation-scripts-shell) — equivalent Bash scripts using `curl` + `jq`
