# Architecture

## Components

- **Schema API** validates supported column kinds, bounds, and category declarations. The local Faker path creates synthetic data without loading source data.
- **SDV adapter** accepts bounded caller-supplied reference rows, detects single-table metadata, fits `GaussianCopulaSynthesizer`, samples the requested rows, and releases the fitted object after the request.
- **LLM text generator** optionally sends generation instructions and a JSON Schema to a configured OpenAI-compatible endpoint. It validates JSON and each generated object against the supplied schema before returning it.
- **Privacy diagnostics** scan generated values for a small set of PII-shaped patterns and calculate optional quasi-identifier uniqueness and exact row overlap.
- **Noisy mean** clamps the input values to declared bounds and adds Laplace noise using sensitivity `(upper_bound - lower_bound) / n` and scale `sensitivity / epsilon`.

```mermaid
sequenceDiagram
  actor Operator
  participant API as FastAPI
  participant Schema as Schema Validator
  participant Gen as Faker / SDV
  participant LLM as Configured LLM (optional)
  participant Scan as Privacy Diagnostics
  participant Result as Response
  Operator->>API: POST generation request
  API->>Schema: Check size, columns and constraints
  alt Schema-only request
    Schema->>Gen: Generate per declared type
  else Fit request with approved rows
    Schema->>Gen: Fit SDV in memory, sample, discard model
  else Text request
    Schema->>LLM: Instructions + output schema (no reference rows)
    LLM-->>Schema: JSON candidate output
  end
  Gen-->>Scan: Generated rows
  Scan-->>Result: Pattern counts, uniqueness, overlap
  Result-->>Operator: Rows + qualified diagnostics
```

## Boundaries

The SDV request does not persist rows or models. It is synchronous and can use substantial CPU and memory; enforce request limits and worker limits at deployment. The privacy metrics do not establish a formal privacy guarantee. LLM generation is optional and requires a configured provider; Presidio scanning is opt-in and requires a configured NLP model. The project does not implement a production privacy accountant.
