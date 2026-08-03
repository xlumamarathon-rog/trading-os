"""MODULE 2 tests — warm singleton reuse, latency probe, clean shutdown."""
import httpx
import pytest

from src.core.connection_manager import ConnectionManager


def ok_transport():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok"})

    return httpx.MockTransport(handler)


def make_cm():
    return ConnectionManager(
        openalgo_base_url="http://openalgo.local",
        mt5_service_url="https://mt5.local",
        openalgo_transport=ok_transport(),
        mt5_transport=ok_transport(),
    )


async def test_startup_probes_latency_both_legs():
    cm = make_cm()
    await cm.startup()
    assert isinstance(cm.latency_ms["openalgo"], float)
    assert isinstance(cm.latency_ms["mt5"], float)
    await cm.shutdown()


async def test_clients_are_reused_singletons():
    cm = make_cm()
    await cm.startup()
    assert cm.get_openalgo() is cm.get_openalgo()
    assert cm.get_mt5() is cm.get_mt5()
    await cm.shutdown()


async def test_get_before_startup_raises():
    cm = make_cm()
    with pytest.raises(RuntimeError):
        cm.get_openalgo()


async def test_shutdown_closes_clients():
    cm = make_cm()
    await cm.startup()
    oa = cm.get_openalgo()
    await cm.shutdown()
    assert oa.is_closed
    with pytest.raises(RuntimeError):
        cm.get_openalgo()


async def test_probe_failure_recorded_not_raised():
    def failing_transport():
        async def handler(request):
            raise httpx.ConnectError("refused")

        return httpx.MockTransport(handler)

    cm = ConnectionManager(
        openalgo_base_url="http://openalgo.local",
        mt5_service_url="https://mt5.local",
        openalgo_transport=failing_transport(),
        mt5_transport=ok_transport(),
    )
    await cm.startup()  # must not raise — health endpoint reports the None
    assert cm.latency_ms["openalgo"] is None
    assert isinstance(cm.latency_ms["mt5"], float)
    await cm.shutdown()
