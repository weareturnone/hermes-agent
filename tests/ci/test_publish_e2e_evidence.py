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
_GH_IMAGE_SOURCE_INSTALL = (
    "gh extension install drogers0/gh-image --pin " f"{_GH_IMAGE_COMMIT}"
)
_GH_IMAGE_BINARY_CHECKSUM = (
    "set -euo pipefail\n"
    f"echo '{_GH_IMAGE_LINUX_AMD64_SHA256}  {_GH_IMAGE_RELEASE_ASSET}' "
    "| sha256sum -c -"
)


def _is_approved_binary_download(run):
    lines = run.splitlines()
    if len(lines) != 2 or lines[0] != "set -euo pipefail":
        return False

    return bool(
        re.fullmatch(
            rf"curl -LO https://[A-Za-z0-9.-]+(?:/[A-Za-z0-9._~-]+)*/"
            rf"{re.escape(_GH_IMAGE_RELEASE_REF)}/"
            rf"{re.escape(_GH_IMAGE_RELEASE_ASSET)}",
            lines[1],
        )
    )


def _is_source_install_attempt(run):
    return bool(
        re.fullmatch(
            r"gh extension install drogers0/gh-image --pin [^\s]+",
            run,
        )
    )


def _is_binary_download_attempt(run):
    lines = run.splitlines()
    if len(lines) != 2 or lines[0] != "set -euo pipefail":
        return False

    return bool(
        re.fullmatch(
            r"curl -LO https://[A-Za-z0-9.-]+(?:/[A-Za-z0-9._~-]+)*/"
            r"gh-image[^\s/]*",
            lines[1],
        )
    )


def _assert_gh_image_identity_is_verified_before_privilege(workflow):
    """Audit parsed workflow steps without installing or executing gh-image."""
    job = workflow["jobs"]["publish"]
    assert "GH_SESSION_TOKEN" not in workflow.get("env", {})
    assert "GH_SESSION_TOKEN" not in job.get("env", {})

    immutable_identity = False
    identity_selections = 0
    binary_download_pending = False

    for step in job["steps"]:
        run_value = step.get("run")
        run = "" if run_value is None else str(run_value)

        if "GH_SESSION_TOKEN" in step.get("env", {}):
            assert immutable_identity, (
                "GH_SESSION_TOKEN must not be exposed before gh-image has an "
                "immutable, fail-closed execution identity"
            )

        if binary_download_pending:
            assert run == _GH_IMAGE_BINARY_CHECKSUM, (
                "gh-image binary selection must immediately use the exact "
                "fail-closed checksum step"
            )
            binary_download_pending = False
            immutable_identity = True
            continue

        if not immutable_identity:
            if run == _GH_IMAGE_SOURCE_INSTALL:
                identity_selections += 1
                immutable_identity = True
                continue

            if _is_approved_binary_download(run):
                identity_selections += 1
                binary_download_pending = True
                continue

            assert not run, (
                "every non-empty run step before gh-image identity selection "
                "must exactly match one reviewed immutable install shape"
            )
            continue

        assert not (
            _is_source_install_attempt(run) or _is_binary_download_attempt(run)
        ), "gh-image must have exactly one immutable identity selection"

    assert not binary_download_pending, (
        "gh-image binary selection is incomplete without exact checksum verification"
    )
    assert identity_selections == 1, (
        "workflow must select exactly one verified gh-image identity"
    )


def _workflow_with_steps(*steps):
    return {"jobs": {"publish": {"steps": list(steps)}}}


def test_publish_workflow_verifies_immutable_gh_image_before_privileged_token():
    workflow = yaml.safe_load(_WORKFLOW_PATH.read_text(encoding="utf-8"))

    _assert_gh_image_identity_is_verified_before_privilege(workflow)


