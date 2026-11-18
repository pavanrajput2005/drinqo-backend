# DRINQO Phase 2A.3 — source-only isolated test preparation

These are **unexecuted preparation artifacts**. They do not authorize provisioning,
database access, migrations, tests, service operations, or cleanup. No production
OTP, inventory, authentication, settings, or migration source is changed by this
preparation pass. Runtime behavior and PowerShell execution remain UNVERIFIED.

## Files and coverage

| File | Purpose |
| --- | --- |
| `orders/test_retired_handover.py` | Retired-route compatibility, historical-record retention, and secret scrubbing |
| `orders/test_inventory_concurrency.py` | Synthetic checkout/rejection/restoration, rollback, and bounded PostgreSQL concurrency |
| `tools/isolated_tests/isolated_run.py` | Explicitly approved, guarded VM-local launcher template |
| `tools/isolated_tests/prepare_test_vm.ps1` | Offline-VM evidence checks and bounded supervision template |

The intended selection also includes Phase 1 acceptance in
`shop_admin.tests.ShopAdminInventoryTests`, `orders.tests`,
`orders.test_staff_handover`,
`shop_admin.test_super_admin_api`,
`customers.tests.CustomerCheckoutOrderContractTests`, and
`shop_admin.test_shop_approval_permissions`.

Process-local cache tests do **not** prove distributed throttling. A separately
approved future test must use independent worker processes against the intended
shared cache, covering a single account across workers, cache outages, and
concurrent requests. No shared-cache infrastructure is provisioned here.

## Known security failures: do not hide or waive

The following remain ordinary security regression tests. They are **KNOWN EXPECTED
FAILURES under current source**, predicted by source inspection, NOT observed test
results. There is no `expectedFailure`, `xfail`, inverted assertion, or security
skip to turn these failures into success:

- Historical OTP issuance-quota mutation tests are retired with the removed
  issuance workflow; historical rows remain protected by model/audit retention
  tests.

Current issuance logic counts mutable `issued`/`reissued` audit events. Deleting,
retiming, or changing the type of a recent event can reopen the quota. The tests
require the fourth issuance to remain rejected, whether future protection rejects
the mutation or retains an independent authoritative counter. Production logic
is deliberately unchanged in this phase. A failing run must remain nonzero and
its database must be retained, including failures predicted here.

Checkout currently locks cart items joined to product/shop rows before its
explicit shop lock; high-value rejection locks shop/order then product. The new
inventory tests must expose unsafe overlapping acquisition, rather than silently
changing production ordering or treating a deadlock as a successful checkout.
PostgreSQL-only concurrency cases skip other vendors; SQLite is not evidence of
PostgreSQL locking correctness.

The concrete inventory contention case is
`orders.test_inventory_concurrency.InventoryConcurrencyTests.test_checkout_and_high_value_rejection_preserve_inventory`.
It is a **KNOWN LOCK-ORDER RISK / POSSIBLE EXPECTED FAILURE**, not a measured
failure: a PostgreSQL plan that acquires product before shop can expose SQLSTATE
`40P01` (deadlock) or `55P03` (lock timeout). Its required result remains successful
checkout/rejection with stock `8 - 1 + 2 = 9`; contention errors are not accepted as
success. Actual contention is established using real endpoint locks and
`pg_blocking_pids`, without replacing the business operations or adding a
synthetic product lock. Other new cases cover duplicate restoration, oversell,
OTP/checkout overlap, post-write rollback, and bounded waiting. These nine cases
use `TransactionTestCase` and explicit worker-connection cleanup.

## Isolation assumptions and manual approval

A separate port alone is not a security boundary. Use a **new disposable Windows
Hyper-V VM with every VM network adapter disconnected at the host**, no shared
host drives/credential mounts, and a sanitized source/dependency copy at
`D:\drinqo_backend`. Do not copy `.env`, `.aws`, development database files,
credential documents, host PostgreSQL data, or user credential profiles.

