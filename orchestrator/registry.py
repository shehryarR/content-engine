from providers.base import StageProvider
from providers.stub_intake import StubIntakeProvider
from providers.stub_script import StubScriptProvider
from providers.stub_voice import StubVoiceProvider
from providers.stub_avatar import StubAvatarProvider
from providers.stub_sync import StubSyncProvider
from providers.stub_captions import StubCaptionsProvider
from providers.stub_assembly import StubAssemblyProvider
from providers.stub_qc import StubQCProvider
from providers.stub_disclosure import StubDisclosureProvider
from providers.stub_publish import StubPublishProvider
from providers.openai_whisper_captions import OpenAIWhisperCaptionsProvider
from orchestrator.provider_config import load_provider_config

_providers: dict[str,StageProvider] ={}


def register(provider:StageProvider)->None:
    _providers[provider.capability]=provider


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

def register_all_stubs() -> None:
    
    
    

    register(StubIntakeProvider())
    register(StubScriptProvider())
    register(StubVoiceProvider())
    register(StubAvatarProvider())
    register(StubSyncProvider())
    register(StubCaptionsProvider())
    register(StubAssemblyProvider())
    register(StubQCProvider())
    register(StubDisclosureProvider())
    register(StubPublishProvider())

def try_register_real_providers() -> list[str]:
    """
    Attempt to register real providers where API keys are configured.
    Returns list of capabilities that registered real providers.
    """
    real = []
    try:
        
        cfg = load_provider_config('script_generation')
        if cfg.get('api_key'):
            from providers.gemini_script import GeminiScriptProvider
            register(GeminiScriptProvider())
            real.append('script_generation')
    except Exception as e:
        print(f'[registry] script_generation real provider unavailable: {e}')
    try:
        cfg = load_provider_config('caption_generation')
        if cfg.get('api_key') or cfg.get('model_size'):

            register(OpenAIWhisperCaptionsProvider())
            real.append('caption_generation')
    except Exception as e:
        print(f'[registry] caption_generation real provider unavailable: {e}')
    return real

register_all_stubs()