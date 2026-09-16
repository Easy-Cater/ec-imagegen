"""
Builds the prompt sent to the restyle (image-to-image) provider. This
service is restyle-only — there is no text-to-image wizard-based prompt
builder, only the restyle prompt.
"""

_RESTYLE_BASE = (
    "Turn this into a professional commercial food photograph. The dish "
    "itself must stay 100% identical - same food, same ingredients, same "
    "portion, same plate/bowl/container, same arrangement of items on it. "
    "Do not add, remove, rearrange, or restyle the food in any way. "
    "Everything else around the food should be rebuilt to professional "
    "studio quality: replace or clean up the background entirely with a "
    "clean, high-end commercial backdrop (neutral studio surface, softly "
    "blurred premium setting, or complementary color background), add "
    "realistic soft directional studio lighting with natural shadows and "
    "highlights on the food, add a shallow depth of field so the plate is "
    "sharp and the background gently falls out of focus, and remove any "
    "clutter, stains, distracting objects, cables, hands, or messy table "
    "surface from the original shot. "
    "This source photo may be a casual or low-quality phone photo - assume "
    "poor lighting, a messy or plain background, a slightly awkward angle, "
    "or an imperfect crop, and fully correct all of that. Reframe the shot "
    "to a proper professional product/food-photography composition: not an "
    "extreme close-up, not too far away, subject filling a natural, "
    "well-composed portion of the frame at a flattering three-quarter or "
    "slightly elevated angle, camera level and steady, no tilted horizon. "
    "Output should look like it was shot by a professional food "
    "photographer for a restaurant menu or ad - polished, appetizing, "
    "photorealistic, 4k, no text, no watermark, no logos."
)

# Consumed one-per-generation: index 0 is used for the merchant's initial
# upload, index 1 for their first "Regenerate" click, index 2 for the
# second, and so on (see job_service.create_restyle_batch /
# regenerate_restyle, which set ImageJob.variation_index to the attempt's
# position in the batch). Kept deliberately longer than the default
# settings.MAX_IMAGES_PER_BATCH so a merchant who regenerates a few times
# doesn't quickly loop back to a look they already rejected. If
# MAX_IMAGES_PER_BATCH is ever raised past len(this list) in .env, it wraps
# around (see build_restyle_prompt) rather than erroring — extend this list
# instead of relying on the wraparound for normal operation.
#
# Each entry only nudges lighting/angle/mood/background — never subject,
# plate, or dish identity, which the base prompt already locks down.
#
# ImageJob.variation_index records exactly which of these was used for a
# given row, so later we can analyze which styles particular merchants
# prefer (see models.ImageJob docstring) — no behavior depends on that
# analysis yet, this is just data collection for now.
RESTYLE_VARIATION_STYLES: list[str] = [
    "Style: clean seamless white/light-grey studio backdrop, bright even "
    "natural daylight, minimal soft shadows, top-down or three-quarter angle.",
    "Style: natural textured surface background (wood, stone, or marble), "
    "warm ambient restaurant lighting with soft directional shadows, "
    "slightly elevated 45-degree angle.",
    "Style: dark, moody solid-color or gradient background, dramatic "
    "high-contrast studio lighting with defined highlights, straight-on "
    "eye-level angle.",
    "Style: soft pastel-colored seamless backdrop, bright diffused "
    "lighting with almost no visible shadow, direct top-down flat-lay "
    "angle.",
    "Style: blurred upscale restaurant interior in the background (bokeh), "
    "warm golden-hour side lighting, close three-quarter angle at table "
    "height.",
    "Style: deep solid jewel-tone background (emerald, burgundy, or navy), "
    "single soft key light from one side creating gentle falloff, "
    "slightly elevated angle.",
    "Style: rustic outdoor daylight setting (garden or patio table, softly "
    "blurred), natural sunlight with light shadows, relaxed three-quarter "
    "angle.",
    "Style: minimalist concrete or slate-grey backdrop, cool-toned "
    "studio lighting with crisp highlights, straight-on eye-level angle.",
]


def build_restyle_prompt(extra_styling: str | None, variation_index: int = 0) -> str:
    """
    variation_index selects a rotating lighting/angle directive so a batch of
    restyle jobs produces visibly distinct results.
    """
    parts = [_RESTYLE_BASE]

    if RESTYLE_VARIATION_STYLES:
        style = RESTYLE_VARIATION_STYLES[variation_index % len(RESTYLE_VARIATION_STYLES)]
        parts.append(style)

    if extra_styling:
        parts.append(f"Additional styling: {extra_styling}.")

    return " ".join(parts)