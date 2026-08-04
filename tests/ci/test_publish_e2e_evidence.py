"""Tests for scripts/ci/publish_e2e_evidence.py."""

from __future__ import annotations

import importlib.util
import re
import sys
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
_GH_IMAGE_LINUX_AMD64_SHA256 = (
    "0505f8c46d63bd603a445fdbfdd6be45e75a80778d97f1edf3580697fa6b7919"
)
_GH_IMAGE_RELEASE_REF = "v1.2.0"
_GH_IMAGE_RELEASE_ASSET = "gh-image_1.2.0_linux_amd64.tar.gz"


def _shell_commands(run):
    return [
        command.strip()
        for command in re.split(r"(?:\n|;|&&|\|\|)", run)
        if command.strip()
    ]


def _is_gh_image_execution(command):
    return bool(
        re.match(r"^gh\s+image(?:\s|$)", command)
        or re.match(r"^(?:\S+/)?gh-image(?:\s|$)", command)
    )


def _assert_gh_image_identity_is_verified_before_privilege(workflow):
    """Audit parsed workflow steps without installing or executing gh-image."""
    job = workflow["jobs"]["publish"]
    assert "GH_SESSION_TOKEN" not in workflow.get("env", {})
    assert "GH_SESSION_TOKEN" not in job.get("env", {})

    immutable_identity = False
    downloaded_accepted_binary = False
    saw_identity_selection = False
    identity_selections = 0

    for step in job["steps"]:
        run = str(step.get("run", ""))

        for command in _shell_commands(run):
            if _is_gh_image_execution(command):
                assert immutable_identity, (
                    "gh-image must not execute before its immutable identity "
                    "has been verified"
                )

            if "gh extension install" in command and "gh-image" in command:
                identity_selections += 1
                saw_identity_selection = True
                pins = re.findall(r"--pin(?:=|\s+)([^\s]+)", command)
                immutable_identity = (
                    identity_selections == 1
                    and pins == [_GH_IMAGE_COMMIT]
                    and "||" not in run
                )
                assert immutable_identity, (
                    "gh-image execution identity must be immutable and verified "
                    "before GH_SESSION_TOKEN exposure"
                )

            if (
                "http" in command
                and _GH_IMAGE_RELEASE_REF in command
                and _GH_IMAGE_RELEASE_ASSET in command
            ):
                identity_selections += 1
                saw_identity_selection = True
                assert identity_selections == 1, (
                    "gh-image must have exactly one immutable identity selection"
                )
                downloaded_accepted_binary = True
                immutable_identity = False

            if "sha256sum" in command:
                checksum_comparison = re.search(
                    r"sha256sum\s+(?:--check|-c)(?:\s|$)", command
                )
                checksum_binding = re.search(
                    rf"{_GH_IMAGE_LINUX_AMD64_SHA256}\s+\*?"
                    rf"{re.escape(_GH_IMAGE_RELEASE_ASSET)}(?:\s|['\"]|$)",
                    command,
                )
                immutable_identity = bool(
                    downloaded_accepted_binary
                    and checksum_comparison
                    and checksum_binding
                    and "set -euo pipefail" in run
                    and "||" not in run
                )
                assert immutable_identity, (
                    "gh-image binary must use a fail-closed checksum comparison "
                    "bound to the accepted release artifact"
                )

        if "GH_SESSION_TOKEN" in step.get("env", {}):
            assert immutable_identity, (
                "GH_SESSION_TOKEN must not be exposed before gh-image has an "
                "immutable, fail-closed execution identity"
            )

    assert saw_identity_selection, "workflow must select a verified gh-image identity"


def _workflow_with_steps(*steps):
    return {"jobs": {"publish": {"steps": list(steps)}}}


def test_publish_workflow_verifies_immutable_gh_image_before_privileged_token():
    workflow = yaml.safe_load(_WORKFLOW_PATH.read_text(encoding="utf-8"))

    _assert_gh_image_identity_is_verified_before_privilege(workflow)


