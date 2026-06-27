from __future__ import annotations
from typing import Literal

# Security score: start 100, subtract per security/ai_antipattern finding severity.
SECURITY_DEDUCTIONS: dict[str, int] = {
    "critical": 25,
    "high":     10,
    "medium":    3,
    "low":       1,
    "info":      0,
}

# Quality score: same deduction table applied to quality-category findings only.
QUALITY_DEDUCTIONS: dict[str, int] = {
    "high":   10,
    "medium":  5,
    "low":     2,
}

# Grade thresholds applied to the combined weighted score (highest-first, first match wins).
GRADE_THRESHOLDS: list[tuple[float, str]] = [
    (90.0, "A"),
    (75.0, "B"),
    (60.0, "C"),
    (45.0, "D"),
    (0.0,  "F"),
]

# Combined score = security_score * 0.7 + quality_score * 0.3
SECURITY_WEIGHT = 0.7
QUALITY_WEIGHT  = 0.3


def calculate_security_score(findings: list[dict]) -> int:
    deductions = sum(
        SECURITY_DEDUCTIONS.get(f["severity"], 0)
        for f in findings
        if f.get("category") != "quality"
    )
    return max(0, 100 - deductions)


def calculate_quality_score(findings: list[dict]) -> int:
    deductions = sum(
        QUALITY_DEDUCTIONS.get(f["severity"], 0)
        for f in findings
        if f.get("category") == "quality"
    )
    return max(0, 100 - deductions)


def calculate_grade(security_score: int, quality_score: int) -> str:
    combined = security_score * SECURITY_WEIGHT + quality_score * QUALITY_WEIGHT
    for threshold, grade in GRADE_THRESHOLDS:
        if combined >= threshold:
            return grade
    return "F"


def calculate_scores(findings: list[dict]) -> tuple[int, int, str]:
    """Returns (security_score, quality_score, grade)."""
    sec = calculate_security_score(findings)
    qual = calculate_quality_score(findings)
    return sec, qual, calculate_grade(sec, qual)
