"""凭据单一所有者。

旧行为：每个 API 凭据存两处 —— Scene 属性（跟着 .blend 保存）与 add-on
preferences 互相镜像；server 读 prefs -> scene -> env，面板画"有 prefs 就画
prefs，否则画 scene"，免费试用按钮还会把 RODIN key 写进 Scene 属性。结果是
同一次调用用哪把 key 取决于当前打开的 .blend。

现在：add-on preferences 是唯一所有者（config.get_credential），环境变量
DSHBB_*/旧名 BLENDERMCP_* 仍是后备（CI/无 UI 场景）；免费试用按钮只写进程内
session override，不覆盖用户已保存的私钥，reload 后自动失效。
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import bpy_stub

bpy = bpy_stub.install()

config = importlib.import_module("extension.dsh_blender_bridge.config")
server_module = importlib.import_module("extension.dsh_blender_bridge.server")
operators = importlib.import_module("extension.dsh_blender_bridge.ui.operators")

CRED_ATTRS = (
    "hyper3d_api_key",
    "sketchfab_api_key",
    "polypizza_api_key",
    "hunyuan3d_secret_id",
    "hunyuan3d_secret_key",
    "hunyuan3d_api_url",
)
ENV_NAMES = (
    "DSHBB_HYPER3D_API_KEY",
    "DSHBB_SKETCHFAB_API_KEY",
    "DSHBB_POLYPIZZA_API_KEY",
    "DSHBB_HUNYUAN3D_SECRET_ID",
    "DSHBB_HUNYUAN3D_SECRET_KEY",
    "DSHBB_HUNYUAN3D_API_URL",  # gitleaks:allow
)


def _prefs(monkeypatch, **attrs):
    """把 bpy.context.preferences 指向带着这些凭据的假 preferences。"""
    prefs = SimpleNamespace(**{name: attrs.get(name, "") for name in CRED_ATTRS})
    fake = SimpleNamespace(
        preferences=SimpleNamespace(
            addons={config.ADDON_MODULE: SimpleNamespace(preferences=prefs)},
        ),
    )
    monkeypatch.setattr(bpy.context, "preferences", fake.preferences)
    return prefs


def _clear_env(monkeypatch):
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(config._LEGACY_ENV_VARS[name], raising=False)
    for name in CRED_ATTRS:
        config.set_session_credential(name, "")


def _server():
    return server_module.DSHBlenderBridgeServer()


def test_a_legacy_scene_credential_is_no_longer_read(monkeypatch):
    _prefs(monkeypatch)
    _clear_env(monkeypatch)
    bpy.context.scene.dshbb_hyper3d_api_key = "legacy-value-from-old-blend"
    try:
        assert _server()._get_hyper3d_api_key() == ""
    finally:
        del bpy.context.scene.dshbb_hyper3d_api_key


def test_preferences_are_the_single_owner_and_env_stays_the_fallback(monkeypatch):
    prefs = _prefs(monkeypatch, hyper3d_api_key="prefs-key")
    _clear_env(monkeypatch)
    server = _server()
    assert server._get_hyper3d_api_key() == "prefs-key"

    monkeypatch.setenv("DSHBB_SKETCHFAB_API_KEY", "env-key")
    assert server._get_sketchfab_api_key() == "env-key"

    monkeypatch.setenv("BLENDERMCP_POLYPIZZA_API_KEY", "legacy-env-key")
    assert server._get_polypizza_api_key() == "legacy-env-key"

    assert prefs.hyper3d_api_key == "prefs-key"


def test_free_trial_overrides_the_session_without_overwriting_the_saved_key(monkeypatch):
    prefs = _prefs(monkeypatch, hyper3d_api_key="saved-private-key")
    _clear_env(monkeypatch)
    context = SimpleNamespace(scene=bpy.context.scene, preferences=bpy.context.preferences)
    operator = operators.DSHBB_OT_SetFreeTrialHyper3DAPIKey()
    reports = []
    operator.report = lambda level, message: reports.append((level, message))
    try:
        assert operator.execute(context) == {"FINISHED"}
        assert prefs.hyper3d_api_key == "saved-private-key"
        assert _server()._get_hyper3d_api_key() == config.RODIN_FREE_TRIAL_KEY
        assert bpy.context.scene.dshbb_hyper3d_mode == "MAIN_SITE"
        assert not hasattr(bpy.context.scene, "dshbb_hyper3d_api_key")
        assert any("session only" in str(message) for _level, message in reports)
    finally:
        config.clear_session_credentials()
        if hasattr(bpy.context.scene, "dshbb_hyper3d_mode"):
            del bpy.context.scene.dshbb_hyper3d_mode

    assert _server()._get_hyper3d_api_key() == "saved-private-key"


def test_free_trial_persists_into_empty_preferences(monkeypatch):
    prefs = _prefs(monkeypatch)
    _clear_env(monkeypatch)
    context = SimpleNamespace(scene=bpy.context.scene, preferences=bpy.context.preferences)
    operator = operators.DSHBB_OT_SetFreeTrialHyper3DAPIKey()
    operator.report = lambda level, message: None
    try:
        operator.execute(context)
        assert prefs.hyper3d_api_key == config.RODIN_FREE_TRIAL_KEY
        assert _server()._get_hyper3d_api_key() == config.RODIN_FREE_TRIAL_KEY
        config.clear_session_credentials()
        assert _server()._get_hyper3d_api_key() == config.RODIN_FREE_TRIAL_KEY
    finally:
        config.clear_session_credentials()
        if hasattr(bpy.context.scene, "dshbb_hyper3d_mode"):
            del bpy.context.scene.dshbb_hyper3d_mode


def test_hunyuan_url_keeps_its_default_and_env_fallback(monkeypatch):
    _prefs(monkeypatch)
    _clear_env(monkeypatch)
    server = _server()
    assert server._get_hunyuan3d_api_url() == "http://localhost:8081"

    monkeypatch.setenv("DSHBB_HUNYUAN3D_API_URL", "http://127.0.0.1:9100")
    assert server._get_hunyuan3d_api_url() == "http://127.0.0.1:9100"

    monkeypatch.delenv("DSHBB_HUNYUAN3D_API_URL")
    monkeypatch.setenv("BLENDERMCP_HUNYUAN3D_API_URL", "http://127.0.0.1:9200")
    assert server._get_hunyuan3d_api_url() == "http://127.0.0.1:9200"
