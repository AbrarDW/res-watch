from reswatch import gpu


def test_null_backend_is_clean():
    g = gpu.NullGpu()
    assert g.info()["available"] is False
    assert g.info()["reason"]
    assert g.devices() == [] and g.per_process() == {}


def test_detect_never_raises():
    g = gpu.detect()
    info = g.info()
    assert set(info) >= {"available", "backend", "reason"}
    assert isinstance(g.devices(), list)
    assert isinstance(g.per_process(), dict)
