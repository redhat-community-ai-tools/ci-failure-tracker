"""
Team Configuration Loader

Loads and validates team-specific configurations from YAML files.
"""

import os
import yaml
from pathlib import Path
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field


@dataclass
class TeamConfig:
    """Team configuration model"""
    # Team info
    team_name: str
    team_id: str
    description: str = ""
    contact: str = ""
    slack_channel: str = ""

    # ReportPortal
    reportportal_url: str = "https://reportportal-openshift.apps.dno.ocp-hub.prod.psi.redhat.com"
    reportportal_project: str = "prow"
    reportportal_filter_id: Optional[int] = None
    job_patterns: List[str] = field(default_factory=list)

    # Jira
    jira_url: str = "https://issues.redhat.com"
    jira_project: str = ""
    jira_parent_epic: str = ""
    jira_issue_type: str = "Sub-task"
    jira_component: str = ""
    jira_labels: List[str] = field(default_factory=list)
    jira_priority: str = "Normal"

    # Tracking
    versions: List[str] = field(default_factory=list)
    platforms: List[str] = field(default_factory=list)
    failure_threshold: int = 3
    failure_rate_threshold: int = 0
    lookback_days: int = 7
    skip_tests: List[str] = field(default_factory=list)
    skip_error_patterns: List[str] = field(default_factory=list)

    # Notifications
    notification_enabled: bool = False
    slack_enabled: bool = False
    slack_webhook: str = ""
    slack_notification_channel: str = ""
    email_enabled: bool = False
    email_recipients: List[str] = field(default_factory=list)

    # Template
    template_name: str = "default"
    template_variables: Dict[str, Any] = field(default_factory=dict)

    # Execution
    parallel: bool = True
    max_workers: int = 5
    retry_attempts: int = 3
    retry_delay: int = 2
    verbose: bool = True
    json_output: bool = True
    json_output_path: str = "./reports/{team_id}-failures-{date}.json"

    # Advanced
    cache_ttl: int = 900
    max_tickets_per_run: int = 50
    dry_run: bool = False

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'TeamConfig':
        """Create TeamConfig from dictionary (loaded YAML)"""
        team = data.get('team', {})
        rp = data.get('reportportal', {})
        jira = data.get('jira', {})
        tracking = data.get('tracking', {})
        notification = data.get('notification', {})
        template = data.get('template', {})
        execution = data.get('execution', {})
        advanced = data.get('advanced', {})

        return cls(
            # Team
            team_name=team.get('name', ''),
            team_id=team.get('id', ''),
            description=team.get('description', ''),
            contact=team.get('contact', ''),
            slack_channel=team.get('slack_channel', ''),

            # ReportPortal
            reportportal_url=rp.get('url', 'https://reportportal-openshift.apps.dno.ocp-hub.prod.psi.redhat.com'),
            reportportal_project=rp.get('project', 'prow'),
            reportportal_filter_id=rp.get('filter_id'),
            job_patterns=rp.get('job_patterns', []),

            # Jira
            jira_url=jira.get('url', 'https://issues.redhat.com'),
            jira_project=jira.get('project', ''),
            jira_parent_epic=jira.get('parent_epic', ''),
            jira_issue_type=jira.get('issue_type', 'Sub-task'),
            jira_component=jira.get('component', ''),
            jira_labels=jira.get('labels', []),
            jira_priority=jira.get('priority', 'Normal'),

            # Tracking
            versions=tracking.get('versions', []),
            platforms=tracking.get('platforms', []),
            failure_threshold=tracking.get('failure_threshold', 3),
            failure_rate_threshold=tracking.get('failure_rate_threshold', 0),
            lookback_days=tracking.get('lookback_days', 7),
            skip_tests=tracking.get('skip_tests', []),
            skip_error_patterns=tracking.get('skip_error_patterns', []),

            # Notifications
            notification_enabled=notification.get('enabled', False),
            slack_enabled=notification.get('slack', {}).get('enabled', False),
            slack_webhook=notification.get('slack', {}).get('webhook_url', ''),
            slack_notification_channel=notification.get('slack', {}).get('channel', ''),
            email_enabled=notification.get('email', {}).get('enabled', False),
            email_recipients=notification.get('email', {}).get('recipients', []),

            # Template
            template_name=template.get('name', 'default'),
            template_variables=template.get('variables', {}),

            # Execution
            parallel=execution.get('parallel', True),
            max_workers=execution.get('max_workers', 5),
            retry_attempts=execution.get('retry_attempts', 3),
            retry_delay=execution.get('retry_delay', 2),
            verbose=execution.get('verbose', True),
            json_output=execution.get('json_output', True),
            json_output_path=execution.get('json_output_path', './reports/{team_id}-failures-{date}.json'),

            # Advanced
            cache_ttl=advanced.get('cache_ttl', 900),
            max_tickets_per_run=advanced.get('max_tickets_per_run', 50),
            dry_run=advanced.get('dry_run', False),
        )

    def validate(self) -> List[str]:
        """Validate configuration and return list of errors"""
        errors = []

        # Required fields
        if not self.team_name:
            errors.append("team.name is required")
        if not self.team_id:
            errors.append("team.id is required")
        if not self.reportportal_project:
            errors.append("reportportal.project is required")
        if not self.job_patterns:
            errors.append("reportportal.job_patterns is required (must have at least one pattern)")
        if not self.jira_project:
            errors.append("jira.project is required")
        if not self.versions:
            errors.append("tracking.versions is required (must have at least one version)")
        if not self.platforms:
            errors.append("tracking.platforms is required (must have at least one platform)")

        # Validate values
        if self.failure_threshold < 1:
            errors.append("tracking.failure_threshold must be >= 1")
        if self.lookback_days < 1:
            errors.append("tracking.lookback_days must be >= 1")
        if self.max_workers < 1:
            errors.append("execution.max_workers must be >= 1")

        # Validate version format (should be X.Y)
        for version in self.versions:
            if not version.replace('.', '').replace('-', '').isalnum():
                errors.append(f"Invalid version format: {version}")

        return errors


