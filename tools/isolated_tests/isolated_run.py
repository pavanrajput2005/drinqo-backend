"""UNEXECUTED TEMPLATE. Entry points require separate approval and an offline VM.

This module is not an application settings module or test-discovery module.
Do not import it from application code. All Django, driver and OS actions are
inside main-invoked functions. Host approval is a trusted manual prerequisite,
not a mathematical proof of network isolation.
"""
import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime, timedelta, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import threading

ROOT = Path(r"D:\drinqo_otp_test_runs")
SOURCE = Path(r"D:\drinqo_backend")
PG_BIN = Path(r"C:\Program Files\PostgreSQL\18\bin")
APPROVAL_ROOT = Path(r"D:\drinqo_test_control")
TIMEOUT_OPTIONS = (
    "-c lock_timeout=5s -c statement_timeout=30s "
    "-c idle_in_transaction_session_timeout=60s"
)
TEST_LABELS = (
    "accounts.tests",
    "accounts.test_phone_identity",
    "customers.tests",
    "customers.test_customer_security_fixes",
    "shop_admin.tests.ShopAdminInventoryTests",
    "orders.tests",
    "orders.test_staff_handover",
    "orders.test_retired_handover",
    "orders.test_inventory_concurrency",
    "orders.test_payments",
    "orders.test_razorpay_gateway",
    "orders.test_payment_operations",
    "shop_admin.test_super_admin_api",
    "customers.tests.CustomerCheckoutOrderContractTests",
    "shop_admin.test_shop_approval_permissions",
)
_JOB_HANDLE = None


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def private_json(path, value):
    """New synthetic run files inherit the supervisor's private directory ACL."""
    require(not path.exists(), "Refusing to overwrite an existing run artifact.")
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2)


def safe_run_directory(value):
    # Examine original paths BEFORE resolve(), which follows junctions/symlinks.
    original = Path(value)
    require(original.is_absolute() and original.parent == ROOT, "Run directory must be directly under the dedicated test root.")
    require(not ROOT.is_symlink() and not ROOT.is_junction(), "Test root must not redirect elsewhere.")
    require(not original.is_symlink() and not original.is_junction(), "Run directory must not redirect elsewhere.")
    require(re.fullmatch(r"otp_[0-9a-f]{12}", original.name) is not None, "Unexpected run directory name.")
    require(original.is_dir(), "Run directory does not exist.")
    path = original.resolve()
    require(path.parent == ROOT.resolve(), "Unexpected resolved run directory.")
    return path


