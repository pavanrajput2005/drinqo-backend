"""Fail-closed runner for one manually created, local disposable PostgreSQL cluster.

This deliberately does *not* provision a server, create roles, or delete a
database.  It may create exactly one named Django test database after checking
the independently supplied cluster identity.  Test databases are retained,
including on success, for explicit manual review.
"""

import argparse
import getpass
import importlib
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import threading
from unittest.mock import patch


SOURCE = Path(r"D:\drinqo_backend")
TIMEOUT_OPTIONS = (
    "-c lock_timeout=5s -c statement_timeout=30s "
    "-c idle_in_transaction_session_timeout=60s"
)
TEST_LABELS = (
    "accounts.tests",
    "accounts.test_phone_identity",
    "accounts.test_age_self_declaration",
    "customers.tests",
    "customers.test_customer_security_fixes",
    "customers.test_age_proof_hardening",
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
    "shop_admin.test_license_document_review",
    "shop_admin.test_onboarding_coordinates",
    "shop_admin.test_location_change_requests",
)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def expected_names(suffix):
    require(re.fullmatch(r"[0-9]{6}", suffix) is not None, "Run suffix must be six digits.")
    return {
        "role": f"drinqo_test_{suffix}",
        "connection": f"drinqo_test_connection_{suffix}",
        "test": f"drinqo_test_database_{suffix}",
        "cluster": "drinqo_isolated_test",
    }


def clean_environment():
    for key in list(os.environ):
        if key.startswith(("PG", "DRINQO_TEST_", "DB_", "DJANGO_", "SIMPLE_JWT", "MSG91_", "GOOGLE_", "AWS_", "AZURE_")):
            os.environ.pop(key, None)
    for key in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONUSERBASE", "PYTHONINSPECT"):
        os.environ.pop(key, None)
    os.environ.update({
        "PYTHON_DOTENV_DISABLED": "1",
        "PYTHONNOUSERSITE": "1",
    })


def control_identity(pg_bin, data_directory, expected_system_id):
    result = subprocess.run(
        [str(pg_bin / "pg_controldata.exe"), "-D", str(data_directory)],
        capture_output=True, text=True, timeout=20, check=False,
    )
    require(result.returncode == 0, "Cannot read PostgreSQL control data.")
    identifier = re.search(r"Database system identifier:\s*(\d+)", result.stdout)
    state = re.search(r"Database cluster state:\s*(.+)", result.stdout)
    require(identifier is not None and identifier.group(1) == expected_system_id,
            "Control-system identifier differs; refusing this cluster.")
    require(state is not None and state.group(1).strip() == "in production",
            "The expected local cluster is not running.")


def database_identity(connection, name):
    row = connection.execute(
        "SELECT oid, pg_get_userbyid(datdba) FROM pg_database WHERE datname=%s", (name,),
    ).fetchone()
    return tuple(row) if row else None


def configure_django(names, password):

    os.environ.update({
        "DRINQO_TEST_MODE": "1",
        "DRINQO_TEST_DB_NAME": names["connection"],
        "DRINQO_TEST_DATABASE_NAME": names["test"],
        "DRINQO_TEST_DB_USER": names["role"],
        "DRINQO_TEST_DB_PASSWORD": password,
        "DRINQO_TEST_DB_HOST": "127.0.0.1",
        "DRINQO_TEST_DB_PORT": "55433",
        "DJANGO_SETTINGS_MODULE": "config.test_settings",
    })
    sys.path.insert(0, str(SOURCE))
    module = importlib.import_module("config.test_settings")
    database = module.DATABASES.get("default", {})
    require(set(module.DATABASES) == {"default"}, "Unexpected Django database aliases.")
    require(
        database.get("ENGINE") == "django.db.backends.postgresql"
        and database.get("HOST") == "127.0.0.1"
        and str(database.get("PORT")) == "55433"
        and database.get("NAME") == names["connection"]
        and database.get("USER") == names["role"]
        and database.get("PASSWORD") == password
        and database.get("TEST", {}).get("NAME") == names["test"]
        and not database.get("TEST", {}).get("MIRROR"),
        "Django database settings do not match the isolated target.",
    )
    database["OPTIONS"] = {"connect_timeout": 5, "options": TIMEOUT_OPTIONS}
    # TestCase wraps each test in a transaction. A zero lifetime lets request
    # teardown close psycopg while that transaction is still active, leaving a
    # closed connection cached by Django. The process is disposable, so retain
    # the verified connection for the bounded run instead.
    database["CONN_MAX_AGE"] = None
    module.SECRET_KEY = secrets.token_urlsafe(64)
    module.SIMPLE_JWT_SIGNING_KEY = secrets.token_urlsafe(64)
    module.SIMPLE_JWT = {**module.SIMPLE_JWT, "SIGNING_KEY": module.SIMPLE_JWT_SIGNING_KEY}
    import django
    django.setup()


