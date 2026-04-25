"""Invoke task package — re-exports all public tasks so ``inv --list`` works."""

import sys

from invoke import Collection

from ._backup import (
    backup_status,
    litestream_setup,
    litestream_status,
    restore_from_yadisk,
    setup_replica,
    setup_replica_backup,
)
from ._common import ALLOWED_DOC_LANGUAGES, ALLOWED_VERSION_TYPES
from ._deploy import (
    bootstrap_catalog,
    deploy,
    import_config,
    logs,
    migrate,
    ssh,
    ssh_replica,
    start,
    status,
    stop,
)
from ._import import (
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
from ._local import (
    backup,
    build_static,
    dev,
    docs_task_factory,
    healthcheck,
    pre,
    reqs,
    test,
    uv,
    ver_task_factory,
    verify_db,
    version,
)
from ._reports import report_expenses, report_income, sql_query
from ._setup import setup, setup_swap, ssh_tailscale_only

__all__ = [
    "backup",
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
    "litestream_setup",
    "litestream_status",
    "logs",
    "migrate",
    "pre",
    "reqs",
    "report_expenses",
    "report_income",
    "restore_from_yadisk",
    "setup",
    "setup_replica",
    "setup_replica_backup",
    "setup_swap",
    "sql_query",
    "ssh",
    "ssh_replica",
    "ssh_tailscale_only",
    "start",
    "status",
    "stop",
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