def vm_evidence():
    """Fresh guest corroboration. The external host review remains indispensable."""
    require(os.name == "nt", "This launcher is only for the approved Windows VM.")
    command = (
        "$ErrorActionPreference='Stop';"
        "$c=Get-CimInstance Win32_ComputerSystem;"
        "$p=Get-CimInstance Win32_ComputerSystemProduct;"
        "$a=@(Get-NetAdapter -IncludeHidden | Where-Object Status -eq 'Up');"
        "$r=@(Get-NetRoute | Where-Object {$_.DestinationPrefix -in @('0.0.0.0/0','::/0')});"
        "[pscustomobject]@{manufacturer=$c.Manufacturer;model=$c.Model;"
        "uuid=$p.UUID;computer=$env:COMPUTERNAME;up_adapters=$a.Count;"
        "default_routes=$r.Count}|ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True, text=True, timeout=20, check=False,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    require(result.returncode == 0, "Guest isolation evidence could not be collected.")
    evidence = json.loads(result.stdout)
    require(
        evidence["manufacturer"] == "Microsoft Corporation"
        and evidence["model"] == "Virtual Machine",
        "Expected a dedicated Hyper-V guest; refusing this host.",
    )
    require(evidence["up_adapters"] == 0 and evidence["default_routes"] == 0,
            "Guest networking is not demonstrably disconnected.")
    return evidence


def verify_host_review(path, evidence, expected_hash=None):
    original = Path(path)
    require(
        original.is_absolute() and original.parent == APPROVAL_ROOT
        and re.fullmatch(r"[A-Za-z0-9_-]+\.json", original.name) is not None,
        "Host approval must be a local JSON file directly under D:\\drinqo_test_control.",
    )
    require(not APPROVAL_ROOT.is_symlink() and not APPROVAL_ROOT.is_junction()
            and not original.is_symlink() and not original.is_junction(),
            "Host approval paths must not redirect to shared/network paths.")
    raw = original.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if expected_hash is not None:
        require(digest == expected_hash, "Host approval artifact changed.")
    review = json.loads(raw)
    require(review.get("approved") is True, "Independent host review was not approved.")
    require(review.get("reviewer") and review.get("vm_name"), "Host reviewer and VM identity are required.")
    require(review.get("all_vm_adapters_disconnected") is True, "Host must verify every VM adapter disconnected.")
    require(review.get("no_host_drives_or_database_mounts") is True, "Host mounts were not cleared.")
    require(review.get("sanitized_source_and_dependencies") is True, "Source/dependency sanitization not approved.")
    require(str(review.get("guest_uuid", "")).lower() == str(evidence["uuid"]).lower(), "Guest UUID mismatch.")
    require(review.get("guest_computer") == evidence["computer"], "Guest computer mismatch.")
    require(str(review.get("development_host_uuid", "")).lower() not in ("", str(evidence["uuid"]).lower()),
            "Development host and guest must have distinct independently recorded UUIDs.")
    reviewed = datetime.fromisoformat(review["reviewed_at_utc"].replace("Z", "+00:00"))
    require(reviewed.tzinfo is not None, "Host review must contain a UTC timestamp.")
    age = datetime.now(timezone.utc) - reviewed.astimezone(timezone.utc)
    require(timedelta(0) <= age <= timedelta(hours=24), "Independent host review must be fresh within 24 hours.")
    return digest


def clean_environment(run):
    """Discard inherited routing and provider credentials before any app import."""
    for key in list(os.environ):
        if key.startswith(("PG", "DRINQO_TEST_", "DB_", "DJANGO_", "SIMPLE_JWT", "MSG91_", "GOOGLE_", "AWS_", "AZURE_")):
            os.environ.pop(key, None)
    for key in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONUSERBASE", "PYTHONINSPECT"):
        os.environ.pop(key, None)
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    os.environ["PYTHONNOUSERSITE"] = "1"
    os.environ["PGPASSFILE"] = str(run / "nonexistent.pgpass")
    os.environ["PGSERVICEFILE"] = str(run / "nonexistent.pg_service.conf")
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"


def arm_process_tree_job():
    """A killed test worker must also kill any child processes it created."""
    global _JOB_HANDLE
    class Basic(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]
    class IO(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
        )]
    class Extended(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", IO),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    handle = kernel.CreateJobObjectW(None, None)
    require(bool(handle), "Cannot create a bounded worker Job Object.")
    info = Extended()
    info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    require(bool(kernel.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info))),
            "Cannot configure worker process-tree containment.")
    require(bool(kernel.AssignProcessToJobObject(handle, kernel.GetCurrentProcess())),
            "Cannot assign worker to its Job Object; refusing unbounded execution.")
    _JOB_HANDLE = handle  # Deliberately kept open until OS process termination.


def read_manifest(run):
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    require(manifest["run_id"] == run.name[4:], "Run identifier mismatch.")
    require(manifest["source"] == str(SOURCE), "Unexpected source location.")
    require(manifest["data_directory"] == str(run / "pgdata"), "Unexpected cluster directory.")
    require(not (run / "pgdata").is_symlink() and not (run / "pgdata").is_junction(),
            "Cluster data directory must not redirect elsewhere.")
    require(manifest["host"] == "127.0.0.1" and manifest["port"] == 55432, "Unexpected database endpoint.")
    run_id = manifest["run_id"]
    for key, expected in {
        "role": f"drinqo_test_{run_id}",
        "connection_database": f"drinqo_test_connection_{run_id}",
        "test_database": f"drinqo_test_database_{run_id}",
        "cluster_tag": f"drinqo_otp_{run_id}",
    }.items():
        require(manifest[key] == expected, "Unexpected role/database/cluster identity.")
    evidence = vm_evidence()
    verify_host_review(manifest["approval_file"], evidence, manifest["approval_sha256"])
    require(str(evidence["uuid"]).lower() == manifest["guest_uuid"].lower(), "Run moved to another guest.")
    return manifest


