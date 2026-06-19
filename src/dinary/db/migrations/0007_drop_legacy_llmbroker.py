"""Drop legacy llmbroker_* tables so llmbroker.ensure_schema owns the schema.

The pre-extraction ``llmbroker_providers`` / ``llmbroker_call_log`` tables were
built by migrations 0004/0005 in an older shape. The standalone ``llmbroker``
package now owns its own ``llmbroker_``-prefixed schema via ``ensure_schema``, so
these tables are dropped here and recreated by the package on next start. Their
data is disposable (config is re-seeded from .deploy/llm_providers.toml).
"""

from yoyo import step

__depends__ = {"0005_income_logging"}

steps = [
    step(
        "DROP TABLE IF EXISTS llmbroker_call_log",
        "SELECT 1",
    ),
    step(
        "DROP TABLE IF EXISTS llmbroker_providers",
        "SELECT 1",
    ),
]
