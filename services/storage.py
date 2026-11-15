import io
import os
import json
import uuid
from datetime import date
from pathlib import Path
import requests
from PIL import Image, ImageOps
import streamlit as st

LOCAL_DIR = Path("local_closet")
LOCAL_IMAGES = LOCAL_DIR / "images"
LOCAL_TRYONS = LOCAL_DIR / "tryons"
LOCAL_JSON = LOCAL_DIR / "closet.json"
LOCAL_PROFILE_PHOTO = LOCAL_DIR / "profile_photo.jpg"


def _get_secret(key: str, default: str = "") -> str:
    try:
        if key in st.secrets and st.secrets[key]:
            return str(st.secrets[key]).strip()
    except Exception:
        pass
    return os.getenv(key, default).strip()


@st.cache_resource
def get_firebase_clients():
    bucket_name = _get_secret("FIREBASE_STORAGE_BUCKET")
    bucket_name = bucket_name.removeprefix("gs://").rstrip("/")
    if not bucket_name:
        return None, None

    raw_json = _get_secret("FIREBASE_SERVICE_ACCOUNT_JSON")
    cred_path = _get_secret("FIREBASE_CREDENTIALS_PATH")

    cred_dict = None
    if raw_json:
        cred_dict = json.loads(raw_json, strict=False)
    else:
        try:
            if "firebase_service_account" in st.secrets:
                cred_dict = dict(st.secrets["firebase_service_account"])
        except Exception:
            pass

    if not cred_dict and not (cred_path and os.path.exists(cred_path)):
        return None, None

    import firebase_admin
    from firebase_admin import credentials, firestore, storage

    if not firebase_admin._apps:
        cred = credentials.Certificate(cred_dict if cred_dict else cred_path)
        firebase_admin.initialize_app(cred, {"storageBucket": bucket_name})

    return firestore.client(), storage.bucket(bucket_name)


@st.cache_data(ttl=86400, show_spinner=False)
def _download_blob_cached(blob_path: str) -> bytes | None:
    _, bucket = get_firebase_clients()
    if not bucket:
        return None
    blob = bucket.blob(blob_path)
    return blob.download_as_bytes() if blob.exists() else None


def save_garment(metadata: dict, cutout_png_bytes: bytes) -> dict:
    db, bucket = get_firebase_clients()
    item_id = uuid.uuid4().hex[:8]
    record = {
        "id": item_id,
        **metadata,
        "status": "clean",
        "wear_count": 0,
        "last_worn": "never",
    }

    if db and bucket:
        blob_path = f"wardrobe/{item_id}.png"
        bucket.blob(blob_path).upload_from_string(cutout_png_bytes, content_type="image/png")
        record["image_ref"] = f"firebase://{blob_path}"
        db.collection("wardrobe").document(item_id).set(record)
        _download_blob_cached.clear()
    else:
        LOCAL_IMAGES.mkdir(parents=True, exist_ok=True)
        img_path = LOCAL_IMAGES / f"{item_id}.png"
        img_path.write_bytes(cutout_png_bytes)
        record["image_ref"] = str(img_path)

        items = load_all_garments()
        items.append(record)
        LOCAL_JSON.write_text(json.dumps(items, indent=2))

    return record


def load_all_garments() -> list[dict]:
    db, _ = get_firebase_clients()
    if db:
        return [doc.to_dict() for doc in db.collection("wardrobe").stream()]
    if LOCAL_JSON.exists():
        return json.loads(LOCAL_JSON.read_text())
    return []


def load_garment_bytes(image_ref: str) -> bytes | None:
    """Loads raw image bytes from private Firebase Storage, HTTP URLs, or local disk."""
    if not image_ref:
        return None
    if image_ref.startswith("firebase://"):
        return _download_blob_cached(image_ref.removeprefix("firebase://"))
    if image_ref.startswith("http://") or image_ref.startswith("https://"):
        resp = requests.get(image_ref, timeout=10)
        return resp.content if resp.ok else None
    path = Path(image_ref)
    if path.exists():
        return path.read_bytes()
    return _download_blob_cached(f"wardrobe/{path.name}")


