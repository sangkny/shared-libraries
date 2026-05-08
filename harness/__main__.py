# shared-libraries/harness/__main__.py
"""
python -m harness 진입점

실행 방법:
    # 컨테이너 내부에서 직접
    python -m harness smoke
    python -m harness domain software --save

    # 래퍼 스크립트를 통해 (WSL)
    ./run_harness.sh smoke
    ./run_harness.sh domain software

    # 래퍼 스크립트를 통해 (PowerShell)
    ./run_harness.ps1 smoke
    ./run_harness.ps1 domain software
"""
import sys
from harness.cli import main

if __name__ == "__main__":
    sys.exit(main())
