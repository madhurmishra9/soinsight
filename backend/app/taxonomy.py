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

RECOMMENDATION_MATRIX: dict[str, str] = {
    "Product":               "Add feature or improvement",
    "Documentation":         "Update Backstage or Confluence",
    "Operational":           "Improve setup, runbooks, or automation",
    "Awareness":             "Improve communication or release notes",
    "Technical":             "Fix or optimise",
    "Adoption / Migration":  "Improve migration guides or tooling",
    "Security / Compliance": "Align with security standards or guardrails",
}


def is_valid(main: str, sub: str) -> bool:
    return main in TAXONOMY and sub in TAXONOMY[main]