def run_tests(names, password):
    import psycopg

    with psycopg.connect(
        host="127.0.0.1", port=55433, dbname=names["connection"], user=names["role"],
        password=password, connect_timeout=5, options=TIMEOUT_OPTIONS, autocommit=True,
        application_name="drinqo_local_disposable_tests",
    ) as connection:
        row = connection.execute(
            "SELECT inet_server_addr()::text, inet_server_port(), "
            "current_setting('data_directory'), current_setting('cluster_name'), "
            "current_database(), current_user"
        ).fetchone()
        # PostgreSQL returns an inet value with its CIDR mask on some builds
        # (for example ``127.0.0.1/32``).  Accept only that equivalent IPv4
        # loopback representation, never a hostname or non-loopback address.
        require(
            row[0] in {"127.0.0.1", "127.0.0.1/32"} and row[1] == 55433
            and Path(row[2]).resolve() == Path(args.data_directory).resolve()
            and row[3] == names["cluster"] and row[4] == names["connection"]
            and row[5] == names["role"],
            "Live PostgreSQL identity mismatch; no test database will be created.",
        )
        require(database_identity(connection, names["connection"])[1] == names["role"],
                "Connection database owner mismatch.")
        require(database_identity(connection, names["test"]) is None,
                "Test database already exists; preserve it and stop.")

    configure_django(names, password)
    from django.db import connections
    from django.db.backends.postgresql.creation import DatabaseCreation
    from config.test_runner import IsolatedTestRunner

    def create_without_drop(creation, verbosity, autoclobber, keepdb=False):
        require(creation.connection.alias == "default" and not autoclobber and not keepdb,
                "Unsafe Django database creation arguments.")
        configured = creation.connection.settings_dict
        require(
            configured["NAME"] == names["connection"] and configured["USER"] == names["role"]
            and configured["HOST"] == "127.0.0.1" and str(configured["PORT"]) == "55433"
            and configured["TEST"]["NAME"] == names["test"] and not configured["TEST"].get("MIRROR"),
            "Django database settings changed after validation.",
        )
        require(creation._get_test_db_name() == names["test"], "Unexpected test database name.")
        with psycopg.connect(
            host="127.0.0.1", port=55433, dbname=names["connection"], user=names["role"],
            password=password, connect_timeout=5, options=TIMEOUT_OPTIONS, autocommit=True,
        ) as connection:
            require(database_identity(connection, names["test"]) is None,
                    "Test database collision; refusing Django fallback/drop behaviour.")
            from psycopg import sql
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(names["test"])))
            identity = database_identity(connection, names["test"])
            require(identity is not None and identity[1] == names["role"],
                    "Created database ownership mismatch.")
        return names["test"]

    class GuardedRunner(IsolatedTestRunner):
        def setup_databases(self, **kwargs):
            require(not self.keepdb and self.interactive and not self.parallel,
                    "Unsafe test runner flags.")
            require(set(connections) == {"default"}, "Unexpected runtime database aliases.")
            with patch.object(DatabaseCreation, "_create_test_db", create_without_drop):
                return super().setup_databases(**kwargs)

        def teardown_databases(self, old_config, **kwargs):
            # Preservation is intentional: no automatic DROP, even on success.
            connections.close_all()
            print("Test database retained; no database was deleted.")

    runner = GuardedRunner(verbosity=2, interactive=True, keepdb=False, parallel=0)
    return runner.run_tests(TEST_LABELS)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suffix", required=True)
    parser.add_argument("--data-directory", required=True)
    parser.add_argument("--system-identifier", required=True)
    parser.add_argument("--pg-bin", default=r"C:\Program Files\PostgreSQL\18\bin")
    parser.add_argument("--approval", required=True)
    global args
    args = parser.parse_args()
    require(args.approval == "APPROVE_LOCAL_DISPOSABLE_TEST_EXECUTION",
            "Explicit execution approval is required.")
    require(Path.cwd().resolve() == SOURCE.resolve(), "Run from D:\\drinqo_backend only.")
    data_directory = Path(args.data_directory)
    require(data_directory.is_absolute() and re.fullmatch(r"drinqo_pg_test_cluster_[0-9]{6}", data_directory.name),
            "Unexpected data-directory name.")
    require(not data_directory.is_symlink() and not data_directory.is_junction(),
            "Test data directory must not redirect elsewhere.")
    clean_environment()
    names = expected_names(args.suffix)
    control_identity(Path(args.pg_bin), data_directory, args.system_identifier)
    password = getpass.getpass(f"Password for {names['role']} (not echoed): ")
    require(password, "A non-empty test-only role password is required.")

    def deadline():
        threading.Event().wait(1200)
        os.write(2, b"Test deadline exceeded; database preserved.\n")
        os._exit(124)
    threading.Thread(target=deadline, daemon=True).start()
    failures = run_tests(names, password)
    if failures:
        raise RuntimeError(f"Regression failures: {failures}; test database preserved.")
    print("All selected tests passed. Test database remains preserved for review.")


if __name__ == "__main__":
    main()
