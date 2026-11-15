import hashlib
import os
import streamlit as st
from dotenv import load_dotenv

load_dotenv()
try:
    for _k, _v in st.secrets.items():
        if isinstance(_v, (str, int, float)):
            os.environ[_k] = str(_v)
except Exception:
    pass

from services.ai_stylist import (
    create_clean_cutout,
    analyze_garment_image,
    extract_whering_grid_items,
    generate_daily_outfits,
    compose_outfit_board,
    generate_outfit_tryon,
)
from services.context import fetch_today_weather
from services.storage import (
    save_garment,
    load_all_garments,
    load_garment_bytes,
    save_profile_photo,
    load_profile_photo,
    delete_profile_photo,
    load_cached_tryon,
    save_cached_tryon,
    delete_garment,
    log_outfit_worn,
    reset_laundry_hamper,
    get_firebase_clients,
    count_unsynced_local_items,
    migrate_local_to_firebase,
)

st.set_page_config(page_title="AI Wardrobe Stylist", page_icon="🧥", layout="wide")

app_pin = os.getenv("APP_PIN", "").strip()
if app_pin and not st.session_state.get("pin_unlocked"):
    st.title("🔒 Personal Wardrobe Stylist")
    entered_pin = st.text_input("Enter PIN to unlock", type="password")
    if st.button("Unlock", type="primary"):
        if entered_pin == app_pin:
            st.session_state["pin_unlocked"] = True
            st.rerun()
        else:
            st.error("Incorrect PIN.")
    st.stop()

CATEGORIES = ["top", "mid_layer", "outerwear", "bottom", "footwear", "accessory"]


@st.cache_data(ttl=1800)
def get_cached_weather():
    return fetch_today_weather()


def make_tryon_cache_key(item_ids: list[str], profile_bytes: bytes | None) -> str:
    subject_tag = hashlib.md5(profile_bytes).hexdigest()[:8] if profile_bytes else "studio"
    combo_tag = "-".join(sorted(item_ids))
    return f"{subject_tag}_{combo_tag}"


api_key = os.getenv("GEMINI_API_KEY", "")
if not api_key or api_key == "your_gemini_api_key_here":
    st.warning("⚠️ Add GEMINI_API_KEY to .streamlit/secrets.toml or .env and restart.")

db, _ = get_firebase_clients()
all_closet_items = load_all_garments()
profile_photo = load_profile_photo()

storage_badge = "☁️ Firebase Connected" if db else "💾 Local Disk Mode"
st.title("🧥 Personal Wardrobe Stylist")
st.caption(f"Storage: {storage_badge} · Closet Size: {len(all_closet_items)} items")

unsynced_count = count_unsynced_local_items()
if unsynced_count:
    with st.container(border=True):
        st.info(f"Found **{unsynced_count}** items in your local PC folder that aren't in Firebase yet.")
        if st.button(f"☁️ Sync {unsynced_count} Local Items to Firebase Now", type="primary"):
            with st.spinner("Uploading local closet images & try-ons to Firebase..."):
                moved = migrate_local_to_firebase()
                st.success(f"Synced {moved} items to Firebase!")
                st.rerun()

tab_today, tab_add, tab_closet = st.tabs(["🌤️ Today's Outfits", "➕ Add Clothing", "🧺 Closet & Laundry"])

