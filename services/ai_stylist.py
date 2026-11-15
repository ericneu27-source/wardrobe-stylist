import base64
import io
import json
import os
import time
from typing import Literal
from PIL import Image, ImageDraw, ImageOps
from pydantic import BaseModel, Field
from google import genai
from google.genai import types
from models import GarmentAnalysis, DailyOutfitPlan

MODEL_ID = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")


class _FastGridItem(BaseModel):
    ymin: int = Field(description="Top edge of item (0-1000), inside grey corner icons")
    xmin: int = Field(description="Left edge of item (0-1000)")
    ymax: int = Field(description="Bottom edge of item (0-1000)")
    xmax: int = Field(description="Right edge of item (0-1000)")
    name: str = Field(description="Short descriptive name, e.g., 'Navy Tipped Knit Polo'")
    category: Literal["top", "mid_layer", "outerwear", "bottom", "footwear", "accessory"]
    primary_color: str
    secondary_color: str = Field(description="Accent color, or 'none' if solid")
    warmth: int = Field(description="1 (hot summer) to 5 (deep winter)")
    formality: int = Field(description="1 (gym/lounge) to 5 (formal)")
    water_resistant: bool
    max_wears_before_wash: int = Field(
        description="1 for tees/polos, 3-4 for sweaters/chinos, 10+ for denim/jackets/shoes"
    )
    style_notes: str = Field(description="Brief fabric and pairing note")


class _FastGridResponse(BaseModel):
    items: list[_FastGridItem] = Field(
        description="Complete clothing or footwear items visible in the 2-column grid (max 8)"
    )


def get_client() -> genai.Client:
    return genai.Client(
        api_key=os.getenv("GEMINI_API_KEY"),
        http_options=types.HttpOptions(timeout=90_000),
    )


def _thinking_config(model_id: str) -> types.ThinkingConfig:
    level = types.ThinkingLevel.MINIMAL if "lite" in model_id else types.ThinkingLevel.LOW
    return types.ThinkingConfig(thinking_level=level)


def _generate_fast(client: genai.Client, label: str, **kwargs):
    """Runs generation with live PC terminal timing and 429 rate-limit backoff."""
    for attempt in range(1, 3):
        start = time.time()
        print(f"[AI Stylist] Starting {label} on {MODEL_ID} (attempt {attempt}/2)...", flush=True)
        try:
            resp = client.models.generate_content(**kwargs)
            print(f"[AI Stylist] Finished {label} in {time.time() - start:.1f}s", flush=True)
            return resp
        except Exception as e:
            err_str = str(e)
            print(f"[AI Stylist] Error during {label}: {err_str}", flush=True)
            if ("429" in err_str or "RESOURCE_EXHAUSTED" in err_str) and attempt < 2:
                time.sleep(12)
            else:
                raise


