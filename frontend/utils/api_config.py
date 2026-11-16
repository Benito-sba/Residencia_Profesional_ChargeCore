import os


def default_api_base_url():
    api_url = os.getenv("CHARGECORE_API_URL", "").strip().rstrip("/")
    if not api_url:
        return "http://localhost:8000/api"
    if not api_url.startswith(("http://", "https://")):
        api_url = f"https://{api_url}"
    if not api_url.endswith("/api"):
        api_url = f"{api_url}/api"
    return api_url
