# shared-libraries/harness/cli.py
"""
Harness CLI — 커맨드라인 인터페이스

사용법 (컨테이너 내부):
    python -m harness smoke
    python -m harness domain software
    python -m harness domain medical --save
    python -m harness tags smoke safety
    python -m harness all
    python -m harness compare
    python -m harness compare --baseline /app/reports/harness/baseline.json

래퍼 스크립트를 통한 사용법 (WSL/PowerShell):
    ./run_harness.sh smoke
    ./run_harness.sh domain software
    ./run_harness.ps1 compare
"""
import argparse
import asyncio
import logging
import sys
from pathlib import Path

log = logging.getLogger("harness.cli")

# 기본 리포트 저장 경로 (컨테이너 내부 — 볼륨 마운트로 호스트와 공유)
DEFAULT_REPORT_DIR = "/app/reports/harness"


# ════════════════════════════════════════════════════════════
# 서브커맨드 핸들러
# ════════════════════════════════════════════════════════════

async def _cmd_smoke(args: argparse.Namespace) -> int:
    """smoke: 스모크 시나리오 실행"""
    from harness.runner import HarnessRunner
    from harness.reporter import HarnessReporter

    print("\n[Harness CLI] 스모크 테스트 시작...")
    runner = HarnessRunner()
    report = await runner.run_smoke()
    report.print_report()

    if args.save:
        reporter = HarnessReporter(output_dir=args.output_dir)
        md_path   = reporter.save_markdown(report, "smoke_latest.md")
        json_path = reporter.save_json(report, "smoke_latest.json")
        print(f"  리포트 저장됨: {md_path}")
        print(f"               {json_path}")

    return _exit_code(report.pass_rate, args.min_pass_rate)


async def _cmd_domain(args: argparse.Namespace) -> int:
    """domain: 도메인별 시나리오 실행"""
    from harness.runner import HarnessRunner
    from harness.reporter import HarnessReporter
    from ontology.base import OntologyDomain

    domain_map = {
        "software":   OntologyDomain.SOFTWARE,
        "medical":    OntologyDomain.MEDICAL,
        "business":   OntologyDomain.BUSINESS,
        "knowledge":  OntologyDomain.KNOWLEDGE,
        "cost":       OntologyDomain.COST,
        "polyglot":   OntologyDomain.POLYGLOT,
        "svg":        OntologyDomain.SVG,
    }
    domain = domain_map[args.name]

    print(f"\n[Harness CLI] {args.name.upper()} 도메인 테스트 시작...")
    runner = HarnessRunner()
    report = await runner.run_domain(domain)
    report.print_report()

    if args.save:
        reporter  = HarnessReporter(output_dir=args.output_dir)
        md_path   = reporter.save_markdown(report, f"domain_{args.name}_latest.md")
        json_path = reporter.save_json(report, f"domain_{args.name}_latest.json")
        print(f"  리포트 저장됨: {md_path}")
        print(f"               {json_path}")

    return _exit_code(report.pass_rate, args.min_pass_rate)


async def _cmd_tags(args: argparse.Namespace) -> int:
    """tags: 태그별 시나리오 실행"""
    from harness.runner import HarnessRunner
    from harness.reporter import HarnessReporter

    tags_str = ", ".join(args.tags)
    print(f"\n[Harness CLI] 태그 [{tags_str}] 테스트 시작...")
    runner = HarnessRunner()
    report = await runner.run_tags(args.tags)
    report.print_report()

    if args.save:
        safe_tags = "_".join(args.tags)
        reporter  = HarnessReporter(output_dir=args.output_dir)
        md_path   = reporter.save_markdown(report, f"tags_{safe_tags}_latest.md")
        json_path = reporter.save_json(report, f"tags_{safe_tags}_latest.json")
        print(f"  리포트 저장됨: {md_path}")
        print(f"               {json_path}")

    return _exit_code(report.pass_rate, args.min_pass_rate)


async def _cmd_all(args: argparse.Namespace) -> int:
    """all: 전체 시나리오 실행"""
    from harness.runner import HarnessRunner
    from harness.reporter import HarnessReporter

    print("\n[Harness CLI] 전체 시나리오 실행 시작...")
    runner = HarnessRunner()
    report = await runner.run_all()
    report.print_report()

    # Phase 1 회귀 추적: 전체 스위트는 매번 `all_latest.*` 를 갱신한다.
    reporter = HarnessReporter(output_dir=args.output_dir)
    md_path = reporter.save_markdown(report, "all_latest.md")
    json_path = reporter.save_json(report, "all_latest.json")
    print(f"  리포트 저장됨: {md_path}")
    print(f"               {json_path}")

    return _exit_code(report.pass_rate, args.min_pass_rate)


