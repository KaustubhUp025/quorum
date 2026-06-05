"""RULE_14 — HTTP/gRPC/DB call without explicit timeout (cascading failure risk)."""

from quorum.rules.base import Rule

RULE = Rule(
    id="RULE_14",
    name="Cascading Timeout Missing",
    description=(
        "An HTTP client call, gRPC stub call, or database query is made without an explicit "
        "timeout or without propagating the parent request context/deadline. Without a timeout, "
        "a slow or unresponsive downstream service holds the caller's goroutine/thread/connection "
        "indefinitely. In a microservices call chain, this cascades upward: thread pools exhaust, "
        "connection queues fill, services fall over in sequence — the textbook cascading failure. "
        "Fix: always pass timeout= kwarg, propagate context/deadline from the parent request, "
        "or use a client configured with a default timeout (httpx.AsyncClient(timeout=...), "
        "http.Client{Timeout: ...})."
    ),
    reference="Google SRE Book Chapter 22 — Cascading Failures",
    reference_url="https://sre.google/sre-book/addressing-cascading-failures/",
    surface_keywords=[
        # Python requests
        "requests.get", "requests.post", "requests.put", "requests.delete", "requests.patch",
        "requests.request",
        # Python httpx
        "httpx.get", "httpx.post", "httpx.asyncclient",
        "client.get(", "client.post(", "client.put(", "client.delete(",
        # Go net/http
        "http.get(", "http.defaultclient", "http.newrequest",
        # JS/TS
        "fetch(", "axios.get", "axios.post", "axios.put", "axios.delete",
        "got(", "got.get",
        # Java
        "resttemplate", "webclient", "httpclient.newhttpclient",
        # DB without timeout
        "engine.execute", "cursor.execute", "db.raw(",
    ],
    surface_patterns=[
        # Python requests — any HTTP method call (likely missing timeout=)
        r'requests\.\s*(?:get|post|put|delete|patch|request)\s*\(',
        # Python httpx standalone calls
        r'httpx\.\s*(?:get|post|put|delete|patch)\s*\(',
        # httpx.AsyncClient or httpx.Client — constructor or context manager
        r'httpx\.\w+Client\s*\(',
        # Go http.Get — uses DefaultClient which has no timeout
        r'\bhttp\.Get\s*\(',
        # Go http.DefaultClient.Do
        r'http\.DefaultClient\.Do\s*\(',
        # Go http.NewRequest without Context
        r'http\.NewRequest\s*\(',
        # JS/TS bare fetch without options
        r'\bfetch\s*\(\s*["\']',
        # JS/TS axios without second arg
        r'axios\.\s*(?:get|post|put|delete)\s*\(\s*["\`]',
        # Java RestTemplate with no-arg constructor — no timeout
        r'new\s+RestTemplate\s*\(\s*\)',
        # Java HttpClient.newHttpClient — uses default (no timeout)
        r'HttpClient\.newHttpClient\s*\(\s*\)',
    ],
    search_query_templates=[
        "timeout configuration httpx AsyncClient timeout seconds",
        "http.Client Timeout transport timeout configuration",
        "requests session timeout adapter mount",
        "axios timeout config default",
        "RestTemplate timeout connect read timeout",
    ],
    reasoning_guidance=(
        "For every HTTP/gRPC/DB call in the diff:\n"
        "1. Check if the call includes a 'timeout=' kwarg or a timeout config. "
        "If present, PASS.\n"
        "2. Check if the function signature includes a 'timeout' or 'deadline' parameter "
        "that is passed through — if yes, PASS (the caller controls it).\n"
        "3. Call semantic_code_search for the client constructor name "
        "(e.g., 'httpx.AsyncClient(timeout' or 'http.Client{Timeout'). "
        "If the client is always constructed with a timeout, PASS.\n"
        "4. Check if the call is inside a 'with httpx.Client(timeout=...) as client:' block "
        "or equivalent — if yes, PASS.\n"
        "5. If no timeout evidence found AND the call is in a service handler (not a test file, "
        "not a CLI utility), FLAG HIGH.\n"
        "6. Reduce confidence to 70% if the enclosing function has a 'timeout' or 'ctx'/'context' "
        "parameter, since the timeout may be propagated in a way not visible in the snippet.\n"
        "Never flag test files or scripts (these are typically covered by ignore_paths: tests/).\n"
        "Never flag calls inside a context manager that configures a client with a timeout."
    ),
)
