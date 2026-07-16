"""
AI analyzer with infrastructure pre-classifiers and improved Vertex AI
prompt engineering.

Pre-classifies known infrastructure failure patterns (SSH flakes, DNS
failures, cloud quota errors) and test-repo fix commits before sending
to Vertex AI. For failures that reach the AI, uses structured prompts
with historical context and chain-of-thought reasoning to improve
classification accuracy.
"""

import os
import re
import requests
import json
import logging
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)

# Confidence threshold below which results are flagged for human review
LOW_CONFIDENCE_THRESHOLD = 60

SSH_PATTERNS = [
    re.compile(r'SSH attempt \d+ failed', re.IGNORECASE),
    re.compile(r'exit status 255'),
    re.compile(r'ssh:.*connection refused', re.IGNORECASE),
    re.compile(r'ssh:.*connection timed out', re.IGNORECASE),
    re.compile(r'ssh:.*no route to host', re.IGNORECASE),
    re.compile(r'bastion.*failed', re.IGNORECASE),
    re.compile(r'bastion.*timed? out', re.IGNORECASE),
    re.compile(r'failed to connect.*ssh', re.IGNORECASE),
    re.compile(r'dial tcp.*:22.*connection refused', re.IGNORECASE),
    re.compile(r'kex_exchange_identification', re.IGNORECASE),
    re.compile(r'connection reset by.*port 22', re.IGNORECASE),
    # Expanded patterns: hung SSH connections and non-255 exit codes
    re.compile(r'ssh.*exit status [1-9]\d*', re.IGNORECASE),
    re.compile(r'ssh command.*timed? out', re.IGNORECASE),
    re.compile(r'ssh.*connection.*closed', re.IGNORECASE),
    re.compile(r'Process exited with status \d+.*ssh', re.IGNORECASE),
    re.compile(r'dial tcp.*:22.*i/o timeout', re.IGNORECASE),
    re.compile(r'dial tcp.*:22.*connection timed out', re.IGNORECASE),
]

TIMEOUT_FLAKE_PATTERNS = [
    re.compile(
        r'Failed to check Windows machine should be in Provisioning phase',
        re.IGNORECASE,
    ),
    re.compile(
        r'waiting up to \d+\S* (?:minutes?|seconds?)',
        re.IGNORECASE,
    ),
    re.compile(
        r'timed? ?out waiting for (?:the )?(?:machine|node|pod|condition)',
        re.IGNORECASE,
    ),
    re.compile(
        r'(?:context deadline exceeded|deadline exceeded)',
        re.IGNORECASE,
    ),
    re.compile(
        r'did not become (?:ready|available|running) within',
        re.IGNORECASE,
    ),
    re.compile(
        r'(?:remained|still) not ready',
        re.IGNORECASE,
    ),
    re.compile(
        r'not (?:ready|running) after (?:waiting up to )?\d+\S*.(?:minute|second)',
        re.IGNORECASE,
    ),
    re.compile(
        r'(?:certificate|CA|cert)\s+rotation',
        re.IGNORECASE,
    ),
    re.compile(
        r'Expected:.*"Running".*Got:.*"Stopped',
        re.IGNORECASE | re.DOTALL,
    ),
]

DNS_PATTERNS = [
    re.compile(r'no such host', re.IGNORECASE),
    re.compile(r'could not resolve host', re.IGNORECASE),
    re.compile(r'dns lookup.*failed', re.IGNORECASE),
    re.compile(r'Temporary failure in name resolution', re.IGNORECASE),
    re.compile(r'lookup.*server misbehaving', re.IGNORECASE),
]

QUOTA_PATTERNS = [
    re.compile(r'quota.*exceeded', re.IGNORECASE),
    re.compile(r'(?:resource|cpu|memory|storage|instance|vcpu).*limit.*exceeded', re.IGNORECASE),
    re.compile(r'InsufficientInstanceCapacity', re.IGNORECASE),
    re.compile(r'CapacityReservation', re.IGNORECASE),
    re.compile(r'QUOTA_EXCEEDED', re.IGNORECASE),
    re.compile(r'ResourceQuotaExceeded', re.IGNORECASE),
]

ASSERTION_PATTERNS = [
    re.compile(r'o\.Expect\(', re.MULTILINE),
    re.compile(r'e2e\.Failf\('),
    re.compile(r'gomega.*to.*equal|gomega.*to.*contain', re.IGNORECASE),
]

KNOWN_FLAKY_TEST_PATTERNS = [
    re.compile(r'(?:certificate|CA|cert|kubelet)\s+(?:CA\s+)?rotation', re.IGNORECASE),
]


