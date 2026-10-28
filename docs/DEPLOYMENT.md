# Deployment

## Local

Install `requirements.txt` in an isolated Python environment and run:

```sh
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## Docker Compose

```sh
docker compose up --build
```

The container has a read-only root filesystem and publishes only on localhost. The SDV fit endpoint is CPU- and memory-intensive; production deployments need request authentication, bounded workers, memory limits, and request timeouts. Do not persist raw input in proxy logs or crash dumps.

For optional text generation, configure `SYNTHETIC_LLM_URL`, `SYNTHETIC_LLM_MODEL`, and `SYNTHETIC_LLM_API_KEY` through a secret manager. For Presidio, set `PRESIDIO_ENABLED=true` and install the NLP package and language model required by the chosen Presidio setup. The base image intentionally does not download a large NLP model.

## Example calls

Use `data/schema.example.json` for `/v1/generate`. For `/v1/fit-generate`, send a JSON object with `reference_rows` and `sample_rows`; include only data approved for this purpose. For `/v1/privacy/noisy-mean`, include `values`, finite `lower_bound`/`upper_bound`, and epsilon between 0 and 10.
