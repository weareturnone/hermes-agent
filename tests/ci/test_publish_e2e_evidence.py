"""Tests for scripts/ci/publish_e2e_evidence.py."""

from __future__ import annotations

import importlib.util
import sys
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

_PATH = Path(__file__).resolve().parents[2] / "scripts" / "ci" / "publish_e2e_evidence.py"
_spec = importlib.util.spec_from_file_location("publish_e2e_evidence", _PATH)
if _spec is None or _spec.loader is None:
    raise ImportError("Failed to load publish_e2e_evidence.py")
_mod = importlib.util.module_from_spec(_spec)
sys.modules["publish_e2e_evidence"] = _mod
_spec.loader.exec_module(_mod)


_WORKFLOW_PATH = (
    Path(__file__).resolve().parents[2]
    / ".github"
    / "workflows"
    / "publish-e2e-evidence.yml"
)
_GH_IMAGE_COMMIT = "44f4b93ecbbe22de6c45fa2f62f519aee564ca8c"
_GH_IMAGE_SOURCE_INSTALL = (
    "gh extension install drogers0/gh-image --pin " f"{_GH_IMAGE_COMMIT}"
)
_PUBLISHER_RUN = (
    "set -euo pipefail\n"
    "\n"
    'PR_NUMBER=$(gh api "repos/$SOURCE_REPO/actions/runs/$SOURCE_RUN_ID" '
    "--jq '.pull_requests[0].number // empty')\n"
    'if [ -z "$PR_NUMBER" ]; then\n'
    '  echo "No pull request is associated with CI run $SOURCE_RUN_ID."\n'
    "  exit 0\n"
    "fi\n"
    "\n"
    'ARTIFACT_NAME=$(gh api "repos/$SOURCE_REPO/actions/runs/$SOURCE_RUN_ID/artifacts" \\\n'
    "  --jq '.artifacts[] | select(.expired == false and (.name | "
    "startswith(\"e2e-evidence-\"))) | .name' \\\n"
    "  | python3 -c 'import sys; print(next(iter(sys.stdin), \"\").strip())')\n"
    'if [ -z "$ARTIFACT_NAME" ]; then\n'
    '  echo "No E2E evidence artifact was produced for CI run $SOURCE_RUN_ID."\n'
    "  exit 0\n"
    "fi\n"
    "\n"
    'EVIDENCE_DIR="$RUNNER_TEMP/e2e-evidence"\n'
    'mkdir -p "$EVIDENCE_DIR"\n'
    'gh run download "$SOURCE_RUN_ID" --repo "$SOURCE_REPO" '
    '--name "$ARTIFACT_NAME" --dir "$EVIDENCE_DIR"\n'
    "\n"
    "python3 scripts/ci/publish_e2e_evidence.py \\\n"
    '  --evidence-dir "$EVIDENCE_DIR" \\\n'
    '  --source-repo "$SOURCE_REPO" \\\n'
    '  --pr-number "$PR_NUMBER"\n'
)
_EXPECTED_PUBLISH_STEPS = [
    {
        "name": "Check out trusted publisher",
        "uses": "actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd",
        "with": {
            "ref": "${{ github.event.repository.default_branch }}",
            "persist-credentials": False,
        },
    },
    {
        "name": "Install gh-image",
        "env": {"GH_TOKEN": "${{ github.token }}"},
        "run": _GH_IMAGE_SOURCE_INSTALL,
    },
    {
        "name": "Download and attach evidence",
        "env": {
            "GH_TOKEN": "${{ github.token }}",
            "GITHUB_TOKEN": "${{ github.token }}",
            "GH_SESSION_TOKEN": "${{ secrets.GH_IMAGE_SESSION_TOKEN }}",
            "SOURCE_REPO": "${{ github.repository }}",
            "SOURCE_RUN_ID": "${{ github.event.workflow_run.id }}",
        },
        "run": _PUBLISHER_RUN,
    },
]
_EXPECTED_PERMISSIONS = {
    "actions": "read",
    "contents": "read",
    "pull-requests": "write",
}
_EXPECTED_PUBLISH_JOB = {
    "name": "Publish inline E2E evidence",
    "if": "github.event.workflow_run.event == 'pull_request'",
    "runs-on": "ubuntu-latest",
    "timeout-minutes": 10,
    "environment": "gh-image",
    "steps": _EXPECTED_PUBLISH_STEPS,
}
_COMPLETE_VALID_WORKFLOW = {
    "name": "Publish E2E evidence",
    True: {
        "workflow_run": {
            "workflows": ["CI"],
            "types": ["completed"],
        },
    },
    "permissions": _EXPECTED_PERMISSIONS,
    "concurrency": {
        "group": "publish-e2e-evidence-${{ github.event.workflow_run.id }}",
        "cancel-in-progress": False,
    },
    "jobs": {"publish": _EXPECTED_PUBLISH_JOB},
}