# --- TAB 1: DAILY STYLIST ---
with tab_today:
    try:
        weather = get_cached_weather()
        c1, c2, c3 = st.columns(3)
        c1.metric(
            "Morning (8a–12p)",
            weather["morning_8am_12pm"]["feels_like_c"],
            f"{weather['morning_8am_12pm']['max_precip_prob_pct']}% precip",
        )
        c2.metric(
            "Afternoon (12p–5p)",
            weather["afternoon_12pm_5pm"]["feels_like_c"],
            f"{weather['afternoon_12pm_5pm']['max_precip_prob_pct']}% precip",
        )
        c3.metric(
            "Evening (5p–10p)",
            weather["evening_5pm_10pm"]["feels_like_c"],
            f"{weather['evening_5pm_10pm']['max_precip_prob_pct']}% precip",
        )
    except Exception as e:
        weather = {"error": str(e)}
        st.warning(f"Weather fetch issue: {e}")

    photo_status = "🟢 Personal Photo Saved" if profile_photo else "Optional — Upload Once to Try Outfits On Yourself"
    with st.expander(f"🪞 Virtual Try-On Photo ({photo_status})"):
        if profile_photo:
            p_col1, p_col2 = st.columns([1, 2])
            p_col1.image(profile_photo, width=140, caption="Your Reference Photo")
            with p_col2:
                st.caption("Tapping '🧍 Try Outfit On Me' renders clothes directly onto this photo.")
                if st.button("🗑️ Remove Photo (Use Studio Model Instead)"):
                    delete_profile_photo()
                    st.rerun()
        else:
            st.caption(
                "Upload one well-lit, standing head-to-toe photo in simple fitted clothes to see outfits on yourself. "
                "If left empty, '🧍 See Worn Together' renders outfits on a studio model."
            )
            new_profile = st.file_uploader(
                "Upload Full-Body Photo", type=["jpg", "jpeg", "png", "webp"], key="profile_uploader"
            )
            if new_profile and st.button("💾 Save My Try-On Photo", type="primary"):
                save_profile_photo(new_profile.getvalue())
                st.success("Saved! Tap '🧍 Try Outfit On Me' on any outfit below.")
                st.rerun()

    schedule = st.text_input(
        "Today's Schedule / Vibe",
        placeholder="e.g., 9:15 AM errands, 12:30 PM lunch, 3:15 PM pickup, casual evening at home",
    )

    clean_items = [i for i in all_closet_items if i.get("status") == "clean"]
    item_lookup = {i["id"]: i for i in all_closet_items}

    if st.button("✨ Generate Today's Outfits", type="primary", disabled=len(clean_items) < 3):
        with st.spinner("Checking weather swings, schedule, and clean closet..."):
            st.session_state["daily_plan"] = generate_daily_outfits(clean_items, weather, schedule)
            st.session_state["board_renders"] = {}

    if len(clean_items) < 3:
        st.info("Add at least a top, bottom, and pair of shoes in the '➕ Add Clothing' tab to generate outfits.")

    if "daily_plan" in st.session_state:
        plan = st.session_state["daily_plan"]
        st.session_state.setdefault("board_renders", {})

        st.success(f"**Weather Layering Note:** {plan.weather_takeaway}")

        for idx, outfit in enumerate(plan.outfits):
            with st.container(border=True):
                st.subheader(f"{idx + 1}. {outfit.title}")
                st.write(outfit.rationale)

                valid_items = [item_lookup[iid] for iid in outfit.item_ids if iid in item_lookup]
                garments_with_bytes = []
                for g in valid_items:
                    g_bytes = load_garment_bytes(g.get("image_ref", ""))
                    if g_bytes:
                        garments_with_bytes.append((g, g_bytes))

                cache_key = make_tryon_cache_key([g["id"] for g, _ in garments_with_bytes], profile_photo)
                tryon_bytes = load_cached_tryon(cache_key) if garments_with_bytes else None

                if garments_with_bytes:
                    if idx not in st.session_state["board_renders"]:
                        st.session_state["board_renders"][idx] = compose_outfit_board(garments_with_bytes)

                    if tryon_bytes:
                        view_choice = st.radio(
                            "View",
                            ["🧍 Worn Together", "🧩 Moodboard Grid"],
                            horizontal=True,
                            key=f"view_mode_{idx}",
                            label_visibility="collapsed",
                        )
                        img_to_display = (
                            tryon_bytes
                            if view_choice == "🧍 Worn Together"
                            else st.session_state["board_renders"][idx]
                        )
                        st.image(img_to_display, width=380)
                    else:
                        st.image(st.session_state["board_renders"][idx], width=380)

                with st.expander(f"📋 Pieces in this outfit ({len(valid_items)})"):
                    for garment in valid_items:
                        st.markdown(f"- **{garment['name']}** *({garment['category']})*")

                btn_col1, btn_col2 = st.columns(2)
                if tryon_bytes:
                    tryon_label = "🔄 Re-Render Try-On"
                else:
                    tryon_label = "🧍 Try Outfit On Me" if profile_photo else "🧍 See Worn Together"

                if btn_col1.button(tryon_label, key=f"tryon_{idx}", use_container_width=True):
                    with st.spinner("Rendering photorealistic try-on (~6–10s)..."):
                        try:
                            rendered = generate_outfit_tryon(garments_with_bytes, profile_photo)
                            save_cached_tryon(cache_key, rendered)
                            st.rerun()
                        except Exception as e:
                            st.error(str(e))

                if btn_col2.button("✅ Wear This Today", key=f"wear_{idx}", use_container_width=True):
                    log_outfit_worn(outfit.item_ids)
                    st.toast("Logged outfit & updated laundry counts!")
                    st.rerun()

