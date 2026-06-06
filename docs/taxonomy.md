# Taxonomy & Recommendation Matrix (authoritative)

> Imported by CLAUDE.md. The classifier and aggregator import the Python structures below.
> Sub-category strings must match **exactly** — they are the validation enum.

## Classification taxonomy — 1 main + 1 sub per question

```python
# backend/app/taxonomy.py
TAXONOMY: dict[str, list[str]] = {
    "Product": [
        "Feature Gap",
        "User / Developer Experience Gap",
        "Integration Gap",
        "Demand Signal",
    ],
    "Documentation": [
        "Missing Documentation",
        "Unclear or poorly explained",
        "Conflicting Information",
        "Information spread across multiple sources",
    ],
    "Operational": [
        "Configuration Complexity",
        "Setup or deployment issues",
        "Environment constraints",
        "Lack of troubleshooting support",
    ],
    "Awareness": [
        "Feature not known",
        "Incorrect assumptions about capability",
        "Poor communication of changes or releases",
    ],
    "Technical": [
        "Reliability issues or instability",
        "Performance or scaling issues",
        "Poor error handling or failures",
    ],
    "Security / Compliance": [
        "Access control or permissions confusion",
        "Network or connectivity issues",
        "Data protection or encryption questions",
        "Compliance or regulatory gaps",
    ],
    "Adoption / Migration": [
        "Migration challenges between platforms/products",
        "Breaking changes or upgrades",
        "Difficulty getting started",
        "Compatibility issues",
    ],
    "Misuse / Noise": [   # supporting category — excluded from patterns/recommendations
        "Incorrect usage",
        "Duplicate questions",
        "Incomplete or low-quality questions",
    ],
}

def is_valid(main: str, sub: str) -> bool:
    return main in TAXONOMY and sub in TAXONOMY[main]
```

## Rules

- `Misuse / Noise` items are stored with their label but **excluded** from patterns, recommendations, and headline
  counts. Total noise volume is itself reported (signal about question quality).
- Classifier output is validated with `is_valid()`. Invalid → retry once with a stricter prompt → else force
  `("Misuse / Noise", "Incomplete or low-quality questions")` with `confidence=0.0` and log it. Never crash a batch.

## Recommendation matrix — Problem Type → Suggested Action

```python
RECOMMENDATION_MATRIX: dict[str, str] = {
    "Product":               "Add feature or improvement",
    "Documentation":         "Update Backstage or Confluence",
    "Operational":           "Improve setup, runbooks, or automation",
    "Awareness":             "Improve communication or release notes",
    "Technical":             "Fix or optimise",
    "Adoption / Migration":  "Improve migration guides or tooling",
    "Security / Compliance": "Align with security standards or guardrails",
}
```

Suggested actions are **surfaced to the product owner as text**. The agent never executes them.
