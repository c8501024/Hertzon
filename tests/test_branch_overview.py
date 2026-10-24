"""tools/branch_overview.py: sorting merged pull requests into unreleased and released, and the page."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import branch_overview as B  # noqa: E402


def _commit(sha, msg, parents=1):
    return {"sha": sha, "html_url": f"u/{sha}", "parents": [{}] * parents, "author": {"login": "a"},
            "commit": {"message": msg, "author": {"name": "a", "date": "2026-10-23T10:00:00Z"}}}


def _pr(number, merge_sha, merged_at="2026-10-23T10:00:00Z", **extra):
    pr = {"number": number, "title": f"PR {number}", "html_url": f"p/{number}", "user": {"login": "a"},
          "labels": [], "merge_commit_sha": merge_sha, "merged_at": merged_at, "created_at": merged_at,
          "head": {"ref": "feature", "sha": "h"}, "base": {"ref": "dev"}}
    pr.update(extra)
    return pr


def _data(**overrides):
    data = {
        "repo": "o/r", "base": "main", "head": "dev",
        "compare": {"ahead_by": 4, "commits": [_commit("c1", "Work"), _commit("m1", "Merge pull request #2", 2),
                                                _commit("d1", "Direct fix"),
                                                _commit("m2", "Merge remote-tracking branch 'origin/main' into dev", 2)]},
        "only_on_base": [_commit("r1", "Merge pull request #9 from o/dev", 2)],
        "merged": [_pr(1, "old"), _pr(2, "m1", _commit_shas={"c1"}, _commit_count=1)],
        "open": [], "release": None, "version_base": "1.0", "version_head": "1.0",
        "fetched": "2026-10-24T08:00:00+00:00",
    }
    data.update(overrides)
    return data


def test_merged_prs_split_by_merge_commit_on_dev():
    m = B.build_model(_data())
    assert [p["number"] for p in m["pending"]] == [2]
    assert [p["number"] for p in m["released"]] == [1]


def test_direct_commits_exclude_pr_commits_and_merges():
    m = B.build_model(_data())
    assert [c["sha"] for c in m["direct"]] == ["d1"]


def test_release_merge_on_main_is_no_warning():
    m = B.build_model(_data())
    assert m["only_on_base"] == []
    page = B.render(m)
    assert "Hotfix" not in page
    hotfix = B.build_model(_data(only_on_base=[_commit("x", "Hotfix")]))
    assert "Hotfix" in B.render(hotfix)


def test_page_shows_blocks_and_version_hint():
    open_pr = _pr(3, None, merged_at=None, draft=True, checks="failure", mergeable_state="dirty",
                  additions=5, deletions=1, changed_files=2)
    page = B.render(B.build_model(_data(open=[open_pr])))
    assert "PR 3" in page and "Merge-Konflikt" in page and "Checks rot" in page and "Entwurf" in page
    assert "Versionsnummer erhöhen" in page
    assert page.count('class="block pending"') == 1 and page.count('class="block direct"') == 1


def test_title_is_escaped():
    pr = _pr(2, "m1", _commit_shas={"c1"})
    pr["title"] = "<script>x</script>"
    page = B.render(B.build_model(_data(merged=[pr])))
    assert "<script>x" not in page
