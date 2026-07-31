# Agentics

**A queue-backed multi-agent workflow system that turns natural-language data questions into approved, validated, and auditable task graphs.**

Upload a CSV and describe what you want to learn. Agentics inspects the data, proposes an execution plan, pauses for human approval, and dispatches the approved graph to independent workers. Analyst agents generate and run Python against scoped inputs, while deterministic services control scheduling, validation, persistence, retries, and state transitions.

![Agentics preview](https://github.com/ebrahimAlhaddad/agentics-multi-agent-system/blob/main/PREVIEW.gif)

## What happens during a run

1. **Inspect and plan** — a conversational planner profiles the uploaded data and converts the request into a typed DAG.
2. **Validate and approve** — code checks graph structure and artifact flow; a semantic reviewer checks whether the plan can answer the question; the user approves it before analysis work begins.
3. **Execute** — a deterministic orchestrator computes the eligible frontier and publishes tasks through SQS-compatible queues to separately running workers.
4. **Verify and synthesize** — workers execute model-written Python in fresh subprocesses, persist declared artifacts, and review outputs before downstream tasks can consume them. A terminal agent writes the final report and performs a separate faithfulness check against the computed evidence.

## Why this is more than a “chat with a CSV” demo

The analysis is the demonstration workload. The engineering focus is the workflow system around the models:

- **Persisted orchestration** — plans become executable task rows in PostgreSQL; queue messages carry identifiers, not workflow state.
- **Deterministic control plane** — models propose and judge work, while code owns DAG validation, dependency resolution, frontier scheduling, task claims, retries, and terminal-state handling.
- **Real process boundaries** — the API, orchestrator, and task workers run as separate services, connected through ElasticMQ locally using the same SQS API used by AWS.
- **Scoped data flow** — tasks declare what they consume and produce. Workers receive only those artifacts, addressed through logical handles rather than storage paths.
- **Failure-aware execution** — conditional database claims prevent duplicate dispatch, failed tasks can be retried, unrecoverable descendants are superseded, and workflow state survives process boundaries.
- **Auditable outputs** — generated code, intermediate artifacts, task attempts, final reports, and faithfulness notes are persisted for inspection.

## What this project demonstrates

- Backend and distributed-systems design
- Agent orchestration with deterministic safety boundaries
- Asynchronous queue consumers and horizontally scalable workers
- Sandboxed execution of model-generated Python
- PostgreSQL-backed workflow state and artifact metadata
- Local and S3-backed object storage adapters
- FastAPI APIs, server-sent event streaming, and a Next.js interface
- Optional Cognito authentication and AWS infrastructure/CI scaffolding
- Unit and integration testing across orchestration, agents, storage, sandboxing, routes, and graph validation

## Stack

| Area | Technology |
|---|---|
| Agent runtime | OpenAI Agents SDK |
| API | Python, FastAPI, SSE |
| Workflow state | PostgreSQL, SQLAlchemy |
| Messaging | SQS API via `boto3`; ElasticMQ locally |
| Execution | Isolated subprocess sandbox, pandas, PyArrow, Matplotlib |
| Artifact storage | Local filesystem or Amazon S3 |
| Frontend | Next.js, React, TypeScript, Tailwind, shadcn/ui |
| Authentication | Amazon Cognito through Amplify, optional locally |
| Local runtime | Docker Compose |
| Infrastructure | AWS CDK, ECS/Fargate, RDS, Amplify, ECR/GitHub Actions |

## Read the engineering details

- [`SUMMARY.md`](https://github.com/ebrahimAlhaddad/agentics-multi-agent-system/blob/main/SUMMARY.md) — concise technical overview, architecture, reliability model, and tradeoffs
- [`DESIGN.md`](https://github.com/ebrahimAlhaddad/agentics-multi-agent-system/blob/main/DESIGN.md) — full implementation-level design and failure analysis

## Current status

The complete queue-backed workflow runs locally with Docker Compose: FastAPI, PostgreSQL, ElasticMQ, a deterministic orchestrator, and independently scalable task workers. Authentication can be disabled and artifact storage can remain local, so no AWS account is required for development.

The repository also contains AWS CDK and CI/CD scaffolding. The full production topology and additional hardening—such as transactional outbox/reconciliation, worker leases, visibility heartbeats, and production load validation—remain documented follow-up work.

---

# Development

## Prerequisites

- Docker Desktop with Docker Compose
- Python 3.11 and Pipenv for running tests or backend processes outside Docker
- Node.js and `pnpm` for the frontend
- An OpenAI API key

## Quick start

### 1. Configure the project

From the repository root:

```bash
cp .env.example .env
cp .env.backend.example .env.backend
cp client/.env.local.example client/.env.local
```

Open `.env` and provide the only required external credential:

```env
OPENAI_API_KEY=sk-...
```

The checked-in examples already configure local PostgreSQL, ElasticMQ, local artifact storage, and disabled authentication. No AWS credentials are required.

### 2. Start the backend topology

```bash
docker compose up --build -d
```

This starts:

- `backend` — FastAPI service
- `orchestrator` — consumes run-advance messages and dispatches eligible DAG tasks
- `worker` — consumes and executes analyst/synthesizer tasks
- `postgres` — workflow and artifact metadata
- `sqs` — ElasticMQ using the SQS wire protocol
- `pgadmin` — optional database UI

Follow the execution logs with:

```bash
docker compose logs -f backend orchestrator worker
```

### 3. Start the frontend

In another terminal:

```bash
cd client
pnpm install
pnpm dev
```

Open **http://localhost:3000/wizard**, upload a CSV, and start a run.

| Service | URL |
|---|---|
| Frontend | http://localhost:3000 |
| FastAPI | http://localhost:8001 |
| OpenAPI docs | http://localhost:8001/docs |
| ElasticMQ UI | http://localhost:9325 |
| pgAdmin | http://localhost:5050 |

## Scale task execution

Tasks in the same DAG frontier are independent and can be handled by separate workers. Scale only the task-worker service:

```bash
docker compose up -d --scale worker=4
```

The orchestrator is intentionally lightweight; it reads persisted state, claims eligible tasks, publishes messages, and returns.

## Useful commands

```bash
# Show running services
docker compose ps

# Follow all backend logs
docker compose logs -f backend orchestrator worker sqs

# Restart one component
docker compose restart worker

# Stop the stack while preserving Postgres and artifact volumes
docker compose down

# Remove the stack and all local data
docker compose down -v
```

The application creates missing database tables at startup. It does not currently run schema migrations or alter existing tables, so use `docker compose down -v` after incompatible model/schema changes.


## Configuration

Important settings are declared in [`backend/settings.py`](backend/settings.py) and documented in [`.env.example`](.env.example).

| Setting | Purpose | Local default |
|---|---|---|
| `OPENAI_API_KEY` | OpenAI credential | required |
| `LLM_MODEL` | Model used by agents | `gpt-4o` |
| `DISABLE_AUTH` | Bypass Cognito locally | `true` |
| `STORAGE_BACKEND` | `local` or `s3` | `local` |
| `QUEUE_ENDPOINT_URL` | ElasticMQ endpoint; empty uses AWS SQS | `http://sqs:9324` |
| `MAX_TASK_ATTEMPTS` | Whole-task retry limit | `3` |
| `MAX_CODE_ATTEMPTS` | Analyst rewrite/review rounds | `3` |
| `SANDBOX_TIMEOUT_S` | Generated-code wall-clock limit | `60` |
| `MAX_UPLOAD_BYTES` | Maximum accepted CSV size | `100 MiB` |

The frontend reads `client/.env.local`, not the repository-root `.env`. Restart `pnpm dev` after changing any `NEXT_PUBLIC_*` value. `DISABLE_AUTH` and `NEXT_PUBLIC_DISABLE_AUTH` should agree.

## Tests

Install backend development dependencies and run the suite:

```bash
cd backend
pipenv install --dev
pipenv run pytest
```

The suite covers DAG validation, orchestration transitions, planner and worker loops, artifact persistence, storage adapters, sandbox behavior, API routes, and data profiling. Unit tests run without credentials or containers; database-backed integration tests can use the local PostgreSQL service.

The generated OpenAPI reference is available at `http://localhost:8001/docs` while the API is running.

## Repository layout

```text
├──backend/
│  ├── server/                  FastAPI application and routers
│  ├── workers/                 queue consumer, orchestrator, and task entry points
│  ├── services/
│  │   ├── agents/              planner and discoverable worker roles
│  │   ├── dag_service.py       deterministic graph validation and frontier logic
│  │   ├── orchestrator_service.py
│  │   ├── artifact_service.py
│  │   ├── run_service.py
│  │   └── session_service.py
│  ├── external/                PostgreSQL, SQS, storage, Cognito, LLM, sandbox
│  ├── models/                  database, domain, and API models
│  └── tests/                   unit and integration tests
│
├──client/                      Next.js demonstration interface
├──docker/                      ElasticMQ configuration
├──infrastructure/             AWS CDK stacks
├──.github/workflows/           ECR/ECS deployment workflows
```
