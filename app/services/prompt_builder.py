"""
Builds the prompt sent to the restyle (image-to-image) provider. This
service is restyle-only — there is no text-to-image wizard-based prompt
builder, only the restyle prompt.

Prompt phrasing is written for instruction-following edit models (e.g.
flux-kontext-dev) — numbered, concrete edit steps rather than descriptive/
narrative language, since these models parse prompts as instructions to
execute, not as a scene description to render from scratch.

style_index behavior:
  - The merchant picks one entry of RESTYLE_VARIATION_STYLES after uploading
    the photo; that entry is used for step 3 of the prompt.
  - Index 0 is the "Clean Studio" style: a seamless light-grey (#F7F7F7)
    backdrop, like the photos on food-delivery app menus. It uses a slightly
    different prompt wrapper (see _CLEAN_STYLE_INDEX / _build_prompt) because
    every other style FORBIDS a plain/pale background.
  - Each "Regenerate" is assigned a different, not-yet-used style by
    job_service.regenerate_restyle, and it arrives here the same way.
  - style_index=None (regenerates after every curated style has been used,
    and legacy rows) lets the model freely choose the surface via
    _FIRST_SURFACE_INSTRUCTION.

Both cases share the same surrounding instructions via _build_prompt() —
only the surface step (and, for the clean style, the background rules)
differs, so there's one template to maintain, not two duplicated blocks.
"""

# Index of the light-grey / clean studio style inside RESTYLE_VARIATION_STYLES.
# It is the FIRST option shown to the merchant.
_CLEAN_STYLE_INDEX = 0

# Step 3 used when style_index is None — the model freely picks the
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

