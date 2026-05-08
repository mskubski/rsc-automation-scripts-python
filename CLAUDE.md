# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A collection of Python scripts for automating operations against the **Rubrik Security Cloud (RSC) GraphQL API**. Scripts use `requests` throughout.

## Credentials

All scripts load credentials from `.env` (never hardcode them), read via `python-dotenv`:

```python
from dotenv import load_dotenv
load_dotenv()
```

Variables provided by `.env`:

| Variable | Purpose |
|---|---|
| `RSC_FQDN` | Hostname of the RSC tenant |
| `RSC_CLIENT_ID` | Service account client ID |
| `RSC_CLIENT_SECRET` | Service account secret |
| `RSC_TOKEN_URI` | OAuth2 token endpoint |
| `RSC_NAME` | Friendly name for this service account |

## Authentication pattern

All scripts use the shared `rsc_auth.py` helper for token caching (Rubrik allows max 10 active tokens per service account):

```python
from rsc_auth import get_token

token = get_token()
```

`rsc_auth.py` decodes the JWT expiry and reuses a cached token from `.rsc_token_cache` as long as it has more than 5 minutes remaining. A new token is only requested when needed.

## GraphQL call patterns

Use these helper functions defined in `rsc_client.py` (shared module):

```python
from rsc_client import gql, gql_vars

# Simple query
result = gql("{ vSphereVmNewConnection { nodes { id name } } }")

# Query/mutation with variables
result = gql_vars("""
  mutation Export($input: VsphereExportSnapshotV2Input!) {
    vsphereVmExportSnapshotV2(input: $input) { id }
  }
""", {"input": {...}})
```

**`rsc_client.py` implementation pattern:**

```python
import os
import requests
from rsc_auth import get_token

def _headers():
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {get_token()}",
    }

def gql(query: str) -> dict:
    resp = requests.post(
        f"https://{os.environ['RSC_FQDN']}/api/graphql",
        headers=_headers(),
        json={"query": query},
    )
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        raise RuntimeError(f"GraphQL error: {data['errors']}")
    return data

def gql_vars(query: str, variables: dict) -> dict:
    resp = requests.post(
        f"https://{os.environ['RSC_FQDN']}/api/graphql",
        headers=_headers(),
        json={"query": query, "variables": variables},
    )
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        raise RuntimeError(f"GraphQL error: {data['errors']}")
    return data

def gql_vars_raw(query: str, variables: dict) -> dict:
    """No error raise — use for retry logic."""
    resp = requests.post(
        f"https://{os.environ['RSC_FQDN']}/api/graphql",
        headers=_headers(),
        json={"query": query, "variables": variables},
    )
    resp.raise_for_status()
    return resp.json()
```

## Script standards (for new scripts)

- Use `python-dotenv` to load `.env`; validate required vars early with `os.environ["VAR"]` (raises `KeyError` on missing)
- Import `gql` / `gql_vars` / `gql_vars_raw` from `rsc_client` — never inline HTTP calls
- Use `argparse` for CLI arguments; fall back to `input()` prompts for interactive scripts
- Raise exceptions on errors; only catch where you can meaningfully recover
- Use f-strings for string formatting
- Type-hint function signatures
- No global state outside of module-level constants

## Dependencies

Manage with a `requirements.txt`:

```
requests
python-dotenv
```

Install with:
```bash
pip3 install -r requirements.txt
```

## Key GraphQL entry points

- **List VMs**: `vSphereVmNewConnection(filter: [{field: IS_RELIC texts: "false"}, {field: IS_REPLICATED texts: "false"}])` → `id name effectiveSlaDomain { id name } powerStatus`
- **VM snapshots**: `vSphereVmNew(fid: "<id>") { snapshotConnection { nodes { id date isOnDemandSnapshot } } }`
- **On-demand backup**: `vsphereBulkOnDemandSnapshot(input: { config: { vms: ["<id>"] slaId: "<id>" } })`
- **Backup status**: `vSphereVMAsyncRequestStatus(id: "<jobId>" clusterUuid: "<clusterId>") { status progress endTime }`
- **VM in-place restore**: `vsphereVmInitiateInPlaceRecovery(input: { id: "<vmId>" config: { requiredRecoveryParameters: { snapshotId: "<snapId>" } } })`
- **VM export to new VM**: `vsphereVmExportSnapshotV2(input: $input)` via GraphQL variables — input `id` = **VM FID** (not snapshot); snapshot goes in `config.requiredRecoveryParameters.snapshotId`; config also has `datastoreId` (String!, required), optional `hostId`, and `mountExportSnapshotJobCommonOptionsV2: {vmName, powerOn, keepMacAddresses, disableNetwork}`
- **List ESXi hosts**: `vSphereHostNewConnection { nodes { id name } }` — use `vSphereHost(fid:)` (not `vSphereHostNew`) for single host queries
- **Host datastores + networks**: `vSphereHost(fid: "<hostId>") { descendantConnection { nodes { id name objectType } } }` — filter by `objectType == "VSphereDatastore"` or `"VSphereNetwork"`
- **VM current host**: `vSphereVmNew(fid: "<vmId>") { currentHost { id name } }`
- **File restore**: `vsphereVmRecoverFilesNew(input: $input)` via GraphQL variables — config includes `shouldUseAgent`, `restoreConfig`, optional `guestCredentials`
- **Browse snapshot files**: `browseSnapshotFileConnection(snapshotFid: "<id>" path: "<path>" first: 100)`
- **Restore activity status**: `activitySeriesConnection(filters: { objectFid: "<vmId>" lastActivityType: [Recovery] lastUpdatedTimeGt: "<time>" })`
- **List clusters**: `clusterConnection(filter: {})` — includes `clusterNodeConnection.nodes.interfaceCidrs { interfaceName cidr }`
- **SLA by name**: `slaDomains(filter: {field: NAME text: "…"}) { nodes { id name } }`
- **Create SLA**: `createGlobalSla(input: { name objectTypes snapshotSchedule { daily { basicSchedule { frequency retention retentionUnit } } } })`
- **Assign SLA**: `assignSla(input: { slaDomainAssignType: protectWithSlaId slaOptionalId: "…" objectIds: ["…"] })`
- **Ruby AI chatbots**: `chatbots { nodes { name id } }` → POST `/api/annapurna/<id>/retrieve`

## File restore: Windows vs Linux paths

Rubrik exposes Windows paths as `/C:/foo/bar`. The restore destination must be computed per OS:

- Linux: `/etc/passwd` → restorePath `/restore/etc`
- Windows: `/C:/Files/report.docx` → restorePath `C:/restore/Files`

RBS (Rubrik Backup Service) is tried first without credentials. If RSC returns error `RBK20100125`, fall back to `guestCredentials: { username, password }`.

## Existing scripts

| Script | Purpose |
|---|---|
| `rsc_auth.py` | Shared token cache helper — call `get_token()` |
| `rsc_client.py` | Shared GraphQL helpers — import `gql`, `gql_vars`, `gql_vars_raw` |
| `startVMbackup.py` | Interactive VM selection, triggers on-demand backup |
