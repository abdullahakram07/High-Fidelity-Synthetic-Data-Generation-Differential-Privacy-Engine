# Data handling and privacy

## Input controls

- Schema-only generation accepts at most 20,000 output rows and 100 columns.
- SDV fitting accepts 20–5,000 reference rows and limits serialized input to 2 MiB.
- The API does not persist submitted reference rows or synthesizer state; memory is released after the request completes.
- Do not provide real sensitive data to this unauthenticated local MVP unless the environment is approved for that purpose.

## Diagnostic meanings

- PII pattern counts cover email-like strings, SSN-shaped strings, and payment-card candidates only. They are not entity recognition and do not prove zero PII exposure.
- Presidio scanning is an optional additional analyzer path, disabled unless `PRESIDIO_ENABLED=true`; its language model and entity coverage must be configured and evaluated for the deployment.
- The optional text-generation endpoint sends only generation instructions and schema to the configured LLM. Do not include private source rows in those instructions; provider retention and memorization risks still apply.
- Quasi-identifier uniqueness is the fraction of distinct combinations among generated rows, not an anonymity guarantee.
- Exact-row overlap is an exact duplicate check against supplied rows. It does not detect near-duplicates, attribute disclosure, membership inference, or sensitive correlations.
- Standard SDV Gaussian Copula fitting is not differentially private.

## Differential privacy endpoint

`POST /v1/privacy/noisy-mean` implements a Laplace mechanism for the mean after clipping values to the request bounds. Its sensitivity assumes one bounded scalar contribution per supplied row. Epsilon is caller-selected for that release. This API has no identity, query policy, or cumulative privacy-budget tracking; repeated releases are not accounted for. It is a mechanism example, not a complete DP service.

## Operational requirements

Before production, define authorized data use, retention, access controls, privacy budget ownership, accounting across releases, monitoring, and a reviewed SDV synthesizer/DP configuration. Keep raw input out of logs and telemetry.
