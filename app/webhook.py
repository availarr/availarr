import logging
import requests
from fastapi import APIRouter, Request
from app.config_server import load_config
from app.utils.normalization import normalize_provider
from app.utils.logging import log_event

#router = APIRouter()
webhook_router = APIRouter()

def get_required_config():
    config = load_config()
    required_keys = ["TMDB_API_KEY", "OVERSEERR_URL", "OVERSEERR_API_KEY", "DISCORD_WEBHOOK_URL"]
    missing = [key for key in required_keys if not config.get(key)]
    if missing:
        raise EnvironmentError(f"Missing required config values: {', '.join(missing)}")
    return config

def get_streaming_providers(tmdb_id, media_type):
    config = get_required_config()
    TMDB_API_KEY = config["TMDB_API_KEY"]
    region = "US"
    url = f"https://api.themoviedb.org/3/{media_type}/{tmdb_id}/watch/providers"
    headers = {"Authorization": f"Bearer {TMDB_API_KEY}"}

    log_event("tmdb_query", url=url, media_type=media_type, tmdb_id=tmdb_id)
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        providers = [
            provider["provider_name"]
            for provider in data.get("results", {}).get(region, {}).get("flatrate", [])
        ]
        log_event("tmdb_response", tmdb_id=tmdb_id, providers=sorted(providers))
        return providers
    except requests.RequestException as e:
        log_event("tmdb_error", tmdb_id=tmdb_id, error=str(e))
        return []

def delete_approved_request(request_id):
    config = get_required_config()
    url = f"{config['OVERSEERR_URL']}/api/v1/request/{request_id}"
    headers = {"X-Api-Key": config['OVERSEERR_API_KEY']}
    try:
        response = requests.delete(url, headers=headers, timeout=10)
        response.raise_for_status()
        log_event("request_deleted", request_id=request_id)
    except requests.RequestException as e:
        log_event("request_delete_failed", request_id=request_id, error=str(e))

def decline_pending_request(request_id):
    config = get_required_config()
    url = f"{config['OVERSEERR_URL']}/api/v1/request/{request_id}/decline"
    headers = {"X-Api-Key": config['OVERSEERR_API_KEY']}
    try:
        response = requests.post(url, headers=headers, timeout=10)
        response.raise_for_status()
        log_event("request_declined", request_id=request_id)
    except requests.RequestException as e:
        log_event("request_decline_failed", request_id=request_id, error=str(e))

def approve_request(request_id):
    config = get_required_config()
    url = f"{config['OVERSEERR_URL']}/api/v1/request/{request_id}/approve"
    headers = {"X-Api-Key": config['OVERSEERR_API_KEY']}
    try:
        response = requests.post(url, headers=headers, timeout=10)
        response.raise_for_status()
        log_event("request_approved", request_id=request_id)
    except requests.RequestException as e:
        log_event("request_approval_failed", request_id=request_id, error=str(e))

def send_discord_notification(title, request_id, available_on, reason, action):
    config = get_required_config()
    content = (
        f"**{title}** (Request ID: {request_id}) was **{action}** "
        f"because it is available on: {', '.join(available_on)}.\nReason: {reason}"
    )
    payload = {"content": content}
    try:
        response = requests.post(config['DISCORD_WEBHOOK_URL'], json=payload, timeout=10)
        response.raise_for_status()
        log_event("discord_notify_success", request_id=request_id)
    except requests.RequestException as e:
        log_event("discord_notify_failed", request_id=request_id, error=str(e))

def send_review_notification(title, request_id, reason):
    config = get_required_config()
    overseerr_url = config["OVERSEERR_URL"].rstrip("/")
    request_link = f"{overseerr_url}/requests/{request_id}"
    content = (
        f"⚠️ **Manual Review Needed**\n"
        f"**{title}** (Request ID: `{request_id}`) is awaiting manual approval.\n"
        f"⛔ **Reason:** {reason}\n"
        f"▶ [Open in Overseerr]({request_link})"
    )
    payload = {"content": content}
    try:
        response = requests.post(config['DISCORD_WEBHOOK_URL'], json=payload, timeout=10)
        response.raise_for_status()
        log_event("review_notify_success", request_id=request_id)
    except requests.RequestException as e:
        log_event("review_notify_failed", request_id=request_id, error=str(e))

def send_approval_notification(title, request_id):
    config = get_required_config()
    overseerr_url = config["OVERSEERR_URL"].rstrip("/")
    request_link = f"{overseerr_url}/requests/{request_id}"
    content = (
        f"✅ **Auto-Approved Request**\n"
        f"**{title}** (Request ID: `{request_id}`) has been automatically approved because it is **not available** on any of your selected streaming platforms.\n"
        f"▶ [View in Overseerr]({request_link})"
    )
    payload = {"content": content}
    try:
        response = requests.post(config['DISCORD_WEBHOOK_URL'], json=payload, timeout=10)
        response.raise_for_status()
        log_event("approval_notify_success", request_id=request_id)
    except requests.RequestException as e:
        log_event("approval_notify_failed", request_id=request_id, error=str(e))

@webhook_router.post("/webhook")
async def handle_webhook(request: Request):
    try:
        payload = await request.json()
        log_event("webhook_received", payload=payload)

        event_type = payload.get("event")
        media = payload.get("media", {})
        request_data = payload.get("request", {})

        media_type = media.get("media_type")
        tmdb_id = media.get("tmdbId")
        title = payload.get("subject") or media.get("title", "Unknown Title")
        request_id = request_data.get("request_id")
        media_status = media.get("status")
        status = 2 if media_status == "PENDING" else 1 if media_status == "APPROVED" else None

        if not all([media_type, tmdb_id, request_id]):
            log_event("missing_fields", media_type=media_type, tmdb_id=tmdb_id, request_id=request_id)
            return {"message": "Missing required fields. Ignored."}

        if status not in [1, 2]:
            log_event("invalid_status", status=media_status, request_id=request_id)
            return {"message": "Request not approved or pending. Ignored."}

        providers = get_streaming_providers(tmdb_id, media_type)
        config = load_config()
        allowed_providers = config.get("PROVIDERS", [])
        normalized_allowed = [normalize_provider(p) for p in allowed_providers]

        matched = set()
        unmatched = []

        for provider in providers:
            provider_normalized = normalize_provider(provider).replace(" with ads", "").replace("standard with ads", "").replace("+", "plus")
            match_attempts = [allowed for allowed in normalized_allowed if allowed == provider_normalized]
            log_event("provider_match_attempt", provider=provider, normalized=provider_normalized, matches=match_attempts)
            if match_attempts:
                matched.add(provider)
            else:
                unmatched.append(provider)

        log_event("provider_match", matched_providers=sorted(matched), unmatched_providers=sorted(unmatched), title=title)

        if matched:
            reason = "Title is already available on a preferred streaming platform"
            action = "deleted" if status == 1 else "declined"

            if status == 1:
                delete_approved_request(request_id)
            else:
                decline_pending_request(request_id)

            send_discord_notification(title, request_id, sorted(matched), reason, action)
            return {"message": f"Request {request_id} {action} due to streaming availability."}

        if status == 2:
            approve_request(request_id)
            send_approval_notification(title, request_id)
            return {"message": f"Request {request_id} auto-approved due to no matching providers."}
        else:
            reason = "Title not found on any preferred provider. Awaiting manual approval."
            send_review_notification(title, request_id, reason)
            return {"message": f"No matching providers found. Notified for manual approval."}

    except Exception as e:
        log_event("webhook_error", error=str(e))
        return {"error": str(e)}
