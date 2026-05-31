"""SARIF 2.1.0 output formatter.

SARIF (Static Analysis Results Interchange Format) is the standard for GitHub
Code Scanning. Upload via `actions/upload-sarif` to get inline PR annotations.

Usage:
    from quorum.sarif import format_sarif
    print(format_sarif(review_result))
"""

from __future__ import annotations

import json

from quorum.models import ReviewResult, Severity

_SARIF_LEVEL: dict[Severity, str] = {
    Severity.CRITICAL: "error",
    Severity.HIGH: "warning",
    Severity.MEDIUM: "note",
    Severity.LOW: "note",
    Severity.PASS: "none",
}


def format_sarif(result: ReviewResult, tool_version: str = "0.1.0") -> str:
    """Return a SARIF 2.1.0 JSON string for all non-PASS findings."""
    from quorum.rules.registry import REGISTRY

    seen_rules: dict[str, dict] = {}
    sarif_results: list[dict] = []

    for finding in result.findings:
        level = _SARIF_LEVEL.get(finding.severity, "note")
        if level == "none":
            continue  # PASS findings have no SARIF result

        if finding.rule_id not in seen_rules:
            rule = REGISTRY.get(finding.rule_id)
            seen_rules[finding.rule_id] = {
                "id": finding.rule_id,
                "name": _rule_name_identifier(finding.rule_name),
                "shortDescription": {"text": finding.rule_name},
                "helpUri": (
                    (rule.reference if rule else None)
                    or "https://github.com/KaustubhUp025/quorum"
                ),
                "properties": {
                    "tags": ["security", "distributed-systems", "coordination"],
                },
            }

        message_text = finding.explanation
        if finding.suggested_fix:
            message_text += f"\n\nSuggested fix: {finding.suggested_fix}"
        if finding.search_evidence:
            message_text += f"\n\nEvidence: {finding.search_evidence}"

        sarif_result: dict = {
            "ruleId": finding.rule_id,
            "level": level,
            "message": {"text": message_text},
            "properties": {
                "confidence": finding.confidence,
                "severity": finding.severity.value,
            },
        }

        if finding.file_path:
            sarif_result["locations"] = [
                {
                    "physicalLocation": {
                        "artifactLocation": {
                            "uri": finding.file_path,
                            "uriBaseId": "%SRCROOT%",
                        },
                        "region": {
                            "startLine": finding.line_number or 1,
                        },
                    }
                }
            ]

        if finding.fix_mr_url:
            sarif_result["fixes"] = [
                {
                    "description": {
                        "text": f"Draft fix available at: {finding.fix_mr_url}"
                    }
                }
            ]

        sarif_results.append(sarif_result)

    sarif_doc = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "Quorum",
                        "version": tool_version,
                        "informationUri": "https://github.com/KaustubhUp025/quorum",
                        "rules": list(seen_rules.values()),
                    }
                },
                "results": sarif_results,
                "properties": {
                    "mrIid": result.mr_iid,
                    "projectId": result.project_id,
                    "surfacesDetected": result.surfaces_detected,
                    "blocked": result.blocked,
                },
            }
        ],
    }
    return json.dumps(sarif_doc, indent=2)


def _rule_name_identifier(rule_name: str) -> str:
    """Convert 'Fencing Token Missing' → 'FencingTokenMissing' (SARIF ruleId convention)."""
    return "".join(word.capitalize() for word in rule_name.replace("/", " ").split())
