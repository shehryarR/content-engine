"""
orchestrator/registry.py

Capability -> provider resolution.

M4 Day 1 (Owner A) change: provider selection is now expressible in
configs/runs/*.yaml. Before this, a capability resolved to an
implementation by (a) a hardcoded stub default, then (b) whether an API
key happened to be present. There was no way for a run config to say
"use faceless_mixed_media for avatar_render", which is precisely what
M4's modality swap requires.

Resolution order, lowest to highest precedence:
    1. stub default            (always registered first, so every
                                capability always has an implementation)
    2. API-key auto-detection  (preserved M1-M3 behaviour)
    3. explicit run config     (`providers:` block) -- authoritative

Nothing here touches contracts/ or graph/. A capability name is still the
only thing the graph knows; vendor names live in this catalog and in run
config, which is where the M0 architecture rules put them.
"""
from providers.base import StageProvider
from providers.stub.stub_intake import StubIntakeProvider
from providers.stub.stub_script import StubScriptProvider
from providers.stub.stub_voice import StubVoiceProvider
from providers.stub.stub_avatar import StubAvatarProvider
from providers.stub.stub_sync import StubSyncProvider
from providers.stub.stub_captions import StubCaptionsProvider
from providers.real.assembly import AssemblyProvider
from providers.stub.stub_qc import StubQCProvider
from providers.stub.stub_disclosure import StubDisclosureProvider
from providers.stub.stub_publish import StubPublishProvider
from providers.real.openai_whisper_captions import OpenAIWhisperCaptionsProvider
from providers.real.gemini_script import GeminiScriptProvider
from providers.real.openai_script import OpenAIScriptProvider
from providers.real.did_avatar import DIDAvatarProvider
from providers.real.elevenlabs_voice import ElevenLabsVoiceProvider
from orchestrator.provider_config import load_provider_config
from providers.real.faceless_mixed_media import FacelessMixedMediaProvider
from providers.real.narration_conform import NarrationConformProvider

_providers: dict[str, StageProvider] = {}


# ── Capability catalog ────────────────────────────────────────────────────────
# capability -> {provider_name: implementation_class}
#
# Adding a modality means adding rows here plus a provider module. It does
# not mean touching graph/ or contracts/. M4's faceless providers land as
# two new entries under avatar_render and media_sync.
_PROVIDER_CATALOG: dict[str, dict[str, type]] = {
    "intake":             {"stub": StubIntakeProvider},
    "script_generation":  {"stub": StubScriptProvider,
                           "openai": OpenAIScriptProvider,
                           "gemini": GeminiScriptProvider},
    "voice_synthesis":    {"stub": StubVoiceProvider,
                           "elevenlabs": ElevenLabsVoiceProvider},
    "avatar_render":      {"stub": StubAvatarProvider,
                           "did": DIDAvatarProvider},
    "media_sync":         {"stub": StubSyncProvider},
    "caption_generation": {"stub": StubCaptionsProvider,
                           "openai_whisper": OpenAIWhisperCaptionsProvider},
    "assembly":           {"stub": AssemblyProvider},
    "quality_control":    {"stub": StubQCProvider},
    "disclosure_check":   {"stub": StubDisclosureProvider},
    "publish":            {"stub": StubPublishProvider},
    "avatar_render":      {"stub": StubAvatarProvider,
                           "did": DIDAvatarProvider,
                           "faceless_mixed_media": FacelessMixedMediaProvider},
    "media_sync":         {"stub": StubSyncProvider,
                           "narration_conform": NarrationConformProvider},
}

# Which provider a capability auto-selects when an API key is present and
# the run config is silent. Preserves exact M1-M3 behaviour.
_KEYED_DEFAULT: dict[str, str] = {
    "script_generation":  "openai",
    "voice_synthesis":    "elevenlabs",
    "avatar_render":      "did",
    "caption_generation": "openai_whisper",
}


class ProviderResolutionError(RuntimeError):
    """A run config named a provider that isn't in the catalog."""


def register(provider: StageProvider) -> None:
    _providers[provider.capability] = provider


def get(capability: str) -> StageProvider:
    if capability not in _providers:
        raise KeyError(
            f"No provider registered for capability '{capability}'. "
            f"Registered: {list(_providers.keys())}"
        )
    return _providers[capability]


def clear() -> None:
    """Remove all registrations. Useful for testing."""
    _providers.clear()


def catalog() -> dict[str, list[str]]:
    """Every selectable provider name, per capability. Used by the CLI's
    `providers` subcommand and by the M4 seam-audit evidence."""
    return {cap: sorted(impls) for cap, impls in _PROVIDER_CATALOG.items()}


def register_all_stubs() -> None:
    for capability, impls in _PROVIDER_CATALOG.items():
        register(impls["stub"]())


def _instantiate(capability: str, name: str) -> StageProvider:
    impls = _PROVIDER_CATALOG.get(capability)
    if impls is None:
        raise ProviderResolutionError(
            f"Unknown capability '{capability}'. "
            f"Valid capabilities: {sorted(_PROVIDER_CATALOG)}"
        )
    cls = impls.get(name)
    if cls is None:
        raise ProviderResolutionError(
            f"Unknown provider '{name}' for capability '{capability}'. "
            f"Valid options: {sorted(impls)}. "
            f"If this provider is being built, add it to _PROVIDER_CATALOG "
            f"in orchestrator/registry.py -- not to graph/ or contracts/."
        )
    return cls()


def try_register_real_providers() -> list[str]:
    """
    Register real providers wherever an API key is configured. Unchanged
    in behaviour from M1-M3: this is the *default* when a run config
    doesn't name a provider explicitly.
    """
    real: list[str] = []

    for capability, default_name in _KEYED_DEFAULT.items():
        try:
            cfg = load_provider_config(capability)
            has_key = bool(cfg.get("api_key")) or (
                capability == "caption_generation" and bool(cfg.get("model_size"))
            )
            if not has_key:
                continue

            # script_generation keeps its `provider:` override in
            # configs/providers/script_generation.yaml.
            name = (cfg.get("provider") or default_name).lower()
            register(_instantiate(capability, name))
            real.append(capability)
            print(f"[registry] {capability}: keyed auto-select -> {name}")
        except Exception as exc:
            print(f"[registry] {capability} real provider unavailable: {exc}")

    return real


def register_from_run_config(providers: dict[str, str] | None = None) -> dict[str, str]:
    """
    Full resolution for one run. Call this instead of calling
    register_all_stubs() + try_register_real_providers() by hand.

    `providers` is the run config's `providers:` block, e.g.

        providers:
          avatar_render: faceless_mixed_media
          media_sync: narration_conform

    Explicit entries win over key-presence auto-detection. An unknown name
    raises rather than silently falling back to a stub -- a faceless run
    that quietly rendered an avatar would pass M4's diff and fail its
    purpose.

    Returns the resolved capability -> provider_name map, which the caller
    should record in evidence alongside the run's telemetry.
    """
    clear()
    register_all_stubs()
    resolved = {cap: "stub" for cap in _PROVIDER_CATALOG}

    for capability in try_register_real_providers():
        cfg = load_provider_config(capability)
        resolved[capability] = (
            cfg.get("provider") or _KEYED_DEFAULT[capability]
        ).lower()

    for capability, name in (providers or {}).items():
        register(_instantiate(capability, name))
        resolved[capability] = name
        print(f"[registry] {capability}: run config -> {name}")

    return resolved