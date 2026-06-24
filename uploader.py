"""YouTube upload with multi-account support, channel selection, and full metadata."""

import json
import os
import re
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone

from config import APP_DATA_DIR, CLIENT_SECRETS_FILE, TOKEN_FILE, TOKENS_DIR
from title_generator import DEFAULT_VIDEO_CATEGORY_ID, YOUTUBE_TAG_TARGET, generate_tags

_BASE = APP_DATA_DIR
_TOKENS_DIR = TOKENS_DIR
_SECRETS_ROOT = CLIENT_SECRETS_FILE
_SECRETS_TOKENS = _TOKENS_DIR / "client_secrets.json"
_TOKEN_LEGACY = TOKEN_FILE  # old single-token path
_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]

# Cache: account_id -> youtube service
_service_cache: dict = {}
_FALLBACK_CATEGORIES = [{"id": DEFAULT_VIDEO_CATEGORY_ID, "title": "Gaming"}]
_NON_ACCOUNT_TOKEN_FILES = {
    "client_secret.json",
    "client_secrets.json",
    "client_secrets.example.json",
}
_ACCOUNT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
YOUTUBE_HTTP_TIMEOUT_SECONDS = 120
YOUTUBE_UPLOAD_TIMEOUT_SECONDS = 2 * 60 * 60
YOUTUBE_UPLOAD_MAX_CHUNKS = 2048
YOUTUBE_PUBLISH_BUFFER = timedelta(minutes=10)


# ── Authentication ───────────────────────────────────────────────────────────


def _client_secrets_path() -> Path:
    """Use root secrets first, but allow tokens/client_secrets.json too."""
    if _SECRETS_ROOT.exists():
        return _SECRETS_ROOT
    return _SECRETS_TOKENS


def _ensure_tokens_dir():
    _TOKENS_DIR.mkdir(exist_ok=True)
    # Migrate legacy single token.json → tokens/ folder
    if _TOKEN_LEGACY.exists():
        try:
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request
            creds = Credentials.from_authorized_user_file(str(_TOKEN_LEGACY), _SCOPES)
            if not creds:
                print("[youtube] Legacy token could not be migrated; keeping token.json")
                return
            if not creds.valid and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            if not creds.valid:
                print("[youtube] Legacy token is not valid; keeping token.json")
                return
            svc = _build_service(creds)
            resp = svc.channels().list(part="snippet", mine=True).execute()
            items = resp.get("items", [])
            if not items:
                print("[youtube] Legacy token has no channel; keeping token.json")
                return
            acct_id = items[0]["id"]
            acct_title = items[0]["snippet"]["title"]
            new_path = _save_token(acct_id, acct_title, creds)
            if new_path.exists():
                _TOKEN_LEGACY.unlink()
                print(f"[youtube] Migrated legacy token.json to tokens/{new_path.name}")
        except Exception as exc:
            print(f"[youtube] Legacy token migration failed; keeping token.json: {exc}")


def _build_service(creds):
    from googleapiclient.discovery import build
    try:
        import httplib2
        from google_auth_httplib2 import AuthorizedHttp

        http = AuthorizedHttp(creds, http=httplib2.Http(timeout=YOUTUBE_HTTP_TIMEOUT_SECONDS))
        return build("youtube", "v3", http=http)
    except Exception:
        return build("youtube", "v3", credentials=creds)


def _validate_account_id(account_id: str) -> str:
    account_id = str(account_id or "").strip()
    if not _ACCOUNT_ID_RE.fullmatch(account_id):
        raise ValueError("Invalid YouTube account id")
    return account_id


def _token_path(account_id: str) -> Path:
    safe_id = _validate_account_id(account_id)
    root = _TOKENS_DIR.resolve()
    path = (root / f"{safe_id}.json").resolve()
    path.relative_to(root)
    return path


def _save_token(account_id: str, account_title: str, creds):
    """Save token with account metadata."""
    account_id = _validate_account_id(account_id)
    data = json.loads(creds.to_json())
    data["_account_id"] = account_id
    data["_account_title"] = account_title
    path = _token_path(account_id)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def _is_account_token_file(path: Path, data: dict | None = None) -> bool:
    """Return True for OAuth account tokens, never OAuth client secret files."""
    if path.name.lower() in _NON_ACCOUNT_TOKEN_FILES:
        return False
    if data is None:
        try:
            data = json.loads(path.read_text())
        except Exception:
            return False
    if not isinstance(data, dict):
        return False
    if "installed" in data or "web" in data:
        return False
    return bool(data.get("_account_id") or (data.get("refresh_token") and data.get("client_id")))


