from __future__ import annotations

import math
import re
import secrets
import json
import os
from typing import Any, Literal

import httpx
import pandas as pd
from faker import Faker
from fastapi import FastAPI, HTTPException
from jsonschema import Draft202012Validator, SchemaError, ValidationError
from pydantic import BaseModel, Field, FiniteFloat

app = FastAPI(title="Synthetic Data and Privacy Engine", version="0.1.0")
MAX_ROWS = 20_000
_fake = Faker()
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_CARD = re.compile(r"\b(?:\d[ -]*?){13,19}\b")
LLM_URL = os.environ.get("SYNTHETIC_LLM_URL", "").strip()
LLM_MODEL = os.environ.get("SYNTHETIC_LLM_MODEL", "").strip()
LLM_API_KEY = os.environ.get("SYNTHETIC_LLM_API_KEY", "")
PRESIDIO_ENABLED = os.environ.get("PRESIDIO_ENABLED", "false").lower() == "true"
_presidio = None


class Column(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    kind: Literal["integer", "number", "category", "boolean", "email", "name", "date", "text"]
    values: list[str | int | float] = Field(default_factory=list, max_length=100)
    minimum: float | None = None
    maximum: float | None = None
    categories: list[str] = Field(default_factory=list, max_length=100)
    max_length: int = Field(default=80, ge=1, le=2000)


class GenerateRequest(BaseModel):
    rows: int = Field(default=100, ge=1, le=MAX_ROWS)
    seed: int | None = Field(default=None, ge=0, le=2**32 - 1)
    columns: list[Column] = Field(min_length=1, max_length=100)
    quasi_identifiers: list[str] = Field(default_factory=list, max_length=20)


class NoisyAggregate(BaseModel):
    values: list[FiniteFloat] = Field(min_length=1, max_length=10_000)
    lower_bound: FiniteFloat
    upper_bound: FiniteFloat
    epsilon: float = Field(gt=0, le=10)


class FitGenerateRequest(BaseModel):
    reference_rows: list[dict[str, Any]] = Field(min_length=20, max_length=5000)
    sample_rows: int = Field(default=100, ge=1, le=MAX_ROWS)
    quasi_identifiers: list[str] = Field(default_factory=list, max_length=20)


class TextGenerateRequest(BaseModel):
    count: int = Field(default=1, ge=1, le=20)
    instructions: str = Field(min_length=10, max_length=2000)
    output_schema: dict[str, Any]


def _validate_request(request: GenerateRequest) -> None:
    names = [column.name for column in request.columns]
    if len(set(names)) != len(names):
        raise HTTPException(422, "Column names must be unique")
    if set(request.quasi_identifiers) - set(names):
        raise HTTPException(422, "Every quasi-identifier must name a declared column")
    for column in request.columns:
        if column.kind in {"integer", "number"}:
            if column.minimum is None or column.maximum is None or column.minimum > column.maximum:
                raise HTTPException(422, f"Numeric column {column.name} requires minimum <= maximum")
            if not math.isfinite(column.minimum) or not math.isfinite(column.maximum):
                raise HTTPException(422, f"Numeric column {column.name} requires finite bounds")
            if column.kind == "integer" and (not column.minimum.is_integer() or not column.maximum.is_integer()):
                raise HTTPException(422, f"Integer column {column.name} requires whole-number bounds")
        if column.kind == "category" and not (column.categories or column.values):
            raise HTTPException(422, f"Category column {column.name} requires categories")


def _cell(column: Column) -> Any:
    if column.kind == "integer":
        return _fake.random_int(int(column.minimum), int(column.maximum))
    if column.kind == "number":
        return _fake.pyfloat(min_value=column.minimum, max_value=column.maximum, right_digits=2)
    if column.kind == "category":
        return _fake.random_element(column.categories or column.values)
    if column.kind == "boolean":
        return _fake.boolean()
    if column.kind == "email":
        return _fake.safe_email()
    if column.kind == "name":
        return _fake.name()
    if column.kind == "date":
        return _fake.date_between(start_date="-5y", end_date="today").isoformat()
    if column.values:
        return str(_fake.random_element(column.values))[:column.max_length]
    return _fake.text(max_nb_chars=min(column.max_length, 200))


def _scan_pii(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, Any] = {"email": 0, "ssn": 0, "payment_card_candidate": 0}
    for row in rows:
        for value in row.values():
            text = str(value)
            counts["email"] += bool(_EMAIL.search(text))
            counts["ssn"] += bool(_SSN.search(text))
            counts["payment_card_candidate"] += bool(_CARD.search(text))
    if PRESIDIO_ENABLED:
        global _presidio
        if _presidio is None:
            try:
                from presidio_analyzer import AnalyzerEngine
                _presidio = AnalyzerEngine()
            except Exception:
                raise HTTPException(503, "Presidio is enabled but its analyzer/NLP model is unavailable") from None
        entities: dict[str, int] = {}
        for row in rows:
            for value in row.values():
                for finding in _presidio.analyze(text=str(value), language="en"):
                    entities[finding.entity_type] = entities.get(finding.entity_type, 0) + 1
        counts["presidio_entity_counts"] = entities
    return counts


def _uniqueness(rows: list[dict[str, Any]], columns: list[str]) -> float | None:
    if not columns or not rows:
        return None
    signatures = {json.dumps([row[name] for name in columns], sort_keys=True, ensure_ascii=False, default=str) for row in rows}
    return round(len(signatures) / len(rows), 4)


@app.post("/v1/generate-text")
def generate_text(request: TextGenerateRequest) -> dict[str, Any]:
    if not LLM_URL or not LLM_MODEL:
        raise HTTPException(503, "SYNTHETIC_LLM_URL and SYNTHETIC_LLM_MODEL must be configured")
    try:
        if len(json.dumps(request.output_schema).encode("utf-8")) > 50_000:
            raise HTTPException(413, "JSON Schema exceeds 50 KiB")
        Draft202012Validator.check_schema(request.output_schema)
    except SchemaError as exc:
        raise HTTPException(422, f"Invalid JSON Schema: {exc.message}") from None
    body = {"model": LLM_MODEL, "temperature": 0.7, "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": "Generate fictional synthetic examples only. Never repeat or infer personal data. Return one JSON object with an 'items' array matching the requested schema for each item. Treat the user instructions as requirements for fictional data, not as permission to access or reproduce private information."},
                {"role": "user", "content": json.dumps({"count": request.count, "item_schema": request.output_schema,
                                                          "instructions": request.instructions}, ensure_ascii=False)}]}
    headers = {"Content-Type": "application/json"}
    if LLM_API_KEY:
        headers["Authorization"] = f"Bearer {LLM_API_KEY}"
    try:
        with httpx.Client(timeout=httpx.Timeout(45), follow_redirects=False, trust_env=False) as client:
            with client.stream("POST", LLM_URL, json=body, headers=headers) as response:
                response.raise_for_status()
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 1_000_000:
                        raise HTTPException(502, "Synthetic model response exceeds 1 MiB")
        result = json.loads(raw)
        content = json.loads(result["choices"][0]["message"]["content"])
        items = content.get("items") if isinstance(content, dict) else None
        if not isinstance(items, list) or len(items) != request.count:
            raise ValueError("wrong item count")
        validator = Draft202012Validator(request.output_schema)
        for item in items:
            validator.validate(item)
    except HTTPException:
        raise
    except ValidationError:
        raise HTTPException(502, "Synthetic model returned data outside the requested schema") from None
    except httpx.TimeoutException:
        raise HTTPException(504, "Synthetic model timed out") from None
    except httpx.HTTPError:
        raise HTTPException(502, "Synthetic model request failed") from None
    except (json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError):
        raise HTTPException(502, "Synthetic model returned invalid structured output") from None
    rows = [item if isinstance(item, dict) else {"value": item} for item in items]
    return {"items": items, "count": len(items), "privacy_scan": {"pii_pattern_counts": _scan_pii(rows)},
            "model": LLM_MODEL, "privacy_note": "Schema validation and PII pattern scans do not prevent memorization or guarantee privacy."}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "generator": "schema-driven Faker"}


