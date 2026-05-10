"""
플랫폼 간 이벤트 채널·타입 (Week 6 — Redis Pub/Sub).

설계 노트:
- 단일 채널로 일원화 → 구독 측에서 event_type 로 필터
- 타입 문자열은 점 표기(dot) 로 도메인.액션 을 표현
"""
from typing import Final

# 동일 Compose 네트워크 내 모든 서비스가 같은 채널을 바라본다.
DEFAULT_EVENTS_CHANNEL: Final[str] = "idea-collection:platform.events"

# MEDI-IOT → CoOps: 진단 확정 후 청구 초안 등 후속 업무 트리거
EVENT_MEDICAL_DIAGNOSIS_COMPLETED: Final[str] = "medical.diagnosis.completed"

# AutoNoGaDa 자기/모니터링: 코드 생성 후 품질 알림 채널 (내부 워커 또는 동일 서비스)
EVENT_CODE_GENERATED: Final[str] = "code.generated"

# CoOps → MEDI-IOT: 계약 승인 시 장비 발주 등 연계
EVENT_CONTRACT_APPROVED: Final[str] = "contract.approved"
