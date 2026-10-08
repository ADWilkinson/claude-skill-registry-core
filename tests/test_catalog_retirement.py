import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from catalog_retirement import LEDGER_PATH, load_ledger, validate_retirement  # noqa: E402
from check_community_intake_diff import (  # noqa: E402
    CommunityIntakeInput,
    validate_community_intake_diff,
)
from test_check_community_intake_diff import (  # noqa: E402
    CATALOG_PATH,
    git,
    init_repo,
    make_skill,
    render_catalog,
    write_catalog,
)


def record(row):
    return {
        "entry": row,
        "reason": "Maintainer confirmed source retirement after review",
        "decision_url": "https://github.com/majiayu000/claude-skill-registry-core/issues/1#issuecomment-1",
        "archive_policy": "retain",
    }


def ledger(repo, records):
    (repo / LEDGER_PATH).parent.mkdir(parents=True, exist_ok=True)
    (repo / LEDGER_PATH).write_text(json.dumps(records))
    git(repo, "add", LEDGER_PATH)
    git(repo, "commit", "-m", "record maintainer decision")


@pytest.mark.parametrize("removed", [(0,), (1,), (2,), (0, 2), (0, 1, 2)])
def test_exact_authorized_single_and_multirow_removal(removed):
    rows = [make_skill(x) for x in ("alpha", "beta", "gamma")]
    assert (
        validate_retirement(
            render_catalog(rows),
            render_catalog([row for i, row in enumerate(rows) if i not in removed]),
            [record(rows[i]) for i in removed],
        )
        == []
    )


@pytest.mark.parametrize(
    "mode",
    ["no_approval", "partial", "stale", "modified", "reorder", "append", "metadata", "format"],
)
def test_retirement_rejects_unapproved_or_mixed_changes(mode):
    rows = [make_skill(x) for x in ("alpha", "beta", "gamma", "delta")]
    approvals = [record(rows[0]), record(rows[1])]
    head = rows[2:]
    if mode == "no_approval":
        approvals = []
    elif mode == "partial":
        approvals.pop()
    elif mode == "stale":
        approvals[0] = record({**rows[0], "description": "old"})
    elif mode == "modified":
        head = [{**head[0], "description": "changed"}, head[1]]
    elif mode == "reorder":
        head.reverse()
    elif mode == "append":
        head.append(make_skill("new"))
    text = render_catalog(head)
    if mode == "metadata":
        text = text.replace('"Community Skills"', '"Other"')
    elif mode == "format":
        text = text.replace("    {", "      {")
    assert validate_retirement(render_catalog(rows), text, approvals)


@pytest.mark.parametrize(
    "value", ["{}", "null", '[{"x":1}]', '[{"entry":1}]', '[{"entry":1,"entry":2}]']
)
def test_malformed_ledger(value):
    with pytest.raises(ValueError):
        load_ledger(value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("archive_policy", "delete"),
        ("reason", " "),
        ("decision_url", "https://evil.test/issues/1"),
        ("decision_url", None),
        ("entry", {}),
    ],
)
def test_bad_record(field, value):
    item = record(make_skill("alpha"))
    item[field] = value
    with pytest.raises(ValueError):
        load_ledger(json.dumps([item]))


def test_duplicate_identity():
    item = record(make_skill("alpha"))
    with pytest.raises(ValueError):
        load_ledger(json.dumps([item, item]))


def test_trusted_base_authorization_and_stale_branch(tmp_path, monkeypatch):
    repo = init_repo(tmp_path)
    rows = [make_skill("alpha"), make_skill("beta")]
    write_catalog(repo, rows, "seed")
    git(repo, "checkout", "-b", "removal")
    write_catalog(repo, rows[:1], "propose removal")
    head = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "main")
    monkeypatch.chdir(repo)
    config = CommunityIntakeInput("main", head, CATALOG_PATH)
    assert validate_community_intake_diff(config)
    ledger(repo, [record(rows[1])])
    # Approval merged after the contributor branch forked can authorize that
    # exact proposal; it is read from base, not untrusted branch contents.
    assert validate_community_intake_diff(config) == []


