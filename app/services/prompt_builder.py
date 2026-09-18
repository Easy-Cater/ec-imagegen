"""
Builds the prompt sent to the restyle (image-to-image) provider. This
service is restyle-only — there is no text-to-image wizard-based prompt
builder, only the restyle prompt.

Prompt phrasing is written for instruction-following edit models (e.g.
flux-kontext-dev) — numbered, concrete edit steps rather than descriptive/
narrative language, since these models parse prompts as instructions to
execute, not as a scene description to render from scratch.

variation_index behavior:
  - index 0 (the merchant's initial upload — first click on "Restyle")
    uses _FIRST_SURFACE_INSTRUCTION for step 3 — lets the model freely
    choose the surface. This is the exact original behavior that was
    already proven to produce strong, reliable first results.
  - index 1+ (each "Regenerate" click) uses one entry from
    RESTYLE_VARIATION_STYLES for step 3 instead, so each regenerate
    attempt gets a visibly different, deliberately-chosen surface rather
    than leaving it to the model's own randomness.

Both cases share the exact same surrounding instructions (steps 1, 2, 4,
5, 6 + the white-background constraint) via _build_prompt() — only step 3
differs, so there's one template to maintain, not two duplicated blocks.
"""

# Step 3 used only for variation_index == 0 — the model freely picks the
# surface. Unchanged from the original prompt that was already working.
_FIRST_SURFACE_INSTRUCTION = (
    "Freely choose ONE realistic, richly textured commercial food-"
    "photography surface for the background - for example dark walnut "
    "wood, pale oak wood, matte concrete, natural stone, slate, marble, "
    "linen fabric, or a bold solid-color seamless backdrop (any color "
    "except white/cream/ivory/pale-grey). Whatever you choose, its color "
    "and texture must be clearly visible and distinguishable from the "
    "plate - never wash it out to white or near-white."
)

# Step 3 used for variation_index >= 1 — one specific, deliberately
# chosen surface per regenerate attempt. Ordered best-first for
# commercial food photography. (variation_index - 1) selects into this
# list via modulo, so regenerate attempts cycle through visibly
# different looks: 1st regenerate = style[0], 2nd = style[1], etc.
RESTYLE_VARIATION_STYLES: list[str] = [
    # 1. Warm wood — most reliably appetite-appealing surface in real
    # commercial food photography. Warm color temperature makes food
    # look fresher and more inviting.
    "Use a warm honey or walnut-toned wooden table, grain clearly "
    "visible and in focus near the plate. Light it with one large soft "
    "key light from the upper-left at roughly 45 degrees, warm color "
    "temperature (~3200K), casting soft directional shadows to the "
    "lower-right of the food. Camera angle: three-quarter, slightly "
    "elevated - the standard professional food-menu angle.",

    # 2. Dark moody / slate — premium restaurant-menu look, strong
    # contrast makes food colors pop.
    "Use a dark charcoal or slate-grey stone surface with visible "
    "natural texture. Light it with one soft key light from directly "
    "to one side at a low angle, neutral-warm color temperature "
    "(~4000K), producing defined highlights on the food and a soft but "
    "visible shadow for dramatic depth. Camera angle: close "
    "three-quarter, at table height for an intimate, premium feel.",

    # 3. Natural stone/marble — clean, modern, works across cuisines.
    "Use a light-to-mid grey natural stone or marble surface with "
    "visible veining and texture (clearly not flat white - the stone's "
    "grey/beige tones and grain must be obviously visible). Light it "
    "with one soft key light from directly above and slightly in "
    "front, daylight-balanced (~5200K), producing soft, minimal shadows "
    "directly under the food. Camera angle: straight-down "
    "three-quarter, slightly elevated.",

    # 4. Rustic outdoor daylight — warm, homestyle, great for comfort food.
    "Use a rustic wooden patio or garden table in soft natural "
    "daylight, with a gently blurred hint of greenery in the far "
    "background (heavy bokeh, unrecognizable as distinct objects). "
    "Light it with natural warm daylight from one side (~5000K), "
    "producing soft, slightly long natural shadows. Camera angle: "
    "relaxed three-quarter, at table height.",

    # 5. Jewel-tone backdrop — bold, makes colorful dishes pop.
    "Use a deep solid jewel-tone backdrop (emerald green, deep "
    "burgundy, or navy blue - choose whichever complements the dish's "
    "own colors best). Light it with a single soft key light from one "
    "side, neutral color temperature (~4500K), with gentle falloff "
    "into shadow on the opposite side of the food for depth. Camera "
    "angle: slightly elevated three-quarter.",

    # 6. Blurred restaurant interior — good variety, kept lower priority
    # since bokeh shapes are more prone to odd artifacts on some models.
    "Use a blurred, warmly-lit upscale restaurant interior in the "
    "background (shallow depth of field, strong soft bokeh, no "
    "recognizable objects or text). Light it with one soft key light "
    "from the side at table height, golden-hour warm color temperature "
    "(~2800K), casting long soft shadows. Camera angle: close "
    "three-quarter, at table height.",

    # # 7. Maximum-quality neutral studio setup — clean, premium, highest
    # # fidelity look. Camera angle left as an editable placeholder so it
    # # can be swapped manually per generation.
    # "Use a premium, richly textured neutral surface (your choice of "
    # "warm light oak wood, honed dark stone, or a matte mid-grey solid "
    # "backdrop - never white/cream/pale-grey) rendered in the highest "
    # "possible fidelity: crisp micro-texture and grain fully resolved, "
    # "no softness, no blur, no compression artifacts anywhere except the "
    # "intentional background bokeh. Light it with one large soft "
    # "key light plus a subtle fill light to control contrast, neutral-"
    # "warm color temperature (~4200K), producing clean, well-defined "
    # "highlights and a soft, natural contact shadow with realistic "
    # "falloff. Camera angle: [MANUALLY SET CAMERA ANGLE HERE - e.g. "
    # "'straight-down flat lay', 'three-quarter at table height', "
    # "'close-up macro three-quarter'] - use professional food-menu "
    # "framing at that angle, sharply focused on the food with maximum "
    # "clarity and detail retention.",
    # 7. Maximum-quality neutral studio setup — clean, premium, highest
    # fidelity look.
    "Use a premium, richly textured neutral surface (your choice of "
    "warm light oak wood, honed dark stone, or a matte mid-grey solid "
    "backdrop - never white/cream/pale-grey) rendered in the highest "
    "possible fidelity: crisp micro-texture and grain fully resolved, "
    "no softness, no blur, no compression artifacts anywhere except the "
    "intentional background bokeh. Light it with one large soft "
    "key light plus a subtle fill light to control contrast, neutral-"
    "warm color temperature (~4200K), producing clean, well-defined "
    "highlights and a soft, natural contact shadow with realistic "
    "falloff. Camera angle: three-quarter view, slightly elevated, "
    "shot at a natural table-height perspective - the standard "
    "professional food-menu angle - sharply focused on the food with "
    "maximum clarity and detail retention.",
]