def _fetch_logs(log_url: str) -> str:
    """Fetch build logs from URL, return empty string on failure."""
    if not log_url:
        return ''
    try:
        headers = {}
        api_token = os.environ.get('API_KEY')
        if api_token and 'qe-private-deck' in log_url:
            headers['Authorization'] = f'Bearer {api_token}'
        response = requests.get(log_url, headers=headers, timeout=30)
        if response.status_code == 200:
            return response.text
    except Exception:
        pass
    return ''


def detect_ssh_flake(error_message: str, pass_rate: Optional[float] = None, log_url: str = None, log_text: str = None) -> Optional[Dict[str, Any]]:
    """
    Pre-classify SSH infrastructure flakes.

    Checks both the error_message and the build logs for SSH patterns.
    Returns a pre-built analysis dict if SSH flake detected, None otherwise.
    """
    if not error_message:
        return None

    combined_text = error_message
    logs = log_text if log_text is not None else ''
    ssh_matches = [p.pattern for p in SSH_PATTERNS if p.search(error_message)]

    if not ssh_matches:
        if not logs and log_url:
            logs = _fetch_logs(log_url)
        if logs:
            combined_text = error_message + '\n' + logs[-3000:]
            ssh_matches = [p.pattern for p in SSH_PATTERNS if p.search(combined_text)]

    if not ssh_matches:
        return None

    assertion_in_error = any(p.search(error_message) for p in ASSERTION_PATTERNS)
    ssh_in_error = any(p.search(error_message) for p in SSH_PATTERNS)

    if assertion_in_error and not ssh_in_error:
        return None

    if pass_rate is not None and pass_rate < 65.0:
        return None

    ssh_only_in_logs = not ssh_in_error and len(ssh_matches) > 0
    logger.info(f"Pre-classified as SSH infrastructure flake (pass_rate={pass_rate}, ssh_patterns={len(ssh_matches)}, ssh_in_logs_only={ssh_only_in_logs})")

    root_cause = ('SSH connectivity failure to Windows node via bastion host. '
                  'Test logic never reached an assertion -- the failure is purely infrastructure.')
    if ssh_only_in_logs:
        root_cause = ('SSH connectivity failures found in build logs caused downstream test assertion to fail. '
                      'The test assertion failed because SSH to the Windows node was unstable, not due to a product bug.')

    return {
        'root_cause': root_cause,
        'component': 'test-infrastructure (SSH connectivity)',
        'confidence': 92,
        'failure_type': 'transient',
        'classification': 'transient',
        'platform_specific': False,
        'affected_platforms': [],
        'evidence': '; '.join(ssh_matches[:3]),
        'suggested_action': 'Retry. Track under WINC-1931 for SSH elimination.',
        'issue_title': 'Transient: SSH connectivity flake to Windows node',
        'issue_description': 'SSH connection to Windows node failed before test logic executed. '
                             'This is a known transient infrastructure issue, not a product bug.',
        'is_product_bug': False,
        'pre_classified': True,
        'pre_classifier': 'ssh_flake_detector',
        'cost': 0.0,
        'analysis_mode': 'pre-classifier',
    }