@pytest.mark.parametrize(
    "steps",
    [
        (
            {
                "run": (
                    "gh extension install drogers0/gh-image "
                    f"--pin {_GH_IMAGE_COMMIT}"
                )
            },
            {"env": {"GH_SESSION_TOKEN": "secret"}, "run": "publish"},
        ),
        (
            {
                "run": (
                    "set -euo pipefail\n"
                    "curl -LO https://example.invalid/gh-image/"
                    f"{_GH_IMAGE_RELEASE_REF}/{_GH_IMAGE_RELEASE_ASSET}"
                )
            },
            {
                "run": (
                    "set -euo pipefail\n"
                    f"echo '{_GH_IMAGE_LINUX_AMD64_SHA256}  "
                    f"{_GH_IMAGE_RELEASE_ASSET}' "
                    "| sha256sum -c -"
                )
            },
            {"env": {"GH_SESSION_TOKEN": "secret"}, "run": "publish"},
        ),
    ],
    ids=["source_commit", "verified_linux_amd64_binary"],
)
def test_gh_image_identity_audit_accepts_approved_workflow_shapes(steps):
    _assert_gh_image_identity_is_verified_before_privilege(
        _workflow_with_steps(*steps)
    )


@pytest.mark.parametrize(
    "steps",
    [
        (
            {"run": "gh extension install drogers0/gh-image --pin v1.2.0"},
            {"env": {"GH_SESSION_TOKEN": "secret"}, "run": "publish"},
        ),
        (
            {
                "run": (
                    "curl -LO https://example.invalid/gh-image/"
                    "v1.2.0/gh-image_1.2.0_linux_amd64.tar.gz"
                )
            },
            {"env": {"GH_SESSION_TOKEN": "secret"}, "run": "publish"},
        ),
        (
            {
                "run": (
                    "curl -LO https://example.invalid/gh-image/"
                    "v1.2.1/gh-image_1.2.1_linux_amd64.tar.gz"
                )
            },
            {
                "run": (
                    f"echo '{_GH_IMAGE_LINUX_AMD64_SHA256}  gh-image' "
                    "| sha256sum -c -"
                )
            },
            {"env": {"GH_SESSION_TOKEN": "secret"}, "run": "publish"},
        ),
        (
            {
                "run": (
                    "gh extension install drogers0/gh-image "
                    f"--pin {_GH_IMAGE_COMMIT}"
                )
            },
            {"run": "gh extension install drogers0/gh-image --pin v1.2.0"},
            {"env": {"GH_SESSION_TOKEN": "secret"}, "run": "publish"},
        ),
        (
            {"env": {"GH_SESSION_TOKEN": "secret"}, "run": "publish"},
            {
                "run": (
                    "gh extension install drogers0/gh-image "
                    f"--pin {_GH_IMAGE_COMMIT}"
                )
            },
        ),
        (
            {
                "run": (
                    "curl -LO https://example.invalid/gh-image/"
                    f"{_GH_IMAGE_RELEASE_REF}/{_GH_IMAGE_RELEASE_ASSET}"
                )
            },
            {
                "run": (
                    f"echo {_GH_IMAGE_LINUX_AMD64_SHA256}; echo sha256sum"
                )
            },
            {"env": {"GH_SESSION_TOKEN": "secret"}, "run": "publish"},
        ),
        (
            {
                "run": (
                    "gh extension install drogers0/gh-image "
                    f"--pin {_GH_IMAGE_COMMIT} || "
                    "gh extension install drogers0/gh-image --pin v1.2.0"
                )
            },
            {"env": {"GH_SESSION_TOKEN": "secret"}, "run": "publish"},
        ),
        (
            {"run": "gh image upload evidence.png"},
            {
                "run": (
                    "gh extension install drogers0/gh-image "
                    f"--pin {_GH_IMAGE_COMMIT}"
                )
            },
            {"env": {"GH_SESSION_TOKEN": "secret"}, "run": "publish"},
        ),
    ],
    ids=[
        "tag_only",
        "missing_verification",
        "digest_ref_mismatch",
        "later_downgrade",
        "verification_after_token_exposure",
        "no_op_digest",
        "same_step_tag_fallback",
        "execution_before_pin",
    ],
)
def test_gh_image_identity_audit_rejects_unsafe_workflow_shapes(steps):
    with pytest.raises(AssertionError):
        _assert_gh_image_identity_is_verified_before_privilege(
            _workflow_with_steps(*steps)
        )


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
