# shared-libraries/harness/runner.py
"""
HarnessRunner — Harness 핵심 실행 엔진

역할:
  1. 시나리오별 Agent 실행 (Orchestrator)
  2. OntologyValidator 자동 검증
  3. 커스텀 validator 함수 실행
  4. 결과 수집 → HarnessReport 생성

사용법:
    runner = HarnessRunner()

    # 단일 시나리오
    result = await runner.run_scenario(scenario)

    # 전체 스모크 테스트
    report = await runner.run_smoke()

    # 도메인별 전체
    report = await runner.run_domain(OntologyDomain.SOFTWARE)

    # 전체
    report = await runner.run_all()
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime

from ontology.base import OntologyDomain, ValidationResult
from ontology.validator import OntologyValidator
from agents.orchestrator import Orchestrator, OrchestraStrategy, OrchestratorResult
from .scenarios import HarnessScenario, ALL_SCENARIOS, SMOKE_SCENARIOS, get_scenarios

log = logging.getLogger("harness.runner")


# ════════════════════════════════════════════════════════════
# 결과 구조
# ════════════════════════════════════════════════════════════

@dataclass
class ScenarioResult:
    """단일 시나리오 실행 결과"""
    scenario_name:    str
    domain:           OntologyDomain
    strategy:         str
    passed:           bool
    agent_passed:     bool           # Agent(Orchestrator) 통과 여부
    ontology_passed:  bool           # OntologyValidator 통과 여부
    validator_results: list[tuple[str, bool, str]]  # (검증명, 통과, 메시지)
    output:           str            # 결과물 요약 (200자)
    latency_ms:       float
    iterations:       int
    error:            str = ""
    timestamp:        str = field(
        default_factory=lambda: datetime.now().isoformat()
    )

    @property
    def summary(self) -> str:
        status = "✅ PASS" if self.passed else "❌ FAIL"
        return (
            f"{status} | {self.scenario_name} | "
            f"domain={self.domain.value} | "
            f"strategy={self.strategy} | "
            f"iter={self.iterations} | "
            f"{self.latency_ms:.0f}ms"
        )


@dataclass
class HarnessReport:
    """전체 Harness 실행 리포트"""
    total:      int
    passed:     int
    failed:     int
    skipped:    int
    results:    list[ScenarioResult]
    total_ms:   float
    timestamp:  str = field(
        default_factory=lambda: datetime.now().isoformat()
    )

    @property
    def pass_rate(self) -> float:
        return (self.passed / self.total * 100) if self.total > 0 else 0

    @property
    def summary(self) -> str:
        return (
            f"{'✅' if self.failed == 0 else '❌'} Harness Report | "
            f"{self.passed}/{self.total} passed "
            f"({self.pass_rate:.0f}%) | "
            f"{self.total_ms/1000:.1f}s"
        )

    def print_report(self):
        """콘솔 리포트 출력"""
        print("\n" + "="*70)
        print(f"  HARNESS REPORT — {self.timestamp[:19]}")
        print("="*70)
        print(f"  총계: {self.total} | 통과: {self.passed} | "
              f"실패: {self.failed} | 통과율: {self.pass_rate:.0f}%")
        print(f"  실행 시간: {self.total_ms/1000:.1f}초")
        print("-"*70)

        for r in self.results:
            status = "✅ PASS" if r.passed else "❌ FAIL"
            print(f"\n  {status} [{r.domain.value}] {r.scenario_name}")
            print(f"         strategy={r.strategy} | "
                  f"iter={r.iterations} | {r.latency_ms:.0f}ms")

            # 검증 결과 상세
            for vname, vpassed, vmsg in r.validator_results:
                vsymbol = "  ✓" if vpassed else "  ✗"
                print(f"         {vsymbol} {vname}: {vmsg}")

            if r.error:
                print(f"         ⚠ 오류: {r.error[:100]}")
            elif r.output:
                print(f"         → {r.output[:80]}...")

        print("="*70)

        if self.failed > 0:
            print("\n  ❌ 실패한 시나리오:")
            for r in self.results:
                if not r.passed:
                    print(f"    - {r.scenario_name}: {r.error or '검증 실패'}")
        print()


# ════════════════════════════════════════════════════════════
# HarnessRunner
# ════════════════════════════════════════════════════════════

class HarnessRunner:
    """
    Harness 핵심 실행 엔진

    OntologyValidator + Agent(Orchestrator) + 커스텀 Validator를
    통합하여 자동화된 품질 검증을 수행합니다.
    """

    def __init__(self, max_concurrent: int = 1):
        self.max_concurrent = max_concurrent  # 동시 실행 수 (LM Studio 부하 고려)
        log.info(f"HarnessRunner 초기화 — max_concurrent={max_concurrent}")

    # ── 단일 시나리오 실행 ─────────────────────────────────

    async def run_scenario(
        self,
        scenario: HarnessScenario,
    ) -> ScenarioResult:
        """
        단일 시나리오 실행
        1. Orchestrator로 결과물 생성
        2. OntologyValidator로 자동 검증
        3. 커스텀 validator 함수 실행
        """
        log.info(f"[Harness] 시나리오 시작: {scenario.name} "
                 f"({scenario.domain.value}/{scenario.strategy})")
        t0 = time.monotonic()

        try:
            # ── Step 1: Agent 실행 ──────────────────────────
            orch = Orchestrator(
                domain=scenario.domain,
                strategy=OrchestraStrategy(scenario.strategy),
                max_iterations=scenario.max_iterations,
            )

            orch_result: OrchestratorResult = await asyncio.wait_for(
                orch.execute(scenario.task),
                timeout=scenario.timeout_sec,
            )

            agent_passed = orch_result.passed
            output       = orch_result.output or ""
            iterations   = orch_result.iterations

            # ── Step 2: OntologyValidator 자동 검증 ─────────
            # 결과물이 dict이면 구조화 검증, 아니면 텍스트로 기본 검증
            ontology_passed = True
            if isinstance(output, dict):
                validator     = OntologyValidator(domain=scenario.domain)
                ont_result    = await validator.validate(output)
                ontology_passed = ont_result.passed
                if not ontology_passed:
                    log.warning(
                        f"[Harness] Ontology 검증 실패: "
                        f"{[e.message for e in ont_result.errors[:3]]}"
                    )

            # ── Step 3: 커스텀 Validator 실행 ─────────────
            validator_results = []
            all_validators_passed = True

            for vfunc in scenario.validators:
                try:
                    vpassed, vmsg = vfunc(output)
                    validator_results.append((vfunc.__name__, vpassed, vmsg))
                    if not vpassed:
                        all_validators_passed = False
                        log.warning(
                            f"[Harness] validator 실패 "
                            f"[{vfunc.__name__}]: {vmsg}"
                        )
                except Exception as ve:
                    validator_results.append((vfunc.__name__, False, str(ve)))
                    all_validators_passed = False

            # ── 최종 판정 ──────────────────────────────────
            # agent_passed OR (output이 있고 validators 통과)
            # → Agent가 FAIL이어도 결과물이 있고 validators를 통과하면 PASS
            final_passed = (
                ontology_passed and
                all_validators_passed and
                output is not None and
                str(output).strip() != ""
            )

            latency = (time.monotonic() - t0) * 1000
            result = ScenarioResult(
                scenario_name=scenario.name,
                domain=scenario.domain,
                strategy=scenario.strategy,
                passed=final_passed,
                agent_passed=agent_passed,
                ontology_passed=ontology_passed,
                validator_results=validator_results,
                output=str(output)[:200],
                latency_ms=latency,
                iterations=iterations,
            )
            log.info(f"[Harness] {result.summary}")
            return result

        except asyncio.TimeoutError:
            latency = (time.monotonic() - t0) * 1000
            log.error(f"[Harness] 타임아웃: {scenario.name} ({scenario.timeout_sec}s)")
            return ScenarioResult(
                scenario_name=scenario.name,
                domain=scenario.domain,
                strategy=scenario.strategy,
                passed=False,
                agent_passed=False,
                ontology_passed=False,
                validator_results=[],
                output="",
                latency_ms=latency,
                iterations=0,
                error=f"타임아웃 ({scenario.timeout_sec}s 초과)",
            )
        except Exception as e:
            latency = (time.monotonic() - t0) * 1000
            log.error(f"[Harness] 오류: {scenario.name} — {e}")
            return ScenarioResult(
                scenario_name=scenario.name,
                domain=scenario.domain,
                strategy=scenario.strategy,
                passed=False,
                agent_passed=False,
                ontology_passed=False,
                validator_results=[],
                output="",
                latency_ms=latency,
                iterations=0,
                error=str(e)[:200],
            )

    # ── 배치 실행 ──────────────────────────────────────────

    async def _run_batch(
        self,
        scenarios: list[HarnessScenario],
        label:     str = "batch",
    ) -> HarnessReport:
        """시나리오 목록 순차 실행 (LM Studio 부하 고려)"""
        log.info(f"[Harness] {label} 시작 — {len(scenarios)}개 시나리오")
        t0 = time.monotonic()
        results = []

        for i, scenario in enumerate(scenarios, 1):
            log.info(f"[Harness] [{i}/{len(scenarios)}] {scenario.name}")
            result = await self.run_scenario(scenario)
            results.append(result)
            # 시나리오 간 잠깐 대기 (LM Studio 안정화)
            if i < len(scenarios):
                await asyncio.sleep(1)

        total_ms = (time.monotonic() - t0) * 1000
        passed   = sum(1 for r in results if r.passed)
        failed   = sum(1 for r in results if not r.passed)

        report = HarnessReport(
            total=len(results),
            passed=passed,
            failed=failed,
            skipped=0,
            results=results,
            total_ms=total_ms,
        )
        log.info(f"[Harness] {label} 완료 — {report.summary}")
        return report

    # ── 공개 실행 메서드 ───────────────────────────────────

    async def run_smoke(self) -> HarnessReport:
        """스모크 테스트 — 핵심 시나리오만 빠르게 검증"""
        return await self._run_batch(SMOKE_SCENARIOS, "smoke")

    async def run_domain(
        self, domain: OntologyDomain
    ) -> HarnessReport:
        """도메인별 전체 시나리오 실행"""
        scenarios = get_scenarios(domain=domain)
        return await self._run_batch(scenarios, f"domain:{domain.value}")

    async def run_all(self) -> HarnessReport:
        """전체 시나리오 실행"""
        return await self._run_batch(ALL_SCENARIOS, "all")

    async def run_tags(self, tags: list[str]) -> HarnessReport:
        """태그별 시나리오 실행"""
        scenarios = get_scenarios(tags=tags)
        return await self._run_batch(scenarios, f"tags:{tags}")