def create_clean_cutout(image_bytes: bytes) -> bytes:
    """Removes background and tightly crops transparent borders for single photos."""
    from rembg import remove

    cutout_bytes = remove(image_bytes)
    img = Image.open(io.BytesIO(cutout_bytes)).convert("RGBA")

    alpha_bbox = img.getchannel("A").getbbox()
    if alpha_bbox:
        img = img.crop(alpha_bbox)

    img.thumbnail((900, 900), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def analyze_garment_image(cutout_png_bytes: bytes) -> GarmentAnalysis:
    """Extracts structured wardrobe metadata from a single garment cutout."""
    client = get_client()
    response = _generate_fast(
        client,
        label="single garment analysis",
        model=MODEL_ID,
        contents=[
            types.Part.from_bytes(data=cutout_png_bytes, mime_type="image/png"),
            (
                "Catalog this clothing item for a personal wardrobe app. "
                "If worn by a cropped store model, catalog only the featured garment."
            ),
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=GarmentAnalysis,
            max_output_tokens=1024,
            thinking_config=_thinking_config(MODEL_ID),
        ),
    )
    return GarmentAnalysis.model_validate_json(response.text)


def extract_whering_grid_items(screenshot_bytes: bytes) -> list[dict]:
    """Detects, crops, and catalogs all complete garments from a Whering grid screenshot."""
    client = get_client()

    full_img = ImageOps.exif_transpose(Image.open(io.BytesIO(screenshot_bytes))).convert("RGB")

    api_img = full_img.copy()
    api_img.thumbnail((800, 800), Image.Resampling.LANCZOS)
    api_buf = io.BytesIO()
    api_img.save(api_buf, format="JPEG", quality=80)

    prompt = (
        "Extract up to 8 complete clothing or footwear items from this 2-column Whering grid.\n\n"
        "1. Set ymin, xmin, ymax, xmax (0-1000) tightly around ONLY the item, excluding grey corner eye/heart icons.\n"
        "2. Skip heavily cut-off scroll-edge items, headers, and the black '+' button.\n"
        "3. On cropped store models, catalog only the showcased garment."
    )

    response = _generate_fast(
        client,
        label="Whering grid scan",
        model=MODEL_ID,
        contents=[
            types.Part.from_bytes(data=api_buf.getvalue(), mime_type="image/jpeg"),
            prompt,
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=_FastGridResponse,
            max_output_tokens=2048,
            thinking_config=_thinking_config(MODEL_ID),
        ),
    )

    parsed = _FastGridResponse.model_validate_json(response.text)
    width, height = full_img.size
    extracted = []

    for item in parsed.items[:10]:
        if item.ymax <= item.ymin or item.xmax <= item.xmin:
            continue

        pad_x = int(width * 0.01)
        pad_y = int(height * 0.01)

        left = max(0, int((item.xmin / 1000.0) * width) - pad_x)
        top = max(0, int((item.ymin / 1000.0) * height) - pad_y)
        right = min(width, int((item.xmax / 1000.0) * width) + pad_x)
        bottom = min(height, int((item.ymax / 1000.0) * height) + pad_y)

        if (right - left) < 40 or (bottom - top) < 40:
            continue

        crop = full_img.crop((left, top, right, bottom))
        crop.thumbnail((800, 800), Image.Resampling.LANCZOS)
        crop_buf = io.BytesIO()
        crop.save(crop_buf, format="PNG")

        metadata = item.model_dump(exclude={"ymin", "xmin", "ymax", "xmax"})
        extracted.append(
            {
                "cutout_bytes": crop_buf.getvalue(),
                "metadata": metadata,
            }
        )

    return extracted


def _paste_garment_tile(canvas: Image.Image, draw: ImageDraw.ImageDraw, img_bytes: bytes, box: tuple[int, int, int, int]):
    """Draws a rounded white lookbook tile and centers the garment inside it."""
    x1, y1, x2, y2 = box
    draw.rounded_rectangle(box, radius=24, fill=(255, 255, 255), outline=(226, 224, 218), width=2)

    raw = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
    white_bg = Image.new("RGBA", raw.size, (255, 255, 255, 255))
    merged = Image.alpha_composite(white_bg, raw).convert("RGB")

    pad = 18
    max_w = max(40, (x2 - x1) - pad * 2)
    max_h = max(40, (y2 - y1) - pad * 2)
    merged.thumbnail((max_w, max_h), Image.Resampling.LANCZOS)

    px = x1 + ((x2 - x1) - merged.width) // 2
    py = y1 + ((y2 - y1) - merged.height) // 2
    canvas.paste(merged, (px, py))


def compose_outfit_board(garments_with_bytes: list[tuple[dict, bytes]]) -> bytes:
    """Stitches all outfit items onto a compact 2-column moodboard grid ($0 instant preview)."""
    W, H = 760, 880
    canvas = Image.new("RGB", (W, H), (243, 242, 238))
    draw = ImageDraw.Draw(canvas)

    order = {"top": 0, "mid_layer": 1, "outerwear": 2, "bottom": 3, "footwear": 4, "accessory": 5}
    sorted_items = sorted(garments_with_bytes, key=lambda pair: order.get(pair[0].get("category", ""), 9))

    upper = [p for p in sorted_items if p[0].get("category") in ("top", "mid_layer", "outerwear")]
    bottoms = [p for p in sorted_items if p[0].get("category") == "bottom"]
    lower_right = [p for p in sorted_items if p[0].get("category") in ("footwear", "accessory")]

    if not upper or not bottoms:
        cols = 2
        rows = max(1, (len(sorted_items) + 1) // 2)
        cell_w = (W - 72) // cols
        cell_h = (H - 24 * (rows + 1)) // rows
        for i, (_, g_bytes) in enumerate(sorted_items):
            r, c = divmod(i, cols)
            bx1 = 24 + c * (cell_w + 24)
            by1 = 24 + r * (cell_h + 24)
            _paste_garment_tile(canvas, draw, g_bytes, (bx1, by1, bx1 + cell_w, by1 + cell_h))
    elif len(upper) == 1 and len(bottoms) == 1 and len(lower_right) == 1:
        _paste_garment_tile(canvas, draw, upper[0][1], (24, 24, 368, 520))
        _paste_garment_tile(canvas, draw, lower_right[0][1], (24, 540, 368, 856))
        _paste_garment_tile(canvas, draw, bottoms[0][1], (392, 24, 736, 856))
    else:
        n_up = len(upper)
        gap = 20
        up_w = (W - 48 - gap * (n_up - 1)) // n_up
        for i, (_, g_bytes) in enumerate(upper):
            ux1 = 24 + i * (up_w + gap)
            _paste_garment_tile(canvas, draw, g_bytes, (ux1, 24, ux1 + up_w, 405))

        _paste_garment_tile(canvas, draw, bottoms[0][1], (24, 425, 368, 856))

        if lower_right:
            n_lr = len(lower_right)
            lr_h = (856 - 425 - gap * (n_lr - 1)) // n_lr
            for i, (_, g_bytes) in enumerate(lower_right):
                ly1 = 425 + i * (lr_h + gap)
                _paste_garment_tile(canvas, draw, g_bytes, (392, ly1, 736, ly1 + lr_h))

    out = io.BytesIO()
    canvas.save(out, format="JPEG", quality=90)
    return out.getvalue()


def generate_outfit_tryon(
    garments_with_bytes: list[tuple[dict, bytes]],
    profile_photo_bytes: bytes | None = None,
) -> bytes:
    """Renders all outfit items worn together on the user's photo (or a studio lookbook model)."""
    client = get_client()
    contents = []

    order = {"top": 0, "mid_layer": 1, "outerwear": 2, "bottom": 3, "footwear": 4, "accessory": 5}
    sorted_garments = sorted(
        garments_with_bytes, key=lambda pair: order.get(pair[0].get("category", ""), 9)
    )[:8]

    if profile_photo_bytes:
        contents.append(types.Part.from_bytes(data=profile_photo_bytes, mime_type="image/jpeg"))

    item_lines = []
    offset = 2 if profile_photo_bytes else 1
    for idx, (garment, g_bytes) in enumerate(sorted_garments, start=offset):
        raw = Image.open(io.BytesIO(g_bytes)).convert("RGBA")
        bg = Image.new("RGBA", raw.size, (255, 255, 255, 255))
        flat = Image.alpha_composite(bg, raw).convert("RGB")
        flat.thumbnail((768, 768), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        flat.save(buf, format="JPEG", quality=88)
        contents.append(types.Part.from_bytes(data=buf.getvalue(), mime_type="image/jpeg"))
        item_lines.append(
            f"- Image {idx} ({garment['category']}): {garment['name']} "
            f"(colors: {garment['primary_color']} / {garment.get('secondary_color', 'none')})"
        )

    items_block = "\n".join(item_lines)
    if profile_photo_bytes:
        prompt = (
            "Image 1 is the person who will wear this outfit. The remaining images are the exact clothing and footwear items from their closet:\n"
            f"{items_block}\n\n"
            "Generate a single photorealistic, full-body head-to-toe photo of the exact person from Image 1 wearing ALL of these items together simultaneously. "
            "Preserve the person's face, head/hair/beard, skin tone, and physical build from Image 1, completely replacing their original clothes and shoes with the provided garments. "
            "Layer upper garments naturally (for example, wearing a mid_layer crewneck sweatshirt or jacket over a collared shirt so the collar peeks out cleanly). "
            "Ignore any cropped store-model bodies inside the clothing reference images—transfer only the exact garments, colors, trims, and shoes onto the person from Image 1 in a natural standing pose against a clean neutral background."
        )
    else:
        prompt = (
            "The attached images show individual clothing and footwear pieces for one complete men's outfit:\n"
            f"{items_block}\n\n"
            "Generate a single photorealistic, full-body head-to-toe studio lookbook photo of one adult male model wearing ALL of these exact items together simultaneously. "
            "Layer upper garments naturally (for example, wearing any mid_layer crewneck sweatshirt or jacket over the base collared shirt so the collar peeks out cleanly). "
            "Ignore any cropped store-model torsos in the reference images and faithfully reproduce the exact garment colors, collar/heel trims, fabric textures, pant silhouette, and sneakers against a clean neutral off-white studio background."
        )

    contents.append(prompt)

    models_to_try = []
    custom_img_model = os.getenv("GEMINI_IMAGE_MODEL", "").strip()
    if custom_img_model:
        models_to_try.append(custom_img_model)
    for m in ("gemini-nano-banana-2.1", "gemini-3.1-flash-image"):
        if m not in models_to_try:
            models_to_try.append(m)

    last_err = None
    for img_model in models_to_try:
        try:
            start = time.time()
            print(f"[AI Stylist] Rendering virtual try-on via {img_model}...", flush=True)
            resp = client.models.generate_content(
                model=img_model,
                contents=contents,
                config=types.GenerateContentConfig(
                    response_modalities=["TEXT", "IMAGE"],
                    image_config=types.ImageConfig(aspect_ratio="3:4", image_size="1K"),
                    thinking_config=types.ThinkingConfig(
                        thinking_level=types.ThinkingLevel.MINIMAL,
                    ),
                ),
            )
            candidates = getattr(resp, "candidates", None) or []
            parts = (
                candidates[0].content.parts
                if candidates and candidates[0].content
                else (getattr(resp, "parts", None) or [])
            )
            generated_bytes = None
            for part in parts:
                if getattr(part, "thought", False):
                    continue
                inline = getattr(part, "inline_data", None)
                if inline and getattr(inline, "data", None):
                    raw_data = inline.data
                    generated_bytes = (
                        base64.b64decode(raw_data) if isinstance(raw_data, str) else bytes(raw_data)
                    )
            if generated_bytes:
                print(f"[AI Stylist] Finished try-on render in {time.time() - start:.1f}s", flush=True)
                return generated_bytes
        except Exception as e:
            last_err = e
            print(f"[AI Stylist] Image model {img_model} error ({e}), trying next...", flush=True)
            if "FreeTier" in str(e) or "limit: 0" in str(e):
                break

    if "FreeTier" in str(last_err) or "limit: 0" in str(last_err):
        raise RuntimeError(
            "Your GEMINI_API_KEY in .env is still on the Free Tier (limit: 0 for image output). "
            "Paste a key from a billing-enabled Google AI Studio / Firebase Blaze project into .env and restart Streamlit."
        )
    raise RuntimeError(f"Could not render outfit preview: {last_err}")


def generate_daily_outfits(
    clean_items: list[dict],
    weather_brief: dict,
    schedule_text: str,
) -> DailyOutfitPlan:
    """Ranks 2-3 outfits from clean items using weather slices and schedule context."""
    client = get_client()

    compact_closet = [
        {
            "id": item["id"],
            "name": item["name"],
            "category": item["category"],
            "colors": f"{item['primary_color']} / {item['secondary_color']}",
            "warmth": item["warmth"],
            "formality": item["formality"],
            "water_resistant": item["water_resistant"],
            "last_worn": item.get("last_worn", "never"),
            "style_notes": item["style_notes"],
        }
        for item in clean_items
    ]

    prompt = f"""
    Build 2 to 3 complete outfits ONLY using item IDs from the closet below.

    TODAY'S HOURLY WEATHER SLICES:
    {json.dumps(weather_brief, indent=2)}

    TODAY'S SCHEDULE & ACTIVITIES:
    {schedule_text or "Flexible day — casual errands and working from home."}

    AVAILABLE CLEAN CLOSET:
    {json.dumps(compact_closet, indent=2)}

    STYLING RULES:
    1. Include at least a top, bottom, and footwear (add mid_layer/outerwear when feels-like temp is below 16°C or for morning/evening swings).
    2. Match formality to the highest-formality non-workout event on the schedule.
    3. Prioritize water_resistant footwear/outerwear if rain or snow probability is 40% or higher.
    4. Avoid items worn yesterday unless outerwear/footwear, and favor cohesive color palettes.
    """

    response = _generate_fast(
        client,
        label="daily outfit generation",
        model=MODEL_ID,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=DailyOutfitPlan,
            max_output_tokens=2048,
            thinking_config=_thinking_config(MODEL_ID),
        ),
    )
    return DailyOutfitPlan.model_validate_json(response.text)