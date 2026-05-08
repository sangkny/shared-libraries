# shared-libraries/harness/reporter.py
"""
HarnessReporter — Harness 리포트 저장 + 기준선 비교

역할:
  1. HarnessReport → Markdown 파일 저장
  2. HarnessReport → JSON 파일 저장
  3. 기준선(baseline) vs 현재 리포트 비교 → 회귀 탐지

사용법:
    reporter = HarnessReporter(output_dir="/reports")

    # 저장
    reporter.save_markdown(report, "smoke_report.md")
    reporter.save_json(report, "smoke_report.json")

    # 비교
    diff = reporter.compare(baseline_report, current_report)
    print(diff.summary)
"""
import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from .runner import HarnessReport, ScenarioResult

log = logging.getLogger("harness.reporter")


# ════════════════════════════════════════════════════════════
# 비교 결과 구조
# ════════════════════════════════════════════════════════════

@dataclass
class ScenarioDiff:
    """단일 시나리오 기준선 vs 현재 비교"""
    scenario_name:    str
    baseline_passed:  bool | None   # None = 기준선에 없음
    current_passed:   bool | None   # None = 현재에 없음
    latency_delta_ms: float         # 양수 = 느려짐, 음수 = 빨라짐
    status:           str           # "same" | "regressed" | "fixed" | "new" | "removed"

    @property
    def is_regression(self) -> bool:
        return self.status == "regressed"

    @property
    def emoji(self) -> str:
        return {
            "same":      "✅",
            "regressed": "❌",
            "fixed":     "🔧",
            "new":       "🆕",
            "removed":   "🗑",
        }.get(self.status, "❓")


@dataclass
class HarnessDiff:
    """전체 기준선 vs 현재 리포트 비교 결과"""
    baseline_timestamp:  str
    current_timestamp:   str
    baseline_pass_rate:  float
    current_pass_rate:   float
    diffs:               list[ScenarioDiff]

    @property
    def regressions(self) -> list[ScenarioDiff]:
        return [d for d in self.diffs if d.status == "regressed"]

    @property
    def fixed(self) -> list[ScenarioDiff]:
        return [d for d in self.diffs if d.status == "fixed"]

    @property
    def pass_rate_delta(self) -> float:
        return self.current_pass_rate - self.baseline_pass_rate

    @property
    def summary(self) -> str:
        delta_str = (
            f"+{self.pass_rate_delta:.0f}%"
            if self.pass_rate_delta >= 0
            else f"{self.pass_rate_delta:.0f}%"
        )
        return (
            f"기준선 비교 | "
            f"통과율: {self.baseline_pass_rate:.0f}% → "
            f"{self.current_pass_rate:.0f}% ({delta_str}) | "
            f"회귀: {len(self.regressions)}건 | "
            f"수정: {len(self.fixed)}건"
        )


# ════════════════════════════════════════════════════════════
# HarnessReporter
# ════════════════════════════════════════════════════════════