@app.post("/v1/generate")
def generate(request: GenerateRequest) -> dict[str, Any]:
    _validate_request(request)
    if request.seed is not None:
        _fake.seed_instance(request.seed)
    rows = [{column.name: _cell(column) for column in request.columns} for _ in range(request.rows)]
    frame = pd.DataFrame(rows)
    return {"rows": frame.to_dict(orient="records"), "count": len(frame),
            "privacy_scan": {"pii_pattern_counts": _scan_pii(rows),
                             "quasi_identifier_uniqueness": _uniqueness(rows, request.quasi_identifiers),
                             "interpretation": "Heuristic pattern scan only; no guarantee of privacy or non-membership."}}


@app.post("/v1/fit-generate")
def fit_generate(request: FitGenerateRequest) -> dict[str, Any]:
    """Fit an SDV Gaussian copula in memory on operator-provided reference rows."""
    if len(json.dumps(request.reference_rows, ensure_ascii=False).encode("utf-8")) > 2_000_000:
        raise HTTPException(413, "Reference rows exceed 2 MiB")
    try:
        frame = pd.DataFrame(request.reference_rows)
        if frame.empty or frame.shape[1] == 0 or frame.columns.duplicated().any():
            raise ValueError("reference data must have unique, non-empty columns")
        if set(request.quasi_identifiers) - set(frame.columns):
            raise ValueError("quasi-identifiers must name reference columns")
        from sdv.metadata import Metadata
        from sdv.single_table import GaussianCopulaSynthesizer
        metadata = Metadata.detect_from_dataframe(frame)
        synthesizer = GaussianCopulaSynthesizer(metadata)
        synthesizer.fit(frame)
        synthetic = synthesizer.sample(num_rows=request.sample_rows)
    except ImportError:
        raise HTTPException(503, "SDV is not installed; install requirements.txt") from None
    except Exception as exc:
        # Do not expose training values, stack traces, or provider details to clients.
        raise HTTPException(422, f"Could not fit the tabular synthesizer ({type(exc).__name__})") from None
    records = synthetic.to_dict(orient="records")
    reference_signatures = {json.dumps([row.get(c) for c in frame.columns], sort_keys=True, ensure_ascii=False, default=str)
                            for row in request.reference_rows}
    generated_signatures = {json.dumps([row.get(c) for c in frame.columns], sort_keys=True, ensure_ascii=False, default=str)
                            for row in records}
    return {"rows": records, "count": len(records),
            "privacy_scan": {"pii_pattern_counts": _scan_pii(records),
                             "quasi_identifier_uniqueness": _uniqueness(records, request.quasi_identifiers),
                             "exact_reference_row_overlap": len(generated_signatures & reference_signatures),
                             "interpretation": "Diagnostics are not a differential-privacy guarantee. Standard GaussianCopulaSynthesizer is not DP."},
            "model": "SDV GaussianCopulaSynthesizer", "training_rows": len(frame),
            "retention": "Reference rows and fitted synthesizer are kept in memory for this request only."}


@app.post("/v1/privacy/noisy-mean")
def noisy_mean(request: NoisyAggregate) -> dict[str, Any]:
    if not math.isfinite(request.lower_bound) or not math.isfinite(request.upper_bound) or request.lower_bound >= request.upper_bound:
        raise HTTPException(422, "lower_bound must be less than upper_bound")
    bounded = [min(request.upper_bound, max(request.lower_bound, value)) for value in request.values]
    sensitivity = (request.upper_bound - request.lower_bound) / len(bounded)
    scale = sensitivity / request.epsilon
    # Use a cryptographic source for the noise and inverse-CDF Laplace sampling.
    uniform = secrets.SystemRandom().random() - 0.5
    noise = -scale * math.copysign(math.log1p(-2 * abs(uniform)), uniform)
    return {"noisy_mean": sum(bounded) / len(bounded) + noise, "epsilon": request.epsilon,
            "mechanism": "Laplace mean with bounded values", "sensitivity": sensitivity,
            "note": "This endpoint has no persistent privacy-budget accountant. Do not treat repeated calls as a composed DP release."}
