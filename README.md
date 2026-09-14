# ec-imagegen

An image-to-image **restyle service** for merchant food photos. A merchant uploads a photo of a dish; the service sends it to **Replicate's Flux Kontext Pro** model to produce several professionally restyled variations (same dish, studio-quality background/lighting/composition) that the merchant can then choose between.

This is a **restyle-only** service — there is no text-to-image generation.

---

## Table of contents

- [How it works](#how-it-works)
- [Architecture](#architecture)
- [Project structure](#project-structure)
- [Requirements](#requirements)
- [Setup](#setup)
- [Configuration](#configuration)
- [Running the service](#running-the-service)
- [API reference](#api-reference)
- [Job lifecycle](#job-lifecycle)
- [Storage](#storage)
- [Error handling & retries](#error-handling--retries)
- [Known limitations](#known-limitations)
- [Roadmap ideas](#roadmap-ideas)

---

## How it works

1. Merchant uploads a photo → `POST /jobs/restyle`.
2. The photo is validated (real image content, allowed format, size limit) and saved to storage.
3. `MAX_RESTYLE_VARIATIONS` (default **3**) jobs are created in the database, each queued for background processing.
4. A background worker picks up each job, builds a style-specific prompt, and calls Replicate to generate the restyled image.
5. The client polls job/batch status until all variations are `completed` (or `failed`).
6. The merchant picks their favorite via `POST /jobs/restyle/select` — no additional generation happens; the chosen image is already full quality.

---

## Architecture

```
Client
  │  POST /jobs/restyle (multipart photo + optional styling notes)
  ▼
FastAPI API (main.py, routers/jobs.py)
  │  validates upload, saves source photo, creates N job rows
  ▼
PostgreSQL (image_jobs table)
  │
  │  enqueues N jobs
  ▼
Redis / RQ queue ("imagegen")
  │
  ▼
RQ Worker (worker.py) — separate process
  │  reads source photo, builds prompt, calls provider
  ▼
Replicate API (Flux Kontext Pro)
  │  returns restyled image bytes
  ▼
Local/S3 storage ─── job marked completed/failed in Postgres
```

The API process and the worker process are fully decoupled — the API never blocks on the (potentially slow, up to several minutes) Replicate call. Both talk to the same Postgres database and Redis instance.

---

## Project structure

```
app/
├── core/
│   └── config.py            # Settings (pydantic-settings), loaded from .env
├── db/
│   ├── database.py          # Engine/session management, get_db() dependency
│   └── models.py             # ImageJob ORM model, JobStatus enum
├── inference/
│   ├── base.py               # InferenceProvider interface, InferenceError
│   └── replicate_provider.py # Replicate/Flux Kontext implementation
├── routers/
│   └── jobs.py                # HTTP endpoints (upload, status, select)
├── services/
│   ├── job_service.py         # Batch creation, selection logic
│   ├── prompt_builder.py      # Deterministic restyle prompt construction
│   └── storage.py             # Storage backend abstraction (local/S3)
├── queue.py                   # Redis connection + RQ queue instance
├── schemas.py                 # Pydantic request/response models
├── worker.py                  # RQ job entry point (process_image_job)
└── main.py                    # FastAPI app, lifespan, health check
requirements.txt
.env
```

---

## Requirements

- Python 3.11+
- PostgreSQL (or SQLite for local dev)
- Redis
- A [Replicate](https://replicate.com) API token with available credit

---

## Setup

```bash
# 1. Clone and enter the project
git clone <repo-url>
cd ec-imagegen

# 2. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env           # then fill in the values (see below)

# 5. Start Postgres and Redis (example via Docker)
docker run -d --name pg -e POSTGRES_USER=ecimagegen -e POSTGRES_PASSWORD=changeme \
  -e POSTGRES_DB=ecimagegen -p 5432:5432 postgres:16
docker run -d --name redis -p 6379:6379 redis:7
```

> Tables are created automatically at API startup (`init_db()` in `database.py`). There is currently no Alembic migration setup — this is fine for local development but should be replaced with real migrations before production use.

---

## Configuration

All configuration is read from a `.env` file at the project root via `pydantic-settings` (`app/core/config.py`).

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | **required** | Postgres (`postgresql+psycopg://...`) or SQLite (`sqlite:///./dev.db`) connection string |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection for the job queue |
| `RQ_QUEUE_NAME` | `imagegen` | RQ queue name |
| `REPLICATE_API_TOKEN` | **required** | Your Replicate API token |
| `RESTYLE_MODEL` | `black-forest-labs/flux-kontext-pro` | Replicate model used for restyling |
| `RESTYLE_PRICE_PER_IMAGE_USD` | `0.04` | Recorded cost per generated image |
| `RESTYLE_POLL_TIMEOUT_SECONDS` | `180` | Max time to poll a single Replicate prediction before treating it as failed/retryable |
| `RESTYLE_JOB_TIMEOUT_SECONDS` | `600` | RQ's own hard timeout for the whole job (covers all retries) |
| `MAX_RESTYLE_VARIATIONS` | `3` | Number of restyled variations generated per upload |
| `IMAGE_SIZE` | `1024x1024` | Target image size passed to the provider |
| `OUTPUT_IMAGE_FORMAT` | `jpg` | Format Replicate returns output in (`jpg`, `png`, `webp`) |
| `MAX_UPLOAD_SIZE_BYTES` | `10485760` (10 MB) | Max accepted upload size |
| `ALLOWED_IMAGE_FORMATS` | **required** | Comma-separated list, e.g. `JPEG,PNG,WEBP,HEIF` |
| `MAX_EXTRA_STYLING_LEN` | `None` (disabled) | Set to an integer to enable merchant/backend styling notes on uploads |
| `STORAGE_BACKEND` | `local` | `local` or `s3` (S3 is not yet implemented) |
| `LOCAL_STORAGE_DIR` | `./storage` | Where images are stored when using local storage |
| `S3_BUCKET` / `S3_REGION` | — | Reserved for future S3 support |
| `DB_POOL_SIZE` | `5` | Postgres connection pool size (ignored for SQLite) |
| `DB_MAX_OVERFLOW` | `10` | Postgres pool overflow (ignored for SQLite) |
| `DB_POOL_RECYCLE_SECONDS` | `1800` | Postgres connection recycle interval (ignored for SQLite) |
| `REQUEST_TIMEOUT_SECONDS` | `60` | General HTTP timeout |
| `MAX_RETRIES` | `3` | Retry attempts for transient provider failures |
| `RETRY_BACKOFF_SECONDS` | `2.0` | Linear backoff multiplier between retries |

> **Security note:** never commit a real `.env` file or API token to version control. Rotate any credential that has ever been exposed in a shared file, chat, or repository.

---

## Running the service

You need **two processes** running against the same Postgres/Redis:

**1. API server**
```bash
uvicorn app.main:app --reload --port 8000
```

**2. Background worker** (handles the actual Replicate calls)
```bash
rq worker imagegen --url redis://localhost:6379/0
```

Health check: `GET /health` → `{"status": "ok"}`

---

## API reference

### `POST /jobs/restyle`
Upload a source photo and create a restyle batch.

**Request:** `multipart/form-data`
| Field | Type | Required | Notes |
|---|---|---|---|
| `photo` | file | yes | JPEG/PNG/WEBP/HEIF (per `ALLOWED_IMAGE_FORMATS`), ≤ `MAX_UPLOAD_SIZE_BYTES` |
| `extra_styling` | string | no | Only applied if `MAX_EXTRA_STYLING_LEN` is configured; otherwise ignored |

**Response:** `201 Created`
```json
{
  "batch_id": "6e2f...",
  "jobs": [
    { "id": 1, "batch_id": "6e2f...", "status": "pending", "model_used": null,
      "source_image_path": "storage/6e2f.../source_20260911_101500.jpg",
      "is_selected": false, "image_path": null, "cost_usd": null, "error_message": null },
    { "id": 2, "...": "..." },
    { "id": 3, "...": "..." }
  ]
}
```

**Errors:** `400` empty file · `413` file too large · `415` invalid/unsupported/unavailable format

---

### `GET /jobs/{job_id}`
Fetch a single job's status.

**Response:** `200` → `JobOut` object · `404` if not found

---

### `GET /jobs/batch/{batch_id}`
Fetch all jobs belonging to a batch (source photo's variations).

**Response:** `200` → array of `JobOut` · `404` if batch not found

---

### `POST /jobs/restyle/select`
Mark one job in a batch as the merchant's chosen image. Clears any prior selection in the same batch. Does **not** trigger new generation.

**Request:**
```json
{ "job_id": 2 }
```

**Response:** `201` → the selected `JobOut` · `404` if `job_id` doesn't exist

---

### `JobOut` shape

```json
{
  "id": 2,
  "batch_id": "6e2f...",
  "status": "completed",
  "model_used": "black-forest-labs/flux-kontext-pro",
  "source_image_path": "storage/6e2f.../source_20260911_101500.jpg",
  "is_selected": true,
  "image_path": "storage/6e2f.../restyle_1_20260911_101530.jpg",
  "cost_usd": 0.04,
  "error_message": null
}
```

---

## Job lifecycle

```
PENDING → PROCESSING → COMPLETED
                     └→ FAILED  (error_message set, short + DB-safe)
```

- Created as `PENDING` when the batch is submitted.
- Moved to `PROCESSING` the moment the worker picks it up.
- Moved to `COMPLETED` once the image is generated and saved, with `image_path`, `model_used`, and `cost_usd` populated.
- Moved to `FAILED` on any unrecoverable error (invalid input, exhausted retries, timeout, billing issue), with a short human-readable `error_message`. Full technical detail (stack traces, provider status codes) is logged server-side only.

Restyle prompts are **not stored** in the database — they're fully deterministic from `variation_index` + `extra_styling` and are rebuilt on demand by the worker (and logged there for debugging).

---

## Storage

Two backends behind a common `StorageBackend` interface (`app/services/storage.py`):

- **`local`** (default): writes to `LOCAL_STORAGE_DIR` on disk, keyed as `{batch_id}/{name}_{timestamp}.{ext}`.
- **`s3`**: reserved for future use — currently raises `NotImplementedError`. Do not set `STORAGE_BACKEND=s3` until this is implemented.

---

## Error handling & retries

- All Replicate calls retry up to `MAX_RETRIES` times with linear backoff (`RETRY_BACKOFF_SECONDS * attempt`).
- Retryable failures: rate limits (`429`), server errors (`5xx`), prediction timeouts, transient network errors.
- Non-retryable failures: insufficient Replicate credit (`402`), rejected requests (`4xx` other than `429`), unmapped output formats.
- Every failure path — including unexpected exceptions and RQ's own timeout kill — updates the job's status to `FAILED` so jobs never get permanently stuck in `PROCESSING`.

---

## Known limitations

- No database migration tooling (tables are created via `create_all()`; use Alembic before production).
- `S3Storage` is a stub — only local disk storage works today.
- `is_selected` uniqueness per batch is enforced in application code, not via a database constraint.
- HEIC/HEIF support depends on `pillow-heif` importing successfully at startup; if it fails (e.g. blocked on locked-down Windows environments), HEIC uploads are rejected but the rest of the service keeps working.

---

## Roadmap ideas

- Implement S3 storage backend.
- Add Alembic migrations.
- Add a database-level uniqueness constraint for `is_selected` per batch.
- Add authentication/authorization to the API.
- Add automated tests and CI.