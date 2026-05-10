# shared-libraries/tests/test_harness.py
"""
Harness 통합 테스트
목적: HarnessRunner가 시나리오를 올바르게 실행하고
     OntologyValidator + Agent 결과를 통합 검증하는지 확인

실행:
    # 스모크 테스트만 (빠름 ~2분)
    docker compose -f docker-compose.dev.yml exec shared-libs \
        pytest tests/test_harness.py::TestHarnessSmoke -v -s

    # 전체 (느림 ~10분)
    docker compose -f docker-compose.dev.yml exec shared-libs \
        pytest tests/test_harness.py -v -s
"""
import json
import tempfile
from pathlib import Path

import pytest
from ontology.base import OntologyDomain
from harness.runner import HarnessRunner, HarnessReport, ScenarioResult
from harness.reporter import HarnessReporter, HarnessDiff
from harness.scenarios import (
    SMOKE_SCENARIOS, SOFTWARE_SCENARIOS, MEDICAL_SCENARIOS,
    HarnessScenario, get_scenarios,
    has_content, has_type_hints, has_def_keyword,
    has_async_keyword, has_medical_term, has_sufficient_length,
    has_svg_root, extract_svg_fragment,
)


# ════════════════════════════════════════════════════════════
# 스모크 테스트 (핵심 — 항상 통과해야 함)
# ════════════════════════════════════════════════════════════
class TestHarnessSmoke:
    """
    목적: 가장 기본적인 시나리오가 동작하는지 빠르게 확인
    단계: Week 1 Day 3 — Harness 기본 동작 검증
    """

    @pytest.mark.asyncio
    async def test_smoke_scenarios_exist(self):
        """스모크 시나리오가 정의되어 있는지 확인"""
        print(f"\n  스모크 시나리오 수: {len(SMOKE_SCENARIOS)}")
        for s in SMOKE_SCENARIOS:
            print(f"  - {s.name} [{s.domain.value}]")
        assert len(SMOKE_SCENARIOS) >= 1, "스모크 시나리오가 없음"

    @pytest.mark.asyncio
    async def test_single_smoke_scenario(self):
        """
        목적: 가장 단순한 시나리오 1개 실행
        단계: add 함수 생성 → 타입힌트/함수정의 검증
        기대: PASS
        """
        runner   = HarnessRunner()
        scenario = SMOKE_SCENARIOS[0]  # simple_add_function

        print(f"\n  시나리오: {scenario.name}")
        print(f"  도메인:   {scenario.domain.value}")
        print(f"  전략:     {scenario.strategy}")
        print(f"  작업:     {scenario.task[:60]}...")

        result = await runner.run_scenario(scenario)

        print(f"\n  결과: {result.summary}")
        print(f"  출력 일부: {result.output[:150]}")
        print(f"  검증 결과:")
        for vname, vpassed, vmsg in result.validator_results:
            print(f"    {'✓' if vpassed else '✗'} {vname}: {vmsg}")

        assert result.output != "", "결과물이 비어있음"
        assert len(result.validator_results) > 0, "검증이 실행되지 않음"


# ════════════════════════════════════════════════════════════
# 시나리오 구성 테스트
# ════════════════════════════════════════════════════════════
class TestHarnessScenarios:
    """
    목적: 시나리오 정의가 올바른지 확인
    단계: 실제 LLM 호출 없이 구조만 검증
    """

    def test_all_scenarios_have_required_fields(self):
        """모든 시나리오가 필수 필드를 가지고 있는지"""
        from harness.scenarios import ALL_SCENARIOS
        for s in ALL_SCENARIOS:
            assert s.name, f"name 없음"
            assert s.task, f"task 없음 [{s.name}]"
            assert s.timeout_sec > 0, f"timeout 잘못됨 [{s.name}]"
            assert s.max_iterations >= 1, f"max_iterations 잘못됨 [{s.name}]"
            assert len(s.validators) >= 1, f"validators 없음 [{s.name}]"
        print(f"\n  전체 시나리오 수: {len(ALL_SCENARIOS)}")
        assert len(ALL_SCENARIOS) >= 12, f"시나리오 수 부족: {len(ALL_SCENARIOS)}"

    def test_get_scenarios_by_domain(self):
        """도메인 필터링 동작 확인"""
        sw = get_scenarios(domain=OntologyDomain.SOFTWARE)
        med = get_scenarios(domain=OntologyDomain.MEDICAL)
        biz = get_scenarios(domain=OntologyDomain.BUSINESS)
        svg = get_scenarios(domain=OntologyDomain.SVG)
        print(f"\n  SOFTWARE: {len(sw)}개")
        print(f"  MEDICAL:  {len(med)}개")
        print(f"  BUSINESS: {len(biz)}개")
        print(f"  SVG:      {len(svg)}개")
        assert len(sw) >= 1
        assert len(med) >= 1
        assert len(biz) >= 1
        assert len(svg) >= 2

    def test_get_scenarios_by_tag(self):
        """태그 필터링 동작 확인"""
        smoke = get_scenarios(tags=["smoke"])
        assert len(smoke) >= 1
        print(f"\n  smoke 태그 시나리오: {len(smoke)}개")

    def test_validator_functions(self):
        """검증 함수들이 올바르게 동작하는지"""
        # has_content
        ok, _ = has_content("def add(a, b): return a+b")
        assert ok is True
        ok, _ = has_content("")
        assert ok is False

        # has_type_hints
        ok, _ = has_type_hints("def add(a: int, b: int) -> int: return a+b")
        assert ok is True
        ok, _ = has_type_hints("def add(a, b): return a+b")
        assert ok is False

        # has_def_keyword
        ok, _ = has_def_keyword("def calculate(): pass")
        assert ok is True
        ok, _ = has_def_keyword("result = 42")
        assert ok is False

        # has_async_keyword
        ok, _ = has_async_keyword("async def fetch(): pass")
        assert ok is True
        ok, _ = has_async_keyword("def fetch(): pass")
        assert ok is False

        # has_medical_term
        ok, _ = has_medical_term("녹내장(H40.1) 소견: 안압 상승")
        assert ok is True
        ok, _ = has_medical_term("일반 텍스트입니다.")
        assert ok is False

        # has_sufficient_length
        ok, _ = has_sufficient_length("a" * 200)
        assert ok is True
        ok, _ = has_sufficient_length("짧음")
        assert ok is False

        ok, _ = has_svg_root("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 1 1'></svg>")
        assert ok is True
        ok, _ = has_svg_root("no markup")
        assert ok is False
        assert "<svg" in extract_svg_fragment("prefix <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 1 1'/> suffix").lower()

        print("\n  ✅ 모든 validator 함수 정상 동작 (SVG 추출 포함)")


