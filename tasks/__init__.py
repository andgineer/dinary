"""Invoke task package — re-exports all public tasks so ``inv --list`` works."""

import sys

from invoke import Collection

from .backups_replica import replica_reset_trust, replica_resync, restore_replica, setup_replica
from .backups_restore import restore_from_yadisk
from .backups_status import backup_status
from .constants import ALLOWED_DOC_LANGUAGES, ALLOWED_VERSION_TYPES
from .db import migrate, restore_primary, verify_db
from .deploy import (
    bootstrap_catalog,
    deploy,
    import_config,
)
from .dev import (
    build_static,
    dev,
    docs_task_factory,
    pre,
    reqs,
    test,
    uv,
    ver_task_factory,
    version,
)
from .imports import (
    import_budget,
    import_budget_all,
    import_catalog,
    import_income,
    import_income_all,
    import_report_2d_3d,
    verify_bootstrap_import,
    verify_bootstrap_import_all,
    verify_income_equivalence,
    verify_income_equivalence_all,
)
from .reports import report_expenses, report_income, sql_query
from .server import healthcheck, logs, restart_server, ssh, ssh_replica, status
from .setup import setup_server

__all__ = [
    "backup_status",
    "bootstrap_catalog",
    "build_static",
    "deploy",
    "dev",
    "docs_task_factory",
    "healthcheck",
    "import_budget",
    "import_budget_all",
    "import_catalog",
    "import_config",
    "import_income",
    "import_income_all",
    "import_report_2d_3d",
    "logs",
    "migrate",
    "pre",
    "reqs",
    "report_expenses",
    "report_income",
    "restart_server",
    "replica_reset_trust",
    "replica_resync",
    "restore_from_yadisk",
    "restore_primary",
    "restore_replica",
    "setup_replica",
    "setup_server",
    "sql_query",
    "ssh",
    "ssh_replica",
    "status",
    "test",
    "uv",
    "ver_task_factory",
    "verify_bootstrap_import",
    "verify_bootstrap_import_all",
    "verify_db",
    "verify_income_equivalence",
    "verify_income_equivalence_all",
    "version",
]

namespace = Collection.from_module(sys.modules[__name__])
for name in ALLOWED_VERSION_TYPES:
    namespace.add_task(ver_task_factory(name), name=f"ver-{name}")  # type: ignore[bad-argument-type]
for name in ALLOWED_DOC_LANGUAGES:
    namespace.add_task(docs_task_factory(name), name=f"docs-{name}")  # type: ignore[bad-argument-type]
