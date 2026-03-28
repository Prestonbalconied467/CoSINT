"""
setup_data.py – Static catalog data for the setup wizard.

All API tool definitions, optional packages, external CLI checks, and
LiteLLM provider presets live here so that setup.py stays focused on
the interactive wizard logic only.
"""

# ── API keys ──────────────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "HaveIBeenPwned - Breach Checks",
        "env": "HIBP_KEY",
        "url": "https://haveibeenpwned.com/API/Key",
        "free": False,
    },
    {
        "name": "Hunter.io - Email & Domain Research",
        "env": "HUNTER_API_KEY",
        "url": "https://hunter.io/api",
        "free": True,
    },
    {
        "name": "Shodan - Port & Service Scanning",
        "env": "SHODAN_KEY",
        "url": "https://account.shodan.io/",
        "free": True,
    },
    {
        "name": "AbuseIPDB - IP Reputation",
        "env": "ABUSEIPDB_KEY",
        "url": "https://www.abuseipdb.com/account/api",
        "free": True,
    },
    {
        "name": "VirusTotal - Malware & Reputation",
        "env": "VIRUSTOTAL_KEY",
        "url": "https://www.virustotal.com/gui/my-apikey",
        "free": True,
    },
    {
        "name": "SecurityTrails - DNS History",
        "env": "SECURITYTRAILS_KEY",
        "url": "https://securitytrails.com/app/account/credentials",
        "free": True,
    },
    {
        "name": "Etherscan - Ethereum Blockchain",
        "env": "ETHERSCAN_KEY",
        "url": "https://etherscan.io/myapikey",
        "free": True,
    },
    {
        "name": "NewsAPI - News Search",
        "env": "NEWSAPI_KEY",
        "url": "https://newsapi.org/register",
        "free": True,
    },
    {
        "name": "Bundestag DIP API",
        "env": "BUNDESTAG_DIP_KEY",
        "url": "https://dip.bundestag.de/uber-dip/hilfe/api",
        "free": True,
    },
    {
        "name": "CourtListener - Public Court Records",
        "env": "COURT_LISTENER_KEY",
        "url": "https://www.courtlistener.com/api/",
        "free": True,
    },
    {
        "name": "GitHub Token - Higher Rate Limits",
        "env": "GITHUB_TOKEN",
        "url": "https://github.com/settings/tokens",
        "free": True,
    },
    {
        "name": "LeakCheck - Breach Database",
        "env": "LEAKCHECK_KEY",
        "url": "https://leakcheck.io/",
        "free": True,
    },
    {
        "name": "IntelX - Darknet & Paste Sites",
        "env": "INTELX_KEY",
        "url": "https://intelx.io/account?tab=developer",
        "free": True,
    },
    {
        "name": "ipinfo.io - IP Geolocation",
        "env": "IPINFO_TOKEN",
        "url": "https://ipinfo.io/account/token",
        "free": True,
    },
    {
        "name": "WhoisXMLAPI - WHOIS Data",
        "env": "WHOISXML_KEY",
        "url": "https://user.whoisxmlapi.com/products",
        "free": True,
    },
    {
        "name": "OpenCorporates - Company Registry",
        "env": "OPENCORPORATES_KEY",
        "url": "https://api.opencorporates.com/",
        "free": False,
    },
    {
        "name": "Northdata - DACH Company Registry",
        "env": "NORTHDATA_KEY",
        "url": "https://www.northdata.com/data/api",
        "free": False,
    },
    {
        "name": "EmailRep.io - Email Reputation",
        "env": "EMAILREP_KEY",
        "url": "https://emailrep.io/key",
        "free": True,
    },
    {
        "name": "Adzuna - Job Listings (App ID)",
        "env": "ADZUNA_APP_ID",
        "url": "https://developer.adzuna.com/",
        "free": True,
    },
    {
        "name": "Adzuna - Job Listings (API Key)",
        "env": "ADZUNA_API_KEY",
        "url": "https://developer.adzuna.com/",
        "free": True,
    },
    {
        "name": "Google Vision API - Reverse Image",
        "env": "GOOGLE_VISION_KEY",
        "url": "https://console.cloud.google.com/",
        "free": True,
    },
    {
        "name": "TinEye - Reverse Image Search",
        "env": "TINEYE_KEY",
        "url": "https://services.tineye.com/TinEyeAPI",
        "free": False,
    },
    {
        "name": "SauceNAO - Reverse Image Search",
        "env": "SAUCENAO_KEY",
        "url": "https://saucenao.com/user.php?page=search-api",
        "free": True,
    },
    {
        "name": "FullContact - Person Enrichment",
        "env": "FULLCONTACT_KEY",
        "url": "https://platform.fullcontact.com/",
        "free": True,
    },
    {
        "name": "Alchemy - NFT & Blockchain Data",
        "env": "ALCHEMY_KEY",
        "url": "https://dashboard.alchemy.com/",
        "free": True,
    },
    {
        "name": "OpenSea - NFT Marketplace",
        "env": "OPENSEA_KEY",
        "url": "https://docs.opensea.io/reference/api-keys",
        "free": True,
    },
    {
        "name": "Blockchair - Multi-chain Blockchain",
        "env": "BLOCKCHAIR_KEY",
        "url": "https://blockchair.com/api",
        "free": True,
    },
    {
        "name": "OpenCage - Geocoding",
        "env": "OPENCAGE_KEY",
        "url": "https://opencagedata.com/api",
        "free": True,
    },
    {
        "name": "NumVerify - Phone Number Lookup",
        "env": "NUMVERIFY_KEY",
        "url": "https://numverify.com/dashboard",
        "free": True,
    },
    {
        "name": "IPHub - VPN/Proxy Detection",
        "env": "IPHUB_KEY",
        "url": "https://iphub.info/api",
        "free": True,
    },
    {
        "name": "Wappalyzer - Tech Stack Detection",
        "env": "WAPPALYZER_KEY",
        "url": "https://www.wappalyzer.com/api/",
        "free": False,
    },
    {
        "name": "Reddit API - Client ID",
        "env": "REDDIT_CLIENT_ID",
        "url": "https://www.reddit.com/prefs/apps",
        "free": True,
    },
    {
        "name": "Reddit API - Client Secret",
        "env": "REDDIT_SECRET",
        "url": "https://www.reddit.com/prefs/apps",
        "free": True,
    },
    {
        "name": "Reddit API - User Agent",
        "env": "REDDIT_USER_AGENT",
        "url": "https://github.com/reddit-archive/reddit/wiki/API",
        "free": True,
    },
    {
        "name": "Twitter API - Bearer Token",
        "env": "TWITTER_BEARER_TOKEN",
        "url": "https://developer.twitter.com/en/docs/authentication/oauth-2-0/bearer-tokens",
        "free": True,
    },
    {
        "name": "Twitch API - Client ID",
        "env": "TWITCH_CLIENT_ID",
        "url": "https://dev.twitch.tv/console/apps",
        "free": True,
    },
    {
        "name": "Twitch API - Client Secret",
        "env": "TWITCH_CLIENT_SECRET",
        "url": "https://dev.twitch.tv/console/apps",
        "free": True,
    },
    {
        "name": "YouTube API Key",
        "env": "YOUTUBE_API_KEY",
        "url": "https://console.cloud.google.com/apis/credentials",
        "free": True,
    },
    {
        "name": "VK API - Access Token",
        "env": "VK_ACCESS_TOKEN",
        "url": "https://vk.com/dev/access_token",
        "free": True,
    },
    {
        "name": "Spotify API - Client ID",
        "env": "SPOTIFY_CLIENT_ID",
        "url": "https://developer.spotify.com/dashboard/applications",
        "free": True,
    },
    {
        "name": "Spotify API - Client Secret",
        "env": "SPOTIFY_CLIENT_SECRET",
        "url": "https://developer.spotify.com/dashboard/applications",
        "free": True,
    },
    {
        "name": "Last.fm API Key",
        "env": "LASTFM_API_KEY",
        "url": "https://www.last.fm/api/account/create",
        "free": True,
    },
    {
        "name": "SoundCloud API - Client ID",
        "env": "SOUNDCLOUD_CLIENT_ID",
        "url": "https://soundcloud.com/you/apps",
        "free": True,
    },
    {
        "name": "Tumblr API Key",
        "env": "TUMBLR_API_KEY",
        "url": "https://www.tumblr.com/oauth/apps",
        "free": True,
    },
    {
        "name": "Flickr API Key",
        "env": "FLICKR_API_KEY",
        "url": "https://www.flickr.com/services/api/misc.api_keys.html",
        "free": True,
    },
    {
        "name": "Gravatar API Key",
        "env": "GRAVATAR_API_KEY",
        "url": "https://en.gravatar.com/site/implement/hash/",
        "free": True,
    },
    {
        "name": "StackExchange API Key",
        "env": "STACKEXCHANGE_API_KEY",
        "url": "https://stackapps.com/apps/oauth/register",
        "free": True,
    },
    {
        "name": "Instagram Account Name",
        "env": "INSTAGRAM_USERNAME",
        "url": "https://www.instagram.com/",
        "free": True,
    },
    {
        "name": "Instagram Account Password",
        "env": "INSTAGRAM_PASSWORD",
        "url": "https://www.instagram.com/",
        "free": True,
    },
    {
        "name": "OCR.space - Image Text Extraction (OCR)",
        "env": "OCR_SPACE_KEY",
        "url": "https://ocr.space/ocrapi",
        "free": True,
    },
]

