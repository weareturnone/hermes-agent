"""Tests for scripts/ci/publish_e2e_evidence.py."""

from __future__ import annotations

import html
import importlib.util
import json
import re
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
_YAML_BOOL_TAG = "tag:yaml.org,2002:bool"
_YAML_12_BOOL_PATTERN = re.compile(
    r"^(?:true|True|TRUE|false|False|FALSE)$"
)
_TRIGGER_SOURCE = (
    "on:\n"
    "  workflow_run:\n"
    "    workflows: [CI]\n"
    "    types: [completed]\n"
)


class _GitHubActionsLoader(yaml.SafeLoader):
    """Parse Actions YAML keys with isolated YAML 1.2 boolean semantics."""

    yaml_implicit_resolvers = {
        first_character: [
            (tag, pattern)
            for tag, pattern in resolvers
            if tag != _YAML_BOOL_TAG
        ]
        for first_character, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }


_GitHubActionsLoader.add_implicit_resolver(
    _YAML_BOOL_TAG,
    _YAML_12_BOOL_PATTERN,
    list("tTfF"),
)


def _load_workflow_source(source):
    return yaml.load(source, Loader=_GitHubActionsLoader)


def _replace_source_trigger_key(source, replacement):
    replacement_source = _TRIGGER_SOURCE.replace("on:", f"{replacement}:", 1)
    mutated, replacements = re.subn(
        rf"^{re.escape(_TRIGGER_SOURCE)}",
        replacement_source,
        source,
        flags=re.MULTILINE,
    )
    assert replacements == 1, "source must contain exactly one top-level on trigger"
    return mutated