class ConfigLoader:
    """Loads and manages team configurations"""

    def __init__(self, teams_dir: str = "teams"):
        self.teams_dir = Path(teams_dir)
        if not self.teams_dir.exists():
            raise FileNotFoundError(f"Teams directory not found: {teams_dir}")

    def list_teams(self) -> List[str]:
        """List all configured team IDs"""
        teams = []
        for file in self.teams_dir.glob("*.yaml"):
            # Skip template and examples
            if file.stem in ['template'] or file.name.endswith('.example'):
                continue
            teams.append(file.stem)
        return sorted(teams)

    def load_team(self, team_id: str) -> TeamConfig:
        """Load configuration for a specific team"""
        config_file = self.teams_dir / f"{team_id}.yaml"

        if not config_file.exists():
            raise FileNotFoundError(f"Config file not found: {config_file}")

        with open(config_file, 'r') as f:
            data = yaml.safe_load(f)

        config = TeamConfig.from_dict(data)

        # Validate
        errors = config.validate()
        if errors:
            error_msg = f"Invalid configuration for team '{team_id}':\n"
            error_msg += "\n".join(f"  - {err}" for err in errors)
            raise ValueError(error_msg)

        return config

    def load_all_teams(self) -> Dict[str, TeamConfig]:
        """Load all team configurations"""
        teams = {}
        for team_id in self.list_teams():
            try:
                teams[team_id] = self.load_team(team_id)
            except Exception as e:
                print(f"Warning: Failed to load team '{team_id}': {e}")
                continue
        return teams

    def validate_config_file(self, config_file: str) -> tuple[bool, List[str]]:
        """Validate a configuration file without loading it fully"""
        try:
            with open(config_file, 'r') as f:
                data = yaml.safe_load(f)

            config = TeamConfig.from_dict(data)
            errors = config.validate()

            return (len(errors) == 0, errors)

        except yaml.YAMLError as e:
            return (False, [f"YAML syntax error: {e}"])
        except Exception as e:
            return (False, [f"Error loading config: {e}"])


def generate_team_template(team_id: str, output_path: Optional[str] = None) -> str:
    """Generate a team configuration template"""
    template = f"""# {team_id.upper()} Team Configuration
# Generated template - fill in your team's details

team:
  name: "{team_id.upper()} Team"
  id: "{team_id}"
  description: "What does your team test?"
  contact: "{team_id}-team@redhat.com"
  slack_channel: "#{team_id}"

reportportal:
  project: "prow"
  job_patterns:
    - "periodic-ci-openshift-YOUR-COMPONENT-release-{{version}}-*"
    # Add more patterns as needed

jira:
  project: "{team_id.upper()}"
  parent_epic: ""  # Optional: parent epic key
  issue_type: "Sub-task"
  component: "Test Infrastructure"
  labels:
    - "ci-failure"
    - "automated"
  priority: "Normal"

tracking:
  versions:
    - "4.21"
    - "4.22"
  platforms:
    - "aws"
    - "gcp"
  failure_threshold: 3
  lookback_days: 7
  skip_tests: []
  skip_error_patterns: []

notification:
  enabled: false

template:
  name: "default"
  variables:
    team_name: "{team_id.upper()} Team"

execution:
  verbose: true
  json_output: true

advanced:
  dry_run: false
"""

    if output_path:
        with open(output_path, 'w') as f:
            f.write(template)

    return template
