"""
Builds the prompt sent to the restyle (image-to-image) provider. This
service is restyle-only — there is no text-to-image wizard-based prompt
builder, only the restyle prompt.

Prompt phrasing is written for instruction-following edit models (e.g.
flux-kontext-dev) — numbered, concrete edit steps rather than descriptive/
narrative language, since these models parse prompts as instructions to
execute, not as a scene description to render from scratch. Vague
descriptive language left more room for the model to reinterpret the
food's structure/texture ("weird noise", flat lighting) than explicit
"do this, don't do that" steps do.
"""

_RESTYLE_BASE = (
    "Edit this food photo. Follow these instructions exactly and in order:\n"
    "1. Do not change the food itself in any way: keep the exact same dish, "
    "same ingredients, same portion size, same plate/bowl/container, same "
    "arrangement, same shape, same texture, same proportions, same crop of "
    "the food within the frame. Do not redraw, resculpt, smooth, sharpen, "
    "or reinterpret the food's geometry or surface detail - copy it "
    "pixel-for-pixel in appearance, only re-lighting and re-compositing it.\n"
    "2. Remove any hand, fingers, arm, or body part holding or touching the "
    "food or its container.\n"
    "3. Replace the background and surface entirely with a clean commercial "
    "food-photography backdrop (see style direction below). Remove all "
    "clutter, other dishes, packaging, cables, stains, and messy table "
    "surface from the original shot.\n"
    "4. Add professional studio lighting as specified in the style "
    "direction below, with realistic soft shadows and highlights that "
    "follow the food's actual shape - do not add glow, haze, plastic "
    "sheen, or painterly softness to the food itself.\n"
    "5. Add a shallow depth of field so the food is tack-sharp and the "
    "background gently blurs.\n"
    "6. Keep the food at the same camera angle and framing as the source "
    "photo, only correcting an awkward tilt or crop if present - do not "
    "reframe to a different angle or distance.\n"
    "Do not change the output's aspect ratio. Do not add any digital "
    "painting, illustration, HDR, or oversharpened look - the result must "
    "look like an unedited, in-camera DSLR photograph with natural noise "
    "and texture, not an AI-generated or artificial rendering. No text, no "
    "watermark, no logos."
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
# plate, or dish identity, which the base prompt already locks down. Each
# lighting direction is stated concretely (light position, quality, color
# temperature) rather than mood words alone, since the edit model follows
# specific instructions more reliably than adjectives like "warm" or
# "moody" on their own.
#
# ImageJob.variation_index records exactly which of these was used for a
# given row, so later we can analyze which styles particular merchants
# prefer (see models.ImageJob docstring) — no behavior depends on that
# analysis yet, this is just data collection for now.
RESTYLE_VARIATION_STYLES: list[str] = [
    "Style direction: seamless white/light-grey studio backdrop. Lighting: "
    "one large soft key light directly above and slightly in front, "
    "daylight-balanced (~5500K), producing soft, minimal shadows directly "
    "under the food. Camera angle: three-quarter, slightly elevated.",
    "Style direction: natural wood, stone, or marble surface background. "
    "Lighting: one soft key light from the upper-left at roughly 45 "
    "degrees, warm color temperature (~3200K), casting soft directional "
    "shadows to the lower-right of the food. Camera angle: elevated "
    "45-degree angle.",
    "Style direction: dark, solid-color or gradient background. Lighting: "
    "one hard key light from directly to one side at a low angle, neutral "
    "color temperature (~4500K), producing defined highlights and a "
    "visible shadow line across the food for dramatic contrast. Camera "
    "angle: straight-on, eye-level.",
    "Style direction: soft pastel-colored seamless backdrop. Lighting: two "
    "large soft diffused lights from front-left and front-right, "
    "daylight-balanced (~5500K), producing an almost shadowless, evenly "
    "lit look. Camera angle: direct top-down flat-lay.",
    "Style direction: blurred upscale restaurant interior in the "
    "background (shallow depth of field, strong bokeh). Lighting: one "
    "soft key light from the side at table height, golden-hour warm "
    "color temperature (~2800K), casting long soft shadows. Camera "
    "angle: close three-quarter, at table height.",
    "Style direction: deep solid jewel-tone background (emerald, "
    "burgundy, or navy). Lighting: single soft key light from one side, "
    "neutral color temperature (~4500K), with gentle falloff into shadow "
    "on the opposite side of the food. Camera angle: slightly elevated.",
    "Style direction: rustic outdoor daylight setting (garden or patio "
    "table, softly blurred). Lighting: natural sunlight from one side, "
    "daylight color temperature (~6000K), producing soft, slightly long "
    "shadows. Camera angle: relaxed three-quarter.",
    "Style direction: minimalist concrete or slate-grey backdrop. "
    "Lighting: one soft key light from directly above, cool color "
    "temperature (~6500K), producing crisp, well-defined highlights and "
    "short shadows. Camera angle: straight-on, eye-level.",
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