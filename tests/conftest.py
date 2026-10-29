import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))  # pgtest 임포트용


@pytest.fixture(autouse=True)
def _reset_service_singletons():
    """trading.service 는 Store/Gateway 를 프로세스 전역에 캐시한다. 테스트 사이에 이전 테스트의 DB(특히 실제 DB)에 묶인 채 남으면
    다른 테스트가 임시 스키마가 아닌 곳에 쓰게 되므로, 매 테스트 전후로 비운다."""
    from stockwatch.trading import service
    service._store = service._gateway = None
    yield
    service._store = service._gateway = None