def _build_prompt(surface_instruction: str) -> str:
    """
    Shared template used for BOTH the first generation and every
    regenerate attempt. Only step 3 (surface/lighting/angle) is injected
    per-call — steps 1, 2, 4, 5, 6 and the white-background constraint
    are identical every time, defined here once.
    """
    return (
        "Edit this food photo. CRITICAL CONSTRAINT: the background must "
        "NEVER be plain white, off-white, or a pale featureless void. It "
        "must always be a clearly visible, textured, colored real-world "
        "surface. Follow these instructions exactly and in order:\n"
        "1. Do not change the food itself in any way: keep the exact same "
        "dish, same ingredients, same portion size, same plate/bowl/"
        "container, same arrangement, same shape, same texture, same "
        "proportions. Do not redraw, resculpt, smooth, sharpen, or "
        "reinterpret the food's geometry or surface detail - copy its "
        "actual appearance faithfully, only re-lighting, re-compositing, "
        "and re-framing it as instructed below.\n"
        "2. Remove any hand, fingers, arm, or body part holding or "
        "touching the food or its container.\n"
        f"3. {surface_instruction} The plate/bowl must clearly rest ON "
        "this surface with the surface's color and texture visible "
        "around it. Remove ALL background elements unrelated to the "
        "dish - including plants, leaves, foliage, furniture, walls, "
        "floor tiles, decor, and any other object in the original photo "
        "- along with all clutter, other dishes, packaging, cables, "
        "stains, and messy table surface from the original shot.\n"
        "4. Add professional studio lighting with realistic soft shadows "
        "and highlights that follow the food's actual shape - do not add "
        "glow, haze, plastic sheen, or painterly softness to the food "
        "itself. Include a soft, visible contact shadow directly beneath "
        "the plate/bowl/container so it looks grounded on the surface, "
        "with a subtle natural falloff/vignette toward the edges of the "
        "frame rather than flat, shadowless, uniform lighting throughout.\n"
        "5. Add a shallow depth of field so the food is tack-sharp and "
        "the background gently blurs.\n"
        "6. Correct the camera angle and framing to a standard "
        "professional commercial food-photography composition, as "
        "specified above - centered in frame, as if shot on a tripod. Do "
        "NOT preserve an awkward, tilted, handheld, or top-down "
        "phone-photo angle from the source image. This applies only to "
        "camera angle/framing/composition - the food's own geometry, "
        "shape, and proportions from instruction 1 must still be kept "
        "identical.\n"
        "Do not change the output's aspect ratio. Do not add any digital "
        "painting, illustration, HDR, or oversharpened look - the result "
        "must look like an unedited, in-camera DSLR photograph with "
        "natural noise and texture, not an AI-generated or artificial "
        "rendering. No text, no watermark, no logos. FINAL REMINDER: the "
        "background must be a clearly colored, clearly textured real "
        "surface - a white, cream, or pale-grey empty background is a "
        "failed result and not acceptable."
    )


def build_restyle_prompt(extra_styling: str | None, variation_index: int = 0) -> str:
    """
    index 0 -> model freely picks the surface (unchanged, proven
    first-generation behavior). index 1+ -> a specific style from
    RESTYLE_VARIATION_STYLES, selected via (variation_index - 1) %
    len(...) so regenerate attempts cycle 0,1,2,3,4,5,0,1,... instead of
    skipping style #1 on the first regenerate.
    """
    if variation_index == 0:
        surface_instruction = _FIRST_SURFACE_INSTRUCTION
    else:
        surface_instruction = RESTYLE_VARIATION_STYLES[
            (variation_index - 1) % len(RESTYLE_VARIATION_STYLES)
        ]

    base = _build_prompt(surface_instruction)

    parts = [base]
    if extra_styling:
        parts.append(f"Additional styling: {extra_styling}.")

    return " ".join(parts)