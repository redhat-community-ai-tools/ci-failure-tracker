# Team Configurations

This directory contains team-specific configuration files for the ReportPortal-Jira Bridge.

## Quick Start: Add Your Team

### Option 1: Use the Init Command (Easiest)

```bash
# Generate a config template for your team
../ci_failure_tracker.py --init-team YOUR_TEAM_ID

# This creates: teams/YOUR_TEAM_ID.yaml
# Edit the file with your team's details
```

### Option 2: Copy Template Manually

```bash
# Copy the template
cp template.yaml YOUR_TEAM_ID.yaml

# Edit with your team's details
vim YOUR_TEAM_ID.yaml
```

## What You Need

To configure your team, you'll need:

1. **Team Information**
   - Team name and ID
   - Contact email
   - Slack channel (optional)

2. **ReportPortal Details**
   - Project name (usually "prow")
   - Job name patterns your team monitors
   - OpenShift versions you test

3. **Jira Details**
   - Your Jira project key (e.g., "STOR", "SDN")
   - Parent epic (optional)
   - Labels and components you use

4. **Tracking Preferences**
   - How many failures before creating ticket?
   - How far back to look?
   - Any tests to skip?

## Configuration File Structure

```yaml
team:
  name: "Your Team Name"
  id: "your_team_id"
  # ... team info

reportportal:
  project: "prow"
  job_patterns:
    - "periodic-ci-*-{version}-*-your-tests-*"
  # ... ReportPortal settings

jira:
  project: "YOUR_PROJECT"
  parent_epic: "YOUR-123"
  # ... Jira settings

tracking:
  versions: ["4.21", "4.22"]
  platforms: ["aws", "gcp"]
  failure_threshold: 3
  # ... tracking settings
```

## Examples

- **`winc.yaml`** - Windows Containers QE (pilot team) ✅
- **`storage.yaml.example`** - Storage QE example
- **`template.yaml`** - Blank template with all options

## Testing Your Config

Before submitting, test your configuration:

```bash
# Dry run - shows what would happen
../ci_failure_tracker.py --team YOUR_TEAM_ID --dry-run --verbose

# Check config is valid
../ci_failure_tracker.py --validate-config teams/YOUR_TEAM_ID.yaml
```

## Submit Your Config

Once your config works:

1. **Test it thoroughly** with `--dry-run`
2. **Submit a Pull Request** adding your `teams/YOUR_TEAM_ID.yaml`
3. **Tag @ci-failure-tracker-maintainers** for review
4. **Once merged**, you're live!

## File Naming Convention

- **Active configs**: `TEAM_ID.yaml` (e.g., `winc.yaml`, `storage.yaml`)
- **Examples**: `TEAM_ID.yaml.example` (not loaded by tool)
- **Template**: `template.yaml` (used by --init-team)

## Need Help?

- **Documentation**: See `../docs/TEAM_ONBOARDING.md` for detailed guide
- **Examples**: Look at `winc.yaml` (working example)
- **Slack**: Ask in #qe-ci-tooling
- **Email**: ci-failure-tracker-support@redhat.com

## Configuration Schema Reference

### Required Fields

- `team.name` - Team display name
- `team.id` - Short team identifier
- `reportportal.project` - ReportPortal project name
- `reportportal.job_patterns` - List of job patterns to monitor
- `jira.project` - Jira project key
- `tracking.versions` - List of OpenShift versions
- `tracking.platforms` - List of cloud platforms

### Optional Fields

- `jira.parent_epic` - Parent epic key (recommended)
- `jira.component` - Jira component name
- `jira.labels` - List of labels to add
- `tracking.failure_threshold` - Min failures (default: 3)
- `tracking.lookback_days` - Days to look back (default: 7)
- `tracking.skip_tests` - Tests to ignore
- `template.name` - Custom template name
- `notification.*` - Notification settings

### Platform Values

Valid platform values for `tracking.platforms`:

- `aws` - Amazon Web Services
- `gcp` - Google Cloud Platform
- `azure` - Microsoft Azure
- `vsphere` - VMware vSphere
- `nutanix` - Nutanix AHV
- `openstack` - OpenStack
- `metal` - Bare metal
- `none` - No cloud provider (UPI/BYOH)

### Job Pattern Variables

In `reportportal.job_patterns`, you can use:

- `{version}` - Replaced with each version from `tracking.versions`
  - Example: `release-{version}` → `release-4.21`, `release-4.22`

### Best Practices

1. **Start Conservative**: Higher thresholds initially
2. **Iterate**: Adjust based on ticket volume
3. **Document**: Add comments explaining unusual settings
4. **Test Thoroughly**: Use --dry-run before going live
5. **Monitor**: Check first week, adjust as needed

## Validation Rules

Your config will be validated for:

- ✅ Required fields present
- ✅ Valid YAML syntax
- ✅ Jira project exists (if validation enabled)
- ✅ Job patterns are valid
- ✅ Versions follow X.Y format
- ✅ Thresholds are positive numbers
- ✅ No duplicate team IDs

## FAQ

**Q: Can I test multiple teams at once?**
```bash
../ci_failure_tracker.py --team team1 --team team2 --dry-run
```

**Q: How do I know what job patterns to use?**
Check ReportPortal for your team's job names, then create a pattern.

**Q: What if my test names don't follow OCP-XXXXX format?**
The tool handles various test name formats - it's flexible.

**Q: Can I customize the ticket format?**
Yes! Create `../templates/YOUR_TEAM_ID.jinja2` and set `template.name: YOUR_TEAM_ID`

**Q: How often should this run?**
Most teams run daily or weekly. Up to you!

**Q: What if I want to stop using this?**
Remove or rename your config file (add .disabled extension).

## Active Teams

Current teams using this tool:

- ✅ **WINC** (Windows Containers) - Pilot team
- 🚧 **Storage** - Coming soon
- 🚧 **Networking** - Coming soon
- 💭 **Your Team?** - Add yourself!

---

**Ready to add your team?** Use `--init-team` or copy `template.yaml`!