def _assert_exact_publisher_graph(workflow):
    """Audit parsed YAML without installing or executing gh-image."""
    assert workflow.get("permissions") == _EXPECTED_PERMISSIONS, (
        "workflow permissions must exactly match the reviewed publisher permissions"
    )
    assert "env" not in workflow, "workflow env must be absent"
    assert "defaults" not in workflow, "workflow defaults must be absent"
    assert workflow.get("jobs") == {"publish": _EXPECTED_PUBLISH_JOB}, (
        "jobs must contain only the exact reviewed publish execution envelope"
    )


def _approved_workflow():
    return deepcopy(_COMPLETE_VALID_WORKFLOW)


def _replace_workflow(**changes):
    workflow = _approved_workflow()
    workflow.update(changes)
    return workflow


def _omit_workflow_key(key):
    workflow = _approved_workflow()
    del workflow[key]
    return workflow


def _replace_job(**changes):
    workflow = _approved_workflow()
    workflow["jobs"]["publish"].update(changes)
    return workflow


def _add_job(name, job):
    workflow = _approved_workflow()
    workflow["jobs"][name] = job
    return workflow


def _replace_step(index, **changes):
    workflow = _approved_workflow()
    workflow["jobs"]["publish"]["steps"][index].update(changes)
    return workflow


def _insert_step(index, step):
    workflow = _approved_workflow()
    workflow["jobs"]["publish"]["steps"].insert(index, step)
    return workflow


def _omit_step(index):
    workflow = _approved_workflow()
    del workflow["jobs"]["publish"]["steps"][index]
    return workflow


def _expose_job_or_workflow_token(scope):
    workflow = _approved_workflow()
    target = workflow if scope == "workflow" else workflow["jobs"]["publish"]
    target["env"] = {"GH_SESSION_TOKEN": "${{ secrets.GH_IMAGE_SESSION_TOKEN }}"}
    return workflow


def test_publish_workflow_has_exact_privileged_publisher_graph():
    workflow = yaml.safe_load(_WORKFLOW_PATH.read_text(encoding="utf-8"))

    _assert_exact_publisher_graph(workflow)


@pytest.mark.parametrize(
    "workflow",
    [
        _approved_workflow(),
    ],
    ids=["exact_source_publisher_graph"],
)
def test_exact_publisher_graph_accepts_approved_shape(workflow):
    _assert_exact_publisher_graph(workflow)


@pytest.mark.parametrize(
    "workflow",
    [
        _expose_job_or_workflow_token("workflow"),
        _expose_job_or_workflow_token("job"),
    ],
    ids=["workflow_env_token", "job_env_token"],
)
def test_exact_publisher_graph_rejects_early_token_scope(workflow):
    with pytest.raises(AssertionError):
        _assert_exact_publisher_graph(workflow)