# ── Runtime / model defaults ───────────────────────────────────────────────────

RUNTIME_VARS = [
    {
        "name": "Default scan model",
        "env": "OSINT_MODEL",
        "hint": "Used by cosint.py as default when --model is not passed",
        "default": "",
    },
]

# ── LiteLLM provider presets ───────────────────────────────────────────────────

LITELLM_DOCS_URL = "https://docs.litellm.ai/docs/providers"

LITELLM_PROVIDER_PRESETS: dict[str, list[str]] = {
    "openai": ["OPENAI_API_KEY"],
    "anthropic": ["ANTHROPIC_API_KEY"],
    "gemini": ["GEMINI_API_KEY"],
    "azure": ["AZURE_API_KEY", "AZURE_API_BASE", "AZURE_API_VERSION"],
}

# ── Optional Python packages ───────────────────────────────────────────────────

OPTIONAL_PY_PACKAGES = [
    {
        "name": "Maigret",
        "package": "maigret",
        "why": "Username search across many platforms (used by osint_username_search).",
        "required": "Optional",
    },
    {
        "name": "Holehe",
        "package": "holehe",
        "why": "Checks where an email is registered (used by osint_email_social_accounts).",
        "required": "Optional",
    },
    {
        "name": "GHunt",
        "package": "ghunt",
        "why": "Google account enrichment via local GHunt CLI (used by osint_google_account_scan).",
        "required": "Optional",
    },
    {
        "name": "builtwith",
        "package": "builtwith",
        "why": "Python tech fingerprinting fallback; not required when whatweb is installed.",
        "required": "Fallback",
    },
    {
        "name": "trufflehog3",
        "package": "trufflehog3",
        "why": "GitHub secret scanning tool (used by osint_leak_github_secrets). You can also download the tool directly instead of the py package.",
        "required": "Optional",
    },
]

# ── External CLI tool checks ───────────────────────────────────────────────────

EXTERNAL_TOOL_CHECKS = [
    {
        "name": "whatweb",
        "why": "Additional website fingerprinting source.",
        "windows": "Install via WSL or Ruby (gem install whatweb).",
        "unix": "Install via package manager or Ruby gem (gem install whatweb).",
    },
    {
        "name": "exiftool",
        "why": "Richer EXIF extraction; Pillow fallback still works.",
        "windows": "Download from https://exiftool.org/ and add it to PATH.",
        "unix": "Install via package manager (e.g. apt install libimage-exiftool-perl).",
    },
    {
        "name": "subfinder",
        "why": "Extra subdomain enumeration source.",
        "windows": "go install github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest",
        "unix": "go install github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest",
    },
    {
        "name": "phoneinfoga",
        "why": "Optional phone OSINT CLI enrichment.",
        "windows": "Install from releases and add binary to PATH.",
        "unix": "Install from releases/package manager and add binary to PATH.",
    },
]