def _add_source_trigger_key(source, addition):
    addition_source = _TRIGGER_SOURCE.replace("on:", f"{addition}:", 1)
    mutated, replacements = re.subn(
        rf"^{re.escape(_TRIGGER_SOURCE)}",
        addition_source + _TRIGGER_SOURCE,
        source,
        flags=re.MULTILINE,
    )
    assert replacements == 1, "source must contain exactly one top-level on trigger"
    return mutated


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
    "on": {
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
    assert workflow == _COMPLETE_VALID_WORKFLOW, (
        'workflow must preserve literal top-level "on" and exactly match the '
        "complete reviewed publisher graph"
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


def _replace_trigger(trigger):
    workflow = _approved_workflow()
    workflow["on"] = trigger
    return workflow


def _omit_trigger():
    workflow = _approved_workflow()
    del workflow["on"]
    return workflow


def _replace_workflow_run(**changes):
    workflow = _approved_workflow()
    workflow["on"]["workflow_run"].update(changes)
    return workflow


def _omit_workflow_run_key(key):
    workflow = _approved_workflow()
    del workflow["on"]["workflow_run"][key]
    return workflow


def _widen_trigger():
    workflow = _approved_workflow()
    workflow["on"]["push"] = {}
    return workflow


def _replace_concurrency(**changes):
    workflow = _approved_workflow()
    workflow["concurrency"].update(changes)
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
    workflow = _load_workflow_source(_WORKFLOW_PATH.read_text(encoding="utf-8"))

    assert "on" in workflow
    assert isinstance(next(key for key in workflow if key == "on"), str)
    assert workflow["concurrency"]["cancel-in-progress"] is False
    assert (
        workflow["jobs"]["publish"]["steps"][0]["with"]["persist-credentials"]
        is False
    )
    _assert_exact_publisher_graph(workflow)


def test_github_actions_loader_uses_isolated_yaml_12_boolean_semantics():
    safe_resolvers = yaml.SafeLoader.yaml_implicit_resolvers
    custom_resolvers = _GitHubActionsLoader.yaml_implicit_resolvers

    assert custom_resolvers is not safe_resolvers
    assert all(
        custom_resolvers[key] is not resolvers
        for key, resolvers in safe_resolvers.items()
    )
    assert yaml.safe_load("on: value") == {True: "value"}
    assert _load_workflow_source("on: value") == {"on": "value"}
    assert _load_workflow_source("on: on\noff: off\nyes: yes\nno: no\n") == {
        "on": "on",
        "off": "off",
        "yes": "yes",
        "no": "no",
    }
    for spelling in ("true", "True", "TRUE"):
        assert _load_workflow_source(f"value: {spelling}")["value"] is True
    for spelling in ("false", "False", "FALSE"):
        assert _load_workflow_source(f"value: {spelling}")["value"] is False
    assert yaml.safe_load("on: value") == {True: "value"}


@pytest.mark.parametrize(
    ("replacement", "case_id"),
    [
        ("true", "on_replaced_by_true"),
        ("yes", "on_replaced_by_yes"),
    ],
    ids=["on_replaced_by_true", "on_replaced_by_yes"],
)
def test_exact_publisher_graph_rejects_replaced_source_trigger(replacement, case_id):
    source = _WORKFLOW_PATH.read_text(encoding="utf-8")
    workflow = _load_workflow_source(
        _replace_source_trigger_key(source, replacement)
    )

    assert "on" not in workflow, case_id
    with pytest.raises(AssertionError, match="literal top-level"):
        _assert_exact_publisher_graph(workflow)


@pytest.mark.parametrize(
    ("addition", "case_id"),
    [
        ("true", "true_key_added_alongside_on"),
        ("yes", "yes_key_added_alongside_on"),
    ],
    ids=["true_key_added_alongside_on", "yes_key_added_alongside_on"],
)
def test_exact_publisher_graph_rejects_colliding_source_trigger(addition, case_id):
    source = _WORKFLOW_PATH.read_text(encoding="utf-8")
    workflow = _load_workflow_source(_add_source_trigger_key(source, addition))

    assert "on" in workflow, case_id
    assert len(workflow) == len(_COMPLETE_VALID_WORKFLOW) + 1, case_id
    with pytest.raises(AssertionError, match="literal top-level"):
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
        _replace_workflow(name="Drifted publisher workflow"),
        _omit_workflow_key("name"),
        _replace_workflow_run(workflows=["Attacker CI"]),
        _omit_workflow_run_key("workflows"),
        _replace_workflow_run(workflows=["CI", "CI"]),
        _replace_workflow_run(workflows=["CI", "Attacker CI"]),
        _replace_trigger({"push": {}}),
        _omit_trigger(),
        _widen_trigger(),
        _replace_workflow_run(types=["requested"]),
        _omit_workflow_run_key("types"),
        _replace_workflow_run(types=["completed", "completed"]),
        _replace_workflow_run(types=["completed", "requested"]),
        _omit_workflow_key("concurrency"),
        _replace_concurrency(group="global"),
        _replace_concurrency(**{"cancel-in-progress": True}),
        _replace_concurrency(attacker="controlled"),
        _replace_workflow(**{"run-name": "attacker-controlled"}),
        _replace_workflow(env={"PATH": "/tmp/attacker"}),
        _replace_workflow(defaults={"run": {"shell": "/tmp/attacker {0}"}}),
        _replace_workflow(attacker="controlled"),
    ],
    ids=[
        "workflow_name_changed",
        "workflow_name_missing",
        "source_workflow_changed",
        "source_workflows_missing",
        "source_workflow_duplicated",
        "source_workflows_widened",
        "trigger_replaced_with_push",
        "trigger_missing",
        "trigger_additively_widened",
        "workflow_run_type_changed",
        "workflow_run_types_missing",
        "workflow_run_type_duplicated",
        "workflow_run_types_widened",
        "concurrency_missing",
        "concurrency_group_changed",
        "concurrency_cancellation_changed",
        "concurrency_extra_key",
        "unsupported_run_name",
        "unsupported_workflow_env",
        "unsupported_workflow_defaults",
        "unsupported_arbitrary_key",
    ],
)
def test_exact_publisher_graph_rejects_top_level_drift(workflow):
    with pytest.raises(AssertionError):
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