def prepare_manifest(run, approval):
    evidence = vm_evidence()
    digest = verify_host_review(approval, evidence)
    require(all(path.name in {"01-manifest.stdout.log", "01-manifest.stderr.log"}
                for path in run.iterdir()), "Preparing requires an unused run directory.")
    run_id = run.name[4:]
    private_json(run / "manifest.json", {
        "run_id": run_id, "source": str(SOURCE), "data_directory": str(run / "pgdata"),
        "host": "127.0.0.1", "port": 55432, "cluster_tag": f"drinqo_otp_{run_id}",
        "role": f"drinqo_test_{run_id}", "connection_database": f"drinqo_test_connection_{run_id}",
        "test_database": f"drinqo_test_database_{run_id}", "guest_uuid": evidence["uuid"],
        "approval_file": str(Path(approval).resolve()), "approval_sha256": digest,
    })
    private_json(run / "synthetic-secrets.json", {
        "bootstrap_password": secrets.token_urlsafe(48), "role_password": secrets.token_urlsafe(48),
        "django_secret": secrets.token_urlsafe(64), "jwt_key": secrets.token_urlsafe(64),
    })
    # This is the only plaintext password utility file, private and deleted after initdb.
    values = json.loads((run / "synthetic-secrets.json").read_text(encoding="utf-8"))
    (run / "initdb-password.txt").write_text(values["bootstrap_password"], encoding="utf-8")
    print("Prepared new synthetic run artifacts; no database utility or connection used.")