def _load_creds(account_id: str):
    """Load credentials for a specific account, refreshing if expired."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    account_id = _validate_account_id(account_id)
    path = _token_path(account_id)
    if not path.exists():
        return None
    creds = Credentials.from_authorized_user_file(str(path), _SCOPES)
    if not creds:
        return None
    if creds.valid:
        return creds
    # Token expired — try to refresh
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            # Save the refreshed token
            data = json.loads(path.read_text())
            title = data.get("_account_title", account_id)
            _save_token(account_id, title, creds)
            print(f"[+] Refreshed token for {title}")
            return creds
        except Exception as e:
            print(f"[!] Token refresh failed for {account_id}: {e}")
    return None


def get_youtube_service(account_id: str = None, force_new: bool = False):
    """Get YouTube service for a specific account. If account_id is None, use first available."""
    _ensure_tokens_dir()

    if account_id is None:
        accounts = list_accounts()
        if not accounts:
            raise RuntimeError("No YouTube accounts connected")
        account_id = accounts[0]["id"]
    account_id = _validate_account_id(account_id)

    if account_id in _service_cache and not force_new:
        return _service_cache[account_id]

    creds = _load_creds(account_id)
    if not creds:
        raise RuntimeError(f"Account {account_id} not connected or token expired")

    svc = _build_service(creds)
    _service_cache[account_id] = svc
    return svc


def add_account() -> dict:
    """Run OAuth flow to add a new account. Returns {id, title} of the added account."""
    _ensure_tokens_dir()
    secrets_path = _client_secrets_path()

    if not secrets_path.exists():
        raise FileNotFoundError(
            "client_secrets.json not found.\n"
            "1. https://console.cloud.google.com → create project\n"
            "2. Enable YouTube Data API v3\n"
            "3. Configure the OAuth consent screen and add yourself as a test user\n"
            "4. Create OAuth 2.0 credentials (Desktop app)\n"
            "5. Download JSON → save as client_secrets.json in the app data folder or tokens folder"
        )

    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_secrets_file(str(secrets_path), _SCOPES)
    creds = flow.run_local_server(port=0)

    # Discover which account this is
    svc = _build_service(creds)
    resp = svc.channels().list(part="snippet,statistics", mine=True).execute()
    items = resp.get("items", [])
    if not items:
        raise RuntimeError("No YouTube channel found for this Google account")

    ch = items[0]
    account_id = ch["id"]
    account_title = ch["snippet"]["title"]

    _save_token(account_id, account_title, creds)
    _service_cache[account_id] = svc

    print(f"[+] Added YouTube account: {account_title} ({account_id})")
    return {"id": account_id, "title": account_title}


def list_accounts() -> list[dict]:
    """Return all connected accounts (from tokens/ folder)."""
    _ensure_tokens_dir()
    accounts = []
    for f in sorted(_TOKENS_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text())
            if not _is_account_token_file(f, data):
                continue
            try:
                account_id = _validate_account_id(data.get("_account_id", f.stem))
            except ValueError:
                continue
            accounts.append({
                "id": account_id,
                "title": data.get("_account_title", f.stem),
            })
        except Exception:
            continue
    return accounts


def is_connected() -> bool:
    """Check if at least one account is connected."""
    _ensure_tokens_dir()
    return len(list_accounts()) > 0


def disconnect(account_id: str = None):
    """Remove a specific account, or all accounts if account_id is None."""
    _ensure_tokens_dir()
    if account_id:
        try:
            account_id = _validate_account_id(account_id)
            path = _token_path(account_id)
        except ValueError:
            return
        if path.exists():
            path.unlink()
        _service_cache.pop(account_id, None)
    else:
        # Remove all
        for f in _TOKENS_DIR.glob("*.json"):
            if _is_account_token_file(f):
                f.unlink()
        _service_cache.clear()


# ── Channel & Category listing ───────────────────────────────────────────────


def list_channels() -> list[dict]:
    """Return uploadable channels backed by connected OAuth account tokens."""
    _ensure_tokens_dir()
    all_channels = []
    seen_ids = set()
    for acct in list_accounts():
        try:
            yt = get_youtube_service(acct["id"])
            # Primary channel (mine=True)
            resp = yt.channels().list(part="snippet,statistics", mine=True).execute()
            for ch in resp.get("items", []):
                if ch["id"] not in seen_ids:
                    seen_ids.add(ch["id"])
                    all_channels.append({
                        "id": ch["id"],
                        "title": ch["snippet"]["title"],
                        "thumbnail": ch["snippet"]["thumbnails"]["default"]["url"],
                        "subscribers": ch["statistics"].get("subscriberCount", "0"),
                        "account_id": acct["id"],
                        "account_title": acct["title"],
                    })
        except Exception as e:
            print(f"[!] Failed to list channels for {acct['title']}: {e}")
    return all_channels


def list_categories(region: str = "US") -> list[dict]:
    """Return assignable YouTube video categories."""
    accounts = list_accounts()
    if not accounts:
        return _FALLBACK_CATEGORIES
    try:
        yt = get_youtube_service(accounts[0]["id"])
        resp = yt.videoCategories().list(part="snippet", regionCode=region).execute()
        categories = [
            {"id": cat["id"], "title": cat["snippet"]["title"]}
            for cat in resp.get("items", [])
            if cat["snippet"].get("assignable")
        ]
        if not any(cat["id"] == DEFAULT_VIDEO_CATEGORY_ID for cat in categories):
            categories.insert(0, _FALLBACK_CATEGORIES[0])
        return categories or _FALLBACK_CATEGORIES
    except Exception:
        return _FALLBACK_CATEGORIES


def _normalize_tags(tags: list | str | None) -> list[str]:
    """Normalize tags and keep the combined string under YouTube's limit buffer."""
    if tags is None:
        raw = generate_tags().split(",")
    elif isinstance(tags, str):
        raw = tags.split(",")
    else:
        raw = tags

    normalized = []
    seen = set()
    for tag in raw:
        cleaned = " ".join(str(tag).strip(" ,#").split())
        key = cleaned.lower()
        if not cleaned or key in seen:
            continue
        normalized.append(cleaned)
        seen.add(key)

    if "shorts" not in seen:
        normalized.insert(0, "shorts")

    capped = []
    for tag in normalized:
        candidate = ", ".join(capped + [tag])
        if len(candidate) > YOUTUBE_TAG_TARGET:
            continue
        capped.append(tag)
    return capped or ["shorts", "gaming"]


