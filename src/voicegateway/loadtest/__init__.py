"""Ingest for an external load generator's run artifacts.

VoiceGateway does not place calls. An external SIP generator does that, writes
artifacts, and this package reads them. Keeping the generator out of this
repository is deliberate: it is AGPL-3.0 and this project is MIT, so it runs as a
separate binary and only its output files cross the boundary.
"""

from voicegateway.loadtest.artifacts import (
    SUPPORTED_SUMMARY_SCHEMA,
    AnswerLatency,
    ArtifactError,
    IntervalSample,
    MalformedArtifact,
    MissingArtifact,
    ParsedTest,
    RunSummary,
    UnknownArtifactSchema,
    parse_stat_csv,
    parse_summary,
    parse_test_directory,
)

__all__ = [
    "SUPPORTED_SUMMARY_SCHEMA",
    "AnswerLatency",
    "ArtifactError",
    "IntervalSample",
    "MalformedArtifact",
    "MissingArtifact",
    "ParsedTest",
    "RunSummary",
    "UnknownArtifactSchema",
    "parse_stat_csv",
    "parse_summary",
    "parse_test_directory",
]