# --- TAB 2: INGESTION (MANUAL SINGLE PHOTO + WHERING BULK IMPORT) ---
with tab_add:
    import_mode = st.radio(
        "Choose Import Method",
        ["📱 Bulk Whering Screenshots", "📸 Single Photo (Camera / File)"],
        horizontal=True,
    )

    st.divider()

    if import_mode == "📱 Bulk Whering Screenshots":
        st.subheader("Bulk Import from Whering Grid Screenshots")
        st.caption(
            "Tip: Scroll through your Whering 'Items' tab and take screenshots. "
            "Upload 4–6 screenshots per batch—Gemini will slice out each tile and tag every piece."
        )

        screenshots = st.file_uploader(
            "Upload Whering Grid Screenshots",
            type=["jpg", "jpeg", "png", "webp"],
            accept_multiple_files=True,
            key="whering_uploader",
        )

        if screenshots and st.button("🔍 Scan Screenshots & Extract Items", type="primary"):
            extracted_batch = []
            progress = st.progress(0, text="Starting screenshot scan...")
            for i, shot in enumerate(screenshots):
                progress.progress(
                    i / len(screenshots),
                    text=f"Scanning screenshot {i + 1} of {len(screenshots)} ({shot.name})...",
                )
                items_found = extract_whering_grid_items(shot.getvalue())
                extracted_batch.extend(items_found)
            progress.progress(1.0, text=f"Done! Found {len(extracted_batch)} items.")
            st.session_state["whering_candidates"] = extracted_batch

        if st.session_state.get("whering_candidates"):
            candidates = st.session_state["whering_candidates"]
            st.markdown(f"### Review Extracted Items ({len(candidates)} found)")
            st.caption("Uncheck any duplicate items from overlapping screenshots before saving.")

            with st.form("save_whering_batch"):
                grid = st.columns(3)
                for idx, cand in enumerate(candidates):
                    meta = cand["metadata"]
                    with grid[idx % 3]:
                        with st.container(border=True):
                            st.image(cand["cutout_bytes"], width=150)
                            st.checkbox("Keep item", value=True, key=f"w_keep_{idx}")
                            st.text_input("Name", value=meta["name"], key=f"w_name_{idx}")
                            st.selectbox(
                                "Category",
                                CATEGORIES,
                                index=CATEGORIES.index(meta["category"]),
                                key=f"w_cat_{idx}",
                            )
                            st.caption(
                                f"Color: {meta['primary_color']} · Warmth: {meta['warmth']}/5 · Formality: {meta['formality']}/5"
                            )

                if st.form_submit_button("💾 Save Checked Items to Closet", type="primary"):
                    saved_count = 0
                    for idx, cand in enumerate(candidates):
                        if st.session_state.get(f"w_keep_{idx}", True):
                            updated_meta = dict(cand["metadata"])
                            updated_meta["name"] = st.session_state[f"w_name_{idx}"]
                            updated_meta["category"] = st.session_state[f"w_cat_{idx}"]
                            save_garment(updated_meta, cand["cutout_bytes"])
                            saved_count += 1
                    del st.session_state["whering_candidates"]
                    st.success(f"Added {saved_count} items to your closet!")
                    st.rerun()

    else:
        st.subheader("Digitize a Single Garment")
        uploaded = st.file_uploader(
            "Snap or upload a photo of an item (JPG/PNG/WEBP)",
            type=["jpg", "jpeg", "png", "webp"],
            key="single_uploader",
        )

        if uploaded and st.button("🪄 Strip Background & Auto-Tag", type="primary"):
            with st.spinner("Removing background with rembg & analyzing with Gemini Vision..."):
                cutout = create_clean_cutout(uploaded.getvalue())
                tags = analyze_garment_image(cutout)
                st.session_state["draft_cutout"] = cutout
                st.session_state["draft_tags"] = tags

        if "draft_cutout" in st.session_state and "draft_tags" in st.session_state:
            col_img, col_form = st.columns([1, 2])
            with col_img:
                st.image(st.session_state["draft_cutout"], caption="Transparent Cutout", width=240)

            with col_form:
                tags = st.session_state["draft_tags"]
                with st.form("confirm_garment"):
                    name = st.text_input("Name", value=tags.name)
                    category = st.selectbox("Category", CATEGORIES, index=CATEGORIES.index(tags.category))
                    c_a, c_b = st.columns(2)
                    primary_color = c_a.text_input("Primary Color", value=tags.primary_color)
                    secondary_color = c_b.text_input("Secondary Color", value=tags.secondary_color)
                    c_w, c_f, c_m = st.columns(3)
                    warmth = c_w.slider("Warmth (1–5)", 1, 5, tags.warmth)
                    formality = c_f.slider("Formality (1–5)", 1, 5, tags.formality)
                    max_wears = c_m.number_input(
                        "Wears Before Wash", min_value=1, max_value=30, value=tags.max_wears_before_wash
                    )
                    water_resistant = st.checkbox("Water Resistant", value=tags.water_resistant)
                    style_notes = st.text_area("Style Notes", value=tags.style_notes)

                    if st.form_submit_button("💾 Save to Closet", type="primary"):
                        save_garment(
                            {
                                "name": name,
                                "category": category,
                                "primary_color": primary_color,
                                "secondary_color": secondary_color,
                                "warmth": warmth,
                                "formality": formality,
                                "max_wears_before_wash": max_wears,
                                "water_resistant": water_resistant,
                                "style_notes": style_notes,
                            },
                            st.session_state["draft_cutout"],
                        )
                        del st.session_state["draft_cutout"]
                        del st.session_state["draft_tags"]
                        st.success(f"Saved {name}!")
                        st.rerun()