# ── Upload ───────────────────────────────────────────────────────────────────


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.astimezone(timezone.utc).replace(microsecond=0)


def upload_to_youtube(
    video_path: Path,
    title: str,
    description: str = "",
    tags: list = None,
    category_id: str = DEFAULT_VIDEO_CATEGORY_ID,
    privacy: str = "private",
    scheduled_time: datetime = None,
    channel_id: str = None,
    account_id: str = None,
    cancel_check=None,
    on_progress=None,
) -> dict | None:
    """Upload a video with full metadata.  Returns {'id', 'url'} or None.

    account_id: OAuth account token to upload with. channel_id is retained for
    metadata/debug display and may match account_id for primary channels.
    """
    from googleapiclient.http import MediaFileUpload

    privacy = str(privacy or "private").lower()
    publish_at = _as_utc(scheduled_time) if privacy == "public" else None
    if publish_at and publish_at <= datetime.now(timezone.utc) + YOUTUBE_PUBLISH_BUFFER:
        minutes = int(YOUTUBE_PUBLISH_BUFFER.total_seconds() // 60)
        raise ValueError(f"scheduled_time must be at least {minutes} minutes in the future")

    yt = get_youtube_service(account_id or channel_id)

    # Ensure Shorts format — append #Shorts to title and description
    if "#Shorts" not in title and "#shorts" not in title:
        title = f"{title} #Shorts"
    title = title[:100]
    if "#Shorts" not in description and "#shorts" not in description:
        description = f"{description}\n\n#Shorts".strip() if description else "#Shorts"
    tags = _normalize_tags(tags)
    category_id = DEFAULT_VIDEO_CATEGORY_ID

    status_privacy = privacy
    if publish_at:
        status_privacy = "private"  # must be private for scheduling

    body = {
        "snippet": {
            "title": title[:100],
            "description": description,
            "tags": tags,
            "categoryId": str(category_id),
        },
        "status": {
            "privacyStatus": status_privacy,
            "selfDeclaredMadeForKids": False,
        },
    }
    if publish_at:
        body["status"]["publishAt"] = publish_at.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    media = MediaFileUpload(
        str(video_path),
        chunksize=8 * 1024 * 1024,
        resumable=True,
        mimetype="video/mp4",
    )

    channel_info = f" -> channel {channel_id}" if channel_id else ""
    print(f"[*] Uploading {video_path.name}{channel_info} ...")
    request = yt.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    started = time.monotonic()
    chunks = 0
    while response is None:
        if cancel_check and cancel_check():
            raise RuntimeError("Upload cancelled")
        if time.monotonic() - started > YOUTUBE_UPLOAD_TIMEOUT_SECONDS:
            raise TimeoutError("YouTube upload timed out")
        if chunks >= YOUTUBE_UPLOAD_MAX_CHUNKS:
            raise TimeoutError("YouTube upload exceeded the maximum chunk count")
        chunks += 1
        status, response = request.next_chunk()
        if response is not None:
            break
        if cancel_check and cancel_check():
            raise RuntimeError("Upload cancelled")
        if status:
            percent = int(status.progress() * 100)
            print(f"    {percent}%")
            if on_progress:
                on_progress(percent)

    vid = response["id"]
    url = f"https://youtu.be/{vid}"
    if on_progress:
        on_progress(100)
    print(f"[+] Uploaded -> {url}")
    return {"id": vid, "url": url}


def build_schedule(
    clip_paths: list,
    start_time: datetime = None,
    interval_hours: int = 24,
) -> list:
    if start_time is None:
        start_time = datetime.now(timezone.utc) + timedelta(hours=1)
    else:
        start_time = _as_utc(start_time)
    return [
        {"path": p, "scheduled_time": start_time + timedelta(hours=interval_hours * i)}
        for i, p in enumerate(clip_paths)
    ]
