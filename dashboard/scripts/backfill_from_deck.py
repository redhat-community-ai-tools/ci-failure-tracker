#!/usr/bin/env python3
"""Backfill job runs and test results from the Prow deck (spyglass).

The gcsweb instance for the private deck can be unavailable (for example
when its GCS credential is rejected) while the deck's own GCS access keeps
working. In that state the dashboard collects nothing because it scrapes
gcsweb for directory listings.

This script uses the deck instead:

1. list ProwJobs from ``prowjobs.js`` (job name, build id, state, times);
2. fetch each job's run page and read the ``lensArtifacts`` map that deck
   produced from GCS;
3. fetch the rendered JUnit lens for that run and parse the per-test rows;
4. insert ``JobRun`` and ``TestResult`` rows into the dashboard database.

It only sees jobs that are still present in ``prowjobs.js``; it is a
backfill/rescue path, not a replacement for a working gcsweb (historical
builds are not enumerable without directory listings).

Usage (inside the dashboard pod):

    python3 /tmp/backfill_from_deck.py --dry-run --days 3
    python3 /tmp/backfill_from_deck.py --days 3 --pattern '*winc*'

Set ``API_KEY`` (deck bearer token) and optionally ``DECK_URL``.
"""

import argparse
import fnmatch
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import ssl
from datetime import datetime, timedelta, timezone

DEFAULT_DECK = "https://qe-private-deck-ci.apps.ci.l2s4.p1.openshiftapps.com"
PLATFORMS = ['aws', 'gcp', 'azure', 'vsphere', 'nutanix', 'metal', 'ovirt', 'openstack']


def http_get(url, token, timeout=120):
    req = urllib.request.Request(url, headers={
        'Authorization': f'Bearer {token}',
        'User-Agent': 'ci-dashboard-deck-backfill/1.0',
    })
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return resp.read()


def strip_rehearse(name):
    return re.sub(r'^rehearse-\d+-', '', name)


def job_metadata(job_name):
    effective = strip_rehearse(job_name)
    version = 'unknown'
    m = re.search(r'release-(\d+\.\d+)', effective)
    if m:
        version = m.group(1)
    elif '-main-' in effective:
        version = '5.1'
    platform = 'unknown'
    for p in PLATFORMS:
        if p in job_name.lower():
            platform = p
            break
    job_type = 'rehearse' if job_name.startswith('rehearse-') else 'periodic'
    return version, platform, job_type


def describe_test(raw_name):
    """Return (test_id, description) using the dashboard's conventions."""
    raw = raw_name.strip().lstrip(':').strip()
    m = re.search(r'OCP-\d+', raw)
    if not m:
        return raw, raw
    test_id = m.group(0)
    desc = raw.split(test_id, 1)[-1]
    bracket = re.search(r'\[sig-[\w-]+\]\s+(.+)', raw)
    if bracket:
        desc = bracket.group(1)
    desc = re.sub(r'^Windows_Containers[:\-\s]+', '', desc)
    desc = re.sub(r'\s*\[[\w-]+\]', '', desc)
    desc = re.sub(r'^[:\-\s]+', '', desc)
    return test_id, (desc.strip() or test_id)


def parse_duration(text):
    text = text.strip()
    m = re.match(r'^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s)?$', text)
    if not m or not any(m.groups()):
        return 0.0
    h, mi, s = m.groups()
    return (int(h or 0) * 3600) + (int(mi or 0) * 60) + float(s or 0)


def parse_junit_lens(page):
    """Parse spyglass' JUnit lens page into test rows.

    Returns a list of dicts: {name, status, duration_seconds, message}.
    Status comes from the tbody the row lives in (failed/passed/skipped).
    """
    tests = []
    for status, tbody in (('failed', 'failed-tbody'),
                          ('passed', 'passed-tbody'),
                          ('skipped', 'skipped-tbody')):
        start = page.find(f'id="{tbody}"')
        if start == -1:
            continue
        nxt = [page.find(f'id="{t}"', start + 1)
               for t in ('failed-tbody', 'passed-tbody', 'skipped-tbody')]
        nxt = [n for n in nxt if n != -1]
        end = min(nxt) if nxt else len(page)
        section = page[start:end]

        if status == 'failed':
            blocks = section.split('<tr class="failure-name">')[1:]
            rows = []
            for block in blocks:
                name_m = re.search(r'test-name">(.*?)</td>', block, re.S)
                dur_m = re.search(r'text-align: right;">\s*([^<]*)</td>', block, re.S)
                msg_m = re.search(r'class="hidden failure-text">(.*?)</tr>', block, re.S)
                if not name_m:
                    continue
                rows.append((name_m.group(1), dur_m.group(1) if dur_m else '',
                             msg_m.group(1) if msg_m else ''))
        else:
            rows = []
            for tr in re.findall(r'<tr>(.*?)</tr>', section, re.S):
                name_m = re.search(r'test-name">(.*?)</td>', tr, re.S)
                dur_m = re.search(r'<td class="mdl-data-table__cell--non-numeric">\s*([^<]*)</td>', tr, re.S)
                if name_m:
                    rows.append((name_m.group(1), dur_m.group(1) if dur_m else '', ''))

        for raw_name, raw_dur, raw_msg in rows:
            name = re.sub(r'<[^>]+>', '', raw_name)
            name = html.unescape(name).replace('\xa0', ' ')
            name = re.sub(r'\bexpand_(more|less)\b', '', name)
            name = re.sub(r'\s+', ' ', name).strip()
            msg = re.sub(r'<[^>]+>', ' ', raw_msg)
            msg = html.unescape(msg)
            msg = re.sub(r'\s*open stdout.*$', '', msg)
            msg = re.sub(r'\s+', ' ', msg).strip() or None
            tests.append({
                'name': name,
                'status': status,
                'duration_seconds': parse_duration(html.unescape(raw_dur)),
                'message': msg,
            })
    return tests


