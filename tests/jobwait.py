"""Waiting for a translation job, for every test file that starts one.

One copy, because `tests/conftest.py` fails any test that starts a job through
`POST /api/translate` and never sees it finish — and three files start jobs, each
with its own request helper. Three poll loops are three budgets that drift.

The deadline is a monotonic clock rather than a count of polls, and generous,
because it is the one timeout left in a run that follows the rules: a stubbed
job finishes in milliseconds, and an unstubbed one is refused at its first
connection and fails at once, so a long deadline costs nothing on either path
and a slow machine is not a failure.
"""

import json
import time
import urllib.request

DEADLINE_SECONDS = 60.0


def finish(base, job_id):
    """Poll one job to a terminal state and hand back its record."""
    until = time.monotonic() + DEADLINE_SECONDS
    while True:
        req = urllib.request.Request(
            base + "/api/job", data=json.dumps({"id": job_id}).encode("utf-8"),
            method="POST", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            job = json.loads(r.read())
        if job.get("done"):
            return job
        if time.monotonic() > until:
            raise AssertionError(f"{job_id} never finished: {job}")
        time.sleep(0.05)
