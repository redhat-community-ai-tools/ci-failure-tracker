---
name: collector-patterns
description: >-
  Teaches agents how to write and modify data collectors following the
  BaseCollector abstract base class pattern. Covers the normalized data
  model, collector registration, and config.yaml integration.
---

# Collector Development Guide

## BaseCollector Interface

All collectors in `dashboard/src/collectors/base.py` must implement:

```python
class BaseCollector(ABC):
    def __init__(self, config: Dict[str, Any]):
        self.config = config

    @abstractmethod
    def collect_job_runs(self, start_date, end_date, job_patterns,
                         versions, platforms) -> List[JobRun]:
        pass

    @abstractmethod
    def collect_test_results(self, start_date, end_date, job_patterns,
                             test_names, versions, platforms) -> List[TestResult]:
        pass

    @abstractmethod
    def health_check(self) -> bool:
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        pass
```

## Normalized Data Classes

### TestStatus Enum
```python
class TestStatus(Enum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"
    UNKNOWN = "unknown"
```

### TestResult Dataclass
Fields: `test_name`, `status` (TestStatus), `timestamp`, `duration_seconds`,
`error_message`, `job_name`, `build_id`, `version`, `platform`,
`test_description`, `job_url`, `log_url`

### JobRun Dataclass
Fields: `job_name`, `build_id`, `status`, `timestamp`, `duration_seconds`,
`version`, `platform`, `total_tests`, `passed_tests`, `failed_tests`,
`skipped_tests`, `job_url`

Has a computed `pass_rate` property.

## Adding a New Collector

1. Create `dashboard/src/collectors/new_collector.py`
2. Implement `BaseCollector` with all abstract methods
3. Register in `dashboard/src/collectors/__init__.py`
4. Add config section under `collector.new_type` in `config.yaml`
5. Add collector selection logic in the Flask app (`server.py`)

## Existing Collectors

| Collector | Auth | Data Source | Status |
|-----------|------|-------------|--------|
| reportportal | API token | ReportPortal REST API | Production |
| gcsweb | API token | qe-private-deck GCS | Production |
| prow_gcs | None | Public GCS buckets | Blocked (403) |
| prow_mcp | None | prow-mcp-server HTTP | POC |

## Common Patterns

- Use `requests.Session()` for connection pooling
- Parse JUnit XML from CI artifacts for test results
- Handle pagination (ReportPortal) or directory listing (GCS)
- Use `concurrent.futures.ThreadPoolExecutor` for parallel fetching
- Normalize all timestamps to UTC
- Map CI-specific status strings to `TestStatus` enum

## Shared Logic Patterns

Several methods are duplicated across collectors with near-identical
implementation. When fixing a bug in any of these, **always check all
collectors** for the same issue:

| Method | Collectors | Purpose |
|--------|------------|---------|
| `_extract_test_name()` | gcsweb, prow_gcs, reportportal | Parse OCP test IDs and descriptions from raw JUnit names |
| `_parse_junit_xml()` | gcsweb, prow_gcs | Parse `<testcase>` elements into TestResult |
| `_extract_metadata()` / `_extract_version_platform()` | gcsweb, prow_gcs, reportportal | Extract version and platform from job names |
| `_map_status()` | gcsweb, reportportal | Map source-specific status strings to TestStatus enum |

Consider extracting shared methods into `BaseCollector` if the
implementations converge. Until then, keep implementations in sync.