# ════════════════════════════════════════════════════════════
# HarnessRunner 전체 실행 테스트
# ════════════════════════════════════════════════════════════
class TestHarnessRunner:
    """
    목적: HarnessRunner 전체 실행 흐름 검증
    단계: Week 1 Day 3 — Harness 완전 통합 테스트
    주의: LLM 실제 호출 — 시간이 오래 걸림 (도메인별 5~10분)
    """

    @pytest.mark.asyncio
    async def test_run_smoke(self):
        """
        목적: 스모크 테스트 전체 실행
        단계: SMOKE_SCENARIOS 모두 실행 → HarnessReport 생성
        기대: 통과율 >= 80%
        """
        runner = HarnessRunner()
        report = await runner.run_smoke()
        report.print_report()

        print(f"\n  통과율: {report.pass_rate:.0f}%")
        assert report.total > 0, "실행된 시나리오 없음"
        assert report.pass_rate >= 80, (
            f"통과율이 너무 낮음: {report.pass_rate:.0f}%\n"
            f"실패: {[r.scenario_name for r in report.results if not r.passed]}"
        )

    @pytest.mark.asyncio
    async def test_run_software_domain(self):
        """
        목적: SOFTWARE 도메인 전체 시나리오 실행
        단계: SOFTWARE_SCENARIOS 모두 실행
        기대: 통과율 >= 70%
        """
        runner = HarnessRunner()
        report = await runner.run_domain(OntologyDomain.SOFTWARE)
        report.print_report()

        assert report.total == len(SOFTWARE_SCENARIOS)
        assert report.pass_rate >= 70, f"SOFTWARE 통과율 낮음: {report.pass_rate:.0f}%"


