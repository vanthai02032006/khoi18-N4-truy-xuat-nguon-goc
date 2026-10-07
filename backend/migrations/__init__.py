"""Package migrations cơ sở dữ liệu dự án TTCS K18C4-N4."""

from app.migrations.scrum_53_batch_lineage import (  # noqa: F401
    downgrade as downgrade_scrum_53,
    upgrade as upgrade_scrum_53,
) if False else ()