def record_control(run, manifest):
    require((run / "pgdata" / "PG_VERSION").read_text().strip() == "18", "Expected PostgreSQL 18 data directory.")
    result = subprocess.run(
        [str(PG_BIN / "pg_controldata.exe"), "-D", manifest["data_directory"]],
        capture_output=True, text=True, check=False, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    require(result.returncode == 0, "Cannot read newly initialized control file.")
    match = re.search(r"Database system identifier:\s*(\d+)", result.stdout)
    require(match is not None, "Cannot pin system identifier; do not guess/localize around this failure.")
    private_json(run / "control-identity.json", {"system_identifier": match.group(1)})
    print("Pinned new cluster control identity; no database connection used.")


def secrets_for(run):
    return json.loads((run / "synthetic-secrets.json").read_text(encoding="utf-8"))


def control_connection(run, manifest, bootstrap=False):
    import psycopg
    values = secrets_for(run)
    user = "drinqo_test_bootstrap" if bootstrap else manifest["role"]
    password = values["bootstrap_password"] if bootstrap else values["role_password"]
    expected_id = json.loads((run / "control-identity.json").read_text())["system_identifier"]
    connection = psycopg.connect(
        host="127.0.0.1", port=55432, dbname="postgres", user=user, password=password,
        connect_timeout=5, options=TIMEOUT_OPTIONS, autocommit=True,
        application_name=f"drinqo_isolated_{manifest['run_id']}",
    )
    try:
        row = connection.execute(
            "SELECT current_setting('server_version_num')::int, inet_server_addr()::text, "
            "inet_server_port(), current_setting('data_directory'), current_setting('cluster_name'), "
            "(pg_control_system()).system_identifier::text, current_database(), current_user"
        ).fetchone()
        require(
            180000 <= row[0] < 190000 and row[1] == "127.0.0.1" and row[2] == 55432
            and Path(row[3]).resolve() == Path(manifest["data_directory"]).resolve()
            and row[4] == manifest["cluster_tag"] and row[5] == expected_id
            and row[6] == "postgres" and row[7] == user,
            "PostgreSQL identity mismatch; no subsequent provisioning/test/drop allowed.",
        )
        return connection
    except BaseException:
        connection.close()
        raise


def database_identity(connection, name):
    row = connection.execute(
        "SELECT oid, pg_get_userbyid(datdba) FROM pg_database WHERE datname=%s", (name,),
    ).fetchone()
    return list(row) if row else None


def provision(run, manifest):
    from psycopg import sql
    require(not (run / "provision-attempt.json").exists(), "No provisioning retry/reuse is allowed.")
    with control_connection(run, manifest, bootstrap=True) as connection:
        require(not connection.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (manifest["role"],)).fetchone(),
                "Test role already exists.")
        require(database_identity(connection, manifest["connection_database"]) is None
                and database_identity(connection, manifest["test_database"]) is None,
                "A target database already exists.")
        private_json(run / "provision-attempt.json", {"attempted": True})
        values = secrets_for(run)
        connection.execute(sql.SQL(
            "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER CREATEDB NOCREATEROLE "
            "NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 16"
        ).format(sql.Identifier(manifest["role"]), sql.Literal(values["role_password"])))
        connection.execute(sql.SQL("CREATE DATABASE {} OWNER {} TEMPLATE template0").format(
            sql.Identifier(manifest["connection_database"]), sql.Identifier(manifest["role"]),
        ))
        connection.execute("REVOKE CONNECT ON DATABASE postgres FROM PUBLIC")
        connection.execute(sql.SQL("GRANT CONNECT ON DATABASE postgres TO {}").format(sql.Identifier(manifest["role"])))
        connection.execute(sql.SQL("GRANT pg_read_all_settings TO {}").format(sql.Identifier(manifest["role"])))
        connection.execute(sql.SQL("GRANT EXECUTE ON FUNCTION pg_control_system() TO {}").format(sql.Identifier(manifest["role"])))
    with control_connection(run, manifest) as connection:
        identity = database_identity(connection, manifest["connection_database"])
        require(identity is not None and identity[1] == manifest["role"], "Connection database owner mismatch.")
        private_json(run / "provisioned.json", {"connection_identity": identity})
    print("Dedicated role and connection database provisioned in the verified cluster only.")


def configure_django(run, manifest):
    require(not (SOURCE / ".env").exists(), "Sanitized VM source must omit .env.")
    for path in (SOURCE / ".aws", SOURCE / "serviceAccountKey.json"):
        require(not path.exists(), "Sanitized VM source contains prohibited credential artifacts.")
    values = secrets_for(run)
    os.environ.update({
        "DRINQO_TEST_MODE": "1", "DRINQO_TEST_DB_NAME": manifest["connection_database"],
        "DRINQO_TEST_DATABASE_NAME": manifest["test_database"], "DRINQO_TEST_DB_USER": manifest["role"],
        "DRINQO_TEST_DB_PASSWORD": values["role_password"], "DRINQO_TEST_DB_HOST": "127.0.0.1",
        "DRINQO_TEST_DB_PORT": "55432", "DJANGO_SETTINGS_MODULE": "config.test_settings",
    })
    sys.path.insert(0, str(SOURCE))
    module = importlib.import_module("config.test_settings")
    require(set(module.DATABASES) == {"default"}, "Unexpected database aliases.")
    database = module.DATABASES["default"]
    require(
        database["ENGINE"] == "django.db.backends.postgresql"
        and database["HOST"] == "127.0.0.1" and database["PORT"] == "55432"
        and database["NAME"] == manifest["connection_database"] and database["USER"] == manifest["role"]
        and database["PASSWORD"] == values["role_password"]
        and database["TEST"]["NAME"] == manifest["test_database"] and not database["TEST"].get("MIRROR"),
        "Django database routing differs from the approved run.",
    )
    database["OPTIONS"] = {"connect_timeout": 5, "options": TIMEOUT_OPTIONS}
    # Keep the connection through TestCase's transaction lifecycle. A zero
    # lifetime can close psycopg during request teardown inside that wrapper.
    database["CONN_MAX_AGE"] = None
    module.SECRET_KEY = values["django_secret"]
    module.SIMPLE_JWT_SIGNING_KEY = values["jwt_key"]
    module.SIMPLE_JWT = {**module.SIMPLE_JWT, "SIGNING_KEY": values["jwt_key"]}
    require(module.CACHES["default"]["BACKEND"] == "django.core.cache.backends.locmem.LocMemCache",
            "Unexpected external cache configuration.")
    require(module.TEST_RUNNER == "config.test_runner.IsolatedTestRunner", "Unexpected project test runner.")
    import django
    django.setup()