@pytest.mark.parametrize(
    "workflow",
    [
        _replace_workflow(env={"PATH": "/tmp/attacker"}),
        _replace_workflow(env={"BASH_ENV": "/tmp/attacker"}),
        _replace_job(env={"PATH": "/tmp/attacker"}),
        _replace_job(env={"BASH_ENV": "/tmp/attacker"}),
        _replace_workflow(defaults={"run": {"shell": "/tmp/attacker {0}"}}),
        _replace_job(defaults={"run": {"shell": "/tmp/attacker {0}"}}),
        _replace_job(container="attacker/image:latest"),
        _replace_job(
            services={"poison": {"image": "attacker/image:latest"}},
        ),
        _replace_job(**{"runs-on": "self-hosted"}),
        _replace_job(environment="attacker"),
        _replace_workflow(permissions={"contents": "write"}),
        _replace_workflow(
            permissions={**_EXPECTED_PERMISSIONS, "id-token": "write"},
        ),
        _omit_workflow_key("permissions"),
        _add_job(
            "attacker",
            {"runs-on": "ubuntu-latest", "steps": [{"run": "true"}]},
        ),
        _replace_job(name="Drifted publisher"),
        _replace_job(**{"if": "always()"}),
        _replace_job(**{"timeout-minutes": 60}),
    ],
    ids=[
        "workflow_path",
        "workflow_bash_env",
        "job_path",
        "job_bash_env",
        "workflow_defaults",
        "job_defaults",
        "job_container",
        "job_services",
        "runner_drift",
        "environment_drift",
        "permissions_drift",
        "permissions_expansion",
        "permissions_missing",
        "unexpected_second_job",
        "job_name_drift",
        "job_if_drift",
        "job_timeout_drift",
    ],
)
def test_exact_publisher_graph_rejects_execution_envelope_drift(workflow):
    with pytest.raises(AssertionError):
        _assert_exact_publisher_graph(workflow)


@pytest.mark.parametrize(
    "workflow",
    [
        _replace_step(0, uses="actions/checkout@v6"),
        _omit_step(0),
        _replace_step(
            0,
            uses="actions/checkout@0000000000000000000000000000000000000000",
        ),
        _replace_step(
            0,
            **{
                "with": {
                    "ref": "${{ github.event.workflow_run.head_sha }}",
                    "persist-credentials": False,
                }
            },
        ),
        _replace_step(
            0,
            **{
                "with": {
                    "ref": "${{ github.event.repository.default_branch }}",
                    "persist-credentials": True,
                }
            },
        ),
        _replace_step(
            0,
            **{"with": {"ref": "${{ github.event.repository.default_branch }}"}},
        ),
        _insert_step(0, {"uses": "attacker/path-hijack-action@0123456789abcdef"}),
        _insert_step(1, {"run": "printf 'prepare publisher'"}),
        _replace_step(
            1,
            run="gh extension install drogers0/gh-image --pin v1.2.0",
        ),
        _replace_step(1, env={"GH_TOKEN": "${{ secrets.OTHER_TOKEN }}"}),
        _replace_step(
            1,
            env={
                "GH_TOKEN": "${{ github.token }}",
                "GH_SESSION_TOKEN": "${{ secrets.GH_IMAGE_SESSION_TOKEN }}",
            },
        ),
        _insert_step(2, deepcopy(_EXPECTED_PUBLISH_STEPS[1])),
        _omit_step(1),
        _insert_step(
            2,
            {
                "run": (
                    "command gh extension install drogers0/gh-image "
                    "--pin v1.2.0"
                )
            },
        ),
        _insert_step(
            2,
            {"run": "g'h' extension install drogers0/gh-image --pin v1.2.0"},
        ),
        _insert_step(
            2,
            {
                "run": (
                    "printf x | gh extension install drogers0/gh-image "
                    "--pin v1.2.0"
                )
            },
        ),
        _insert_step(
            2,
            {"run": "(gh extension install drogers0/gh-image --pin v1.2.0)"},
        ),
        _insert_step(
            2,
            {
                "run": (
                    "result=$(gh extension install drogers0/gh-image "
                    "--pin v1.2.0)"
                )
            },
        ),
        _insert_step(
            2,
            {
                "run": (
                    f"{_GH_IMAGE_SOURCE_INSTALL} || "
                    "gh extension install drogers0/gh-image --pin v1.2.0"
                )
            },
        ),
        _insert_step(
            2,
            {
                "run": (
                    "ref=v1.2.0; gh extension install drogers0/gh-image "
                    '--pin "$ref" --force'
                )
            },
        ),
        _insert_step(
            2,
            {
                "run": (
                    "curl -LO https://example.invalid/gh-image-v1.2.0; "
                    "install gh-image /usr/local/bin/gh-image"
                )
            },
        ),
        _insert_step(2, {"uses": "attacker/replace-publisher@0123456789abcdef"}),
        _insert_step(2, {"run": "printf 'between selection and publisher'"}),
        _replace_step(
            2,
            env={
                "GH_TOKEN": "${{ github.token }}",
                "GITHUB_TOKEN": "${{ github.token }}",
                "GH_SESSION_TOKEN": "${{ secrets.OTHER_SESSION_TOKEN }}",
                "SOURCE_REPO": "${{ github.repository }}",
                "SOURCE_RUN_ID": "${{ github.event.workflow_run.id }}",
            },
        ),
        _replace_step(2, run=f"{_PUBLISHER_RUN}echo drift\n"),
        _replace_step(
            2,
            run=(
                "gh extension install drogers0/gh-image --pin v1.2.0 --force; "
                "gh image evidence.png"
            ),
        ),
        _insert_step(3, deepcopy(_EXPECTED_PUBLISH_STEPS[2])),
        _omit_step(2),
        _insert_step(3, {"uses": "attacker/post-publish@0123456789abcdef"}),
        _insert_step(3, {"run": "gh image evidence.png"}),
    ],
    ids=[
        "mutable_checkout",
        "missing_checkout",
        "wrong_checkout",
        "checkout_ref_drift",
        "checkout_persist_credentials_drift",
        "checkout_persist_credentials_missing",
        "review_executable_action_before_selection",
        "command_before_install",
        "source_pin_drift",
        "install_env_drift",
        "token_before_selection",
        "duplicate_install",
        "selector_omission",
        "review_wrapped_later_downgrade",
        "quoted_later_downgrade",
        "pipeline_later_downgrade",
        "subshell_later_downgrade",
        "command_substitution_later_downgrade",
        "fallback_later_downgrade",
        "review_variable_based_later_downgrade",
        "binary_replacement",
        "action_after_selection",
        "extra_command_between_selection_and_publisher",
        "privileged_env_drift",
        "privileged_script_drift",
        "review_downgrade_inside_token_bearing_step",
        "publisher_duplication",
        "publisher_omission",
        "executable_action_post_publisher",
        "executable_command_post_publisher",
    ],
)
def test_exact_publisher_graph_rejects_unsafe_workflow_shapes(workflow):
    with pytest.raises(AssertionError):
        _assert_exact_publisher_graph(workflow)


