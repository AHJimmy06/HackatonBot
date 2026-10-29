import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "motor: prueba del motor por pasos (no fuerza el agente libre)")


@pytest.fixture(autouse=True)
def legacy_agent_by_default(request, monkeypatch):
    """Las pruebas originales validan el agente libre; las marcadas `motor` usan el motor."""
    if request.node.get_closest_marker("motor"):
        return
    from app.motor import engine
    monkeypatch.setattr(engine, "global_mode", lambda: "libre")