def test(run, manifest):
    # Failure markers are never removed. A failed/migration-interrupted run is not reusable.
    require((run / "provisioned.json").exists(), "Provisioning not verified.")
    require(not (run / "test-attempt.json").exists(), "This run already attempted tests; create a fresh approved run.")
    with control_connection(run, manifest) as connection:
        provisioned = json.loads((run / "provisioned.json").read_text())
        require(database_identity(connection, manifest["connection_database"]) == provisioned["connection_identity"],
                "Connection database OID/owner changed since provisioning.")
        require(database_identity(connection, manifest["test_database"]) is None, "Existing test database: abort.")
    private_json(run / "test-attempt.json", {"attempted": True})
    sys.stdin = open(os.devnull)
    configure_django(run, manifest)
    from unittest.mock import patch
    from django.db import connections
    from django.db.backends.postgresql.creation import DatabaseCreation
    from config.test_runner import IsolatedTestRunner

    def create_without_collision_drop(creation, verbosity, autoclobber, keepdb=False):
        require(creation.connection.alias == "default" and not autoclobber and not keepdb,
                "Unsafe database creation arguments.")
        configured = creation.connection.settings_dict
        require(
            configured["ENGINE"] == "django.db.backends.postgresql"
            and configured["HOST"] == "127.0.0.1" and str(configured["PORT"]) == "55432"
            and configured["NAME"] == manifest["connection_database"]
            and configured["USER"] == manifest["role"]
            and configured["PASSWORD"] == secrets_for(run)["role_password"]
            and configured["TEST"]["NAME"] == manifest["test_database"]
            and not configured["TEST"].get("MIRROR")
            and configured["OPTIONS"] == {"connect_timeout": 5, "options": TIMEOUT_OPTIONS},
            "Database settings changed after test discovery; abort before creation.",
        )
        name = creation._get_test_db_name()
        require(name == manifest["test_database"], "Unexpected test database name.")
        with control_connection(run, manifest) as connection:
            require(database_identity(connection, name) is None, "Test database collision.")
            # One attempt only: errors propagate; Django's DROP/prompt fallback is never called.
            from psycopg import sql
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
            identity = database_identity(connection, name)
            require(identity is not None and identity[1] == manifest["role"], "Created test database owner mismatch.")
            private_json(run / "created-test-database.json", {"identity": identity})
        return name

    class GuardedRunner(IsolatedTestRunner):
        def setup_databases(self, **kwargs):
            require(not self.keepdb and self.interactive and not self.parallel, "Unsafe test runner options.")
            require(set(connections) == {"default"}, "Unexpected runtime aliases.")
            with patch.object(DatabaseCreation, "_create_test_db", create_without_collision_drop):
                return super().setup_databases(**kwargs)

        def teardown_databases(self, old_config, **kwargs):
            # Deliberately never call default DROP, even on success.
            # Failure/partial setup retains the database for review.
            print("Test database preserved. Separate approved drop action is required.")
            connections.close_all()

        def run_suite(self, suite, **kwargs):
            result = super().run_suite(suite, **kwargs)
            private_json(run / "test-result-counts.json", {
                "tests_run": result.testsRun, "failures": len(result.failures),
                "errors": len(result.errors), "skips": len(result.skipped),
                "expected_failures": len(result.expectedFailures),
                "unexpected_successes": len(result.unexpectedSuccesses),
            })
            require(not result.skipped and not result.expectedFailures and not result.unexpectedSuccesses,
                    "Skipped/expected-failure tests cannot establish this PostgreSQL security run.")
            return result

    runner = GuardedRunner(verbosity=2, interactive=True, keepdb=False, parallel=0)
    failures = runner.run_tests(TEST_LABELS)
    if failures:
        private_json(run / "failed.json", {"failed": True})
        raise RuntimeError("Regression failures: database preserved; do not declare success.")
    # Do not write a terminal success marker here. The supervising PowerShell
    # process writes it only after this worker has actually exited with code 0.
    # If the worker or supervisor is interrupted, preservation wins over cleanup.
    private_json(run / "runner-success.json", {"runner_success": True})
    print("Runner reported success; supervisor exit attestation is still required.")