def _png(width: int = 4, height: int = 3) -> bytes:
    return _mod.PNG_SIGNATURE + b"\x00\x00\x00\rIHDR" + width.to_bytes(4, "big") + height.to_bytes(4, "big")


def test_load_evidence_validates_manifest_and_pngs(tmp_path):
    (tmp_path / "shot.png").write_bytes(_png())
    (tmp_path / "diff.png").write_bytes(_png())
    (tmp_path / "actual.png").write_bytes(_png())
    (tmp_path / "expected.png").write_bytes(_png())
    (tmp_path / "e2e-evidence.json").write_text(
        """{
          "version": 1,
          "screenshots": [{"name": "main-view.png", "file": "shot.png"}],
          "diffs": [{"name": "main-view", "diff": "diff.png", "actual": "actual.png", "expected": "expected.png"}]
        }""",
        encoding="utf-8",
    )

    files, payloads = _mod.load_evidence(tmp_path)

    assert [item.label for item in files] == [
        "new screenshot: main-view.png",
        "visual diff: main-view",
        "visual actual: main-view",
        "visual expected: main-view",
    ]
    assert set(payloads) == {"shot.png", "diff.png", "actual.png", "expected.png"}


def test_load_evidence_rejects_path_escape_and_non_png(tmp_path):
    (tmp_path / "e2e-evidence.json").write_text(
        '{"version":1,"screenshots":[{"name":"bad","file":"../secret.png"}],"diffs":[]}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unsafe filename"):
        _mod.load_evidence(tmp_path)

    (tmp_path / "e2e-evidence.json").write_text(
        '{"version":1,"screenshots":[{"name":"bad","file":"not-png.png"}],"diffs":[]}',
        encoding="utf-8",
    )
    (tmp_path / "not-png.png").write_bytes(b"not a png")

    with pytest.raises(ValueError, match="not a PNG"):
        _mod.load_evidence(tmp_path)




def test_upload_evidence_accepts_only_attachment_urls(tmp_path, monkeypatch):
    shot = tmp_path / "shot.png"
    shot.write_bytes(_png())
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return _mod.subprocess.CompletedProcess(
            args,
            0,
            stdout="![shot.png](https://github.com/user-attachments/assets/12345678-1234-1234-1234-123456789abc)\n",
        )

    monkeypatch.setattr(_mod.subprocess, "run", fake_run)

    result = _mod.upload_evidence(
        [_mod.EvidenceFile("shot.png", "new screenshot: shot.png")],
        tmp_path,
        "NousResearch/hermes-agent",
        "bot-session-token",
    )

    assert result == {"shot.png": "https://github.com/user-attachments/assets/12345678-1234-1234-1234-123456789abc"}
    assert calls[0][0] == ["gh", "image", "--repo", "NousResearch/hermes-agent", str(shot)]
    assert calls[0][1]["env"]["GH_SESSION_TOKEN"] == "bot-session-token"




def test_upload_evidence_reports_gh_image_error(tmp_path, monkeypatch, capsys):
    shot = tmp_path / "shot.png"
    shot.write_bytes(_png())

    def fake_run(args, **kwargs):
        raise _mod.subprocess.CalledProcessError(
            1,
            args,
            output="upload output",
            stderr="upload error",
        )

    monkeypatch.setattr(_mod.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="Failed to upload shot.png.*upload error"):
        _mod.upload_evidence(
            [_mod.EvidenceFile("shot.png", "new screenshot: shot.png")],
            tmp_path,
            "NousResearch/hermes-agent",
            "bot-session-token",
        )

    captured = capsys.readouterr()
    assert "Failed to upload shot.png" in captured.err
    assert "upload output" in captured.err
    assert "upload error" in captured.err


def test_publish_marks_evidence_upload_failure_in_pr_comment(tmp_path, monkeypatch):
    comment = {
        "id": 123,
        "body": "before\n<!-- hermes-e2e-evidence:start -->\npending\n<!-- hermes-e2e-evidence:end -->\nafter",
    }
    updates = []

    monkeypatch.setattr(
        _mod,
        "load_evidence",
        lambda evidence_dir: (
            [_mod.EvidenceFile("shot.png", "new screenshot: shot.png")],
            {},
        ),
    )
    monkeypatch.setattr(_mod, "_wait_for_review_comment", lambda *args: comment)
    monkeypatch.setattr(
        _mod,
        "upload_evidence",
        lambda *args: (_ for _ in ()).throw(
            RuntimeError("Failed to upload shot.png: bad <response>")
        ),
    )
    monkeypatch.setattr(
        _mod,
        "_api_request",
        lambda url, token, method, payload: updates.append((
            url,
            token,
            method,
            payload,
        )),
    )

    with pytest.raises(RuntimeError, match="Failed to upload shot.png"):
        _mod.publish(
            "github-token",
            "NousResearch/hermes-agent",
            tmp_path,
            "69868",
            "image-token",
        )

    assert updates == [
        (
            "https://api.github.com/repos/NousResearch/hermes-agent/issues/comments/123",
            "github-token",
            "PATCH",
            {
                "body": "before\n<!-- hermes-e2e-evidence:start -->\n<sub>inline evidence upload failed.</sub>\n\n<pre>Failed to upload shot.png: bad &lt;response&gt;</pre>\n<!-- hermes-e2e-evidence:end -->\nafter"
            },
        )
    ]


def test_find_review_comment_requires_the_evidence_marker():
    pending = "<!-- hermes-ci-review-bot -->\n<!-- hermes-e2e-evidence:start -->\npending\n<!-- hermes-e2e-evidence:end -->"

    assert _mod._find_review_comment([{"body": "<!-- hermes-ci-review-bot --> no evidence"}]) is None
    assert _mod._find_review_comment([{"body": pending, "id": 123}]) == {"body": pending, "id": 123}


def test_replace_evidence_marker_requires_exactly_one_marker():
    with pytest.raises(ValueError, match="does not contain one"):
        _mod.replace_evidence_marker("no marker", "evidence")
