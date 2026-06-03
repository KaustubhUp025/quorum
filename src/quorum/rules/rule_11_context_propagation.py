"""RULE_11 — Context error masking (cancel/deadline identity lost)."""

from quorum.rules.base import Rule

RULE = Rule(
    id="RULE_11",
    name="Context Error Masking",
    description=(
        "A function receives a context.Context but, when the context is cancelled or "
        "deadline-exceeded, returns a generic hardcoded error string instead of ctx.Err(). "
        "Callers lose the ability to distinguish context.Canceled from "
        "context.DeadlineExceeded, which breaks error-handling chains and makes distributed "
        "tracing and retry decisions incorrect. "
        "The fix is to return ctx.Err() (or wrap it) on the ctx.Done() path, "
        "and to check ctx.Err() before entering any blocking loop."
    ),
    reference="Go blog — Contexts and structs (Sameer Ajmani)",
    reference_url="https://go.dev/blog/context-and-structs",
    surface_keywords=[
        # Go context done/cancel handling
        "ctx.done", "ctx.err", "context.canceled", "context.deadlineexceeded",
        "select {", "case <-ctx",
        # Python
        "asyncio.cancelederror", "task.cancel", "cancelled()",
        # Java
        "interruptedexception", "cancellationexception",
        # Generic retry + context combination
        "retry", "deadline", "timeout", "cancel",
    ],
    surface_patterns=[
        # Go: select with ctx.Done but no ctx.Err() return
        r'case\s+<-\w+\.Done\(\)',
        # Go: common masking — returning hardcoded string on ctx.Done branch
        r'case\s+<-\w+\.Done\(\)\s*:\s*\n\s*return\s+(?:nil,\s*)?(?:fmt\.Errorf|errors\.New)',
        # Go: blocking call before ctx check at loop entry
        r'for\s+[^{]*\{[^}]*DialPipe|[^}]*Connect|[^}]*Dial[^}]*\n[^}]*select',
        # Python: asyncio masked cancel
        r'except\s+asyncio\.CancelledError\s*:\s*\n\s*raise\s+\w*Error',
        # Java: swallowed interrupt
        r'catch\s*\(\s*InterruptedException',
    ],
    search_query_templates=[
        "context cancellation error propagation ctx.Err return",
        "select ctx.Done return context error",
        "context deadline exceeded vs canceled distinction",
    ],
    reasoning_guidance=(
        "For every `select { case <-ctx.Done(): ... }` block in the diff:\n"
        "1. Check whether the branch returns ctx.Err() (or wraps it with fmt.Errorf('...%%w', ctx.Err())). "
        "If it returns a hardcoded error string, flag HIGH — callers cannot distinguish "
        "cancellation from timeout.\n"
        "2. Check whether the loop or function body checks ctx.Err() BEFORE the first "
        "blocking call (DialPipe, Connect, Read, etc.). If not, a pre-cancelled context "
        "will block for up to one full attempt timeout before detecting cancellation — flag MEDIUM.\n"
        "3. Use get_file_contents to read the full function, not just the diff snippet. "
        "The masking is often only visible when you see the full return path.\n"
        "Do NOT flag if the branch correctly returns ctx.Err() or wraps it with %%w."
    ),
)