def drop_test_database(run, manifest):
    """Only the unique, successfully tested database may be removed here."""
    from psycopg import sql
    require(
        (run / "runner-success.json").exists()
        and (run / "supervisor-success.json").exists()
        and not (run / "failed.json").exists(),
        "Failed or interrupted runs must be preserved; this cleanup action refuses them.",
    )
    expected = json.loads((run / "created-test-database.json").read_text())["identity"]
    with control_connection(run, manifest) as connection:
        require(database_identity(connection, manifest["test_database"]) == expected,
                "Refusing teardown: database OID/owner changed.")
        require(expected[1] == manifest["role"], "Unexpected database owner.")
        # No FORCE: unexpected active sessions cause failure instead of termination.
        connection.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(manifest["test_database"])))
    private_json(run / "dropped.json", {"dropped": True})
    print("Only the pinned, successfully tested database was dropped.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare-manifest", "record-control", "provision", "test", "verify", "drop"))
    parser.add_argument("--run-directory", required=True)
    parser.add_argument("--host-approval-file")
    parser.add_argument("--approval", required=True)
    args = parser.parse_args()
    required = {
        "prepare-manifest": "APPROVE_OFFLINE_PREPARATION", "record-control": "APPROVE_OFFLINE_PREPARATION",
        "provision": "APPROVE_ISOLATED_PROVISIONING", "test": "APPROVE_ISOLATED_TEST_EXECUTION",
        "verify": "APPROVE_ISOLATED_IDENTITY_READ", "drop": "APPROVE_PINNED_SUCCESSFUL_TEST_DATABASE_DROP",
    }
    require(args.approval == required[args.mode], "Explicit action approval is missing.")
    require(sys.flags.isolated, "Use the reviewed supervisor, which launches Python with -I -B.")
    if args.mode == "test":
        # Independent hard exit survives a killed/interrupted PowerShell supervisor
        # and cannot be delayed by executor shutdown or finally blocks.
        def hard_deadline():
            threading.Event().wait(1200)
            os.write(2, b"Test deadline exceeded; preserve the isolated environment.\n")
            os._exit(124)
        threading.Thread(target=hard_deadline, daemon=True).start()
    run = safe_run_directory(args.run_directory)
    clean_environment(run)
    arm_process_tree_job()
    if args.mode == "prepare-manifest":
        require(args.host_approval_file is not None, "Independent host approval artifact is required.")
        prepare_manifest(run, args.host_approval_file)
        return 0
    manifest = read_manifest(run)
    if args.mode == "record-control":
        record_control(run, manifest)
    elif args.mode == "provision":
        provision(run, manifest)
    elif args.mode == "test":
        test(run, manifest)
    elif args.mode == "verify":
        with control_connection(run, manifest):
            pass
        print("Cluster identity verified; no application/test database contents inspected.")
    else:
        drop_test_database(run, manifest)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        # Never print driver parameters, SQL/password exceptions, or raw payloads.
        print("Isolated launcher aborted. Preserve the run; review source and sanitized server logs.", file=sys.stderr)
        raise SystemExit(1)