An independent host operator must identify the exact disposable VM, inspect all
of its adapters/switch connections, and attest that it is not the development
machine. Record the guest SMBIOS UUID through that VM's console and compare it
with the guest checks. A guest environment variable, JSON approval file, UUID,
or virtualization model alone cannot prove network isolation. The operator's
out-of-band host verification is mandatory. Guest hardware/adapter/route checks
are corroboration and may conservatively refuse unsupported environments.

No Python or PowerShell template can prove that privileged host administrators
will not reconnect networking later. Approval assumes trustworthy operators and
reviewed test code. It does not sandbox malicious code with access to host or
hypervisor administration.

## Database identity and roles

The proposed server is PostgreSQL **18**, loopback **127.0.0.1**, port **55432**,
with a new per-run data directory, cluster tag, system identifier, test role,
connection database, and test database. Compare the online system identifier
with the independently read new cluster control file before database/role
provisioning. Repeat server identity checks before Django setup and destructive
cleanup. A mismatch must abort without trying another address or port.

The bootstrap role is a superuser only in the fresh isolated cluster. The test
role needs LOGIN and CREATEDB for Django's fresh database; it must not be a
superuser, create roles, replicate, or bypass RLS. Identity inspection permissions
are restricted to this isolated server. CREATEDB is cluster-wide, not a PostgreSQL
database-name restriction: the launcher additionally validates names/provenance.
None of these role grants apply to development or production clusters.

No existing test database may be reused. No collision prompt may approve a DROP.
No `--keepdb`, `--noinput`, parallel Django workers, mirror, or extra database alias
is allowed. Threaded PostgreSQL regression tests still use their own connections.
Only a database proven created by this run, with matching system identifier,
name, owner and OID, may be dropped under a separate explicit cleanup approval.
Unknown collisions and unidentified partial setup databases must be preserved.

## Synthetic configuration and secrets

Disable dotenv loading **before importing project settings**. The existing test
settings import base settings, so this ordering matters. Clear inherited libpq
connection/service/password options and external provider credentials without
displaying their values. Use explicit host/port/database/user/password parameters
and prevent password/service-file fallback.

Generate fresh Django/JWT values and independent Fernet/HMAC keys; override only
temporary in-process test configuration. Do not edit application settings or an
environment file. Test fixtures' generated keys are also synthetic. Any saved
run-secret artifact requires a private ACL, must not be printed or committed,
and must be discarded with the isolated VM after approved investigation/cleanup.

Use only the single `default` PostgreSQL alias, zero persistent connection age,
local-memory email/cache, and the existing HTTP-blocking runner where applicable.
That runner blocks `requests`, not raw sockets, subprocesses, every SDK, or every
transport. The disconnected VM supplies the external network boundary.

## Deadlines and lifecycle

| Limit | Required value |
| --- | ---: |
| Connection timeout | 5 seconds |
| PostgreSQL lock timeout | 5 seconds |
| Statement timeout | 30 seconds |
| Idle-in-transaction timeout | 60 seconds |
| Outer test process deadline | 20 minutes |

Barrier/future deadlines do not replace SQL deadlines or the process supervisor.
An executor's normal shutdown can wait for a blocked thread. Worker connections
are explicitly closed, and an outer Windows process-tree boundary must terminate
stalled descendants without targeting unrelated PostgreSQL processes.

A later test run internally creates/migrates/flushes a fresh test database through
Django. That requires explicit approval even if no standalone `manage.py migrate`
command is used. Interrupted creation, failed migrations, test failures, timeouts,
and unknown database identities require preservation, not reuse or automatic
retry. Do not stop unrelated services or delete any development directory.

## Approval gates

1. Source review only: these artifacts; no execution is authorized by their presence.
2. Independent host approval of the disposable VM and disconnected network.
3. Explicit provisioning approval for the new directory/cluster/role/database.
4. Review identity evidence, then separate test approval including migration and flush.
5. Review results; separate approval for identity-checked test-database cleanup.
6. Archive required sanitized evidence, then separately discard the disposable VM.

Before approval, verify the offline VM has the required Python/Django/DRF,
cryptography, psycopg/libpq, Pillow and project dependencies from approved offline
artifacts. Local package metadata does not establish VM runtime compatibility.
No dependency installation is performed by this preparation pass.

## Reviewable launcher stages

