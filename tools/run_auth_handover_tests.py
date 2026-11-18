"""Create a NEW PostgreSQL cluster for this task; never read project .env.

Preserves its uniquely named data/test database/logs; stops only the server it
created. No DROP, directory deletion, shared cluster, or service operations.
"""
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
from unittest.mock import patch


def main():
    root = Path(__file__).resolve().parents[1]
    pg = Path(r"C:\Program Files\PostgreSQL\18\bin")
    runs = root / "tools" / "test_runs"
    runs.mkdir(exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="auth_handover_", dir=runs)).resolve()
    if not run.is_relative_to(runs.resolve()):
        raise RuntimeError("Unexpected test workspace")
    data = run / "pgdata"
    password = secrets.token_urlsafe(40)
    password_file = run / "bootstrap.password"
    password_file.write_text(password, encoding="ascii")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PG", "DB_", "DJANGO_", "AWS_", "GOOGLE_", "APPLE_", "EMAIL_", "MSG91_"))}
    env["PYTHON_DOTENV_DISABLED"] = "1"
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    def command(exe, args):
        # Windows server grandchildren can retain pipe handles. File-backed
        # output keeps startup/timeout supervision bounded even after pg_ctl exits.
        log = run / (exe + ".log")
        with log.open("a", encoding="utf-8") as output:
            result = subprocess.run([str(pg / exe), *args], env=env, stdout=output,
                                    stderr=subprocess.STDOUT, text=True, timeout=45, creationflags=flags)
        if result.returncode:
            raise RuntimeError(f"{exe} failed; retained diagnostic log in {run.name}")

    try:
        command("initdb.exe", ["-D", str(data), "-U", "drinqo_test_bootstrap", "-A", "scram-sha-256", "--pwfile", str(password_file), "--encoding=UTF8", "--locale=C"])
    finally:
        password_file.unlink(missing_ok=True)
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]
    cluster = "drinqo_auth_handover_" + secrets.token_hex(6)
    command("pg_ctl.exe", ["-D", str(data), "-l", str(run / "postgres.log"), "-w", "-t", "20", "-o", f"-h 127.0.0.1 -p {port} -c cluster_name={cluster}", "start"])
    try:
        import psycopg
        from psycopg import sql
        connection_args = dict(host="127.0.0.1", port=port, user="drinqo_test_bootstrap", password=password, dbname="postgres", connect_timeout=5,
                               options="-c statement_timeout=30000 -c lock_timeout=5000")
        suffix = secrets.token_hex(8)
        role, db = "drinqo_auth_test_" + suffix, "drinqo_auth_test_db_" + suffix
        role_password = secrets.token_urlsafe(40)
        with psycopg.connect(**connection_args, autocommit=True) as conn:
            path, actual_cluster = conn.execute("SELECT current_setting('data_directory'),current_setting('cluster_name')").fetchone()
            if Path(path).resolve() != data or actual_cluster != cluster:
                raise RuntimeError("Fresh cluster identity mismatch")
            system_id = conn.execute("SELECT system_identifier::text FROM pg_control_system()").fetchone()[0]
            conn.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE").format(sql.Identifier(role), sql.Literal(role_password)))
            if conn.execute("SELECT 1 FROM pg_database WHERE datname=%s", (db,)).fetchone():
                raise RuntimeError("Unexpected new database collision")
            conn.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(db), sql.Identifier(role)))
        (run / "manifest.json").write_text(json.dumps({"system_identifier": system_id, "data_directory": str(data), "cluster": cluster, "port": port, "database": db, "role": role}, indent=2))
        os.environ.update(env)
        os.environ["DJANGO_SETTINGS_MODULE"] = "config.mobile_auth_test_settings"
        sys.path.insert(0, str(root))
        from config import mobile_auth_test_settings as cfg
        cfg.DATABASES = {"default": {
            "ENGINE": "django.db.backends.postgresql", "NAME": db, "USER": role,
            "PASSWORD": role_password, "HOST": "127.0.0.1", "PORT": str(port),
            "CONN_MAX_AGE": 0, "OPTIONS": {"connect_timeout": 5, "options": "-c statement_timeout=30000 -c lock_timeout=5000 -c idle_in_transaction_session_timeout=60000"},
        }}
        import django
        django.setup()
        from django.core.management import call_command
        from django.test.runner import DiscoverRunner
        from django.db import connections
        call_command("check")
        call_command("migrate", verbosity=0)
        call_command("makemigrations", check=True, dry_run=True, verbosity=1)

        class FreshDatabaseRunner(DiscoverRunner):
            def setup_databases(self, **kwargs):
                # This database was created, identified and migrated above.
                return []

            def teardown_databases(self, old_config, **kwargs):
                connections.close_all()  # Retain all database evidence.

        labels = sys.argv[1:] or [
            "accounts.test_mobile_auth", "accounts.test_final_auth", "orders.test_staff_handover",
            "accounts.tests.GoogleIdentityVerificationTests", "accounts.tests.GoogleAuthenticationTests",
            "accounts.tests.JWTAuthenticationTests", "accounts.tests.LogoutTests",
            "accounts.tests.PasswordRecoveryTests", "accounts.test_api_integration",
            "accounts.test_age_self_declaration", "orders.test_razorpay_gateway",
        ]
        with patch("requests.sessions.Session.request", side_effect=RuntimeError("External HTTP disabled in tests")), patch("urllib.request.urlopen", side_effect=RuntimeError("External HTTP disabled in tests")):
            failures = FreshDatabaseRunner(verbosity=1).run_tests(labels)
        print("Retained isolated test artifacts:", run)
        return bool(failures)
    finally:
        # Verified-new directory only; no stop against an existing shared service.
        command("pg_ctl.exe", ["-D", str(data), "-m", "fast", "-w", "-t", "20", "stop"])


if __name__ == "__main__":
    raise SystemExit(main())
