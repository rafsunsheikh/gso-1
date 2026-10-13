"""A vision model loads with its image part; the image part is never listed as a model."""
from pathlib import Path

from thecmanager import llm


def _fake_models(tmp_path: Path) -> Path:
    folder = tmp_path / "models" / "Qwen3-VL-8B"
    folder.mkdir(parents=True)
    (folder / "Qwen3VL-8B-Instruct-Q4_K_M.gguf").write_bytes(b"x")
    (folder / "mmproj-Qwen3VL-8B-Instruct-Q8_0.gguf").write_bytes(b"x")
    plain = tmp_path / "models" / "Qwen3-8B"
    plain.mkdir()
    (plain / "Qwen3-8B-Q4_K_M.gguf").write_bytes(b"x")
    return tmp_path / "models"


def test_image_part_is_found_and_not_listed(tmp_path, monkeypatch):
    root = _fake_models(tmp_path)
    monkeypatch.setattr(llm, "_MIN_MODEL_BYTES", 0)
    monkeypatch.setenv("MANAGER_MODEL_DIRS", str(root))
    models = {m["name"]: m for m in llm.list_models()}
    assert set(models) == {"Qwen3VL-8B-Instruct-Q4_K_M", "Qwen3-8B-Q4_K_M"}
    assert models["Qwen3VL-8B-Instruct-Q4_K_M"]["vision"] is True
    assert models["Qwen3-8B-Q4_K_M"]["vision"] is False


def test_start_passes_the_image_part(tmp_path, monkeypatch):
    root = _fake_models(tmp_path)
    model = str(root / "Qwen3-VL-8B" / "Qwen3VL-8B-Instruct-Q4_K_M.gguf")
    seen = {}

    class FakeProc:
        pid = 4242

        def poll(self):
            return None

    def fake_popen(cmd, **kw):
        seen["cmd"] = cmd
        return FakeProc()

    monkeypatch.setattr(llm, "server_bin", lambda: "/bin/echo")
    monkeypatch.setattr(llm, "_health", lambda port: False)
    monkeypatch.setattr(llm.subprocess, "Popen", fake_popen)
    llm.start(model, port=18080, ctx=32768)
    assert "--mmproj" in seen["cmd"]
    assert seen["cmd"][seen["cmd"].index("--mmproj") + 1].endswith("mmproj-Qwen3VL-8B-Instruct-Q8_0.gguf")

    llm.start(model, port=18080, ctx=32768, vision=False)
    assert "--mmproj" not in seen["cmd"]


def test_a_chosen_model_starts_with_gso1_on_its_remembered_flags(tmp_path, monkeypatch):
    root = _fake_models(tmp_path)
    model = str(root / "Qwen3-VL-8B" / "Qwen3VL-8B-Instruct-Q4_K_M.gguf")
    started = {}
    monkeypatch.setattr(llm, "start", lambda **kw: started.update(kw) or {"ok": True})
    monkeypatch.setattr(llm, "_health", lambda port: False)
    assert llm.start_autostart_model()["ok"] is False  # nothing chosen yet
    llm.remember_preset(model, dict(llm.DEFAULT_PRESET, ctx=32768, port=8080))
    llm.set_autostart(model)
    assert llm.autostart_model() == model
    assert llm.start_autostart_model() == {"ok": True}
    assert started["model_path"] == model and started["ctx"] == 32768 and started["vision"] is True
    # A server already answering is left alone.
    started.clear()
    monkeypatch.setattr(llm, "_health", lambda port: True)
    assert llm.start_autostart_model()["ok"] is True and started == {}
    llm.set_autostart("")
    assert llm.autostart_model() == ""
