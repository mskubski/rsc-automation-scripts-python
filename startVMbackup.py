#!/usr/bin/env python3
"""Interactive VM selection and on-demand backup trigger."""

import sys

from rsc_client import gql, gql_vars

VM_QUERY = """
query {
  vSphereVmNewConnection(
    filter: [
      {field: IS_RELIC texts: "false"}
      {field: IS_REPLICATED texts: "false"}
    ]
  ) {
    nodes {
      id
      name
      effectiveSlaDomain {
        id
        name
      }
    }
  }
}
"""

BACKUP_MUTATION = """
mutation TriggerBackup($input: VsphereBulkOnDemandSnapshotInput!) {
  vsphereBulkOnDemandSnapshot(input: $input) {
    responses {
      id
    }
  }
}
"""


def main() -> None:
    print("Authenticating with RSC...")

    print("\nFetching VM inventory...")
    data = gql(VM_QUERY)
    vms = data["data"]["vSphereVmNewConnection"]["nodes"]

    if not vms:
        print("Error: No VMs found in inventory.", file=sys.stderr)
        sys.exit(1)

    print("\nAvailable VMs:")
    print("-" * 62)
    print(f"  {'No.':<4} {'VM Name':<40} Assigned SLA")
    print("-" * 62)
    for i, vm in enumerate(vms, start=1):
        sla = vm["effectiveSlaDomain"]["name"] if vm["effectiveSlaDomain"] else "None"
        print(f"  {i:<4} {vm['name']:<40} {sla}")
    print("-" * 62)

    raw = input("\nEnter the number of the VM to back up: ").strip()
    if not raw.isdigit() or not (1 <= int(raw) <= len(vms)):
        print(f"Error: Invalid selection. Enter a number between 1 and {len(vms)}.", file=sys.stderr)
        sys.exit(1)

    vm = vms[int(raw) - 1]
    vm_id = vm["id"]
    vm_name = vm["name"]
    sla_id = vm["effectiveSlaDomain"]["id"]
    sla_name = vm["effectiveSlaDomain"]["name"]

    print(f"\nSelected VM : {vm_name}")
    print(f"Using SLA   : {sla_name}")

    print("\nTriggering on-demand backup...")
    result = gql_vars(
        BACKUP_MUTATION,
        {"input": {"config": {"vms": [vm_id], "slaId": sla_id}}},
    )

    job_id = result["data"]["vsphereBulkOnDemandSnapshot"]["responses"][0]["id"]

    print("\nSUCCESS! On-demand backup started.")
    print(f"  VM      : {vm_name}")
    print(f"  SLA     : {sla_name}")
    print(f"  Job ID  : {job_id}")


if __name__ == "__main__":
    main()