@pytest.mark.parametrize(
    "steps",
    [
        (
            {"uses": "actions/checkout@immutable"},
            {},
            {"run": ""},
            {"run": _GH_IMAGE_SOURCE_INSTALL},
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
            {"run": _GH_IMAGE_BINARY_CHECKSUM},
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
            {"run": _GH_IMAGE_SOURCE_INSTALL},
            {"run": "gh extension install drogers0/gh-image --pin v1.2.0"},
            {"env": {"GH_SESSION_TOKEN": "secret"}, "run": "publish"},
        ),
        (
            {"env": {"GH_SESSION_TOKEN": "secret"}, "run": "publish"},
            {"run": _GH_IMAGE_SOURCE_INSTALL},
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
                    f"{_GH_IMAGE_SOURCE_INSTALL} || "
                    "gh extension install drogers0/gh-image --pin v1.2.0"
                )
            },
            {"env": {"GH_SESSION_TOKEN": "secret"}, "run": "publish"},
        ),
        (
            {"run": "gh image upload evidence.png"},
            {"run": _GH_IMAGE_SOURCE_INSTALL},
            {"env": {"GH_SESSION_TOKEN": "secret"}, "run": "publish"},
        ),
        (
            {"run": "command gh image upload evidence.png"},
            {"run": _GH_IMAGE_SOURCE_INSTALL},
            {"env": {"GH_SESSION_TOKEN": "secret"}, "run": "publish"},
        ),
        (
            {"run": "env gh image upload evidence.png"},
            {"run": _GH_IMAGE_SOURCE_INSTALL},
            {"env": {"GH_SESSION_TOKEN": "secret"}, "run": "publish"},
        ),
        (
            {"run": "/usr/bin/gh image upload evidence.png"},
            {"run": _GH_IMAGE_SOURCE_INSTALL},
            {"env": {"GH_SESSION_TOKEN": "secret"}, "run": "publish"},
        ),
        (
            {"run": "/tmp/gh-image upload evidence.png"},
            {"run": _GH_IMAGE_SOURCE_INSTALL},
            {"env": {"GH_SESSION_TOKEN": "secret"}, "run": "publish"},
        ),
        (
            {"run": "printf x | gh image upload evidence.png"},
            {"run": _GH_IMAGE_SOURCE_INSTALL},
            {"env": {"GH_SESSION_TOKEN": "secret"}, "run": "publish"},
        ),
        (
            {"run": "(gh image upload evidence.png)"},
            {"run": _GH_IMAGE_SOURCE_INSTALL},
            {"env": {"GH_SESSION_TOKEN": "secret"}, "run": "publish"},
        ),
        (
            {"run": "result=$(gh image upload evidence.png)"},
            {"run": _GH_IMAGE_SOURCE_INSTALL},
            {"env": {"GH_SESSION_TOKEN": "secret"}, "run": "publish"},
        ),
        (
            {"run": "g'h' image upload evidence.png"},
            {"run": _GH_IMAGE_SOURCE_INSTALL},
            {"env": {"GH_SESSION_TOKEN": "secret"}, "run": "publish"},
        ),
        (
            {"run": "printf 'prepare publisher'"},
            {"run": _GH_IMAGE_SOURCE_INSTALL},
            {"env": {"GH_SESSION_TOKEN": "secret"}, "run": "publish"},
        ),
        (
            {
                "run": (
                    f"{_GH_IMAGE_SOURCE_INSTALL}; "
                    "command gh image upload evidence.png"
                )
            },
            {"env": {"GH_SESSION_TOKEN": "secret"}, "run": "publish"},
        ),
        (
            {
                "run": (
                    f"{_GH_IMAGE_SOURCE_INSTALL}\n"
                    "if ! true; then command gh image upload evidence.png; fi"
                )
            },
            {"env": {"GH_SESSION_TOKEN": "secret"}, "run": "publish"},
        ),
        (
            {"run": _GH_IMAGE_SOURCE_INSTALL},
            {"run": _GH_IMAGE_SOURCE_INSTALL},
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
        "command_wrapper_before_pin",
        "env_wrapper_before_pin",
        "path_qualified_gh_before_pin",
        "path_qualified_gh_image_before_pin",
        "pipeline_before_pin",
        "subshell_before_pin",
        "command_substitution_before_pin",
        "obfuscated_preselection_run",
        "unrelated_nonempty_run_before_selection",
        "same_step_execution_after_install",
        "conditional_fallback_after_install",
        "duplicate_immutable_selection",
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
