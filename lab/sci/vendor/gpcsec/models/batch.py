"""Persistent batch-state contracts with explicit transitions and recoverability."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

BATCH_SCHEMA_VERSION = 1


class BatchJobStatus(StrEnum):
    QUEUED = "queued"
    IMPORTING = "importing"
    IMPORTED = "imported"
    NEEDS_REVIEW = "needs_review"
    ANALYZING = "analyzing"
    SUCCESS = "success"
    WARNING = "warning"
    FAILED = "failed"
    CANCELLED = "cancelled"


ALLOWED_TRANSITIONS: dict[BatchJobStatus, frozenset[BatchJobStatus]] = {
    BatchJobStatus.QUEUED: frozenset({BatchJobStatus.IMPORTING, BatchJobStatus.CANCELLED}),
    BatchJobStatus.IMPORTING: frozenset(
        {BatchJobStatus.IMPORTED, BatchJobStatus.FAILED, BatchJobStatus.CANCELLED}
    ),
    BatchJobStatus.IMPORTED: frozenset(
        {BatchJobStatus.NEEDS_REVIEW, BatchJobStatus.ANALYZING, BatchJobStatus.CANCELLED}
    ),
    BatchJobStatus.NEEDS_REVIEW: frozenset(
        {BatchJobStatus.QUEUED, BatchJobStatus.ANALYZING, BatchJobStatus.CANCELLED}
    ),
    BatchJobStatus.ANALYZING: frozenset(
        {
            BatchJobStatus.SUCCESS,
            BatchJobStatus.WARNING,
            BatchJobStatus.FAILED,
            BatchJobStatus.CANCELLED,
        }
    ),
    BatchJobStatus.SUCCESS: frozenset({BatchJobStatus.QUEUED}),
    BatchJobStatus.WARNING: frozenset({BatchJobStatus.QUEUED}),
    BatchJobStatus.FAILED: frozenset({BatchJobStatus.QUEUED}),
    BatchJobStatus.CANCELLED: frozenset({BatchJobStatus.QUEUED}),
}


@dataclass(frozen=True, slots=True)
class BatchIssue:
    code: str
    message: str
    severity: str = "warning"
    schema_version: int = BATCH_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.code or not self.message or self.severity not in {"warning", "error"}:
            raise ValueError("batch issue needs code, message and warning/error severity")


@dataclass(frozen=True, slots=True)
class BatchJob:
    job_id: str
    source_path: str
    status: BatchJobStatus = BatchJobStatus.QUEUED
    progress: float = 0.0
    sample_ids: tuple[str, ...] = ()
    issues: tuple[BatchIssue, ...] = ()
    method_id: str | None = None
    calibration_id: str | None = None
    attempt: int = 0
    schema_version: int = BATCH_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.job_id or not self.source_path:
            raise ValueError("batch job identity and source path are required")
        if not 0.0 <= self.progress <= 1.0 or self.attempt < 0:
            raise ValueError("batch progress or attempt is invalid")


@dataclass(frozen=True, slots=True)
class BatchRun:
    run_id: str
    name: str
    created_utc: str
    updated_utc: str
    jobs: tuple[BatchJob, ...]
    schema_version: int = BATCH_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.run_id or not self.name or not self.created_utc or not self.updated_utc:
            raise ValueError("batch run identity, name and timestamps are required")
        ids = [job.job_id for job in self.jobs]
        if len(ids) != len(set(ids)):
            raise ValueError("batch run job IDs must be unique")