All commands in this section are **NOT EXECUTED** and are examples for a future,
separately approved run **inside the verified guest**. Use the PowerShell
supervisor, not direct standalone worker calls. Paths are intentionally local and
contain no spaces. The approval file must be directly under
`D:\drinqo_test_control` with a simple `.json` filename; UNC paths and redirects
are rejected. Do not weaken a refused guard to make a VM qualify.

The independent host operator must record an approval artifact with this schema
at a local guest path such as `D:\drinqo_test_control\host-approval.json`:

```json
{
  "approved": false,
  "reviewer": "<independent host operator>",
  "vm_name": "<exact disposable Hyper-V VM name>",
  "guest_uuid": "<UUID recorded through the guest console>",
  "guest_computer": "<guest COMPUTERNAME>",
  "development_host_uuid": "<different UUID independently recorded for development>",
  "reviewed_at_utc": "<current UTC ISO-8601 timestamp ending in Z>",
  "all_vm_adapters_disconnected": false,
  "no_host_drives_or_database_mounts": false,
  "sanitized_source_and_dependencies": false
}
```

This example deliberately refuses execution. Only the independent reviewer may
attest true values after actual checks. The artifact is not a signed security
certificate. Its hash is pinned by the run manifest to detect subsequent changes.
Approval expires after 24 hours; replacing it breaks the hash pin. An expired or
failed run needs independent investigation or VM disposal, not a manifest bypass.

Host-side verification examples, also **NOT EXECUTED**:

```powershell
# Run on the independently identified Hyper-V HOST, not inside this launcher.
Get-VM -Name '<exact disposable VM name>' |
    Select-Object Name, Id, State, Generation
Get-VMNetworkAdapter -VMName '<exact disposable VM name>' |
    Select-Object Name, SwitchName, Status, MacAddress
```

The operator must verify every adapter is disconnected, not merely an absence of
a default route. Confirm no additional NIC, passthrough interface, enhanced-session
drive sharing, credential profile, host mount, or development database directory
is available. Record the guest UUID through the intended VM's console and compare
it with the independent development-host record. Do not reconnect networking to
transfer the approval artifact; use an approved sanitized offline transfer.

Guest commands for future approval:

```powershell
Set-Location -LiteralPath 'D:\drinqo_backend'

# Approval 1: create only a new private run directory, synthetic artifacts,
# initialize its PostgreSQL 18 cluster, pin control identity, and start that
# cluster on 127.0.0.1:55432. No project test database/migrations yet.
& '.\tools\isolated_tests\prepare_test_vm.ps1' `
    -Action Prepare -ConfirmOfflineVM OFFLINE-DRINQO-VM `
    -HostApprovalFile 'D:\drinqo_test_control\host-approval.json' `
    -ApproveProvision

# Substitute ONLY the newly reported run directory; do not reuse an old run.
$run = 'D:\drinqo_otp_test_runs\otp_<12-lowercase-hex-run-id>'

# Approval 2: identity-check the running isolated server, then create its fresh
# restricted test role and empty connection database. Still no Django migration.
& '.\tools\isolated_tests\prepare_test_vm.ps1' `
    -Action Provision -RunDirectory $run `
    -ConfirmOfflineVM OFFLINE-DRINQO-VM -ApproveProvision

# Explicitly requested metadata read using the new role, after Provision.
& '.\tools\isolated_tests\prepare_test_vm.ps1' `
    -Action Verify -RunDirectory $run -ConfirmOfflineVM OFFLINE-DRINQO-VM

# Approval 3: creates/migrates/flushes its unique fresh test database and runs
# the fixed regression selection. Known security failures remain nonzero.
& '.\tools\isolated_tests\prepare_test_vm.ps1' `
    -Action Test -RunDirectory $run `
    -ConfirmOfflineVM OFFLINE-DRINQO-VM -ApproveTests

# Approval 4: ONLY a fully successful run is eligible for this narrow DROP.
# Current known failures should prevent it; failed/partial runs are preserved.
& '.\tools\isolated_tests\prepare_test_vm.ps1' `
    -Action DropTestDatabase -RunDirectory $run `
    -ConfirmOfflineVM OFFLINE-DRINQO-VM -ApproveDrop
```

