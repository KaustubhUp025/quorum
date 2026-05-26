"""RULE_03 — Saga forward step has no compensating transaction."""

from quorum.rules.base import Rule

RULE = Rule(
    id="RULE_03",
    name="Saga Compensation Missing",
    description=(
        "A saga orchestrator or choreography handler adds a new forward step (e.g. shipOrder, "
        "chargeCard, reserveInventory) but no corresponding compensating transaction "
        "(cancelShipment, refundCard, releaseInventory) exists anywhere in the project. "
        "Without a compensation, a failure in a later saga step leaves the system in a "
        "partially-executed state with no rollback path."
    ),
    reference="microservices.io — Saga pattern",
    reference_url="https://microservices.io/patterns/data/saga.html",
    surface_keywords=[
        "saga", "orchestrator", "step(", ".step(", "addstep",
        "choreography", "compensat", "rollback", "sagastep",
        "sagaorchestrator", "eventuate", "temporal", "camunda",
    ],
    surface_patterns=[
        r'saga\w*\.step\s*\(',
        r'@\w*Saga\b',
        r'SagaStep\s*\(',
        r'orchestrator\.add\w*\(',
        r'\.compensate\s*\(',
        r'on\w+Failed\s*\(',
    ],
    search_query_templates=[
        "compensation handler rollback for saga step",
        "cancel or undo handler for the forward operation added in diff",
        "compensating transaction event handler",
        "saga compensate on failure handler",
    ],
    reasoning_guidance=(
        "Extract the name of each new saga step from the diff (e.g. `shipOrder`). "
        "For each step, check the semantic search results for a corresponding compensation "
        "(e.g. `cancelShipment`, `undoShipOrder`, `revertShip`). "
        "Flag CRITICAL if any forward step has zero matching compensation found across the project. "
        "Flag MEDIUM if compensations exist but appear non-idempotent (no idempotency key check)."
    ),
)