def save_profile_photo(raw_bytes: bytes) -> None:
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw_bytes))).convert("RGB")
    img.thumbnail((1200, 1200), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    jpeg_bytes = buf.getvalue()

    _, bucket = get_firebase_clients()
    if bucket:
        bucket.blob("wardrobe/_profile_photo.jpg").upload_from_string(jpeg_bytes, content_type="image/jpeg")
        _download_blob_cached.clear()
    else:
        LOCAL_DIR.mkdir(parents=True, exist_ok=True)
        LOCAL_PROFILE_PHOTO.write_bytes(jpeg_bytes)


def load_profile_photo() -> bytes | None:
    _, bucket = get_firebase_clients()
    if bucket:
        return _download_blob_cached("wardrobe/_profile_photo.jpg")
    if LOCAL_PROFILE_PHOTO.exists():
        return LOCAL_PROFILE_PHOTO.read_bytes()
    return None


def delete_profile_photo() -> None:
    _, bucket = get_firebase_clients()
    if bucket:
        blob = bucket.blob("wardrobe/_profile_photo.jpg")
        if blob.exists():
            blob.delete()
        _download_blob_cached.clear()
    if LOCAL_PROFILE_PHOTO.exists():
        LOCAL_PROFILE_PHOTO.unlink()


def load_cached_tryon(cache_key: str) -> bytes | None:
    _, bucket = get_firebase_clients()
    if bucket:
        return _download_blob_cached(f"wardrobe/tryons/{cache_key}.jpg")
    local_file = LOCAL_TRYONS / f"{cache_key}.jpg"
    if local_file.exists():
        return local_file.read_bytes()
    return None


def save_cached_tryon(cache_key: str, image_bytes: bytes) -> None:
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    jpeg_bytes = buf.getvalue()

    _, bucket = get_firebase_clients()
    if bucket:
        bucket.blob(f"wardrobe/tryons/{cache_key}.jpg").upload_from_string(
            jpeg_bytes, content_type="image/jpeg"
        )
        _download_blob_cached.clear()
    else:
        LOCAL_TRYONS.mkdir(parents=True, exist_ok=True)
        (LOCAL_TRYONS / f"{cache_key}.jpg").write_bytes(jpeg_bytes)


def delete_garment(item_id: str) -> None:
    db, bucket = get_firebase_clients()
    if db and bucket:
        db.collection("wardrobe").document(item_id).delete()
        blob = bucket.blob(f"wardrobe/{item_id}.png")
        if blob.exists():
            blob.delete()
        _download_blob_cached.clear()
    else:
        items = load_all_garments()
        remaining = []
        for item in items:
            if item["id"] == item_id:
                img_path = Path(item.get("image_ref", ""))
                if img_path.exists():
                    img_path.unlink()
            else:
                remaining.append(item)
        LOCAL_JSON.write_text(json.dumps(remaining, indent=2))


def log_outfit_worn(item_ids: list[str]) -> None:
    db, _ = get_firebase_clients()
    today_str = date.today().isoformat()
    items = load_all_garments()

    for item in items:
        if item["id"] in item_ids:
            item["wear_count"] = item.get("wear_count", 0) + 1
            item["last_worn"] = today_str
            if item["wear_count"] >= item.get("max_wears_before_wash", 1):
                item["status"] = "in_wash"
            if db:
                db.collection("wardrobe").document(item["id"]).set(item)

    if not db:
        LOCAL_JSON.write_text(json.dumps(items, indent=2))


def reset_laundry_hamper() -> None:
    db, _ = get_firebase_clients()
    items = load_all_garments()
    for item in items:
        if item.get("status") == "in_wash":
            item["status"] = "clean"
            item["wear_count"] = 0
            if db:
                db.collection("wardrobe").document(item["id"]).set(item)
    if not db:
        LOCAL_JSON.write_text(json.dumps(items, indent=2))


def count_unsynced_local_items() -> int:
    db, bucket = get_firebase_clients()
    if not (db and bucket and LOCAL_JSON.exists()):
        return 0
    local_items = json.loads(LOCAL_JSON.read_text())
    if not local_items:
        return 0
    cloud_ids = {doc.id for doc in db.collection("wardrobe").list_documents()}
    return sum(1 for item in local_items if item["id"] not in cloud_ids)


def migrate_local_to_firebase() -> int:
    """Uploads existing local_closet items, profile photo, and cached try-ons to Firebase."""
    db, bucket = get_firebase_clients()
    if not (db and bucket and LOCAL_JSON.exists()):
        return 0

    local_items = json.loads(LOCAL_JSON.read_text())
    cloud_ids = {doc.id for doc in db.collection("wardrobe").list_documents()}
    migrated = 0

    for item in local_items:
        item_id = item["id"]
        if item_id in cloud_ids:
            continue
        local_img = Path(item.get("image_ref", ""))
        if not local_img.exists():
            local_img = LOCAL_IMAGES / f"{item_id}.png"
        if not local_img.exists():
            continue

        blob_path = f"wardrobe/{item_id}.png"
        bucket.blob(blob_path).upload_from_string(local_img.read_bytes(), content_type="image/png")
        cloud_record = dict(item)
        cloud_record["image_ref"] = f"firebase://{blob_path}"
        db.collection("wardrobe").document(item_id).set(cloud_record)
        migrated += 1

    if LOCAL_PROFILE_PHOTO.exists():
        p_blob = bucket.blob("wardrobe/_profile_photo.jpg")
        if not p_blob.exists():
            p_blob.upload_from_string(LOCAL_PROFILE_PHOTO.read_bytes(), content_type="image/jpeg")

    if LOCAL_TRYONS.exists():
        for tryon_file in LOCAL_TRYONS.glob("*.jpg"):
            t_blob = bucket.blob(f"wardrobe/tryons/{tryon_file.name}")
            if not t_blob.exists():
                t_blob.upload_from_string(tryon_file.read_bytes(), content_type="image/jpeg")

    _download_blob_cached.clear()
    return migrated