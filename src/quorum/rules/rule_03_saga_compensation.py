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
        # Language-agnostic framework and concept keywords
        "saga", "orchestrator", "choreography", "compensat", "sagastep",
        "sagaorchestrator", "eventuate", "temporal", "camunda",
        # Step / rollback signals
        "step(", ".step(", "addstep", "rollback",
        # Java / Kotlin Spring (most common)
        "@saga", "sagamanager", "sagatype",
        # Go (conductor, Temporal Go SDK)
        "workflow.ExecuteActivity", "workflow.Go",
        "saga.NewSaga", "activity.Execute",
        # JavaScript / TypeScript (NestJS CQRS, Moleculer sagas)
        "SagaBuilder", "startWith(", "compensation(",
        # Python (Temporal Python SDK, Prefect)
        "workflow.execute_activity", "@workflow.defn",
        # .NET (MassTransit, NServiceBus)
        "SagaStateMachine", "ISaga", "CorrelatedBy",
    ],
    surface_patterns=[
        # Generic OO: saga.step(...)
        r'saga\w*\.step\s*\(',
        # Java: @Saga, @SagaEventHandler
        r'@\w*Saga\b',
        r'SagaStep\s*\(',
        r'orchestrator\.add\w*\(',
        r'\.compensate\s*\(',
        r'on\w+Failed\s*\(',
        # Temporal (Go / Java / Python / TypeScript)
        r'workflow\.ExecuteActivity\s*\(',
        r'workflow\.execute_activity\s*\(',
        r'workflow\.executeActivity\s*\(',
        # Go: saga.NewSaga() or conductor saga patterns
        r'saga\.New\w*\s*\(',
        # JavaScript/TypeScript NestJS: @Saga() decorator
        r'@Saga\s*\(\s*\)',
        r'SagaBuilder\s*\.',
        # .NET MassTransit
        r'ISaga\b',
        r'SagaStateMachine\b',
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
