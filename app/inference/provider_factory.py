"""
Single place that decides which InferenceProvider implementation to use,
driven by settings.INFERENCE_PROVIDER (.env) so switching providers is a
config change, not a code change / redeploy.

worker.py imports get_restyle_provider from HERE, not from a specific
provider module directly — that's what keeps both fal_provider.py and
replicate_provider.py live and swappable instead of one silently rotting.
"""
from app.core.config import Settings
from app.inference.base import InferenceProvider

_SUPPORTED_PROVIDERS = ("fal", "replicate")


def get_restyle_provider(settings: Settings) -> InferenceProvider:
    provider = settings.INFERENCE_PROVIDER.strip().lower()

    if provider == "fal":
        from app.inference.fal_provider import get_restyle_provider as _get_fal
        return _get_fal(settings)

    if provider == "replicate":
        from app.inference.replicate_provider import get_restyle_provider as _get_replicate
        return _get_replicate(settings)

    raise ValueError(
        f"Unknown INFERENCE_PROVIDER '{settings.INFERENCE_PROVIDER}'. "
        f"Must be one of {_SUPPORTED_PROVIDERS} (set in .env)."
    )