async def _cmd_decision(args: argparse.Namespace) -> int:
    """decision: 4-에이전트 A/B harness"""
    from harness.decision_runner import run_decision_harness

    print("\n[Harness CLI] DECISION (4-agent) 시작...")
    report = await run_decision_harness()
    report.print_report()
    if args.save:
        from harness.reporter import HarnessReporter

        reporter = HarnessReporter(output_dir=args.output_dir)
        path = reporter.output_dir / "decision_latest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        import json

        path.write_text(
            json.dumps(
                {
                    "agreement_rate": report.agreement_rate,
                    "pass_rate": report.pass_rate,
                    "results": [r.__dict__ for r in report.results],
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        print(f"  저장: {path}")
    return _exit_code(report.pass_rate * 100, args.min_pass_rate)


async def _cmd_compare(args: argparse.Namespace) -> int:
    """compare: 현재 실행 결과를 기준선과 비교"""
    from harness.runner import HarnessRunner
    from harness.reporter import HarnessReporter

    reporter = HarnessReporter(output_dir=args.output_dir)
    runner   = HarnessRunner()

    # ── 현재 결과 실행 ──────────────────────────────────────
    suite = getattr(args, "suite", "smoke")
    print(f"\n[Harness CLI] compare 모드 — suite={suite}")

    if suite == "smoke":
        print("  현재 스모크 테스트 실행 중...")
        report = await runner.run_smoke()
    elif suite == "all":
        print("  현재 전체 테스트 실행 중...")
        report = await runner.run_all()
    else:
        from ontology.base import OntologyDomain
        domain_map = {
            "software":   OntologyDomain.SOFTWARE,
            "medical":    OntologyDomain.MEDICAL,
            "business":   OntologyDomain.BUSINESS,
            "knowledge":  OntologyDomain.KNOWLEDGE,
            "cost":       OntologyDomain.COST,
            "polyglot":   OntologyDomain.POLYGLOT,
            "svg":        OntologyDomain.SVG,
        }
        domain = domain_map.get(suite)
        if domain is None:
            print(f"  ❌ 알 수 없는 suite: {suite}", file=sys.stderr)
            return 1
        print(f"  현재 {suite.upper()} 도메인 테스트 실행 중...")
        report = await runner.run_domain(domain)

    report.print_report()

    # ── 기준선 로드 ─────────────────────────────────────────
    baseline_path = _resolve_baseline(args, suite, reporter)

    if baseline_path is None:
        # 기준선 없음 → 현재 결과를 새 기준선으로 저장
        ts = report.timestamp[:19].replace(":", "").replace("T", "_").replace("-", "")
        json_path = reporter.save_json(report, f"baseline_{suite}_{ts}.json")
        reporter.save_markdown(report, f"baseline_{suite}_{ts}.md")
        print(f"\n  📌 기준선 없음 — 현재 결과를 기준선으로 저장:")
        print(f"     {json_path}")
        return _exit_code(report.pass_rate, args.min_pass_rate)

    # ── 기준선 비교 ─────────────────────────────────────────
    print(f"\n  기준선 로드 중: {baseline_path}")
    try:
        baseline = reporter.load_json(str(baseline_path))
    except Exception as e:
        print(f"  ❌ 기준선 로드 실패: {e}", file=sys.stderr)
        return 1

    diff = reporter.compare(baseline, report)
    print(f"\n  {diff.summary}")

    if diff.regressions:
        print(f"\n  ❌ 회귀 발생 ({len(diff.regressions)}건):")
        for d in diff.regressions:
            print(f"     - {d.scenario_name}")

    if diff.fixed:
        print(f"\n  🔧 수정됨 ({len(diff.fixed)}건):")
        for d in diff.fixed:
            print(f"     + {d.scenario_name}")

    # 현재 결과 저장
    ts = report.timestamp[:19].replace(":", "").replace("T", "_").replace("-", "")
    current_json = reporter.save_json(report, f"current_{suite}_{ts}.json")
    current_md   = reporter.save_markdown(
        report, f"current_{suite}_{ts}.md"
    )

    # 비교 결과 Markdown 저장
    compare_md_path = Path(args.output_dir) / f"compare_{suite}_{ts}.md"
    compare_content = reporter.compare_summary_markdown(diff)
    compare_md_path.write_text(compare_content, encoding="utf-8")

    print(f"\n  저장된 파일:")
    print(f"    현재 리포트: {current_json}")
    print(f"    비교 결과:   {compare_md_path}")

    # 회귀가 있으면 exit code 1
    if diff.regressions:
        return 1
    return _exit_code(report.pass_rate, args.min_pass_rate)


def _resolve_baseline(
    args: argparse.Namespace,
    suite: str,
    reporter,
) -> Path | None:
    """기준선 JSON 파일 경로 결정 (명시적 > 자동 탐색)"""
    # 명시적 지정
    if getattr(args, "baseline", None):
        p = reporter._resolve(args.baseline)
        if p.exists():
            return p
        print(f"  ⚠ 지정한 기준선 파일을 찾을 수 없음: {args.baseline}", file=sys.stderr)
        return None

    # 자동 탐색: baseline_<suite>_*.json 중 최신 파일
    output_dir = Path(args.output_dir)
    candidates = sorted(output_dir.glob(f"baseline_{suite}_*.json"), reverse=True)
    if candidates:
        return candidates[0]

    # 없으면 None
    return None


# ════════════════════════════════════════════════════════════
# 공통 유틸
# ════════════════════════════════════════════════════════════

def _exit_code(pass_rate: float, min_pass_rate: float) -> int:
    """통과율 기준으로 exit code 결정"""
    if pass_rate < min_pass_rate:
        print(
            f"\n  ❌ 통과율 {pass_rate:.0f}% < 기준 {min_pass_rate:.0f}%",
            file=sys.stderr,
        )
        return 1
    print(f"\n  ✅ 통과율 {pass_rate:.0f}% (기준 {min_pass_rate:.0f}% 충족)")
    return 0


def _add_common_args(parser: argparse.ArgumentParser):
    """공통 옵션 추가"""
    parser.add_argument(
        "--save",
        action="store_true",
        help="리포트를 파일로 저장",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_REPORT_DIR,
        metavar="DIR",
        help=f"리포트 저장 디렉토리 (기본: {DEFAULT_REPORT_DIR})",
    )
    parser.add_argument(
        "--min-pass-rate",
        type=float,
        default=80.0,
        metavar="RATE",
        help="최소 통과율 %% (기본: 80, 미달 시 exit 1)",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="WARNING",
        help="로그 레벨 (기본: WARNING — INFO로 상세 출력)",
    )


# ════════════════════════════════════════════════════════════
# 파서 구성
# ════════════════════════════════════════════════════════════

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="harness",
        description="shared-libraries Harness CLI — 시나리오 기반 품질 검증",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  python -m harness smoke
  python -m harness domain software --save
  python -m harness tags smoke safety --save
  python -m harness all --min-pass-rate 90
  python -m harness compare
  python -m harness compare --baseline /app/reports/harness/baseline_smoke_20260501.json
        """,
    )

    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    # ── smoke ──────────────────────────────────────────────
    smoke_p = subparsers.add_parser("smoke", help="스모크 시나리오 실행 (빠름 ~1분)")
    _add_common_args(smoke_p)

    # ── domain ─────────────────────────────────────────────
    domain_p = subparsers.add_parser("domain", help="도메인별 전체 시나리오 실행")
    domain_p.add_argument(
        "name",
        choices=[
            "software", "medical", "business", "knowledge", "cost",
            "polyglot", "svg",
        ],
        help="도메인 이름",
    )
    _add_common_args(domain_p)

    # ── tags ───────────────────────────────────────────────
    tags_p = subparsers.add_parser("tags", help="태그별 시나리오 실행")
    tags_p.add_argument("tags", nargs="+", metavar="TAG", help="하나 이상의 태그")
    _add_common_args(tags_p)

    # ── all ────────────────────────────────────────────────
    all_p = subparsers.add_parser("all", help="전체 시나리오 실행 (느림 ~15분)")
    _add_common_args(all_p)

    # ── compare ────────────────────────────────────────────
    compare_p = subparsers.add_parser(
        "compare",
        help="실행 후 기준선과 비교 (회귀 탐지)",
    )
    compare_p.add_argument(
        "--baseline",
        metavar="FILE",
        help="기준선 JSON 파일 경로 (미지정 시 최신 파일 자동 탐색)",
    )
    compare_p.add_argument(
        "--suite",
        choices=[
            "smoke", "all",
            "software", "medical", "business",
            "knowledge", "cost", "polyglot", "svg",
        ],
        default="smoke",
        help="실행할 테스트 스위트 (기본: smoke)",
    )
    _add_common_args(compare_p)

    # ── decision (4-agent A/B) ─────────────────────────────
    decision_p = subparsers.add_parser(
        "decision",
        help="4-에이전트 legacy vs four_agent 시나리오 (decision_scenarios.json)",
    )
    _add_common_args(decision_p)

    return parser


# ════════════════════════════════════════════════════════════
# 메인 진입점
# ════════════════════════════════════════════════════════════

def main(argv: list[str] | None = None) -> int:
    parser  = build_parser()
    args    = parser.parse_args(argv)

    # 로그 레벨 설정
    log_level = getattr(args, "log_level", "WARNING")
    logging.basicConfig(
        level=getattr(logging, log_level),
        format="%(levelname)-8s %(name)s — %(message)s",
    )

    if not args.command:
        parser.print_help()
        return 0

    handlers = {
        "smoke":    _cmd_smoke,
        "domain":   _cmd_domain,
        "tags":     _cmd_tags,
        "all":      _cmd_all,
        "compare":  _cmd_compare,
        "decision": _cmd_decision,
    }

    handler = handlers.get(args.command)
    if handler is None:
        print(f"알 수 없는 명령: {args.command}", file=sys.stderr)
        return 1

    return asyncio.run(handler(args))