def fetch_run(run_url, token):
    page = http_get(run_url, token).decode('utf-8', 'replace')
    src_m = re.search(r'var src = "([^"]+)"', page)
    lens_m = re.search(r'var lensArtifacts = (\{.*?\});', page, re.S)
    if not src_m or not lens_m:
        return None
    return src_m.group(1), json.loads(lens_m.group(1))


def fetch_junit_tests(deck, token, src, lens_artifacts):
    junit_index = None
    for index, artifacts in lens_artifacts.items():
        if any(a.lower().endswith('.xml') and 'junit' in a.lower() for a in artifacts):
            junit_index = index
    if junit_index is None:
        return []
    req = json.dumps({
        'artifacts': lens_artifacts[junit_index],
        'index': int(junit_index),
        'src': src,
    })
    url = f"{deck}/spyglass/lens/junit/iframe?req=" + urllib.parse.quote(req)
    page = http_get(url, token).decode('utf-8', 'replace')
    return parse_junit_lens(page)


def collect(deck, token, days, pattern, suite_filter, dry_run, db_path):
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)

    raw = http_get(f"{deck}/prowjobs.js?var=allBuilds", token, timeout=300)
    payload = json.loads(raw[raw.index(b'{'):].rstrip(b'; \n\r\t'))
    jobs = payload.get('items', [])
    print(f"prowjobs: {len(jobs)} total")

    selected = []
    for job in jobs:
        name = job.get('spec', {}).get('job', '')
        status = job.get('status', {})
        if not fnmatch.fnmatch(name, pattern):
            continue
        started = status.get('startTime')
        if not started:
            continue
        started_dt = datetime.fromisoformat(started.replace('Z', '+00:00'))
        if not (start <= started_dt <= end):
            continue
        selected.append(job)

    print(f"selected {len(selected)} job(s) matching {pattern!r} in last {days}d")

    sys.path.insert(0, '/app/src')
    from collectors.base import JobRun, TestResult, TestStatus
    from storage.database import DashboardDatabase

    job_runs, test_results = [], []
    for job in selected:
        name = job['spec']['job']
        status = job.get('status', {})
        url = status.get('url', '')
        started = datetime.fromisoformat(status['startTime'].replace('Z', '+00:00'))
        finished = status.get('completionTime')
        duration = 0.0
        if finished:
            duration = (datetime.fromisoformat(finished.replace('Z', '+00:00')) - started).total_seconds()

        fetched = fetch_run(url, token)
        if not fetched:
            print(f"  SKIP {name}/{status.get('build_id')}: no spyglass data")
            continue
        src, lens_artifacts = fetched
        tests = fetch_junit_tests(deck, token, src, lens_artifacts)
        if suite_filter:
            tests = [t for t in tests if suite_filter in t['name']]
        if not tests:
            print(f"  SKIP {name}/{status.get('build_id')}: no matching tests")
            continue

        version, platform, job_type = job_metadata(name)
        counts = {'passed': 0, 'failed': 0, 'skipped': 0}
        for t in tests:
            counts[t['status']] += 1

        result_status = TestStatus.PASSED if status.get('state') == 'success' else TestStatus.FAILED
        job_runs.append(JobRun(
            job_name=name,
            build_id=str(status.get('build_id')),
            status=result_status,
            timestamp=started,
            duration_seconds=duration,
            version=version,
            platform=platform,
            total_tests=counts['passed'] + counts['failed'],
            passed_tests=counts['passed'],
            failed_tests=counts['failed'],
            skipped_tests=counts['skipped'],
            job_url=url,
            job_type=job_type,
        ))

        for t in tests:
            test_id, description = describe_test(t['name'])
            test_results.append(TestResult(
                test_name=test_id,
                status=TestStatus(t['status']) if t['status'] in ('passed', 'failed', 'skipped') else TestStatus.UNKNOWN,
                timestamp=started,
                duration_seconds=t['duration_seconds'] or None,
                error_message=t['message'],
                job_name=name,
                build_id=str(status.get('build_id')),
                version=version,
                platform=platform,
                test_description=description,
                job_url=url,
                job_type=job_type,
            ))
        print(f"  {name}/{status.get('build_id')}: {counts['passed']} passed, "
              f"{counts['failed']} failed, {counts['skipped']} skipped")

    print(f"collected {len(job_runs)} job run(s), {len(test_results)} test result(s)")
    if dry_run:
        print("dry run: nothing written")
        return 0

    db = DashboardDatabase(db_path)
    try:
        print(f"inserted {db.insert_job_runs(job_runs)} job run(s)")
        print(f"inserted {db.insert_test_results(test_results)} test result(s)")
    finally:
        db.close()
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=3)
    parser.add_argument('--pattern', default='*winc*')
    parser.add_argument('--suite-filter', default='Windows_Containers')
    parser.add_argument('--db', default='/data/dashboard.db')
    parser.add_argument('--deck', default=os.environ.get('DECK_URL', DEFAULT_DECK))
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    token = os.environ.get('API_KEY', '')
    if not token:
        print('API_KEY is required', file=sys.stderr)
        return 2
    return collect(args.deck, token, args.days, args.pattern,
                   args.suite_filter, args.dry_run, args.db)


if __name__ == '__main__':
    sys.exit(main())
