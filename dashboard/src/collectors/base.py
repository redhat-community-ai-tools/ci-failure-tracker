"""
Base collector interface for pluggable data sources.

This allows switching between ReportPortal, Prow GCS, Sippy, etc.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import List, Dict, Any, Optional
from enum import Enum


class TestStatus(Enum):
    """Normalized test status across all collectors"""
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"
    UNKNOWN = "unknown"


@dataclass
class TestResult:
    """Normalized test result from any data source"""
    test_name: str
    status: TestStatus
    timestamp: datetime
    duration_seconds: Optional[float]
    error_message: Optional[str]

    # Metadata
    job_name: str
    build_id: str
    version: str  # e.g., "4.21", "4.22"
    platform: str  # e.g., "aws", "gcp", "azure"
    test_description: Optional[str] = None  # Human-readable test description

    # Links
    job_url: Optional[str] = None
    log_url: Optional[str] = None


@dataclass
class JobRun:
    """Normalized job run from any data source"""
    job_name: str
    build_id: str
    status: TestStatus  # Overall job status
    timestamp: datetime
    duration_seconds: Optional[float]
    version: str
    platform: str

    # Statistics
    total_tests: int
    passed_tests: int
    failed_tests: int
    skipped_tests: int

    # Links
    job_url: Optional[str] = None

    @property
    def pass_rate(self) -> float:
        """Calculate pass rate percentage"""
        if self.total_tests == 0:
            return 0.0
        return (self.passed_tests / self.total_tests) * 100.0


class BaseCollector(ABC):
    """Abstract base class for data collectors"""

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize collector with configuration

        Args:
            config: Collector-specific configuration
        """
        self.config = config

    @abstractmethod
    def collect_job_runs(
        self,
        start_date: datetime,
        end_date: datetime,
        job_patterns: Optional[List[str]] = None,
        versions: Optional[List[str]] = None,
        platforms: Optional[List[str]] = None
    ) -> List[JobRun]:
        """
        Collect job runs within date range

        Args:
            start_date: Start of date range
            end_date: End of date range
            job_patterns: Optional list of job name patterns to filter
            versions: Optional list of versions to filter (e.g., ["4.21", "4.22"])
            platforms: Optional list of platforms to filter (e.g., ["aws", "gcp"])

        Returns:
            List of normalized JobRun objects
        """
        pass

    @abstractmethod
    def collect_test_results(
        self,
        start_date: datetime,
        end_date: datetime,
        job_patterns: Optional[List[str]] = None,
        test_names: Optional[List[str]] = None,
        versions: Optional[List[str]] = None,
        platforms: Optional[List[str]] = None
    ) -> List[TestResult]:
        """
        Collect individual test results within date range

        Args:
            start_date: Start of date range
            end_date: End of date range
            job_patterns: Optional list of job name patterns to filter
            test_names: Optional list of test names to filter (e.g., ["OCP-11111"])
            versions: Optional list of versions to filter
            platforms: Optional list of platforms to filter

        Returns:
            List of normalized TestResult objects
        """
        pass

    @abstractmethod
    def health_check(self) -> bool:
        """
        Check if the data source is accessible

        Returns:
            True if healthy, False otherwise
        """
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the collector name (e.g., 'reportportal', 'prow-gcs')"""
        pass
