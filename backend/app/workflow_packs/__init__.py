"""Static import point for every workflow pack.

Adding a pack means adding one import line here (plus the pack module); the
engine imports this package once and every binder / structured handler is
registered into ``app.workflows.registry`` from reviewed code. SOP metadata
can only reference the registered keys — it can never import code.
"""

from app.workflow_packs import screen  # noqa: F401
