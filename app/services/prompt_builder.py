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
    "arrangement, same shape, same texture, same proportions. Do not "
    "redraw, resculpt, smooth, sharpen, or reinterpret the food's geometry "
    "or surface detail - copy its actual appearance faithfully, only "
    "re-lighting, re-compositing, and re-framing it as instructed below.\n"
    "2. Remove any hand, fingers, arm, or body part holding or touching the "
    "food or its container.\n"
    "3. Replace the background with a light neutral commercial food-"
    "photography surface - for example pale wood, light stone, matte "
    "concrete, or a soft light-grey seamless backdrop. Do NOT make the "
    "background a flat, pure-white, texture-less void with no visible "
    "surface - the plate/bowl must appear to be resting on a real surface, "
    "not floating in empty white space. Remove ALL background elements "
    "unrelated to the dish - including plants, leaves, foliage, greenery, "
    "furniture, walls, floor tiles, decor, and any other object in the "
    "original photo - along with all clutter, other dishes, packaging, "
    "cables, stains, and messy table surface from the original shot. The "
    "final background must contain nothing except the chosen studio "
    "surface/backdrop.\n"
    "4. Add professional studio lighting with realistic soft shadows and "
    "highlights that follow the food's actual shape - do not add glow, "
    "haze, plastic sheen, or painterly softness to the food itself. "
    "Include a soft, visible contact shadow directly beneath the plate/"
    "bowl/container so it looks grounded on the surface, with a subtle "
    "natural falloff/vignette toward the edges of the frame rather than "
    "flat, shadowless, uniform white lighting throughout.\n"
    "5. Add a shallow depth of field so the food is tack-sharp and the "
    "background gently blurs.\n"
    "6. Correct the camera angle and framing to a standard professional "
    "commercial food-photography composition - a clean three-quarter "
    "elevated angle or straight-on eye-level angle, centered in frame, as "
    "if shot on a tripod directly above/in front of the plate. Do NOT "
    "preserve an awkward, tilted, handheld, or top-down phone-photo angle "
    "from the source image - correct it to look like a deliberate studio "
    "shot. This applies only to camera angle/framing/composition - the "
    "food's own geometry, shape, and proportions from instruction 1 must "
    "still be kept identical.\n"
    "Do not change the output's aspect ratio. Do not add any digital "
    "painting, illustration, HDR, or oversharpened look - the result must "
    "look like an unedited, in-camera DSLR photograph with natural noise "
    "and texture, not an AI-generated or artificial rendering. No text, no "
    "watermark, no logos."
)

# DISABLED. Previously consumed one-per-generation: index 0 for the
# merchant's initial upload, index 1 for their first "Regenerate" click,
# etc (see job_service.create_restyle_batch / regenerate_restyle, which
# still set ImageJob.variation_index to the attempt's position in the
# batch — that tracking is untouched, it's just no longer used to pick a
# style here).
#
# The base prompt (_RESTYLE_BASE) plus the model's own generation
# randomness on each run is what now drives variation between attempts in
# a batch, instead of rotating through this fixed list of lighting/
# background directives. Left commented out (rather than deleted) in case
# this is reinstated later — nothing else in this file depends on it while
# it's disabled (see build_restyle_prompt below).
#
# RESTYLE_VARIATION_STYLES: list[str] = [
#     "Style direction: seamless white/light-grey studio backdrop. Lighting: "
#     "one large soft key light directly above and slightly in front, "
#     "daylight-balanced (~5500K), producing soft, minimal shadows directly "
#     "under the food. Camera angle: three-quarter, slightly elevated.",
#     "Style direction: natural wood, stone, or marble surface background. "
#     "Lighting: one soft key light from the upper-left at roughly 45 "
#     "degrees, warm color temperature (~3200K), casting soft directional "
#     "shadows to the lower-right of the food. Camera angle: elevated "
#     "45-degree angle.",
#     "Style direction: dark, solid-color or gradient background. Lighting: "
#     "one hard key light from directly to one side at a low angle, neutral "
#     "color temperature (~4500K), producing defined highlights and a "
#     "visible shadow line across the food for dramatic contrast. Camera "
#     "angle: straight-on, eye-level.",
#     "Style direction: soft pastel-colored seamless backdrop. Lighting: two "
#     "large soft diffused lights from front-left and front-right, "
#     "daylight-balanced (~5500K), producing an almost shadowless, evenly "
#     "lit look. Camera angle: direct top-down flat-lay.",
#     "Style direction: blurred upscale restaurant interior in the "
#     "background (shallow depth of field, strong bokeh). Lighting: one "
#     "soft key light from the side at table height, golden-hour warm "
#     "color temperature (~2800K), casting long soft shadows. Camera "
#     "angle: close three-quarter, at table height.",
#     "Style direction: deep solid jewel-tone background (emerald, "
#     "burgundy, or navy). Lighting: single soft key light from one side, "
#     "neutral color temperature (~4500K), with gentle falloff into shadow "
#     "on the opposite side of the food. Camera angle: slightly elevated.",
#     "Style direction: rustic outdoor daylight setting (garden or patio "
#     "table, softly blurred). Lighting: natural sunlight from one side, "
#     "daylight color temperature (~6000K), producing soft, slightly long "
#     "shadows. Camera angle: relaxed three-quarter.",
#     "Style direction: minimalist concrete or slate-grey backdrop. "
#     "Lighting: one soft key light from directly above, cool color "
#     "temperature (~6500K), producing crisp, well-defined highlights and "
#     "short shadows. Camera angle: straight-on, eye-level.",
# ]


def build_restyle_prompt(extra_styling: str | None, variation_index: int = 0) -> str:
    """
    RESTYLE_VARIATION_STYLES is disabled (see above) — this no longer
    rotates through a fixed list of lighting/background directives.
    Every call now sends the same _RESTYLE_BASE instructions; visible
    difference between attempts in a batch comes from the model's own
    generation randomness on each run, not from prompt variation.

    variation_index is still accepted (job_service still passes it, and
    ImageJob still records it) but is intentionally unused here now.
    """
    parts = [_RESTYLE_BASE]

    if extra_styling:
        parts.append(f"Additional styling: {extra_styling}.")

    return " ".join(parts)