class HarnessReporter:
    """
    HarnessReport 저장 및 비교 유틸리티

    output_dir: 리포트 저장 기본 디렉토리
    """

    def __init__(self, output_dir: str = "/reports/harness"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        log.info(f"HarnessReporter 초기화 — output_dir={self.output_dir}")

    # ── 경로 헬퍼 ──────────────────────────────────────────

    def _resolve(self, filename: str) -> Path:
        """상대 경로면 output_dir 기준, 절대 경로면 그대로"""
        p = Path(filename)
        if p.is_absolute():
            p.parent.mkdir(parents=True, exist_ok=True)
            return p
        return self.output_dir / filename

    # ── Markdown 저장 ──────────────────────────────────────

    def save_markdown(
        self,
        report: HarnessReport,
        filename: str = "",
    ) -> Path:
        """
        HarnessReport → Markdown 파일 저장

        Args:
            report:   저장할 리포트
            filename: 파일명 (미지정 시 자동 생성)

        Returns:
            저장된 파일 경로
        """
        if not filename:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"harness_{ts}.md"

        path = self._resolve(filename)
        content = self._build_markdown(report)
        path.write_text(content, encoding="utf-8")
        log.info(f"Markdown 리포트 저장 → {path}")
        return path

    def _build_markdown(self, report: HarnessReport) -> str:
        """HarnessReport → Markdown 문자열 생성"""
        ts = report.timestamp[:19].replace("T", " ")
        lines = [
            f"# Harness Report — {ts}",
            "",
            "## 요약",
            "",
            f"| 항목 | 값 |",
            f"|------|-----|",
            f"| 총 시나리오 | {report.total} |",
            f"| 통과 | {report.passed} |",
            f"| 실패 | {report.failed} |",
            f"| 통과율 | {report.pass_rate:.0f}% |",
            f"| 실행 시간 | {report.total_ms/1000:.1f}초 |",
            "",
            "## 시나리오별 결과",
            "",
        ]

        for r in report.results:
            status = "✅ PASS" if r.passed else "❌ FAIL"
            lines += [
                f"### {status} `{r.scenario_name}`",
                "",
                f"- **도메인**: `{r.domain.value}`",
                f"- **전략**: `{r.strategy}`",
                f"- **반복 횟수**: {r.iterations}",
                f"- **실행 시간**: {r.latency_ms:.0f}ms",
                f"- **Agent 통과**: {'✅' if r.agent_passed else '❌'}",
                f"- **Ontology 통과**: {'✅' if r.ontology_passed else '❌'}",
                "",
            ]

            if r.validator_results:
                lines.append("**검증 결과:**")
                lines.append("")
                for vname, vpassed, vmsg in r.validator_results:
                    symbol = "✓" if vpassed else "✗"
                    lines.append(f"- {symbol} `{vname}`: {vmsg}")
                lines.append("")

            if r.error:
                lines += [f"**오류:** `{r.error[:200]}`", ""]
            elif r.output:
                preview = r.output[:300].replace("\n", " ")
                lines += [f"**출력 일부:**", f"```", preview, "```", ""]

        if report.failed > 0:
            lines += [
                "## 실패 시나리오 목록",
                "",
            ]
            for r in report.results:
                if not r.passed:
                    lines.append(
                        f"- `{r.scenario_name}`: {r.error or '검증 실패'}"
                    )
            lines.append("")

        lines += [
            "---",
            f"*생성: {ts} | shared-libraries HarnessReporter*",
        ]
        return "\n".join(lines)

    # ── JSON 저장 ──────────────────────────────────────────

    def save_json(
        self,
        report: HarnessReport,
        filename: str = "",
    ) -> Path:
        """
        HarnessReport → JSON 파일 저장

        Args:
            report:   저장할 리포트
            filename: 파일명 (미지정 시 자동 생성)

        Returns:
            저장된 파일 경로
        """
        if not filename:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"harness_{ts}.json"

        path = self._resolve(filename)
        data = self._serialize_report(report)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log.info(f"JSON 리포트 저장 → {path}")
        return path

    def _serialize_report(self, report: HarnessReport) -> dict:
        """HarnessReport → JSON 직렬화 가능한 dict"""
        return {
            "timestamp":  report.timestamp,
            "total":      report.total,
            "passed":     report.passed,
            "failed":     report.failed,
            "skipped":    report.skipped,
            "pass_rate":  round(report.pass_rate, 2),
            "total_ms":   round(report.total_ms, 2),
            "results": [
                {
                    "scenario_name":    r.scenario_name,
                    "domain":           r.domain.value,
                    "strategy":         r.strategy,
                    "passed":           r.passed,
                    "agent_passed":     r.agent_passed,
                    "ontology_passed":  r.ontology_passed,
                    "validator_results": [
                        {"name": vn, "passed": vp, "message": vm}
                        for vn, vp, vm in r.validator_results
                    ],
                    "output":           r.output,
                    "latency_ms":       round(r.latency_ms, 2),
                    "iterations":       r.iterations,
                    "error":            r.error,
                    "timestamp":        r.timestamp,
                }
                for r in report.results
            ],
        }

    # ── 기준선 로드 ────────────────────────────────────────

    def load_json(self, filename: str) -> HarnessReport:
        """
        JSON 파일에서 HarnessReport 로드

        Args:
            filename: JSON 파일 경로

        Returns:
            복원된 HarnessReport
        """
        from ontology.base import OntologyDomain

        path = self._resolve(filename)
        data = json.loads(path.read_text(encoding="utf-8"))

        results = []
        for r in data["results"]:
            results.append(
                ScenarioResult(
                    scenario_name=r["scenario_name"],
                    domain=OntologyDomain(r["domain"]),
                    strategy=r["strategy"],
                    passed=r["passed"],
                    agent_passed=r["agent_passed"],
                    ontology_passed=r["ontology_passed"],
                    validator_results=[
                        (v["name"], v["passed"], v["message"])
                        for v in r["validator_results"]
                    ],
                    output=r["output"],
                    latency_ms=r["latency_ms"],
                    iterations=r["iterations"],
                    error=r["error"],
                    timestamp=r["timestamp"],
                )
            )

        return HarnessReport(
            total=data["total"],
            passed=data["passed"],
            failed=data["failed"],
            skipped=data["skipped"],
            results=results,
            total_ms=data["total_ms"],
            timestamp=data["timestamp"],
        )

    # ── 기준선 비교 ────────────────────────────────────────

    def compare(
        self,
        baseline: HarnessReport,
        current:  HarnessReport,
    ) -> HarnessDiff:
        """
        기준선 vs 현재 리포트 비교 → 회귀/수정 탐지

        Args:
            baseline: 기준선 리포트 (이전 통과 기록)
            current:  현재 리포트

        Returns:
            HarnessDiff — 변화 요약
        """
        base_map = {r.scenario_name: r for r in baseline.results}
        curr_map = {r.scenario_name: r for r in current.results}
        all_names = sorted(set(base_map) | set(curr_map))

        diffs = []
        for name in all_names:
            b = base_map.get(name)
            c = curr_map.get(name)

            if b is None:
                status = "new"
                latency_delta = c.latency_ms if c else 0.0
            elif c is None:
                status = "removed"
                latency_delta = 0.0
            elif b.passed and not c.passed:
                status = "regressed"
                latency_delta = c.latency_ms - b.latency_ms
            elif not b.passed and c.passed:
                status = "fixed"
                latency_delta = c.latency_ms - b.latency_ms
            else:
                status = "same"
                latency_delta = c.latency_ms - b.latency_ms

            diffs.append(
                ScenarioDiff(
                    scenario_name=name,
                    baseline_passed=b.passed if b else None,
                    current_passed=c.passed if c else None,
                    latency_delta_ms=latency_delta,
                    status=status,
                )
            )

        diff = HarnessDiff(
            baseline_timestamp=baseline.timestamp,
            current_timestamp=current.timestamp,
            baseline_pass_rate=baseline.pass_rate,
            current_pass_rate=current.pass_rate,
            diffs=diffs,
        )
        log.info(f"비교 완료 — {diff.summary}")
        return diff

    def compare_summary_markdown(self, diff: HarnessDiff) -> str:
        """HarnessDiff → Markdown 요약 문자열"""
        delta_str = (
            f"+{diff.pass_rate_delta:.0f}%"
            if diff.pass_rate_delta >= 0
            else f"{diff.pass_rate_delta:.0f}%"
        )
        lines = [
            "## 기준선 비교 결과",
            "",
            f"| 항목 | 기준선 | 현재 | 변화 |",
            f"|------|--------|------|------|",
            f"| 통과율 | {diff.baseline_pass_rate:.0f}% | "
            f"{diff.current_pass_rate:.0f}% | {delta_str} |",
            f"| 회귀 | - | - | {len(diff.regressions)}건 |",
            f"| 수정 | - | - | {len(diff.fixed)}건 |",
            "",
        ]

        if diff.diffs:
            lines += ["### 시나리오별 변화", ""]
            for d in diff.diffs:
                delta_ms = (
                    f"+{d.latency_delta_ms:.0f}ms"
                    if d.latency_delta_ms >= 0
                    else f"{d.latency_delta_ms:.0f}ms"
                )
                lines.append(
                    f"- {d.emoji} `{d.scenario_name}` — "
                    f"상태: **{d.status}** | 지연 변화: {delta_ms}"
                )
            lines.append("")

        return "\n".join(lines)
