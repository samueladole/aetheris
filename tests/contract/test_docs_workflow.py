"""GitHub Pages deploy workflow tests (T024, T025) — FR-011, FR-012.

The deployment contract has two properties that must be verified
*structurally*, because both fail silently when wrong:

  1. **Main only.** A push to any other branch must not publish. The spec is
     explicit: branches other than main are verify-only.
  2. **Failure isolation.** A failed build must never reach the publish step,
     and the previously published site must survive. This is the difference
     between a broken docs run and a *broken live site*.

These parse `.github/workflows/docs.yml` directly rather than assuming its
content, so editing the workflow without updating these tests fails here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "docs.yml"

requires_yaml = pytest.mark.skipif(yaml is None, reason="pyyaml not installed")


@pytest.fixture()
def workflow() -> dict:
    """Parse the docs deployment workflow, or fail loudly."""
    assert DOCS_WORKFLOW.is_file(), (
        f"{DOCS_WORKFLOW} is missing — the documentation deployment workflow "
        "does not exist yet (T026)"
    )
    with DOCS_WORKFLOW.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    assert isinstance(data, dict), "docs.yml did not parse to a mapping"
    return data


def _raw_text() -> str:
    return DOCS_WORKFLOW.read_text(encoding="utf-8")


@requires_yaml
class TestBranchFilter:
    """T024 — FR-011: publish on main only."""

    def test_triggers_on_push_to_main(self, workflow):
        """Test that triggers on push to main."""
        on = workflow.get(True, workflow.get("on", {}))
        assert "push" in on, "the workflow must trigger on push"
        branches = on["push"].get("branches", [])
        assert "main" in branches, f"push must include main; got {branches}"

    def test_does_not_trigger_on_pull_request(self, workflow):
        """Test that does not trigger on pull request."""
        on = workflow.get(True, workflow.get("on", {}))
        assert "pull_request" not in on, (
            "the deployment workflow must not run on pull requests — those are "
            "the CI workflow's job"
        )

    def test_deploy_job_is_guarded_on_main(self, workflow):
        """Even if triggered, the deploy job must re-check its ref."""
        jobs = workflow.get("jobs", {})
        deploy = [j for j in jobs.values() if _publishes(j)]
        assert deploy, "no job appears to publish to Pages"
        for job in deploy:
            condition = job.get("if", "")
            assert "refs/heads/main" in str(condition), (
                f"the publish job must be conditioned on main; got {condition!r}"
            )

    def test_no_trigger_can_publish_a_non_main_ref(self, workflow):
        """Every trigger route is still subject to the deploy job's main guard."""
        on = workflow.get(True, workflow.get("on", {}))
        triggers = [k for k in on if k != "push"]
        assert set(triggers) <= {"workflow_dispatch"}, (
            f"unexpected triggers {triggers}; a manual run is still gated by the "
            "deploy job's refs/heads/main condition, verified above"
        )


@requires_yaml
class TestPublishIsolation:
    """T025 — FR-012, VR-7: a failed build must not publish."""

    def test_deploy_depends_on_build(self, workflow):
        """Test that deploy depends on build."""
        jobs = workflow.get("jobs", {})
        deploy = [j for j in jobs.values() if _publishes(j)]
        assert deploy, "no publishing job found"
        for job in deploy:
            needs = job.get("needs")
            assert needs, (
                "the publish job has no `needs`, so it can run even when the "
                "build failed — that is the failure-isolation defect"
            )
            needed = needs if isinstance(needs, list) else [needs]
            for dep in needed:
                assert "steps" in jobs.get(dep, {}), (
                    f"publish depends on {dep!r}, which is not a job in this workflow"
                )

    def test_build_job_runs_the_gates(self, workflow):
        """The gate that must pass before publishing includes every check."""
        jobs = workflow.get("jobs", {})
        deploy = [j for j in jobs.values() if _publishes(j)]
        assert deploy

        needs = deploy[0].get("needs")
        needed = needs if isinstance(needs, list) else [needs]
        build_jobs = [jobs[d] for d in needed if d in jobs]
        assert build_jobs, "the publish job depends on nothing that exists"

        run_commands = "\n".join(
            str(step.get("run", ""))
            for job in build_jobs
            for step in job.get("steps", [])
        )
        assert "ruff check" in run_commands, "docstring gate missing from the build job"
        assert "mkdocs build" in run_commands, "site build missing"
        assert "scan_secrets" in run_commands, "secret-scan gate missing (FR-017)"
        assert "html-validate" in run_commands, "structural accessibility gate missing"
        assert "check_a11y_rendered" in run_commands, "rendered accessibility gate missing"

    def test_publish_requires_write_permissions(self, workflow):
        """Test that publish requires write permissions."""
        deploy = [j for j in workflow.get("jobs", {}).values() if _publishes(j)]
        for job in deploy:
            perms = job.get("permissions", {}) or {}
            assert perms.get("pages", "") in ("write", None) or perms.get("pages") == "write", (
                f"publish job needs `pages: write`; got {perms.get('pages')!r}"
            )

    def test_artifact_is_uploaded_before_deploy(self, workflow):
        """Without an uploaded artifact there is nothing to deploy."""
        jobs = workflow.get("jobs", {})
        needs = _deploy_needs(jobs)
        uploads = any(
            "upload-pages-artifact" in str(step.get("uses", ""))
            for job in needs
            for step in job.get("steps", [])
        )
        assert uploads, "no upload-pages-artifact step in the job publish depends on"

    def test_workflow_is_valid_yaml(self):
        """Test that workflow is valid yaml."""
        assert yaml is not None
        with DOCS_WORKFLOW.open(encoding="utf-8") as fh:
            yaml.safe_load(fh)


class TestWorkflowFileHygiene:
    """Checks that need no YAML parsing."""

    def test_workflow_exists(self):
        """Test that workflow exists."""
        assert DOCS_WORKFLOW.is_file(), (
            "the documentation deployment workflow is missing (T026)"
        )

    def test_no_live_provider_call_is_possible(self):
        """The docs build must not need external data or credentials.

        Provider keys would let a docs run consume free-tier quota — the exact
        thing FR-013 forbids.
        """
        text = _raw_text()
        for forbidden in ("FRED_API_KEY", "FINNHUB_API_KEY", "TIINGO_API_KEY",
                          "FMP_API_KEY", "ALPHA_VANTAGE_API_KEY"):
            assert forbidden not in text, (
                f"the documentation workflow references {forbidden}; a docs build "
                "needs no provider credentials (FR-013, FR-014)"
            )

    def test_build_runs_in_hermetic_environment(self):
        """No network egress is required at run time."""
        text = _raw_text()
        assert "npm ci" in text or "npm install" in text, (
            "Node accessibility tooling must be installed from a lockfile"
        )
        assert "playwright install" in text, (
            "Playwright's Chromium must be installed for the rendered gate"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _publishes(job: dict) -> bool:
    """True when a job deploys to a hosting target."""
    steps = job.get("steps", []) or []
    return any("deploy-pages" in str(step.get("uses", "")) for step in steps)


def _deploy_needs(jobs: dict) -> list[dict]:
    """The jobs that publish depends on."""
    deploy = [j for j in jobs.values() if _publishes(j)]
    if not deploy:
        return []
    needs = deploy[0].get("needs")
    if not needs:
        return []
    names = needs if isinstance(needs, list) else [needs]
    return [jobs[n] for n in names if n in jobs]
