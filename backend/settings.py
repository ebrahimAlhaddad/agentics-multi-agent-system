from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings
import logging
import os


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Optional settings
    PROJECT_NAME: str = Field(default="Agentics", title="Project Name")
    LOG_LEVEL: str = Field(default=logging.getLevelName(logging.INFO), title="Log Level")
    DISABLE_AUTH: bool = Field(default=False, title="Disable Authentication")

    # One provider. There is no switch, so there is no branch to get wrong.
    OPENAI_API_KEY: Optional[str] = Field(default=None, title="OpenAI API Key")
    LLM_MODEL: Optional[str] = Field(default=None, title="Model name")
    #: Retries of a single HTTP call to OpenAI — a rate limit or a 5xx, not a bad
    #: answer. The client's own default is 2 with a 0.5s initial backoff, which
    #: is short against a tokens-per-minute limit that asks for several seconds.
    #: Distinct from MAX_TASK_ATTEMPTS: this retries one request, that re-runs
    #: the whole task.
    MAX_LLM_RETRIES: int = Field(
        default=6, title="Retries of one rate-limited or failed model request"
    )

    # Sandbox for model-written code
    SANDBOX_TIMEOUT_S: float = Field(default=60.0, title="Sandbox wall-clock timeout")
    #: Memory is the container's job (docker --memory / an ECS task limit),
    #: which caps RSS. RLIMIT_AS used to be set here and was always disabled: it
    #: caps address space, and pyarrow reserves gigabytes of it while using a
    #: fraction in RSS, so enforcing it broke every parquet read.
    MAX_OUTPUT_BYTES: int = Field(
        default=100 * 1024 * 1024,
        title="Total bytes one sandbox run may leave in its output directory",
    )

    # Orchestration
    MAX_TASK_ATTEMPTS: int = Field(default=3, title="Attempts before a task is abandoned")
    # Both of these bound how much a single request can spend: each retry is
    # another model call, so they are the knobs a runaway loop is billed through.
    MAX_CODE_ATTEMPTS: int = Field(
        default=3, title="Rewrites of one task's code before it is failed"
    )
    MAX_PLANNING_ATTEMPTS: int = Field(
        default=3, title="Re-plans after structural validation rejects a plan"
    )
    # No envelope size cap any more: a message is a handler name plus two ids, so
    # it cannot approach a queue limit. Likewise no dispatch concurrency or task
    # timeout — concurrency is the number of worker processes, and the timeout is
    # the queue's visibility timeout in docker/elasticmq.conf.

    # Queue. Locally this points at the ElasticMQ container, which speaks the
    # SQS protocol; deployed environments set it empty and boto3 talks to real
    # SQS with the instance role. Same client either way.
    QUEUE_ENDPOINT_URL: Optional[str] = Field(
        default="http://sqs:9324", title="SQS endpoint; empty means real AWS SQS"
    )
    # Standard. An advance says "look at this run", so order does not matter and
    # a duplicate costs one query — the conditional claim in run_service is what
    # keeps two orchestrators from dispatching the same task.
    QUEUE_RUNS: str = Field(default="runs", title="Run advance queue")
    # Standard, deliberately: the frontier is dispatched because those tasks are
    # independent, and a FIFO group per run would serialise them again.
    QUEUE_TASKS: str = Field(default="tasks", title="Task envelope queue")
    QUEUE_WAIT_SECONDS: int = Field(default=20, title="Long poll seconds per receive")

    # Object storage. Defaults to a local directory so development needs no AWS;
    # deployed environments set STORAGE_BACKEND=s3 and a bucket.
    STORAGE_BACKEND: str = Field(default="local", title="Storage Backend (local|s3)")
    STORAGE_LOCAL_PATH: str = Field(default="/tmp/agentics-storage", title="Local Storage Root")
    STORAGE_S3_BUCKET: Optional[str] = Field(default=None, title="Artifact S3 Bucket")
    STORAGE_S3_PREFIX: Optional[str] = Field(default=None, title="Artifact S3 Key Prefix")
    # A CSV past this is read into memory whole, so raising it needs chunked
    # ingestion rather than just a bigger number.
    MAX_UPLOAD_BYTES: int = Field(
        default=100 * 1024 * 1024, title="Largest accepted upload, in bytes"
    )


    # Required settings
    MAX_TOKENS: int = Field(..., title="Max Tokens")
    AWS_ACCESS_KEY: str = Field(default="", title="AWS Access Key ID")
    AWS_SECRET_ACCESS_KEY: str = Field(default="", title="AWS Secret Access Key")
    # FRONTEND_URL: str = Field(...,title="Frontend URL")
    BACKEND_PORT: int = Field(..., title="Port")
    BACKEND_HOST: str = Field(..., title="Host")
    DEFAULT_AWS_REGION: str = Field(..., title="Default AWS Region")
    POSTGRES_USER: str = Field(..., title="Postgres User")
    POSTGRES_PASSWORD: str = Field(..., title="Postgres Password")
    POSTGRES_DB: str = Field(..., title="Postgres Database")
    POSTGRES_HOST: str = Field(..., title="Postgres Host")
    POSTGRES_PORT: int = Field(..., title="Postgres Port")

    @property
    def model_name(self) -> str:
        return self.LLM_MODEL or "gpt-4o"

    class Config:
        extra = "allow"
        # Look for .env file in the project root (one level up from app)
        env_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
        env_file_encoding = "utf-8"
        case_sensitive = True


# exports settings singleton
settings = Settings() 