def test_self_authorization_rejected(tmp_path, monkeypatch):
    repo = init_repo(tmp_path)
    rows = [make_skill("alpha"), make_skill("beta")]
    write_catalog(repo, rows, "seed")
    base = git(repo, "rev-parse", "HEAD")
    ledger(repo, [record(rows[1])])
    write_catalog(repo, rows[:1], "drop beta")
    monkeypatch.chdir(repo)
    assert (
        "separate PRs"
        in validate_community_intake_diff(CommunityIntakeInput(base, "HEAD", CATALOG_PATH))[0]
    )


def test_ledger_only_proposal_and_immutability(tmp_path, monkeypatch):
    repo = init_repo(tmp_path)
    rows = [make_skill("alpha"), make_skill("beta")]
    write_catalog(repo, rows, "seed")
    base = git(repo, "rev-parse", "HEAD")
    ledger(repo, [record(rows[1])])
    monkeypatch.chdir(repo)
    assert validate_community_intake_diff(CommunityIntakeInput(base, "HEAD", CATALOG_PATH)) == []
    base = git(repo, "rev-parse", "HEAD")
    ledger(repo, [])
    assert (
        "append-only"
        in validate_community_intake_diff(CommunityIntakeInput(base, "HEAD", CATALOG_PATH))[0]
    )


def test_malformed_present_base_fails_closed(tmp_path, monkeypatch):
    repo = init_repo(tmp_path)
    rows = [make_skill("alpha")]
    write_catalog(repo, rows, "seed")
    ledger(repo, {"invalid": True})
    base = git(repo, "rev-parse", "HEAD")
    write_catalog(repo, rows + [make_skill("beta")], "add")
    monkeypatch.chdir(repo)
    assert (
        "retirement validation failed"
        in validate_community_intake_diff(CommunityIntakeInput(base, "HEAD", CATALOG_PATH))[0]
    )


def test_removal_cannot_change_other_files(tmp_path, monkeypatch):
    repo = init_repo(tmp_path)
    rows = [make_skill("alpha"), make_skill("beta")]
    write_catalog(repo, rows, "seed")
    ledger(repo, [record(rows[1])])
    base = git(repo, "rev-parse", "HEAD")
    write_catalog(repo, rows[:1], "remove")
    (repo / "other").write_text("unrelated")
    git(repo, "add", "other")
    git(repo, "commit", "-m", "unrelated edit")
    monkeypatch.chdir(repo)
    assert (
        "only the community"
        in validate_community_intake_diff(CommunityIntakeInput(base, "HEAD", CATALOG_PATH))[0]
    )


@pytest.mark.parametrize(
    "path",
    [
        "/tree/main/issues/fake",
        "/issues/not-an-issue",
        "/pull/0",
        "/issues/1/extra",
        "/issues/1?x=1",
    ],
)
def test_decision_url_requires_exact_numeric_issue_or_pr(path):
    item = record(make_skill("alpha"))
    item["decision_url"] = "https://github.com/majiayu000/claude-skill-registry-core" + path
    with pytest.raises(ValueError):
        load_ledger(json.dumps([item]))


def test_changed_row_can_be_reauthorized_without_rewriting_history(tmp_path, monkeypatch):
    repo = init_repo(tmp_path)
    original = make_skill("alpha")
    updated = {
        **original,
        "path": "new-path",
        "source_url": "https://github.com/acme/alpha/blob/main/new-path/SKILL.md",
    }
    write_catalog(repo, [original], "seed")
    old_record = record(original)
    ledger(repo, [old_record])
    # An independently accepted metadata correction makes the prior decision stale.
    write_catalog(repo, [updated], "correct metadata")
    stale_base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-b", "stale-removal")
    write_catalog(repo, [], "remove updated row")
    monkeypatch.chdir(repo)
    assert validate_community_intake_diff(CommunityIntakeInput(stale_base, "HEAD", CATALOG_PATH))
    git(repo, "checkout", "main")
    # Keep identity identical to exercise metadata-only reauthorization as well.
    revised = {**updated, "description": "Corrected description"}
    ledger(repo, [old_record, record(updated)])
    write_catalog(repo, [revised], "metadata revision")
    base = git(repo, "rev-parse", "HEAD")
    ledger(repo, [old_record, record(updated), record(revised)])
    assert validate_community_intake_diff(CommunityIntakeInput(base, "HEAD", CATALOG_PATH)) == []
    base = git(repo, "rev-parse", "HEAD")
    write_catalog(repo, [], "authorized removal")
    assert validate_community_intake_diff(CommunityIntakeInput(base, "HEAD", CATALOG_PATH)) == []