# ════════════════════════════════════════════════════════════
# HarnessReporter 테스트 (LLM 호출 없음)
# ════════════════════════════════════════════════════════════
class TestHarnessReporter:
    """
    목적: HarnessReporter 저장/로드/비교 기능 검증
    단계: 더미 리포트로 파일 I/O 및 비교 로직 검증
    """

    def _make_result(
        self,
        name: str,
        passed: bool,
        latency_ms: float = 5000.0,
        timestamp: str = "2026-05-09T00:00:00",
    ) -> ScenarioResult:
        """더미 ScenarioResult 생성 헬퍼"""
        from ontology.base import OntologyDomain

        return ScenarioResult(
            scenario_name=name,
            domain=OntologyDomain.SOFTWARE,
            strategy="pipeline",
            passed=passed,
            agent_passed=passed,
            ontology_passed=True,
            validator_results=[
                ("has_content", passed, "결과물이 있음" if passed else "결과물 없음")
            ],
            output="def add(a: int, b: int) -> int: return a + b" if passed else "",
            latency_ms=latency_ms,
            iterations=1,
            error="" if passed else "검증 실패",
            timestamp=timestamp,
        )

    def _make_dummy_report(
        self,
        results: list[ScenarioResult],
        timestamp: str = "2026-05-09T00:00:00",
    ) -> HarnessReport:
        """더미 HarnessReport 생성"""
        passed = sum(1 for r in results if r.passed)
        failed = len(results) - passed
        return HarnessReport(
            total=len(results),
            passed=passed,
            failed=failed,
            skipped=0,
            results=results,
            total_ms=30000.0,
            timestamp=timestamp,
        )

    def test_save_and_load_json(self):
        """JSON 저장 후 로드 시 원본과 동일한지 확인"""
        with tempfile.TemporaryDirectory() as tmpdir:
            reporter = HarnessReporter(output_dir=tmpdir)
            results  = [
                self._make_result("add_function", True),
                self._make_result("bmi_calc", True),
                self._make_result("bad_scenario", False),
            ]
            report = self._make_dummy_report(results)

            path = reporter.save_json(report, "test_report.json")
            assert path.exists(), "JSON 파일이 생성되지 않음"

            loaded = reporter.load_json("test_report.json")
            assert loaded.total     == report.total
            assert loaded.passed    == report.passed
            assert loaded.failed    == report.failed
            assert loaded.pass_rate == report.pass_rate
            assert len(loaded.results) == len(report.results)

            print(f"\n  ✅ JSON 저장/로드 성공: {path}")

    def test_save_markdown(self):
        """Markdown 파일 저장 및 기본 내용 확인"""
        with tempfile.TemporaryDirectory() as tmpdir:
            reporter = HarnessReporter(output_dir=tmpdir)
            results  = [
                self._make_result("add_function", True),
                self._make_result("bad_scenario", False),
            ]
            report = self._make_dummy_report(results)

            path = reporter.save_markdown(report, "test_report.md")
            assert path.exists(), "Markdown 파일이 생성되지 않음"

            content = path.read_text(encoding="utf-8")
            assert "# Harness Report" in content
            assert "통과율" in content
            assert "add_function" in content
            assert "bad_scenario" in content

            print(f"\n  ✅ Markdown 저장 성공: {path}")
            print(f"  파일 크기: {path.stat().st_size} bytes")

    def test_compare_regression_detection(self):
        """회귀 탐지: 이전에 통과한 시나리오가 실패로 변환"""
        with tempfile.TemporaryDirectory() as tmpdir:
            reporter = HarnessReporter(output_dir=tmpdir)

            # 기준선: scenario_a, scenario_b 모두 PASS
            baseline = self._make_dummy_report(
                [
                    self._make_result("scenario_a", True, timestamp="2026-05-08T10:00:00"),
                    self._make_result("scenario_b", True, timestamp="2026-05-08T10:00:00"),
                ],
                timestamp="2026-05-08T10:00:00",
            )
            # 현재: scenario_a PASS, scenario_b FAIL (회귀)
            current = self._make_dummy_report(
                [
                    self._make_result("scenario_a", True,  timestamp="2026-05-09T10:00:00"),
                    self._make_result("scenario_b", False, timestamp="2026-05-09T10:00:00"),
                ],
                timestamp="2026-05-09T10:00:00",
            )

            diff = reporter.compare(baseline, current)

            print(f"\n  {diff.summary}")
            assert len(diff.regressions) >= 1, "회귀가 탐지되지 않음"
            assert diff.pass_rate_delta < 0, "통과율이 낮아졌어야 함"

            md = reporter.compare_summary_markdown(diff)
            assert "회귀" in md
            print(f"  ✅ 회귀 탐지 성공: {len(diff.regressions)}건")

    def test_compare_fix_detection(self):
        """수정 탐지: 이전에 실패한 시나리오가 통과로 변환"""
        with tempfile.TemporaryDirectory() as tmpdir:
            reporter = HarnessReporter(output_dir=tmpdir)

            # 기준선: scenario_a PASS, scenario_b FAIL
            baseline = self._make_dummy_report(
                [
                    self._make_result("scenario_a", True,  timestamp="2026-05-08T10:00:00"),
                    self._make_result("scenario_b", False, timestamp="2026-05-08T10:00:00"),
                ],
                timestamp="2026-05-08T10:00:00",
            )
            # 현재: scenario_a, scenario_b 모두 PASS (수정)
            current = self._make_dummy_report(
                [
                    self._make_result("scenario_a", True, timestamp="2026-05-09T10:00:00"),
                    self._make_result("scenario_b", True, timestamp="2026-05-09T10:00:00"),
                ],
                timestamp="2026-05-09T10:00:00",
            )

            diff = reporter.compare(baseline, current)

            print(f"\n  {diff.summary}")
            assert len(diff.fixed) >= 1, "수정이 탐지되지 않음"
            assert diff.pass_rate_delta > 0, "통과율이 높아졌어야 함"
            print(f"  ✅ 수정 탐지 성공: {len(diff.fixed)}건")

    def test_auto_filename_generation(self):
        """파일명 미지정 시 자동 생성 확인"""
        with tempfile.TemporaryDirectory() as tmpdir:
            reporter = HarnessReporter(output_dir=tmpdir)
            report   = self._make_dummy_report(
                [self._make_result("add_function", True)]
            )

            json_path = reporter.save_json(report)
            md_path   = reporter.save_markdown(report)

            assert json_path.suffix == ".json"
            assert md_path.suffix   == ".md"
            assert "harness_" in json_path.name
            assert "harness_" in md_path.name

            print(f"\n  ✅ 자동 파일명: {json_path.name}, {md_path.name}")