def detect_infra_flake(error_message: str, log_url: str = None, log_text: str = None) -> Optional[Dict[str, Any]]:
    """
    Pre-classify DNS and cloud quota infrastructure failures.

    Returns a pre-built analysis dict if an infrastructure pattern is
    detected, None otherwise.

    Unlike detect_ssh_flake, this has no pass_rate gate. DNS failures are
    always infrastructure regardless of pass rate. Quota errors similarly
    indicate environment constraints, not product behavior.
    """
    if not error_message:
        return None

    combined_text = error_message
    logs = log_text if log_text is not None else ''
    if not logs and log_url:
        logs = _fetch_logs(log_url)
    if logs:
        combined_text = error_message + '\n' + logs[-3000:]

    # Check DNS failures
    dns_matches = [p.pattern for p in DNS_PATTERNS if p.search(combined_text)]
    if dns_matches:
        assertion_in_error = any(p.search(error_message) for p in ASSERTION_PATTERNS)
        dns_in_error = any(p.search(error_message) for p in DNS_PATTERNS)
        if not (assertion_in_error and not dns_in_error):
            logger.info("Pre-classified as DNS infrastructure flake "
                        f"(dns_patterns={len(dns_matches)})")
            return {
                'root_cause': 'DNS resolution failure in CI environment. '
                              'The test failed because cluster DNS was '
                              'temporarily unavailable, not due to a '
                              'product bug.',
                'component': 'test-infrastructure (DNS)',
                'confidence': 88,
                'failure_type': 'system_issue',
                'classification': 'system_issue',
                'platform_specific': False,
                'affected_platforms': [],
                'evidence': '; '.join(dns_matches[:3]),
                'suggested_action': 'Retry. If persistent, investigate '
                                    'cluster DNS pods and config.',
                'issue_title': 'System: DNS resolution failure in CI',
                'issue_description': 'DNS resolution failed during test '
                                     'execution. This is a CI '
                                     'infrastructure issue.',
                'is_product_bug': False,
                'pre_classified': True,
                'pre_classifier': 'dns_flake_detector',
                'cost': 0.0,
                'analysis_mode': 'pre-classifier',
            }

    # Check cloud quota failures
    quota_matches = [p.pattern for p in QUOTA_PATTERNS if p.search(combined_text)]
    if quota_matches:
        logger.info("Pre-classified as cloud quota/capacity issue "
                    f"(quota_patterns={len(quota_matches)})")
        return {
            'root_cause': 'Cloud provider quota or capacity limit '
                          'exceeded. The test environment could not be '
                          'provisioned due to resource constraints.',
            'component': 'test-infrastructure (cloud quota)',
            'confidence': 90,
            'failure_type': 'system_issue',
            'classification': 'system_issue',
            'platform_specific': True,
            'affected_platforms': [],
            'evidence': '; '.join(quota_matches[:3]),
            'suggested_action': 'Retry later or request quota increase '
                                'from cloud provider.',
            'issue_title': 'System: Cloud quota/capacity exceeded',
            'issue_description': 'Cloud provider quota or instance '
                                 'capacity was exceeded, preventing '
                                 'test environment provisioning.',
            'is_product_bug': False,
            'pre_classified': True,
            'pre_classifier': 'quota_detector',
            'cost': 0.0,
            'analysis_mode': 'pre-classifier',
        }

    return None


