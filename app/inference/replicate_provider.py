"""
Replicate implementation of InferenceProvider — image-to-image restyling
via Flux Kontext Pro. Takes an uploaded merchant photo + prompt, returns
a professionally restyled version.
"""
import asyncio
import io
import logging
import time

import replicate

from app.core.config import Settings
from app.inference.base import GeneratedImage, InferenceError, InferenceProvider

logger = logging.getLogger(__name__)

# Maps settings.OUTPUT_IMAGE_FORMAT -> the MIME type stored on GeneratedImage.
# Add an entry here if OUTPUT_IMAGE_FORMAT is ever set to a format not listed.
_FORMAT_TO_CONTENT_TYPE = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
}


class ReplicateRestyleProvider(InferenceProvider):
    def __init__(self, settings: Settings):
        self._settings = settings
        self._client = replicate.Client(api_token=settings.REPLICATE_API_TOKEN)

    async def generate(
        self,
        *,
        prompt: str,
        model: str,
        size: str,
        input_image: bytes,
    ) -> GeneratedImage:
        return await asyncio.to_thread(self._generate_sync, prompt, model, input_image)

    def _generate_sync(self, prompt: str, model: str, input_image: bytes) -> GeneratedImage:
        settings = self._settings
        last_err: Exception | None = None
        output_format = settings.OUTPUT_IMAGE_FORMAT

        logger.info(
            "Replicate restyle starting: model=%s, prompt_len=%d, image_size_bytes=%d, output_format=%s",
            model, len(prompt), len(input_image), output_format,
        )

        for attempt in range(1, settings.MAX_RETRIES + 1):
            try:
                prediction = self._client.predictions.create(
                    model=model,
                    input={
                        "prompt": prompt,
                        "image": io.BytesIO(input_image),
                    },
                )
                logger.info("Replicate prediction created: id=%s, status=%s", prediction.id, prediction.status)

                poll_start = time.time()
                while prediction.status not in ("succeeded", "failed", "canceled"):
                    if time.time() - poll_start > settings.RESTYLE_POLL_TIMEOUT_SECONDS:
                        raise InferenceError(
                            f"Replicate prediction {prediction.id} timed out "
                            f"(last status: {prediction.status})",
                            retryable=True,
                        )
                    time.sleep(3)
                    prediction.reload()

                if prediction.status == "failed":
                    logger.error(
                        "Replicate prediction FAILED. id=%s error=%r logs=%r input=%r",
                        prediction.id,
                        prediction.error,
                        getattr(prediction, "logs", None),
                        {k: (v if k != "image" else "<bytes omitted>")
                         for k, v in (prediction.input or {}).items()},
                    )
                    raise InferenceError(
                        f"Replicate prediction failed: {prediction.error or '(no error message returned)'} "
                        f"[id={prediction.id}, check https://replicate.com/p/{prediction.id}]",
                        retryable=True,
                    )
                if prediction.status == "canceled":
                    raise InferenceError("Replicate prediction was canceled", retryable=True)

                output = prediction.output
                file_obj = output[0] if isinstance(output, list) else output
                image_bytes = file_obj.read() if hasattr(file_obj, "read") else self._download(str(file_obj))

                content_type = _FORMAT_TO_CONTENT_TYPE.get(output_format.lower())
                if content_type is None:
                    raise InferenceError(
                        f"OUTPUT_IMAGE_FORMAT '{output_format}' has no known content-type mapping. "
                        f"Add it to _FORMAT_TO_CONTENT_TYPE in replicate_provider.py.",
                        retryable=False,
                    )

                return GeneratedImage(
                    content=image_bytes,
                    content_type=content_type,
                    provider="replicate",
                    model=model,
                    cost_usd=settings.RESTYLE_PRICE_PER_IMAGE_USD,
                )

            except InferenceError as e:
                last_err = e
                if not e.retryable or attempt == settings.MAX_RETRIES:
                    raise

            except replicate.exceptions.ReplicateError as e:
                # Map Replicate's own error type to retryable/non-retryable by
                # HTTP status, instead of falling through to the generic
                # except-Exception branch below (which used to retry
                # everything 3x — including permanent failures like "402
                # insufficient credit", wasting retries and tripping rate
                # limits).
                status = getattr(e, "status", None) or getattr(e, "status_code", None)
                if status == 402:
                    raise InferenceError(
                        f"Replicate account has insufficient credit: {e}. "
                        f"Add credit at https://replicate.com/account/billing#billing",
                        retryable=False,
                    ) from e
                if status == 429 or (status is not None and status >= 500):
                    last_err = e
                    logger.warning("Replicate transient error (attempt %s/%s, status=%s): %s",
                                    attempt, settings.MAX_RETRIES, status, e)
                    if attempt == settings.MAX_RETRIES:
                        raise InferenceError(f"Replicate call failed after retries: {e}", retryable=True) from e
                else:
                    raise InferenceError(f"Replicate rejected request (status={status}): {e}", retryable=False) from e

            except Exception as e:
                last_err = e
                logger.exception("Unexpected exception calling Replicate (attempt %s)", attempt)
                if attempt == settings.MAX_RETRIES:
                    raise InferenceError(f"Replicate call failed after retries: {e}", retryable=True) from e

            backoff = settings.RETRY_BACKOFF_SECONDS * attempt
            logger.warning("Replicate call failed (attempt %s/%s): %s — retrying in %.1fs",
                           attempt, settings.MAX_RETRIES, last_err, backoff)
            time.sleep(backoff)

        raise InferenceError(f"Replicate call failed: {last_err}", retryable=False)

    @staticmethod
    def _download(url: str) -> bytes:
        import requests
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
        return resp.content


def get_restyle_provider(settings: Settings) -> InferenceProvider:
    return ReplicateRestyleProvider(settings)