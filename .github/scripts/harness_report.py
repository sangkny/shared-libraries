#!/usr/bin/env python3
"""
Harness JSON → GitHub Issues (Phase 2 W10).

사용법:
  GITHUB_REPOSITORY=owner/repo \\
  GH_TOKEN 또는 GITHUB_TOKEN \\
  python3 harness_report.py reports/harness/all_latest.json

  또는 파이프(표준 입력):
  cat reports/harness/all_latest.json | python3 harness_report.py -

환경변수 SKIP_IF_NO_FAILED=1 이면 실패 항목이 없을 때 아무 작업 안 함.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


def _post_issue(repo: str, token: str, title: str, body: str) -> None:
    url = f"https://api.github.com/repos/{repo}/issues"
    payload = json.dumps({"title": title, "body": body}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        if resp.status not in {200, 201}:
            raise RuntimeError(f"GitHub Issues API {resp.status}")


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: harness_report.py <report.json | ->", file=sys.stderr)
        return 2
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    token = (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or "").strip()

    src = sys.argv[1].strip()
    if src in ("-", "stdin", "/dev/stdin"):
        raw = json.load(sys.stdin)
    else:
        report_path = Path(src)
        raw = json.loads(report_path.read_text(encoding="utf-8"))
    results = raw.get("results") or []

    failures = [
        r
        for r in results
        if isinstance(r, dict) and not r.get("passed", True)
    ]
    summary = raw.get("summary") or ""

    print(f"== Harness report: passed={raw.get('passed')} total={raw.get('total')} ==")

    gh_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if gh_summary:
        with open(gh_summary, "a", encoding="utf-8") as gh:
            gh.write(f"### Harness 결과\n")
            gh.write(f"- 통과율 {raw.get('pass_rate')}%\n")
            gh.write(f"- 실패 `{len(failures)}`건\n")

    skip_no_fail = os.environ.get("SKIP_IF_NO_FAILED", "")

    if not failures:
        if skip_no_fail:
            print("실패 없음 — Issue 미생성")
        return 0

    if not repo or not token:
        print("GITHUB_REPOSITORY 또는 토큰 없음 — Issue 생략", file=sys.stderr)
        return 0

    for r in failures:
        name = r.get("scenario_name", "?")
        dom = r.get("domain", "?")
        title = f"[Harness FAIL] {name} — {dom}"
        body = "```json\n"
        body += json.dumps(r, indent=2, ensure_ascii=False)[:8000]
        body += "\n```\n"
        if summary:
            body += f"\n_Report summary_: {summary}\n"
        try:
            _post_issue(repo, token, title, body)
            print(f"Issue 생성: {title}")
        except urllib.error.HTTPError as e:
            print(f"Issue 생성 실패 {name}: HTTP {e.code} {e.read()[:400]}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