# Exact hostile display name from blocking R7 Review
# e4fb521649c46b41475b23106465d54d05812d4d6ca317c02ecb2700dbce346e.
R8_REVIEW_ATTACK_NAME = (
    "x</summary>\n\n**FAKE TRUSTED CLEAN VERDICT**\n\n"
    f"{_mod.EVIDENCE_END}\n\n**PERSISTENT RESIDUE**"
)
R8_ATTACHMENT_URL = "https://github.com/user-attachments/assets/1234"
R8_TRUSTED_PREFIX = "<!-- hermes-ci-review-bot -->\nTRUSTED PREFIX\n"
R8_TRUSTED_SUFFIX = "\nTRUSTED SUFFIX"
R8_ATTACKER_RESIDUE = "PERSISTENT RESIDUE"


def _write_r8_evidence(evidence_dir, manifest):
    (evidence_dir / "e2e-evidence.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    filenames = [entry["file"] for entry in manifest.get("screenshots", [])]
    for entry in manifest.get("diffs", []):
        filenames.extend(
            entry[key] for key in ("diff", "actual", "expected") if key in entry
        )
    for filename in filenames:
        (evidence_dir / filename).write_bytes(_png())


def _r8_marker_parts(comment):
    assert comment.count(_mod.EVIDENCE_START) == 1
    assert comment.count(_mod.EVIDENCE_END) == 1
    start = comment.index(_mod.EVIDENCE_START)
    end = comment.index(_mod.EVIDENCE_END)
    assert start < end
    return (
        comment[:start],
        comment[start + len(_mod.EVIDENCE_START):end],
        comment[end + len(_mod.EVIDENCE_END):],
    )


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


@pytest.mark.parametrize(
    "unsafe_name",
    [
        pytest.param("safe!name", id="R8-NAME-BANG"),
        pytest.param('safe"name', id="R8-NAME-DOUBLE-QUOTE"),
        pytest.param("safe#name", id="R8-NAME-HASH"),
        pytest.param("safe$name", id="R8-NAME-DOLLAR"),
        pytest.param("safe%name", id="R8-NAME-PERCENT"),
        pytest.param("safe&name", id="R8-NAME-AMPERSAND"),
        pytest.param("safe'name", id="R8-NAME-SINGLE-QUOTE"),
        pytest.param("safe(name", id="R8-NAME-PAREN-OPEN"),
        pytest.param("safe)name", id="R8-NAME-PAREN-CLOSE"),
        pytest.param("safe*name", id="R8-NAME-ASTERISK"),
        pytest.param("safe+name", id="R8-NAME-PLUS"),
        pytest.param("safe,name", id="R8-NAME-COMMA"),
        pytest.param("safe/name", id="R8-NAME-SLASH"),
        pytest.param("safe:name", id="R8-NAME-COLON"),
        pytest.param("safe;name", id="R8-NAME-SEMICOLON"),
        pytest.param("safe<name", id="R8-NAME-ANGLE-OPEN"),
        pytest.param("safe=name", id="R8-NAME-EQUALS"),
        pytest.param("safe>name", id="R8-NAME-ANGLE-CLOSE"),
        pytest.param("safe?name", id="R8-NAME-QUESTION"),
        pytest.param("safe@name", id="R8-NAME-AT"),
        pytest.param("safe[name", id="R8-NAME-BRACKET-OPEN"),
        pytest.param("safe\\name", id="R8-NAME-BACKSLASH"),
        pytest.param("safe]name", id="R8-NAME-BRACKET-CLOSE"),
        pytest.param("safe^name", id="R8-NAME-CARET"),
        pytest.param("safe`name", id="R8-NAME-BACKTICK"),
        pytest.param("safe{name", id="R8-NAME-BRACE-OPEN"),
        pytest.param("safe|name", id="R8-NAME-PIPE"),
        pytest.param("safe}name", id="R8-NAME-BRACE-CLOSE"),
        pytest.param("safe~name", id="R8-NAME-TILDE"),
        pytest.param("safe\nname", id="R8-NAME-C0-LF"),
        pytest.param("safe\rname", id="R8-NAME-C0-CR"),
        pytest.param("safe\x00name", id="R8-NAME-C0-NUL"),
        pytest.param("safe\tname", id="R8-NAME-C0-TAB"),
        pytest.param("safe\x1bname", id="R8-NAME-C0-ESC"),
        pytest.param("safe\x1fname", id="R8-NAME-C0-UNIT-SEPARATOR"),
        pytest.param("safe\x7fname", id="R8-NAME-DEL"),
        pytest.param("safeéname", id="R8-NAME-UNICODE-E-ACUTE"),
        pytest.param("safe＜name", id="R8-NAME-UNICODE-FULLWIDTH-ANGLE"),
        pytest.param("safe\u202ename", id="R8-NAME-UNICODE-RTL-OVERRIDE"),
        pytest.param("", id="R8-NAME-EMPTY"),
        pytest.param(" leading", id="R8-NAME-LEADING-SPACE"),
        pytest.param(".leading", id="R8-NAME-LEADING-DOT"),
        pytest.param("-leading", id="R8-NAME-LEADING-HYPHEN"),
        pytest.param(_mod.EVIDENCE_START, id="R8-NAME-LITERAL-START-MARKER"),
        pytest.param(_mod.EVIDENCE_END, id="R8-NAME-LITERAL-END-MARKER"),
        pytest.param(
            "hermes-e2e-evidence:start",
            id="R8-NAME-START-MARKER-FRAGMENT",
        ),
        pytest.param(
            "hermes-e2e-evidence:end",
            id="R8-NAME-END-MARKER-FRAGMENT",
        ),
        pytest.param(R8_REVIEW_ATTACK_NAME, id="R8-NAME-REVIEW-PAYLOAD"),
    ],
)
def test_r8_load_evidence_rejects_unsafe_display_names(tmp_path, unsafe_name):
    manifest = {
        "version": 1,
        "screenshots": [{"name": unsafe_name, "file": "safe.png"}],
        "diffs": [],
    }
    _write_r8_evidence(tmp_path, manifest)

    with pytest.raises(
        ValueError,
        match=re.compile("unsafe evidence display name", re.IGNORECASE),
    ) as exc_info:
        _mod.load_evidence(tmp_path)

    if unsafe_name:
        assert unsafe_name not in str(exc_info.value)


@pytest.mark.parametrize(
    ("label_kind", "maximum_name_length"),
    [
        pytest.param("screenshot", 112, id="R8-LIMIT-SCREENSHOT-AT-128"),
        pytest.param("diff", 115, id="R8-LIMIT-DIFF-AT-128"),
        pytest.param("actual", 113, id="R8-LIMIT-ACTUAL-AT-128"),
        pytest.param("expected", 111, id="R8-LIMIT-EXPECTED-AT-128"),
    ],
)
def test_r8_load_evidence_enforces_complete_label_limit(
    tmp_path, label_kind, maximum_name_length
):
    maximum_name = "a" * maximum_name_length
    if label_kind == "screenshot":
        maximum_manifest = {
            "version": 1,
            "screenshots": [{"name": maximum_name, "file": "shot.png"}],
            "diffs": [],
        }
    else:
        entry = {"name": maximum_name, "diff": "diff.png"}
        if label_kind in ("actual", "expected"):
            entry[label_kind] = f"{label_kind}.png"
        maximum_manifest = {
            "version": 1,
            "screenshots": [],
            "diffs": [entry],
        }
    _write_r8_evidence(tmp_path, maximum_manifest)
    files, _ = _mod.load_evidence(tmp_path)
    assert any(len(item.label) == 128 for item in files)

    if label_kind == "expected":
        all_companions = deepcopy(maximum_manifest)
        all_companions["diffs"][0]["actual"] = "actual.png"
        _write_r8_evidence(tmp_path, all_companions)
        all_files, _ = _mod.load_evidence(tmp_path)
        assert [item.filename for item in all_files] == [
            "diff.png",
            "actual.png",
            "expected.png",
        ]

    rejected_name = maximum_name + "a"
    rejected_manifest = deepcopy(maximum_manifest)
    entries = (
        rejected_manifest["screenshots"]
        if label_kind == "screenshot"
        else rejected_manifest["diffs"]
    )
    entries[0]["name"] = rejected_name
    _write_r8_evidence(tmp_path, rejected_manifest)
    with pytest.raises(
        ValueError,
        match=re.compile(
            "evidence display label exceeds 128 characters", re.IGNORECASE
        ),
    ) as exc_info:
        _mod.load_evidence(tmp_path)
    assert rejected_name not in str(exc_info.value)


@pytest.mark.parametrize(
    "hostile_label",
    [
        pytest.param(
            "unsafe <b>& label</b> [alt] "
            f"{_mod.EVIDENCE_START} {_mod.EVIDENCE_END}",
            id="R8-RENDER-DIRECT-HOSTILE",
        ),
    ],
)
def test_r8_render_evidence_defends_against_hostile_internal_label(hostile_label):
    rendered = _mod.render_evidence(
        [_mod.EvidenceFile("safe.png", hostile_label)],
        {"safe.png": R8_ATTACHMENT_URL},
    )

    assert f"<summary>{html.escape(hostile_label)}</summary>" in rendered
    assert f"![E2E evidence]({R8_ATTACHMENT_URL})" in rendered
    assert f"![{hostile_label}]" not in rendered
    assert rendered.count(_mod.EVIDENCE_START) == 1
    assert rendered.count(_mod.EVIDENCE_END) == 1


@pytest.mark.parametrize(
    ("comment", "requires_new_message"),
    [
        pytest.param(_mod.EVIDENCE_END, False, id="R8-TOPOLOGY-MISSING-START"),
        pytest.param(_mod.EVIDENCE_START, False, id="R8-TOPOLOGY-MISSING-END"),
        pytest.param(
            f"{_mod.EVIDENCE_START}\n{_mod.EVIDENCE_START}\n{_mod.EVIDENCE_END}",
            True,
            id="R8-TOPOLOGY-DUPLICATE-START",
        ),
        pytest.param(
            f"{_mod.EVIDENCE_START}\n{_mod.EVIDENCE_END}\n{_mod.EVIDENCE_END}",
            True,
            id="R8-TOPOLOGY-DUPLICATE-END",
        ),
        pytest.param(
            f"{_mod.EVIDENCE_START}\n{_mod.EVIDENCE_END}\n"
            f"{_mod.EVIDENCE_START}\n{_mod.EVIDENCE_END}",
            True,
            id="R8-TOPOLOGY-DUPLICATE-PAIR",
        ),
        pytest.param(
            f"{_mod.EVIDENCE_END}\n{_mod.EVIDENCE_START}\n{_mod.EVIDENCE_END}",
            True,
            id="R8-TOPOLOGY-END-BEFORE-START",
        ),
        pytest.param(
            f"{_mod.EVIDENCE_END}\n{_mod.EVIDENCE_START}",
            False,
            id="R8-TOPOLOGY-REVERSED-PAIR",
        ),
    ],
)
def test_r8_replace_evidence_marker_requires_exact_topology(
    comment, requires_new_message
):
    with pytest.raises(ValueError) as exc_info:
        _mod.replace_evidence_marker(comment, "replacement")
    assert comment not in str(exc_info.value)
    if requires_new_message:
        assert re.search(
            "exactly one ordered evidence marker pair",
            str(exc_info.value),
            re.IGNORECASE,
        )


@pytest.mark.parametrize(
    "comment_body",
    [
        pytest.param(
            f"{_mod.EVIDENCE_START}\n{_mod.EVIDENCE_START}\n{_mod.EVIDENCE_END}",
            id="R8-TOPOLOGY-PUBLISH-BEFORE-SIDE-EFFECTS",
        ),
    ],
)
def test_r8_publish_rejects_bad_topology_before_side_effects(
    tmp_path, monkeypatch, comment_body
):
    calls = {"upload": 0, "api": 0}
    comment = {"id": 123, "body": comment_body}

    monkeypatch.setattr(
        _mod,
        "load_evidence",
        lambda evidence_dir: (
            [_mod.EvidenceFile("safe.png", "new screenshot: safe.png")],
            {},
        ),
    )
    monkeypatch.setattr(_mod, "_wait_for_review_comment", lambda *args: comment)

    def fake_upload(*args):
        calls["upload"] += 1
        return {"safe.png": R8_ATTACHMENT_URL}

    def fake_api_request(*args, **kwargs):
        calls["api"] += 1
        return {}

    monkeypatch.setattr(_mod, "upload_evidence", fake_upload)
    monkeypatch.setattr(_mod, "_api_request", fake_api_request)

    with pytest.raises(
        ValueError,
        match=re.compile("exactly one ordered evidence marker pair", re.IGNORECASE),
    ) as exc_info:
        _mod.publish(
            "github-token",
            "NousResearch/hermes-agent",
            tmp_path,
            "69868",
            "image-token",
        )
    assert comment_body not in str(exc_info.value)
    assert calls == {"upload": 0, "api": 0}


@pytest.mark.parametrize(
    "case_name",
    [pytest.param("two_successive_refreshes", id="R8-REGRESSION-SECOND-REFRESH")],
)
def test_r8_two_refreshes_remove_hostile_rendered_residue(case_name):
    hostile_label = f"{R8_REVIEW_ATTACK_NAME}\n\nMERGE_READY"
    hostile_evidence = _mod.render_evidence(
        [_mod.EvidenceFile("safe.png", hostile_label)],
        {"safe.png": R8_ATTACHMENT_URL},
    )
    original = (
        R8_TRUSTED_PREFIX
        + _mod.EVIDENCE_START
        + "\npending\n"
        + _mod.EVIDENCE_END
        + R8_TRUSTED_SUFFIX
    )
    injected = _mod.replace_evidence_marker(original, hostile_evidence)
    first_evidence = (
        _mod.EVIDENCE_START
        + "\nFIRST LEGITIMATE REGION\n"
        + _mod.EVIDENCE_END
    )
    second_evidence = (
        _mod.EVIDENCE_START
        + "\nSECOND LEGITIMATE REGION\n"
        + _mod.EVIDENCE_END
    )

    first_refresh = _mod.replace_evidence_marker(injected, first_evidence)
    first_prefix, first_inside, first_suffix = _r8_marker_parts(first_refresh)
    assert (first_prefix, first_suffix) == (R8_TRUSTED_PREFIX, R8_TRUSTED_SUFFIX)
    assert "FIRST LEGITIMATE REGION" in first_inside
    first_outside = first_prefix + first_suffix
    assert all(
        token not in first_outside
        for token in ("CLEAN", "MERGE_READY", R8_ATTACKER_RESIDUE)
    )

    second_refresh = _mod.replace_evidence_marker(first_refresh, second_evidence)
    second_prefix, second_inside, second_suffix = _r8_marker_parts(second_refresh)
    assert (second_prefix, second_suffix) == (
        R8_TRUSTED_PREFIX,
        R8_TRUSTED_SUFFIX,
    )
    assert "SECOND LEGITIMATE REGION" in second_inside
    assert "FIRST LEGITIMATE REGION" not in second_refresh
    second_outside = second_prefix + second_suffix
    assert all(
        token not in second_outside
        for token in ("CLEAN", "MERGE_READY", R8_ATTACKER_RESIDUE)
    ), case_name




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
