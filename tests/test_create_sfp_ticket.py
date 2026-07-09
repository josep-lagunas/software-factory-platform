"""Tests for tools/create_sfp_ticket.py — the SFP-194 single-ticket creator.

Covers TC-SFP194-001..014 + edge cases. Network is mocked at the cjt helper
boundary (create_issue / jira_api); an autouse fixture additionally patches
urllib.request.urlopen to raise, so a forgotten mock cannot escape to the
network (TC-013). The REAL cjt.markdown_to_adf is used as an oracle
(deep-equal); it is never reimplemented in the assertions.
"""
import sys
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# --- import the helper + its dependency directly from tools/ ----------------
ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

import create_jira_tickets as cjt  # noqa: E402
import create_sfp_ticket  # noqa: E402

SKILL_MD = ROOT / ".claude" / "skills" / "create-sfp-ticket" / "SKILL.md"
BOT_EMOJI = "\U0001F916"      # 🤖
HUMAN_EMOJI = "\U0001F464"    # 👤


# ============================================================
# Autouse: pin cjt env globals + guarantee NO network (TC-013)
# ============================================================

@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Pin the cjt env globals (so _require_env passes) and make any real
    urllib.request.urlopen call blow up — tests must mock at the cjt boundary."""
    monkeypatch.setattr(cjt, "JIRA_SITE", "https://example.atlassian.net")
    monkeypatch.setattr(cjt, "JIRA_EMAIL", "bot@example.com")
    monkeypatch.setattr(cjt, "JIRA_API_TOKEN", "token")
    monkeypatch.setattr(cjt, "JIRA_PROJECT", "SFP")

    def _boom(*_args, **_kwargs):
        raise AssertionError(
            "network call detected — mock cjt.jira_api / cjt.create_issue instead")
    monkeypatch.setattr(urllib.request, "urlopen", _boom)


def _write_body(tmp_path, text="**Context:**\n- one\n- two\n"):
    """Write a small markdown body to a tmp file; return its path (str)."""
    p = tmp_path / "body.md"
    p.write_text(text)
    return str(p)


def _patch_create_issue(monkeypatch, key="SFP-194"):
    created = MagicMock(return_value=key)
    monkeypatch.setattr(cjt, "create_issue", created)
    return created


def _patch_jira_api(monkeypatch, get_payload):
    """Return a Mock whose GET -> get_payload, PUT -> {ok:True,status:204}."""
    def _fake(method, endpoint, data=None):
        if method == "GET":
            return get_payload
        if method == "PUT":
            return {"ok": True, "status": 204}
        raise AssertionError("unexpected method " + str(method))
    api = MagicMock(side_effect=_fake)
    monkeypatch.setattr(cjt, "jira_api", api)
    return api


# ============================================================
# CREATE — TC-001 .. TC-005
# ============================================================

@pytest.mark.parametrize("executor, codepoint", [
    ("bot", BOT_EMOJI),
    ("human", HUMAN_EMOJI),
])
def test_TC001_summary_emoji(tmp_path, monkeypatch, executor, codepoint):
    """TC-001: summary carries the literal executor-emoji codepoint."""
    created = _patch_create_issue(monkeypatch)
    create_sfp_ticket.main([
        "--title", "Title", "--area", "SPEC",
        "--executor", executor, "--phase", "platform",
        "--description", _write_body(tmp_path),
    ])
    summary = created.call_args.args[0]["summary"]
    assert codepoint in summary          # literal codepoint substring


@pytest.mark.parametrize("executor, present, absent", [
    ("bot", "ai-agent", "manual"),
    ("human", "manual", "ai-agent"),
])
def test_TC002_executor_label(tmp_path, monkeypatch, executor, present, absent):
    """TC-002: executor -> exactly one of ai-agent / manual in labels."""
    created = _patch_create_issue(monkeypatch)
    create_sfp_ticket.main([
        "--title", "T", "--area", "SPEC",
        "--executor", executor, "--phase", "platform",
        "--description", _write_body(tmp_path),
    ])
    labels = created.call_args.args[0]["labels"]
    assert present in labels
    assert absent not in labels


def test_TC003_extras_sanitized_deduped(tmp_path, monkeypatch):
    """TC-003: extras sanitized (spaces -> '-') + deduped, order preserved.
    NOTE: do NOT assert case-folding (sanitize_label only handles spaces)."""
    created = _patch_create_issue(monkeypatch)
    create_sfp_ticket.main([
        "--title", "T", "--area", "spec area",
        "--executor", "bot", "--phase", "platform",
        "--labels", "extra one,extra-one,Two,Two",
        "--description", _write_body(tmp_path),
    ])
    labels = created.call_args.args[0]["labels"]
    assert "extra-one" in labels          # "extra one" -> "extra-one"
    assert labels.count("extra-one") == 1  # deduped with the explicit one
    assert "Two" in labels
    assert labels.count("Two") == 1        # deduped


def test_TC004_area_in_summary_and_labels(tmp_path, monkeypatch):
    """TC-004: area appears in labels AND as the byte-identical [AREA] prefix."""
    created = _patch_create_issue(monkeypatch)
    create_sfp_ticket.main([
        "--title", "T", "--area", "SPECIAL",
        "--executor", "bot", "--phase", "platform",
        "--description", _write_body(tmp_path),
    ])
    fields = created.call_args.args[0]
    assert fields["summary"] == "[SPECIAL] {} T".format(BOT_EMOJI)  # byte-identical
    assert "SPECIAL" in fields["labels"]


def test_TC005_create_payload_deep_equal(tmp_path, monkeypatch, capsys):
    """TC-005: full create payload deep-equals the expected dict; printed key
    == create_issue return; create_issue called exactly once."""
    body_text = "**Context:**\nhello\n"
    created = _patch_create_issue(monkeypatch, key="SFP-194")
    create_sfp_ticket.main([
        "--title", "Title", "--area", "SPEC",
        "--executor", "bot", "--phase", "platform",
        "--labels", "x,y",
        "--description", _write_body(tmp_path, body_text),
    ])
    expected = {
        "project": {"key": "SFP"},
        "issuetype": {"name": "Task"},
        "summary": "[SPEC] {} Title".format(BOT_EMOJI),
        "description": cjt.markdown_to_adf(body_text),   # REAL oracle
        "labels": ["platform", "ai-agent", "SPEC", "x", "y"],
    }
    assert created.call_count == 1
    assert created.call_args.args[0] == expected           # deep equal
    assert capsys.readouterr().out.strip() == "SFP-194"     # printed key


# ============================================================
# UPDATE — TC-006 .. TC-010
# ============================================================

def test_TC006_update_existing_str(tmp_path, monkeypatch, capsys):
    """TC-006: existing description as a str -> re-converted via markdown_to_adf;
    GET then PUT observed; PUT carries summary + description + labels."""
    api = _patch_jira_api(monkeypatch, {
        "fields": {"description": "**Ctx:** markdown str", "labels": ["keep"]}})
    create_sfp_ticket.main([
        "--update", "--key", "SFP-1", "--summary", "[SPEC] {} T".format(BOT_EMOJI),
        "--description-source", "existing", "--labels-mode", "merge",
    ])
    methods = [c.args[0] for c in api.call_args_list]
    assert methods == ["GET", "PUT"]                       # GET then PUT
    put_fields = api.call_args_list[1].args[2]["fields"]
    assert put_fields["summary"] == "[SPEC] {} T".format(BOT_EMOJI)
    assert put_fields["description"] == cjt.markdown_to_adf("**Ctx:** markdown str")
    assert put_fields["labels"] == ["keep"]                 # merge, no extras
    assert capsys.readouterr().out.strip() == "SFP-1"


def test_TC007_update_existing_adf_passthrough(tmp_path, monkeypatch):
    """TC-007 (R1, highest-risk): existing description already an ADF dict ->
    passthrough verbatim; markdown_to_adf NOT called."""
    adf = {"type": "doc", "version": 1,
           "content": [{"type": "paragraph",
                        "content": [{"type": "text", "text": "x"}]}]}
    api = _patch_jira_api(monkeypatch, {"fields": {"description": adf, "labels": []}})
    m2a = MagicMock(wraps=cjt.markdown_to_adf)
    monkeypatch.setattr(cjt, "markdown_to_adf", m2a)
    create_sfp_ticket.main([
        "--update", "--key", "SFP-1", "--summary", "S",
        "--description-source", "existing", "--labels-mode", "replace",
    ])
    put_desc = api.call_args_list[1].args[2]["fields"]["description"]
    assert put_desc is adf           # passthrough verbatim (same object)
    assert m2a.call_count == 0       # markdown_to_adf NOT called (R1 holds)


def test_TC008_merge_dedupe(tmp_path, monkeypatch):
    """TC-008: merge -> existing ∪ parsed, deduped, existing order first.
    Use DISTINCT sets so the union is observable."""
    api = _patch_jira_api(monkeypatch, {"fields": {"description": "", "labels": ["a", "b"]}})
    create_sfp_ticket.main([
        "--update", "--key", "SFP-1", "--summary", "S",
        "--description-source", "existing", "--labels-mode", "merge",
        "--labels", "c,d,b",
    ])
    put_labels = api.call_args_list[1].args[2]["fields"]["labels"]
    assert put_labels == ["a", "b", "c", "d"]   # existing first, b deduped


def test_TC009_replace(tmp_path, monkeypatch):
    """TC-009: replace -> only sanitized parsed labels; existing dropped."""
    api = _patch_jira_api(monkeypatch, {"fields": {"description": "", "labels": ["a", "b"]}})
    create_sfp_ticket.main([
        "--update", "--key", "SFP-1", "--summary", "S",
        "--description-source", "existing", "--labels-mode", "replace",
        "--labels", "c d,e",
    ])
    put_labels = api.call_args_list[1].args[2]["fields"]["labels"]
    assert put_labels == ["c-d", "e"]           # sanitized; existing dropped


def test_TC010_description_source_file(tmp_path, monkeypatch):
    """TC-010: description-source file -> read --description-file -> markdown_to_adf."""
    f = tmp_path / "desc.md"
    f.write_text("# Body from file\n- a\n")
    api = _patch_jira_api(monkeypatch, {"fields": {"description": "old", "labels": []}})
    create_sfp_ticket.main([
        "--update", "--key", "SFP-1", "--summary", "S",
        "--description-source", "file", "--description-file", str(f),
        "--labels-mode", "merge",
    ])
    put_desc = api.call_args_list[1].args[2]["fields"]["description"]
    assert put_desc == cjt.markdown_to_adf("# Body from file\n- a\n")


# ============================================================
# CLI GUARDS — TC-011, TC-012, TC-013
# ============================================================

@pytest.mark.parametrize("omit", ["--title", "--area", "--executor", "--description"])
def test_TC011_create_missing_required(tmp_path, monkeypatch, omit):
    """TC-011: create missing a required flag -> SystemExit."""
    _patch_create_issue(monkeypatch)
    pairs = {
        "--title": ["--title", "T"],
        "--area": ["--area", "SPEC"],
        "--executor": ["--executor", "bot"],
        "--phase": ["--phase", "platform"],
        "--description": ["--description", _write_body(tmp_path)],
    }
    argv = []
    for flag, val in pairs.items():
        if flag != omit:
            argv.extend(val)
    with pytest.raises(SystemExit):
        create_sfp_ticket.main(argv)


@pytest.mark.parametrize("argv_extra", [
    # missing --key
    ["--summary", "S", "--description-source", "existing", "--labels-mode", "merge"],
    # missing --summary
    ["--key", "SFP-1", "--description-source", "existing", "--labels-mode", "merge"],
    # missing --description-source
    ["--key", "SFP-1", "--summary", "S", "--labels-mode", "merge"],
    # invalid --description-source choice (argparse exit 2)
    ["--key", "SFP-1", "--summary", "S", "--description-source", "bogus", "--labels-mode", "merge"],
    # invalid --labels-mode choice (argparse exit 2)
    ["--key", "SFP-1", "--summary", "S", "--description-source", "existing", "--labels-mode", "bogus"],
])
def test_TC012_update_missing_or_invalid(monkeypatch, argv_extra):
    """TC-012: update missing/invalid flags -> SystemExit (manual or argparse)."""
    monkeypatch.setattr(cjt, "jira_api", MagicMock())
    with pytest.raises(SystemExit):
        create_sfp_ticket.main(["--update"] + argv_extra)


def test_TC013_no_network_guard():
    """TC-013: the autouse fixture makes urllib.request.urlopen raise — a
    forgotten mock cannot escape to the network."""
    with pytest.raises(AssertionError):
        urllib.request.urlopen("http://example.com")


# ============================================================
# CONTRACT — TC-014
# ============================================================

def test_TC014_skill_contract():
    """TC-014: SKILL.md documents the template + emoji/executor/label rules +
    merge/replace."""
    text = SKILL_MD.read_text()
    assert "### SFP-N [AREA]" in text                  # heading template
    assert BOT_EMOJI in text and HUMAN_EMOJI in text   # emoji map
    assert "ai-agent" in text and "manual" in text     # executor -> label
    assert "manual-core" in text and "platform" in text  # phase labels
    assert "merge" in text and "replace" in text        # label modes
    assert "ID-070" in text                             # body template ref


# ============================================================
# EDGE CASES
# ============================================================

def test_edge_empty_description(tmp_path, monkeypatch):
    """Edge: empty description body -> ADF doc with content: []."""
    created = _patch_create_issue(monkeypatch)
    create_sfp_ticket.main([
        "--title", "T", "--area", "A", "--executor", "bot",
        "--phase", "platform", "--description", _write_body(tmp_path, ""),
    ])
    desc = created.call_args.args[0]["description"]
    assert desc == {"type": "doc", "version": 1, "content": []}


def test_edge_labels_csv_empty_entries(tmp_path, monkeypatch):
    """Edge: labels CSV with empty / whitespace entries -> dropped."""
    created = _patch_create_issue(monkeypatch)
    create_sfp_ticket.main([
        "--title", "T", "--area", "A", "--executor", "bot", "--phase", "platform",
        "--labels", ",  ,x,",
        "--description", _write_body(tmp_path),
    ])
    labels = created.call_args.args[0]["labels"]
    assert labels == ["platform", "ai-agent", "A", "x"]


def test_edge_update_none_description(tmp_path, monkeypatch):
    """Edge: None description from GET (source=existing) -> empty ADF doc."""
    api = _patch_jira_api(monkeypatch, {"fields": {"description": None, "labels": []}})
    create_sfp_ticket.main([
        "--update", "--key", "SFP-1", "--summary", "S",
        "--description-source", "existing", "--labels-mode", "merge",
    ])
    put_desc = api.call_args_list[1].args[2]["fields"]["description"]
    assert put_desc == {"type": "doc", "version": 1, "content": []}


def test_edge_create_description_stdin(tmp_path, monkeypatch):
    """Edge: --description '-' reads the body from stdin."""
    monkeypatch.setattr("sys.stdin", MagicMock(read=lambda: "body text\n"))
    created = _patch_create_issue(monkeypatch)
    create_sfp_ticket.main([
        "--title", "T", "--area", "A", "--executor", "bot",
        "--phase", "platform", "--description", "-",
    ])
    desc = created.call_args.args[0]["description"]
    assert desc == cjt.markdown_to_adf("body text\n")


def test_edge_create_issue_returns_none(tmp_path, monkeypatch):
    """Edge: create_issue returning None -> SystemExit (covers the else branch)."""
    monkeypatch.setattr(cjt, "create_issue", MagicMock(return_value=None))
    with pytest.raises(SystemExit):
        create_sfp_ticket.main([
            "--title", "T", "--area", "A", "--executor", "bot",
            "--phase", "platform", "--description", _write_body(tmp_path),
        ])


def test_edge_update_get_returns_none(tmp_path, monkeypatch):
    """Edge: GET returns None (fetch failed) -> SystemExit."""
    monkeypatch.setattr(cjt, "jira_api", MagicMock(return_value=None))
    with pytest.raises(SystemExit):
        create_sfp_ticket.main([
            "--update", "--key", "SFP-1", "--summary", "S",
            "--description-source", "existing", "--labels-mode", "merge",
        ])


def test_edge_file_source_without_file(tmp_path, monkeypatch):
    """Edge: --description-source file without --description-file -> SystemExit."""
    api = _patch_jira_api(monkeypatch, {"fields": {"description": "", "labels": []}})
    with pytest.raises(SystemExit):
        create_sfp_ticket.main([
            "--update", "--key", "SFP-1", "--summary", "S",
            "--description-source", "file", "--labels-mode", "merge",
        ])
    # GET happened, but no PUT (we exited before it)
    assert [c.args[0] for c in api.call_args_list] == ["GET"]


def test_edge_require_env_missing(tmp_path, monkeypatch):
    """Edge: missing JIRA env -> _require_env exits before any network call
    (covers the env-fail branch)."""
    monkeypatch.setattr(cjt, "JIRA_SITE", "")
    monkeypatch.setattr(cjt, "JIRA_EMAIL", "")
    monkeypatch.setattr(cjt, "JIRA_API_TOKEN", "")
    _patch_create_issue(monkeypatch)
    with pytest.raises(SystemExit):
        create_sfp_ticket.main([
            "--title", "T", "--area", "A", "--executor", "bot",
            "--description", _write_body(tmp_path),
        ])
