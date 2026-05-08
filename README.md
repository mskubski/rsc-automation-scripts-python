# RSC Automation Scripts — Python

Python scripts for automating operations against the **Rubrik Security Cloud (RSC) GraphQL API**.  
All scripts use [`requests`](https://docs.python-requests.org/) and share a common authentication and GraphQL client layer.

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

## Project structure

```
.
├── rsc_auth.py          # Shared token cache helper
├── rsc_client.py        # Shared GraphQL client (gql, gql_vars, gql_vars_raw)
├── startVMbackup.py     # On-demand VM backup
├── requirements.txt     # Python dependencies
├── .env                 # Credentials (not committed)
└── .gitignore
```

---

## Related

- [rsc-automation-scripts-shell](https://github.com/mskubski/rsc-automation-scripts-shell) — equivalent Bash scripts using `curl` + `jq`