# --- TAB 3: CLOSET & LAUNDRY ---
with tab_closet:
    hamper_count = sum(1 for i in all_closet_items if i.get("status") == "in_wash")

    top_bar_l, top_bar_r = st.columns([3, 1])
    status_filter = top_bar_l.radio("Filter", ["All", "Clean", "In Hamper"], horizontal=True)
    if hamper_count > 0 and top_bar_r.button(f"🧺 Wash Hamper ({hamper_count})"):
        reset_laundry_hamper()
        st.rerun()

    filtered = [
        i
        for i in all_closet_items
        if status_filter == "All"
        or (status_filter == "Clean" and i.get("status") == "clean")
        or (status_filter == "In Hamper" and i.get("status") == "in_wash")
    ]

    grid = st.columns(4)
    for idx, item in enumerate(filtered):
        with grid[idx % 4]:
            with st.container(border=True):
                thumb = load_garment_bytes(item.get("image_ref", ""))
                if thumb:
                    st.image(thumb, width=140)
                badge = "🟢 Clean" if item.get("status") == "clean" else "🧺 In Wash"
                st.markdown(
                    f"**{item['name']}** ({item['category']})  \n{badge} · Wears: {item.get('wear_count', 0)}/{item.get('max_wears_before_wash', 1)}"
                )
                st.caption(f"Last worn: {item.get('last_worn', 'never')}")
                if st.button("🗑️ Remove", key=f"del_{item['id']}"):
                    delete_garment(item["id"])
                    st.rerun()