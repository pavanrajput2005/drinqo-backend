# Local disposable PostgreSQL test runner

This runner is separate from the offline Hyper-V workflow.  It is only for a
fresh, manually created local PostgreSQL 18 cluster and will never drop a
database.  It validates the provided PostgreSQL control-system ID, data
directory, loopback address, port `55433`, cluster name, role, connection DB,
and an absent test DB before Django can create anything.

Do not invoke `manage.py test` directly for this workflow.  The runner owns
test database creation and blocks Django's collision/drop fallback.

The operator must explicitly provide the known identity and type the dedicated
test-role password interactively.  It is not written to disk by the runner.

Multiple preserved test attempts may use the same verified cluster only with a
new six-digit suffix, new dedicated role, new connection database, and an
absent test database. The cluster directory suffix need not equal that run
suffix; the runner pins the directory and control-system identifier instead.

After a test attempt, preserve the generated `drinqo_test_database_<suffix>`.
Any later database deletion is a separate, manual, approval-required activity.