def detect_timeout_flake(
    error_message: str,
    pass_rate: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """
    Pre-classify known timeout/precondition flakes.

    Catches precondition timeouts (e.g. waiting for machine to reach
    Provisioning phase) and generic condition timeouts that are known
    transient failures when the pass rate is high.

    Returns a pre-built analysis dict if a timeout flake is detected,
    None otherwise.
    """
    if not error_message:
        return None

    # Only pre-classify as transient if pass rate is known and above 50%
    # (66.7% = 2/3 passing is still clearly intermittent, not persistent)
    if pass_rate is None or pass_rate < 50.0:
        return None

    timeout_matches = [
        p.pattern for p in TIMEOUT_FLAKE_PATTERNS if p.search(error_message)
    ]
    if not timeout_matches:
        return None

    # Do not pre-classify if there is a real assertion failure alongside
    # the timeout — the timeout may be incidental
    assertion_in_error = any(
        p.search(error_message) for p in ASSERTION_PATTERNS
    )
    if assertion_in_error:
        return None

    logger.info(
        "Pre-classified as timeout/precondition flake "
        f"(pass_rate={pass_rate}, patterns={len(timeout_matches)})"
    )

    return {
        'root_cause': (
            'Precondition or condition timeout in CI environment. '
            'The test waited for an expected state that did not '
            'arrive in time. With a high pass rate this is a '
            'transient timing issue, not a product bug.'
        ),
        'component': 'test-infrastructure (timeout)',
        'confidence': 85,
        'failure_type': 'transient',
        'classification': 'transient',
        'platform_specific': False,
        'affected_platforms': [],
        'evidence': '; '.join(timeout_matches[:3]),
        'suggested_action': (
            'Known transient flake -- not a product bug. '
            'Track under WINC-1931 (SSH/infrastructure elimination). '
            'No action needed unless pass rate drops below 50%.'
        ),
        'issue_title': 'Transient: Precondition/condition timeout flake',
        'issue_description': (
            'Test timed out waiting for a precondition or expected '
            'state. This is a known transient CI timing issue, '
            'not a product defect. Tracked under WINC-1931.'
        ),
        'is_product_bug': False,
        'pre_classified': True,
        'pre_classifier': 'timeout_flake_detector',
        'cost': 0.0,
        'analysis_mode': 'pre-classifier',
    }


def detect_known_flaky_test(
    test_name: str,
    test_description: str,
    pass_rate: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """
    Pre-classify by TEST NAME when the error text is garbage or missing.

    Some tests (cert rotation, CA rotation) produce corrupted output but
    are known transient flakes. Match against the test name/description
    instead of the error message.
    """
    if pass_rate is None or pass_rate < 50.0:
        return None

    combined = f"{test_name} {test_description}"
    matches = [
        p.pattern for p in KNOWN_FLAKY_TEST_PATTERNS if p.search(combined)
    ]
    if not matches:
        return None

    logger.info(
        "Pre-classified by test name as known flaky "
        f"(pass_rate={pass_rate}, patterns={len(matches)})"
    )

    return {
        'root_cause': (
            'Known transient flake identified by test name. '
            'This test category (certificate/CA rotation) has '
            'intermittent failures due to timing-sensitive operations '
            'in CI environments. Not a product bug.'
        ),
        'component': 'test-infrastructure (known-flaky)',
        'confidence': 85,
        'failure_type': 'transient',
        'classification': 'transient',
        'platform_specific': False,
        'affected_platforms': [],
        'evidence': '; '.join(matches[:3]),
        'suggested_action': (
            'Known transient flake -- not a product bug. '
            'Track under WINC-1931 (SSH/infrastructure elimination). '
            'No action needed unless pass rate drops below 50%.'
        ),
        'issue_title': 'Transient: Known flaky test pattern',
        'issue_description': (
            'Test matches a known transient failure category '
            '(cert/CA rotation). Tracked under WINC-1931.'
        ),
        'is_product_bug': False,
        'pre_classified': True,
        'pre_classifier': 'known_flaky_test_detector',
        'cost': 0.0,
        'analysis_mode': 'pre-classifier',
    }


_TEST_ID_RE = re.compile(r'(OCP-\d+)')

# Default test repository to search for fix commits
_DEFAULT_TEST_REPO = 'openshift/openshift-tests-private'


def _extract_test_id(test_name: str) -> Optional[str]:
    """Extract OCP test case ID from a test name.

    Returns the first ``OCP-\\d+`` token found in *test_name*, or
    ``None`` if the name does not contain one.
    """
    if not test_name:
        return None
    m = _TEST_ID_RE.search(test_name)
    return m.group(1) if m else None


def _is_fix_commit(message: str, test_id: str) -> bool:
    """Return True if *message* is a fix commit for *test_id*.

    A commit counts as a fix when:
    1. It references the test ID (e.g. ``OCP-42204``), **and**
    2. It contains the word "fix" (case-insensitive) anywhere in the
       message.

    Commits like ``Automate OCP-42204 ...`` that reference the ID but
    do not contain "fix" are not treated as fixes.
    """
    if not message or not test_id:
        return False
    if test_id not in message:
        return False
    return bool(re.search(r'\bfix\b', message, re.IGNORECASE))


def _search_test_repo_commits(
    test_id: str,
    github_repo: str,
    github_token: str,
) -> List[str]:
    """Search *github_repo* for commits referencing *test_id*.

    Uses the GitHub Search API and returns a list of commit messages
    that match.  Returns an empty list on any error (network, auth,
    rate-limit).
    """
    url = 'https://api.github.com/search/commits'
    params = {'q': f'repo:{github_repo} {test_id}', 'per_page': '30'}
    headers = {
        'Accept': 'application/vnd.github+json',
        'Authorization': f'Bearer {github_token}',
    }
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        if resp.status_code != 200:
            logger.warning(
                'GitHub commit search returned %d for %s in %s',
                resp.status_code, test_id, github_repo,
            )
            return []
        data = resp.json()
        return [
            item['commit']['message']
            for item in data.get('items', [])
            if item.get('commit', {}).get('message')
        ]
    except Exception as exc:
        logger.warning(
            'GitHub commit search failed for %s: %s', test_id, exc,
        )
        return []


def detect_test_repo_fix(
    test_name: str,
    github_repo: Optional[str] = None,
    github_token: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Pre-classify failures whose test has fix commits in the test repo.

    Searches the configured testing repository's git history (via the
    GitHub Search API) for commits that reference the failing test's
    OCP ID with a fix-related keyword.  If such commits exist, the
    failure is classified as an **automation bug** — the test itself
    has known issues that have been fixed (or are being fixed) in the
    test repo.

    Returns a pre-built analysis dict if fix commits are found, or
    ``None`` if not (allowing the pipeline to fall through to AI
    analysis).

    Environment variables
    ---------------------
    TEST_REPO_GITHUB_TOKEN : str
        GitHub personal-access token with read access to the test
        repository.  Required; if unset the detector is skipped.
    TEST_REPO_GITHUB_REPO : str
        ``owner/name`` of the test repository (default:
        ``openshift/openshift-tests-private``).
    """
    test_id = _extract_test_id(test_name)
    if not test_id:
        return None

    token = github_token or os.environ.get('TEST_REPO_GITHUB_TOKEN', '')
    if not token:
        return None

    repo = (
        github_repo
        or os.environ.get('TEST_REPO_GITHUB_REPO', '')
        or _DEFAULT_TEST_REPO
    )

    commit_messages = _search_test_repo_commits(test_id, repo, token)
    if not commit_messages:
        return None

    fix_messages = [m for m in commit_messages if _is_fix_commit(m, test_id)]
    if not fix_messages:
        return None

    # Build concise evidence from the first few fix commit subjects
    evidence_lines = []
    for msg in fix_messages[:3]:
        subject = msg.split('\n', 1)[0][:120]
        evidence_lines.append(subject)
    evidence = '; '.join(evidence_lines)

    logger.info(
        'Pre-classified %s as automation bug — %d fix commit(s) '
        'found in %s',
        test_id, len(fix_messages), repo,
    )

    return {
        'root_cause': (
            f'Test {test_id} has {len(fix_messages)} fix commit(s) '
            f'in the testing repository ({repo}). The failure is '
            f'likely an automation bug — the test code has known '
            f'issues that have been addressed by test-repo fixes.'
        ),
        'component': 'test-automation',
        'confidence': 80,
        'failure_type': 'automation_bug',
        'classification': 'automation_bug',
        'platform_specific': False,
        'affected_platforms': [],
        'evidence': evidence,
        'suggested_action': (
            f'Check whether the relevant fix has been backported to '
            f'the release branch under test. '
            f'{len(fix_messages)} fix commit(s) found in {repo}.'
        ),
        'issue_title': f'Automation Bug: {test_id} has fix commits '
                       f'in test repo',
        'issue_description': (
            f'Test {test_id} was flagged as an automation bug because '
            f'{len(fix_messages)} fix commit(s) were found in {repo}. '
            f'Verify the fix has been cherry-picked to the target '
            f'release branch.'
        ),
        'is_product_bug': False,
        'pre_classified': True,
        'pre_classifier': 'test_repo_fix_detector',
        'cost': 0.0,
        'analysis_mode': 'pre-classifier',
    }


def _apply_confidence_review(analysis: Dict[str, Any]) -> Dict[str, Any]:
    """
    Flag low-confidence results for human review.

    When the model's confidence is below the threshold, mark the result
    so the dashboard can surface it for manual triage.
    """
    confidence = analysis.get('confidence', 0)
    if confidence < LOW_CONFIDENCE_THRESHOLD:
        analysis['needs_human_review'] = True
        existing_reason = analysis.get('review_reason')
        low_conf_reason = (
            f'Low confidence ({confidence}%). '
            'Automated classification may be inaccurate.'
        )
        if existing_reason:
            analysis['review_reason'] = (
                f'{existing_reason} {low_conf_reason}'
            )
        else:
            analysis['review_reason'] = low_conf_reason
        # Downgrade is_product_bug when confidence is low to avoid
        # false-positive bug filings
        if analysis.get('is_product_bug', False):
            analysis['is_product_bug'] = False
            analysis['review_reason'] += (
                ' Product bug flag cleared pending human review.'
            )
    else:
        analysis['needs_human_review'] = False

    return analysis


def _derive_is_product_bug(analysis: Dict[str, Any]) -> Dict[str, Any]:
    """
    Set is_product_bug consistently from classification and confidence.
    """
    classification = analysis.get('classification',
                                  analysis.get('failure_type', ''))
    confidence = analysis.get('confidence', 0)

    # Only mark as product bug when classification says so AND
    # confidence is above the review threshold
    analysis['is_product_bug'] = (
        classification == 'product_bug'
        and confidence >= LOW_CONFIDENCE_THRESHOLD
    )
    return analysis


class HybridFailureAnalyzer:
    """
    Real AI failure analyzer using Google Vertex AI.

    Uses Claude via Vertex AI API (~$0.02 per analysis).
    NO pattern matching - real AI only.
    """

    def __init__(self):
        self.vertex_project_id = os.getenv('ANTHROPIC_VERTEX_PROJECT_ID')
        self.vertex_region = os.getenv('ANTHROPIC_VERTEX_REGION')

        # Initialize Vertex AI client
        if self.vertex_project_id and self.vertex_region:
            try:
                import anthropic
                self.claude_client = anthropic.AnthropicVertex(
                    project_id=self.vertex_project_id,
                    region=self.vertex_region
                )
                logger.info(f"Vertex AI client initialized (project: {self.vertex_project_id}, region: {self.vertex_region})")
            except ImportError:
                self.claude_client = None
                logger.warning("anthropic[vertex] package not installed - run: pip install 'anthropic[vertex]'")
            except Exception as e:
                self.claude_client = None
                logger.warning(f"Failed to initialize Vertex AI client: {e}")
        else:
            self.claude_client = None
            logger.warning("Vertex AI credentials not set. Set ANTHROPIC_VERTEX_PROJECT_ID and ANTHROPIC_VERTEX_REGION environment variables.")

    def analyze_failure(
        self,
        test_name: str,
        error_message: str,
        log_url: str,
        platform: str,
        version: str,
        pass_rate: Optional[float] = None,
        test_description: str = ''
    ) -> Dict[str, Any]:
        """
        Analyze failure using pre-classifier + Vertex AI (Claude via Google Cloud).

        Step 1: Check for known infrastructure patterns (SSH flakes) -- free, instant
        Step 2: If not pre-classified, use Vertex AI (~$0.02 per analysis)

        Args:
            test_name: Test identifier (e.g., OCP-39030)
            error_message: Error message from test failure
            log_url: URL to build logs
            platform: Platform (aws, azure, gcp, etc.)
            version: OpenShift version
            pass_rate: Test pass rate (used by pre-classifier to confirm transient)

        Returns:
            Analysis dictionary with root_cause, component, confidence, etc.
        """

        # Step 1: Pre-classify known infrastructure patterns
        # Fetch logs once for all pre-classifiers to avoid duplicate requests
        logs = _fetch_logs(log_url) if log_url else ''

        pre_result = detect_ssh_flake(error_message, pass_rate, log_text=logs)
        if pre_result:
            logger.info(f"Pre-classified {test_name} as SSH flake (skipping Vertex AI, saved ~$0.024)")
            return pre_result

        # Step 1b: Check DNS and quota infrastructure patterns
        infra_result = detect_infra_flake(error_message, log_text=logs)
        if infra_result:
            logger.info(f"Pre-classified {test_name} as infrastructure issue (skipping Vertex AI)")
            return infra_result

        # Step 1c: Check timeout/precondition flake patterns
        timeout_result = detect_timeout_flake(error_message, pass_rate)
        if timeout_result:
            logger.info(f"Pre-classified {test_name} as timeout flake (skipping Vertex AI)")
            return timeout_result

        # Step 1d: Check test name for known flaky test categories
        # (catches cert rotation tests even when error text is corrupted)
        flaky_result = detect_known_flaky_test(test_name, test_description, pass_rate)
        if flaky_result:
            logger.info(f"Pre-classified {test_name} by test name as known flaky (skipping Vertex AI)")
            return flaky_result

        # Step 1e: Check test repo git history for fix commits
        # (catches tests that have been fixed in the testing repo)
        fix_result = detect_test_repo_fix(test_name)
        if fix_result:
            logger.info(f"Pre-classified {test_name} as automation bug via test repo fix commits (skipping Vertex AI)")
            return fix_result

        # Step 2: Use Vertex AI for analysis
        logger.info(f"Analyzing {test_name} with Vertex AI")
        api_result = self._try_api_analysis(
            test_name, error_message, log_url, platform, version,
            pass_rate=pass_rate
        )

        if api_result:
            logger.info(f"Used Vertex AI (cost: ~$0.024) for {test_name}")
            api_result['cost'] = 0.024  # Approximate cost with Sonnet
            api_result['analysis_mode'] = 'vertex-ai'
            # Post-process: derive is_product_bug and flag low confidence
            api_result = _derive_is_product_bug(api_result)
            api_result = _apply_confidence_review(api_result)
            return api_result

        # Vertex AI failed - return error
        logger.error(f"Vertex AI analysis failed for {test_name}")
        return {
            'error': 'Vertex AI analysis failed',
            'root_cause': 'Vertex AI analysis failed - check credentials and quota',
            'component': 'vertex-ai',
            'confidence': 0,
            'analysis_mode': 'failed',
            'cost': 0.0,
            'needs_human_review': True,
            'review_reason': 'AI analysis failed. Manual investigation required.',
        }

    def _try_api_analysis(
        self,
        test_name: str,
        error_message: str,
        log_url: str,
        platform: str,
        version: str,
        pass_rate: Optional[float] = None
    ) -> Optional[Dict[str, Any]]:
        """Analyze failure using Vertex AI with structured prompt."""
        try:
            if not self.claude_client:
                logger.error("No Claude API client available")
                return None

            # Fetch logs (truncated for cost optimization)
            logs = _fetch_logs(log_url)

            logs_excerpt = logs[-3000:] if len(logs) > 3000 else logs

            # Build historical context string
            history_context = ""
            if pass_rate is not None:
                history_context = (
                    f"\n**Historical pass rate:** {pass_rate:.1f}%"
                )
                if pass_rate >= 90.0:
                    history_context += (
                        " (usually passes — this failure is likely "
                        "transient or a recent regression)"
                    )
                elif pass_rate < 50.0:
                    history_context += (
                        " (frequently failing — likely a persistent "
                        "bug or systemic issue)"
                    )

            # Build prompt with chain-of-thought reasoning
            prompt = self._build_analysis_prompt(
                test_name, error_message, logs_excerpt,
                platform, version, history_context
            )

            # Call Claude API
            response = self.claude_client.messages.create(
                model="claude-sonnet-4",  # Cheaper, still capable
                max_tokens=2048,
                messages=[{
                    "role": "user",
                    "content": prompt
                }]
            )

            # Parse response
            response_text = response.content[0].text
            analysis = self._parse_analysis_response(
                response_text, platform
            )
            return analysis

        except Exception as e:
            logger.error(f"API analysis error: {e}")
            return None

    def _build_analysis_prompt(
        self,
        test_name: str,
        error_message: str,
        logs_excerpt: str,
        platform: str,
        version: str,
        history_context: str
    ) -> str:
        """Build the structured analysis prompt for Vertex AI."""
        return f"""Analyze this Windows Containers OpenShift CI test failure step by step.

## Test Context
- **Test:** {test_name}
- **Platform:** {platform}
- **Version:** {version}{history_context}

## Error Message
```
{error_message}
```

## Build Logs (last 3000 chars)
```
{logs_excerpt}
```

## Instructions

Think through the analysis in two steps:

**Step 1 — Identify the failure mechanism:**
- What specific error or exception caused the test to fail?
- Did the test reach a real assertion (Expect, Failf, gomega matcher),
  or did it fail before test logic executed?
- Are there infrastructure signals (SSH errors, DNS failures, timeouts,
  quota limits) in the logs?

**Step 2 — Classify based on evidence:**

Choose exactly one failure_type from these categories. Read each
description carefully before deciding:

- **product_bug**: A defect in OpenShift or Windows Container product
  code (WMCO, kubelet, hybrid-overlay, CSI driver, etc.). The test
  assertion failed because the product behaved incorrectly. Look for
  assertion failures comparing actual vs expected product behavior.

- **automation_bug**: A defect in the test code itself. The test has
  wrong assertions, incorrect setup/teardown, hardcoded values that
  don't match the environment, or race conditions in test logic. The
  product may be working correctly but the test is broken.

- **system_issue**: An infrastructure or environment problem — cloud
  provider outages, DNS failures, storage unavailability, node
  provisioning failures, certificate expiry, or cluster installation
  failures. The product and tests may both be correct, but the
  environment is broken.

- **transient**: An intermittent/flaky failure caused by timing,
  resource contention, or temporary connectivity issues. Key signals:
  the test usually passes (high pass rate), SSH connection failures
  with exit status 255, brief network blips, or pod scheduling
  delays. If SSH connectivity failed and the test never reached a
  real assertion, this is transient — not a product bug.

- **to_investigate**: There is genuinely not enough information in the
  error message and logs to classify. Use this only as a last resort.
  Set confidence below 50 when using this category.

## WMCO Domain Knowledge

Use this domain context to distinguish preconditions from product assertions:

- **Provisioning phase checks** (e.g. "should be in Provisioning phase",
  "waiting for machine") are **preconditions**, not product assertions.
  Timeouts here are flakes caused by cloud provider provisioning speed.
- WMCO reconciliation only starts **after** a machine reaches Running
  phase. A timeout waiting for Provisioning does not indicate a WMCO bug.
- Secret existence checks (cloud-private-key, windows-user-data) are
  **setup validations**. Failures indicate missing test prerequisites.
- **SSH connectivity issues** are tracked under WINC-1931 (SSH
  elimination). Always reference this tracker. SSH failures are
  infrastructure issues, not product bugs.
- **hybrid-overlay-node certificate rotation** is a known flaky area.
  Service stop/restart during cert rotation is often a timing issue.
- **CSI driver daemonset readiness** (e.g. "csi-driver-node-windows
  daemonset is not ready after waiting") is a persistent issue across
  multiple platforms (Azure, vSphere). Low pass rates across platforms
  indicate a systemic test or product issue, not a platform-specific
  bug. Classify as **automation_bug** if the test setup/install logic
  is flawed, or **product_bug** if the CSI driver itself fails.
- **When pass rate is low (<50%), the failure is persistent, not
  transient.** Do NOT suggest "retry" or "monitor." Instead identify
  the specific component bug and recommend filing or updating a Jira
  issue for the responsible team (WMCO team for Windows CSI driver
  issues, storage team for CSI driver itself).
- **Suggested actions must be specific and actionable.** Never suggest
  generic phrases like "investigate the logs", "investigate why X
  happens", "check pod logs and node conditions", or just "retry."
  These are useless to the team. Instead:
  - Reference the specific Jira tracker if one exists (e.g. WINC-1931)
  - Identify whether the failure is a precondition vs product assertion
  - State whether this is a known flake pattern or a new regression
  - For persistent failures: name the specific component owner and
    suggest filing/updating a specific Jira issue

## Key Distinctions

- SSH/bastion failures (exit status 255, connection refused on port 22)
  with no test assertion reached → **transient**, component
  "test-infrastructure (SSH connectivity)". Reference WINC-1931.
- DNS resolution failures → **system_issue**
- Cloud quota/capacity exceeded → **system_issue**
- Test assertion comparing wrong expected value → **automation_bug**
- Pod CrashLoopBackOff with product container logs showing a panic →
  **product_bug**
- Timeout waiting for a condition that intermittently takes too long →
  **transient** (if pass rate is high) or **product_bug** (if pass rate
  is low and timeout is generous)
- Precondition timeout (waiting for Provisioning phase, waiting for
  machine/node readiness before test logic) → **transient**
- CSI driver daemonset not ready on Windows nodes across multiple
  platforms with low pass rate → **automation_bug** or **product_bug**,
  persistent systemic issue needing a Jira ticket
- Any failure with pass rate <50%: this is persistent, NOT transient.
  Classify as product_bug or automation_bug, never transient.

## Required Output

Return ONLY a JSON object (no markdown fencing, no extra text):
{{
  "root_cause": "1-2 sentence description of what caused the failure",
  "component": "affected component (e.g., windows-machine-config-operator, kubelet, test-infrastructure)",
  "confidence": <0-100 integer>,
  "failure_type": "<one of: product_bug, automation_bug, system_issue, transient, to_investigate>",
  "platform_specific": <true or false>,
  "affected_platforms": ["<platform names if platform_specific>"],
  "evidence": "Key log lines or error patterns that support your classification",
  "suggested_action": "Specific, actionable next step. NEVER use generic phrases like 'investigate why X happens', 'check logs', 'investigate the issue'. Instead: name the bug owner, the Jira ticket, or the specific code path.",
  "issue_title": "<Type>: <brief description>",
  "issue_description": "Detailed description for a tracking issue"
}}

Set confidence to reflect how certain you are about the classification:
- 90-100: Clear-cut, strong evidence for exactly one category
- 70-89: Likely correct, but some ambiguity
- 50-69: Uncertain, multiple categories could apply
- Below 50: Guessing, insufficient evidence
"""

    @staticmethod
    def _parse_analysis_response(
        response_text: str, platform: str
    ) -> Optional[Dict[str, Any]]:
        """Parse the AI response into a structured analysis dict."""
        # Try extracting JSON from markdown code fence
        json_match = re.search(
            r'```(?:json)?\s*(\{.*?\})\s*```',
            response_text, re.DOTALL
        )
        if json_match:
            try:
                analysis = json.loads(json_match.group(1))
                if 'failure_type' in analysis:
                    analysis['classification'] = analysis['failure_type']
                return analysis
            except json.JSONDecodeError:
                pass

        # Try parsing entire response as JSON
        try:
            analysis = json.loads(response_text.strip())
            if 'failure_type' in analysis:
                analysis['classification'] = analysis['failure_type']
            return analysis
        except json.JSONDecodeError:
            pass

        # Fallback: return raw text with low confidence
        logger.warning(
            "Could not parse API response as JSON, returning raw text"
        )
        return {
            'root_cause': response_text[:200],
            'raw_analysis': response_text,
            'component': 'unknown',
            'confidence': 30,
            'classification': 'to_investigate',
            'failure_type': 'to_investigate',
            'platform_specific': False,
            'affected_platforms': [platform],
            'evidence': 'See raw_analysis',
            'suggested_action': 'Manual investigation needed',
            'needs_human_review': True,
            'review_reason': 'AI response could not be parsed as JSON.',
        }

