"""
Replicate implementation of InferenceProvider — image-to-image restyling
via Flux img2img.

Takes an uploaded merchant photo + prompt, preprocesses the image to
dimensions compatible with the Flux model, and returns a professionally
restyled version.
"""
import asyncio
import io
import logging
import time

from PIL import Image

import replicate

from app.core.config import Settings
from app.inference.base import GeneratedImage, InferenceError, InferenceProvider

logger = logging.getLogger(__name__)

# Maps settings.OUTPUT_IMAGE_FORMAT -> the MIME type stored on GeneratedImage.
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
        return await asyncio.to_thread(
            self._generate_sync,
            prompt,
            model,
            input_image,
        )

    def _generate_sync(
        self,
        prompt: str,
        model: str,
        input_image: bytes,
    ) -> GeneratedImage:
        settings = self._settings
        last_err: Exception | None = None
        output_format = settings.OUTPUT_IMAGE_FORMAT

        # ------------------------------------------------------------
        # Validate and preprocess input image
        # ------------------------------------------------------------
        try:
            image = Image.open(io.BytesIO(input_image))

            original_width, original_height = image.size

            logger.info(
                "Replicate input image: width=%d height=%d mode=%s format=%s",
                image.width,
                image.height,
                image.mode,
                image.format,
            )

            # Convert to RGB for the image model.
            if image.mode != "RGB":
                image = image.convert("RGB")

            # --------------------------------------------------------
            # Normalize image dimensions for Flux.
            #
            # Flux works best with dimensions aligned to multiples of
            # 32. We preserve the aspect ratio and do not crop.
            # --------------------------------------------------------

            TARGET_MULTIPLE = 32

            width = image.width
            height = image.height

            # Round each dimension to the nearest multiple of 32.
            new_width = round(width / TARGET_MULTIPLE) * TARGET_MULTIPLE
            new_height = round(height / TARGET_MULTIPLE) * TARGET_MULTIPLE

            # Protect against dimensions becoming too small.
            new_width = max(TARGET_MULTIPLE, new_width)
            new_height = max(TARGET_MULTIPLE, new_height)

            if new_width != width or new_height != height:
                logger.info(
                    "Resizing image for Flux compatibility: "
                    "%dx%d -> %dx%d",
                    width,
                    height,
                    new_width,
                    new_height,
                )

                image = image.resize(
                    (new_width, new_height),
                    Image.Resampling.LANCZOS,
                )

            processed_image = io.BytesIO()

            image.save(
                processed_image,
                format="JPEG",
                quality=95,
                optimize=True,
            )

            processed_input_image = processed_image.getvalue()

            logger.info(
                "Replicate processed image: original=%dx%d "
                "processed=%dx%d bytes=%d",
                original_width,
                original_height,
                image.width,
                image.height,
                len(processed_input_image),
            )

        except Exception as e:
            raise InferenceError(
                f"Failed to preprocess input image: {e}",
                retryable=False,
            ) from e

        logger.info(
            "Replicate restyle starting: model=%s, prompt_len=%d, "
            "image_size_bytes=%d, output_format=%s",
            model,
            len(prompt),
            len(processed_input_image),
            output_format,
        )

        # ------------------------------------------------------------
        # Replicate inference
        # ------------------------------------------------------------
        for attempt in range(1, settings.MAX_RETRIES + 1):
            try:
                prediction = self._client.predictions.create(
                    version=model,
                    input={
                        "prompt": prompt,
                        "image": io.BytesIO(processed_input_image),
                    },
                )

                logger.info(
                    "Replicate prediction created: id=%s, status=%s",
                    prediction.id,
                    prediction.status,
                )

                # ----------------------------------------------------
                # Poll prediction
                # ----------------------------------------------------
                poll_start = time.time()

                while prediction.status not in (
                    "succeeded",
                    "failed",
                    "canceled",
                ):
                    if (
                        time.time() - poll_start
                        > settings.RESTYLE_POLL_TIMEOUT_SECONDS
                    ):
                        raise InferenceError(
                            f"Replicate prediction {prediction.id} timed out "
                            f"(last status: {prediction.status})",
                            retryable=True,
                        )

                    time.sleep(3)
                    prediction.reload()

                # ----------------------------------------------------
                # Prediction failed
                # ----------------------------------------------------
                if prediction.status == "failed":
                    error_message = (
                        prediction.error
                        or "(no error message returned)"
                    )

                    logger.error(
                        "Replicate prediction FAILED. id=%s error=%r "
                        "logs=%r input=%r",
                        prediction.id,
                        prediction.error,
                        getattr(prediction, "logs", None),
                        {
                            k: (
                                v
                                if k != "image"
                                else "<bytes omitted>"
                            )
                            for k, v in (
                                prediction.input or {}
                            ).items()
                        },
                    )

                    # Known deterministic model/input errors should not
                    # be retried because retrying the same input will
                    # produce the same failure and may cause rate limits.
                    non_retryable_errors = (
                        "Shape mismatch",
                        "shape mismatch",
                        "can't divide axis",
                        "EinopsError",
                        "Invalid input",
                        "invalid input",
                    )

                    retryable = not any(
                        error_text in error_message
                        for error_text in non_retryable_errors
                    )

                    raise InferenceError(
                        f"Replicate prediction failed: {error_message} "
                        f"[id={prediction.id}, "
                        f"check https://replicate.com/p/{prediction.id}]",
                        retryable=retryable,
                    )

                # ----------------------------------------------------
                # Prediction canceled
                # ----------------------------------------------------
                if prediction.status == "canceled":
                    raise InferenceError(
                        "Replicate prediction was canceled",
                        retryable=True,
                    )

                # ----------------------------------------------------
                # Extract output
                # ----------------------------------------------------
                output = prediction.output

                if not output:
                    raise InferenceError(
                        f"Replicate prediction {prediction.id} succeeded "
                        f"but returned no output.",
                        retryable=False,
                    )

                file_obj = (
                    output[0]
                    if isinstance(output, list)
                    else output
                )

                image_bytes = (
                    file_obj.read()
                    if hasattr(file_obj, "read")
                    else self._download(str(file_obj))
                )

                # ----------------------------------------------------
                # Validate output format
                # ----------------------------------------------------
                content_type = _FORMAT_TO_CONTENT_TYPE.get(
                    output_format.lower()
                )

                if content_type is None:
                    raise InferenceError(
                        f"OUTPUT_IMAGE_FORMAT '{output_format}' has no "
                        f"known content-type mapping. Add it to "
                        f"_FORMAT_TO_CONTENT_TYPE in "
                        f"replicate_provider.py.",
                        retryable=False,
                    )

                logger.info(
                    "Replicate restyle succeeded: prediction_id=%s "
                    "output_bytes=%d",
                    prediction.id,
                    len(image_bytes),
                )

                return GeneratedImage(
                    content=image_bytes,
                    content_type=content_type,
                    provider="replicate",
                    model=model,
                    cost_usd=settings.RESTYLE_PRICE_PER_IMAGE_USD,
                )

            # --------------------------------------------------------
            # Our normalized inference errors
            # --------------------------------------------------------
            except InferenceError as e:
                last_err = e

                if (
                    not e.retryable
                    or attempt == settings.MAX_RETRIES
                ):
                    raise

            # --------------------------------------------------------
            # Replicate API errors
            # --------------------------------------------------------
            except replicate.exceptions.ReplicateError as e:
                status = (
                    getattr(e, "status", None)
                    or getattr(e, "status_code", None)
                )

                # Insufficient credit
                if status == 402:
                    raise InferenceError(
                        f"Replicate account has insufficient credit: {e}. "
                        f"Add credit at "
                        f"https://replicate.com/account/billing#billing",
                        retryable=False,
                    ) from e

                # Rate limit / server errors
                if status == 429 or (
                    status is not None and status >= 500
                ):
                    last_err = e

                    logger.warning(
                        "Replicate transient error "
                        "(attempt %s/%s, status=%s): %s",
                        attempt,
                        settings.MAX_RETRIES,
                        status,
                        e,
                    )

                    if attempt == settings.MAX_RETRIES:
                        raise InferenceError(
                            f"Replicate call failed after retries: {e}",
                            retryable=True,
                        ) from e

                else:
                    # Other 4xx errors are deterministic.
                    raise InferenceError(
                        f"Replicate rejected request "
                        f"(status={status}): {e}",
                        retryable=False,
                    ) from e

            # --------------------------------------------------------
            # Unexpected exceptions
            # --------------------------------------------------------
            except Exception as e:
                last_err = e

                logger.exception(
                    "Unexpected exception calling Replicate "
                    "(attempt %s)",
                    attempt,
                )

                if attempt == settings.MAX_RETRIES:
                    raise InferenceError(
                        f"Replicate call failed after retries: {e}",
                        retryable=True,
                    ) from e

            # --------------------------------------------------------
            # Retry backoff
            # --------------------------------------------------------
            backoff = settings.RETRY_BACKOFF_SECONDS * attempt

            logger.warning(
                "Replicate call failed (attempt %s/%s): %s "
                "— retrying in %.1fs",
                attempt,
                settings.MAX_RETRIES,
                last_err,
                backoff,
            )

            time.sleep(backoff)

        raise InferenceError(
            f"Replicate call failed: {last_err}",
            retryable=False,
        )

    @staticmethod
    def _download(url: str) -> bytes:
        import requests

        resp = requests.get(url, timeout=60)
        resp.raise_for_status()

        return resp.content


def get_restyle_provider(settings: Settings) -> InferenceProvider:
    return ReplicateRestyleProvider(settings)