# One specific, deliberately chosen surface per style. The list index is the
# style_index stored on ImageJob. Index 0 = Clean Studio (light grey).
RESTYLE_VARIATION_STYLES: list[str] = [
    # 0. Clean light-grey studio — matches the look of food-delivery app
    # menu photos (measured background ~ RGB 247,247,247 / #F7F7F7 with a
    # soft ~#E4E4E4 contact shadow). Uses the "clean" prompt wrapper.
    "Use a seamless, perfectly clean, evenly lit light-grey studio "
    "backdrop, a solid uniform color of approximately #F7F7F7 (RGB 247, "
    "247, 247), like a professional food-delivery-app menu photo. No "
    "texture, no pattern, no gradient banding, no visible horizon line, "
    "table edge, or props. Light it with a large, soft, diffused "
    "overhead-front light, neutral daylight color temperature (~5500K), "
    "producing a soft, subtle light-grey contact shadow (about #E4E4E4) "
    "directly beneath and slightly to the lower-right of the food. "
    "Camera angle: three-quarter view, slightly elevated - the standard "
    "e-commerce menu-photo angle.",

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


# Short merchant-facing names, SAME ORDER as RESTYLE_VARIATION_STYLES.
RESTYLE_STYLE_LABELS: list[str] = [
    "Clean Studio",
    "Warm Wood",
    "Dark Slate",
    "Marble",
    "Rustic Outdoor",
    "Jewel Tone",
    "Restaurant Blur",
    "Premium Studio",
]

assert len(RESTYLE_STYLE_LABELS) == len(RESTYLE_VARIATION_STYLES), (
    "RESTYLE_STYLE_LABELS and RESTYLE_VARIATION_STYLES must have the same length"
)


def get_style_options() -> list[dict]:
    """Options shown to the merchant after they upload a photo."""
    return [{"index": i, "label": label} for i, label in enumerate(RESTYLE_STYLE_LABELS)]


def _build_prompt(surface_instruction: str, *, clean_background: bool = False) -> str:
    """
    Shared template used for BOTH the first generation and every
    regenerate attempt. Only the surface step is injected per-call.

    clean_background=False (all textured styles): identical to the
    original prompt — background must NEVER be plain white/pale.
    clean_background=True (Clean Studio style): the opening constraint,
    the shadow/vignette wording in step 4 and the FINAL REMINDER are
    flipped, because the original ones forbid exactly what this style wants.
    """
    if clean_background:
        opening = (
            "Edit this food photo. CRITICAL CONSTRAINT: the background must "
            "be a seamless, perfectly clean, uniform light-grey (#F7F7F7) "
            "professional studio backdrop - no texture, no pattern, no "
            "props, no clutter, no visible edges. Follow these instructions "
            "exactly and in order:\n"
        )
        step3_tail = (
            " The plate/bowl must rest naturally on this backdrop with a "
            "soft grounded shadow. Remove ALL background elements "
            "unrelated to the dish - including plants, leaves, foliage, "
            "furniture, walls, floor tiles, decor, and any other object in "
            "the original photo - along with all clutter, other dishes, "
            "packaging, cables, stains, and messy table surface from the "
            "original shot.\n"
        )
        step4 = (
            "4. Add clean, soft, even professional studio lighting with "
            "realistic soft shadows and highlights that follow the food's "
            "actual shape - do not add glow, haze, plastic sheen, or "
            "painterly softness to the food itself. Include a soft, "
            "subtle light-grey contact shadow directly beneath the "
            "plate/bowl/container so it looks grounded. Keep the backdrop "
            "evenly lit with NO dark vignette and NO dark edges.\n"
        )
        step5 = (
            "5. Keep the food tack-sharp with crisp detail, on a smooth, "
            "uniform backdrop.\n"
        )
        final = (
            "FINAL REMINDER: the background must be a uniform, clean, "
            "light-grey (about #F7F7F7) studio backdrop - a colored, "
            "textured, dark, or cluttered background is a failed result "
            "and not acceptable."
        )
    else:
        opening = (
            "Edit this food photo. CRITICAL CONSTRAINT: the background must "
            "NEVER be plain white, off-white, or a pale featureless void. It "
            "must always be a clearly visible, textured, colored real-world "
            "surface. Follow these instructions exactly and in order:\n"
        )
        step3_tail = (
            " The plate/bowl must clearly rest ON "
            "this surface with the surface's color and texture visible "
            "around it. Remove ALL background elements unrelated to the "
            "dish - including plants, leaves, foliage, furniture, walls, "
            "floor tiles, decor, and any other object in the original photo "
            "- along with all clutter, other dishes, packaging, cables, "
            "stains, and messy table surface from the original shot.\n"
        )
        step4 = (
            "4. Add professional studio lighting with realistic soft shadows "
            "and highlights that follow the food's actual shape - do not add "
            "glow, haze, plastic sheen, or painterly softness to the food "
            "itself. Include a soft, visible contact shadow directly beneath "
            "the plate/bowl/container so it looks grounded on the surface, "
            "with a subtle natural falloff/vignette toward the edges of the "
            "frame rather than flat, shadowless, uniform lighting throughout.\n"
        )
        step5 = (
            "5. Add a shallow depth of field so the food is tack-sharp and "
            "the background gently blurs.\n"
        )
        final = (
            "FINAL REMINDER: the "
            "background must be a clearly colored, clearly textured real "
            "surface - a white, cream, or pale-grey empty background is a "
            "failed result and not acceptable."
        )

    return (
        opening
        + "1. Do not change the food itself in any way: keep the exact same "
        "dish, same ingredients, same portion size, same plate/bowl/"
        "container, same arrangement, same shape, same texture, same "
        "proportions. Do not redraw, resculpt, smooth, sharpen, or "
        "reinterpret the food's geometry or surface detail - copy its "
        "actual appearance faithfully, only re-lighting, re-compositing, "
        "and re-framing it as instructed below.\n"
        "2. Remove any hand, fingers, arm, or body part holding or "
        "touching the food or its container.\n"
        f"3. {surface_instruction}"
        + step3_tail
        + step4
        + step5
        + "6. Correct the camera angle and framing to a standard "
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
        "rendering. No text, no watermark, no logos. "
        + final
    )


def build_restyle_prompt(extra_styling: str | None, style_index: int | None = None) -> str:
    """
    style_index -> the merchant-selected (or auto-assigned regenerate) entry
    of RESTYLE_VARIATION_STYLES. None -> model freely picks the surface.
    """
    clean = False
    if style_index is None:
        surface_instruction = _FIRST_SURFACE_INSTRUCTION
    else:
        if not 0 <= style_index < len(RESTYLE_VARIATION_STYLES):
            raise ValueError(f"style_index {style_index} out of range")
        surface_instruction = RESTYLE_VARIATION_STYLES[style_index]
        clean = style_index == _CLEAN_STYLE_INDEX

    base = _build_prompt(surface_instruction, clean_background=clean)

    parts = [base]
    if extra_styling:
        parts.append(f"Additional styling: {extra_styling}.")

    return " ".join(parts)