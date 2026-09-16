"""
fal.ai implementation of InferenceProvider — image-to-image restyling via
Flux 2 Turbo Edit (fal-ai/flux-2/turbo/edit). Takes an uploaded merchant
photo + prompt, returns a professionally restyled version.

fal's edit endpoint takes image URLs, not raw bytes, so the source photo is
uploaded to fal's CDN first (fal_client.upload_file) and the resulting URL
is passed as `image_urls`.
"""
import asyncio
import logging
import os
import tempfile
import time
from pathlib import Path

import fal_client
import httpx

from app.core.config import Settings
from app.inference.base import GeneratedImage, InferenceError, InferenceProvider

logger = logging.getLogger(__name__)

# Maps settings.OUTPUT_IMAGE_FORMAT -> the MIME type stored on GeneratedImage.
# fal's edit endpoint always returns PNG regardless of input format, so this
# only matters for how we label the bytes we store — add an entry here if
# OUTPUT_IMAGE_FORMAT is ever set to something not listed.
_FORMAT_TO_CONTENT_TYPE = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
}


class FalRestyleProvider(InferenceProvider):
    def __init__(self, settings: Settings):
        self._settings = settings
        # fal_client reads its key from the FAL_KEY env var — set it here so
        # callers don't need to export it separately from .env.
        if settings.FAL_KEY:
            os.environ["FAL_KEY"] = settings.FAL_KEY

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
            "fal restyle starting: model=%s, prompt_len=%d, image_size_bytes=%d, output_format=%s",
            model, len(prompt), len(input_image), output_format,
        )

        for attempt in range(1, settings.MAX_RETRIES + 1):
            try:
                image_url = self._upload_source_image(input_image)

                # subscribe() submits, polls the queue, and fetches the
                # result in one blocking call (equivalent to the old
                # Replicate create+poll+reload loop, just internalized by
                # fal_client). Overall wall-clock is still bounded by RQ's
                # own RESTYLE_JOB_TIMEOUT_SECONDS on the enclosing job.
                result = fal_client.subscribe(
                    model,
                    arguments={
                        "prompt": prompt,
                        "image_urls": [image_url],
                    },
                    with_logs=False,
                )

                images = result.get("images") or []
                if not images:
                    # NOTE: fal_client.subscribe() doesn't hand back a
                    # separate "handler" object with a request_id in this
                    # call style — `result` IS the final response. There is
                    # nothing else to identify the request by here, so we
                    # just dump the (small) result payload for debugging.
                    raise InferenceError(
                        f"fal request returned no images [result={result!r}]",
                        retryable=True,
                    )

                output_url = images[0]["url"]
                image_bytes = self._download(output_url)

                content_type = _FORMAT_TO_CONTENT_TYPE.get(output_format.lower())
                if content_type is None:
                    raise InferenceError(
                        f"OUTPUT_IMAGE_FORMAT '{output_format}' has no known content-type mapping. "
                        f"Add it to _FORMAT_TO_CONTENT_TYPE in fal_provider.py.",
                        retryable=False,
                    )

                return GeneratedImage(
                    content=image_bytes,
                    content_type=content_type,
                    provider="fal",
                    model=model,
                    cost_usd=settings.RESTYLE_PRICE_PER_IMAGE_INR,
                )

            except InferenceError as e:
                last_err = e
                if not e.retryable or attempt == settings.MAX_RETRIES:
                    raise

            except fal_client.client.FalClientError as e:
                # Distinguish permanent request errors (bad prompt, invalid
                # key, validation) from transient ones (rate limit, 5xx) so
                # we don't burn retries on failures that will never succeed.
                status = getattr(e, "status_code", None)
                if status == 401 or status == 403:
                    raise InferenceError(
                        f"fal.ai rejected the API key (status={status}): {e}",
                        retryable=False,
                    ) from e
                if status == 429 or (status is not None and status >= 500):
                    last_err = e
                    logger.warning("fal transient error (attempt %s/%s, status=%s): %s",
                                    attempt, settings.MAX_RETRIES, status, e)
                    if attempt == settings.MAX_RETRIES:
                        raise InferenceError(f"fal call failed after retries: {e}", retryable=True) from e
                else:
                    raise InferenceError(f"fal rejected request (status={status}): {e}", retryable=False) from e

            except Exception as e:
                last_err = e
                logger.exception("Unexpected exception calling fal.ai (attempt %s)", attempt)
                if attempt == settings.MAX_RETRIES:
                    raise InferenceError(f"fal call failed after retries: {e}", retryable=True) from e

            backoff = settings.RETRY_BACKOFF_SECONDS * attempt
            logger.warning("fal call failed (attempt %s/%s): %s — retrying in %.1fs",
                           attempt, settings.MAX_RETRIES, last_err, backoff)
            time.sleep(backoff)

        raise InferenceError(f"fal call failed: {last_err}", retryable=False)

    @staticmethod
    def _upload_source_image(input_image: bytes) -> str:
        """
        fal's edit endpoint needs a URL, not raw bytes. Write to a temp file
        and hand it to fal_client.upload_file, which pushes it to fal's CDN
        and returns a URL. (Deliberately not using encode_file/data-URLs —
        those inline the whole image as base64 in the request, which bloats
        payload size for nothing.)
        """
        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as tmp:
            tmp.write(input_image)
            tmp_path = tmp.name
        try:
            return fal_client.upload_file(tmp_path)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    @staticmethod
    def _download(url: str) -> bytes:
        resp = httpx.get(url, timeout=60)
        resp.raise_for_status()
        return resp.content


def get_restyle_provider(settings: Settings) -> InferenceProvider:
    return FalRestyleProvider(settings)