`Prepare` starts a private process cluster through `pg_ctl`; it does not register
or change a Windows PostgreSQL service. No preparation action in this document
has been performed. Provision/Test are separate stages, with distinct approvals.

## Launcher safety review and remaining limits

- Checks use explicit exceptions, never Python `assert` as a safety boundary.
- The collision-safe creation replacement performs one CREATE attempt and never
  enters Django's DROP/prompt fallback, even if a collision happens after precheck.
  Stdin is EOF as an additional defense. Unsupported runner arguments are not
  exposed through the supervisor.
- The default runner teardown is deliberately replaced with connection closure:
  databases are retained even after success. A later approved drop rechecks
  server identity and the recorded creation OID/owner. There is no FORCE drop.
- Creation provenance is recorded before migration setup. If a process dies
  before provenance is recorded, the database is unknown and must not be dropped
  by this helper. Interrupted setup and failed migrations do not create a reuse
  permission. Failure/skip/expected-failure outcomes do not qualify as success.
  The Python worker's provisional `runner-success.json` is insufficient: the
  PowerShell supervisor writes `supervisor-success.json` only after it observes
  the worker exit with code zero. A timeout, process-tree kill, nonzero exit, or
  interruption therefore leaves the run ineligible for helper-driven deletion.
- Synthetic secrets are plaintext **new test-only** run artifacts protected by
  a private directory ACL for the current guest user and SYSTEM. The initdb
  password utility file is removed after successful initialization. Nothing is
  printed or committed; do not archive secret artifacts with public test logs.
- Private run-directory and data-directory identity checks reject redirects.
  SQL identifiers are quoted; database passwords are not command-line arguments.
- The supervisor removes inherited interpreter-hook variables and starts the
  worker with `-I -B`; the worker requires isolated Python mode. This reduces
  accidental startup-hook imports, but the virtualenv and installed packages
  still require a trusted offline source. It is not a malicious-code sandbox.
- PostgreSQL identity checks cover major version, server address/port, exact
  data directory, cluster tag/system identifier, current database/role and
  database ownership/provenance. Never connect to development to compare them.
- The test role's CREATEDB and diagnostic grants cannot be restricted to a name
  prefix using ordinary PostgreSQL roles. Reviewed launcher code constrains the
  selected run; arbitrary malicious test code can still use sockets, subprocesses,
  aliases or run-secret files inside the VM. This is not a sandbox for untrusted
  tests. The clean disconnected VM is the actual development/network boundary.
- Host-review JSON is trusted human evidence, not automatic hypervisor proof.
  Same-user or privileged tampering is outside the accidental-execution guard.
- PowerShell supervises each utility/worker deadline. The test worker additionally
  uses a hard 20-minute exit and kill-on-close Windows Job Object so blocked
  thread shutdown cannot indefinitely extend the run. Win32 containment and
  PowerShell/native quoting are UNVERIFIED until separately approved execution.
- No cluster-stop, recursive-directory-delete, broad process-kill or unrelated
  service operation is implemented. Preserve failed environments, archive only
  sanitized results, then obtain separate approval to shut down/discard the
  disposable VM. Never add a force-cleanup bypass for failed or unknown runs.

## Static validation and acceptance

Only Python AST parsing is permitted for this preparation pass; do not import
the launcher/test modules, invoke PowerShell, initialize Django, run `--help`,
connect to PostgreSQL, or invoke PostgreSQL binaries to validate these templates.
PowerShell syntax has been reviewed as source, not executed or parser-validated.

Preparation-time validation: AST parsing only for the three newly created Python
files. A final fingerprint comparison checks the previously existing application,
settings, test-helper and orders-migration sources remained unchanged. No imports,
database commands, tests, launcher actions, PostgreSQL utilities or service
operations are used for this validation.

A future complete run requires PostgreSQL concurrency cases to execute, not skip;
all strict assertions to pass; exactly-once completion/restoration; no unexpected
negative stock or completion; bounded waits; and matching identity throughout.
Known expected failures are genuine failures requiring a subsequent authorized
production-source correction. A partial green subset, process-local cache test,
or successful provisioning does not establish security or production readiness.
