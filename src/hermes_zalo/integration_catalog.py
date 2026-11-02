"""Curated connector and report templates for the Integrations console pages.

Plain data, no I/O. Honesty rules for this catalog:

- ``remote`` templates point at an MCP server URL published by the platform owner.
- ``local`` templates are stdio servers published by the platform owner; the
  console never launches processes, so they are shown with a config snippet
  for the operator to install on the server.
- ``bridge`` templates have no official MCP server we know of. They describe the
  official API a bridge would wrap; once the owner runs a bridge, its HTTP URL
  can be installed like any remote server.
- Links are https and limited to pages we are confident exist. No package names
  are invented.

Capability ``access`` values: ``read`` (reads data), ``write`` (changes data
in the account), ``public`` (can post publicly), ``messages`` (can send
messages or email to people) and ``unknown`` (depends on the server).
"""

CATEGORIES = (
    ("social", "Social"),
    ("messaging", "Messaging"),
    ("analytics", "Analytics"),
    ("productivity", "Productivity"),
    ("developer", "Developer"),
    ("custom", "Custom"),
)

ACCESS_LABELS = {
    "read": "Read only",
    "write": "Changes data",
    "public": "Can post publicly",
    "messages": "Can send messages",
    "unknown": "Review its tools",
}

BRIDGE_NOTE = ("This catalog does not include a hosted MCP endpoint for this app. Run a bridge that wraps "
               "the official API (read-only tools first), then install the bridge's URL here.")


def _cap(label, access="read"):
    return {"label": label, "access": access}


def _cred(variable, label, help_, *, writable=True):
    return {"variable": variable, "label": label, "help": help_, "writable": writable}


def _doc(label, url):
    return {"label": label, "url": url}


_GOOGLE_OAUTH = (
    _cred("GOOGLE_OAUTH_CLIENT_ID", "Google OAuth client ID",
          "OAuth client for a bridge you run. The console does not perform the Google sign-in."),
    _cred("GOOGLE_OAUTH_CLIENT_SECRET", "Google OAuth client secret",
          "Stored in the bot home .env for a bridge that Hermes starts locally."),
)

CONNECTORS = (
    # ---- Social -----------------------------------------------------------------
    {
        "id": "facebook-pages", "name": "Facebook Pages", "category": "social", "initials": "f",
        "description": "Page insights, posts and comments through the Meta Graph API.",
        "kind": "bridge", "server_name": "facebook-pages", "auth": "bearer",
        "api": "Meta Graph API (Pages, Page Insights, comments)",
        "capabilities": (_cap("Read Page insights and reach"), _cap("Read posts and comments"),
                         _cap("Reply to comments", "public"), _cap("Publish posts", "public")),
        "credentials": (_cred("META_PAGE_ACCESS_TOKEN", "Page access token",
                              "Used by a bridge that Hermes starts locally. For an HTTP bridge keep it in the "
                              "bridge's own configuration and give Hermes only the bridge token."),),
        "docs": (_doc("Graph API", "https://developers.facebook.com/docs/graph-api"),),
        "note": BRIDGE_NOTE,
    },
    {
        "id": "instagram", "name": "Instagram", "category": "social", "initials": "IG",
        "description": "Professional account insights, media and comments through the Instagram Platform API.",
        "kind": "bridge", "server_name": "instagram", "auth": "bearer",
        "api": "Instagram Platform API (professional accounts)",
        "capabilities": (_cap("Read account and media insights"), _cap("Read comments"),
                         _cap("Reply to comments", "public"), _cap("Publish media", "public")),
        "credentials": (_cred("INSTAGRAM_ACCESS_TOKEN", "Instagram access token",
                              "Used by a bridge that Hermes starts locally."),),
        "docs": (_doc("Instagram Platform", "https://developers.facebook.com/docs/instagram-platform"),),
        "note": BRIDGE_NOTE,
    },
    {
        "id": "threads", "name": "Threads", "category": "social", "initials": "@",
        "description": "Threads posts, replies and insights through the Threads API.",
        "kind": "bridge", "server_name": "threads", "auth": "bearer",
        "api": "Threads API",
        "capabilities": (_cap("Read posts and insights"), _cap("Read replies"), _cap("Publish posts", "public")),
        "credentials": (_cred("THREADS_ACCESS_TOKEN", "Threads access token",
                              "Used by a bridge that Hermes starts locally."),),
        "docs": (_doc("Threads API", "https://developers.facebook.com/docs/threads"),),
        "note": BRIDGE_NOTE,
    },
    {
        "id": "x-twitter", "name": "X (Twitter)", "category": "social", "initials": "X",
        "description": "Posts, mentions and engagement through the X API v2.",
        "kind": "bridge", "server_name": "x-twitter", "auth": "bearer",
        "api": "X API v2 (app-only bearer for reading; user context to post)",
        "capabilities": (_cap("Read posts and mentions"), _cap("Read engagement counts"),
                         _cap("Post and reply (needs user-context auth in the bridge)", "public")),
        "credentials": (_cred("X_BEARER_TOKEN", "X app bearer token",
                              "App-only token: reading only. Used by a bridge that Hermes starts locally."),),
        "docs": (_doc("X API", "https://developer.x.com/en/docs/x-api"),),
        "note": BRIDGE_NOTE + " Your X API access tier limits what can be read.",
    },
    {
        "id": "tiktok", "name": "TikTok", "category": "social", "initials": "TT",
        "description": "Profile and video statistics through TikTok for Developers APIs.",
        "kind": "bridge", "server_name": "tiktok", "auth": "bearer",
        "api": "TikTok Display API and Content Posting API",
        "capabilities": (_cap("Read profile and video statistics"), _cap("Publish videos", "public")),
        "credentials": (_cred("TIKTOK_ACCESS_TOKEN", "TikTok user access token",
                              "Used by a bridge that Hermes starts locally."),),
        "docs": (_doc("TikTok for Developers", "https://developers.tiktok.com/"),),
        "note": BRIDGE_NOTE,
    },
    {
        "id": "youtube", "name": "YouTube", "category": "social", "initials": "YT",
        "description": "Channel, video and comment data through the YouTube Data and Analytics APIs.",
        "kind": "bridge", "server_name": "youtube", "auth": "bearer",
        "api": "YouTube Data API v3 and YouTube Analytics API",
        "capabilities": (_cap("Read public channel and video statistics"), _cap("Read comments"),
                         _cap("Read channel analytics (OAuth in the bridge)"), _cap("Reply to comments", "public")),
        "credentials": (_cred("YOUTUBE_API_KEY", "YouTube Data API key",
                              "Public data only. Channel analytics need an OAuth sign-in inside the bridge."),),
        "docs": (_doc("YouTube Data API", "https://developers.google.com/youtube/v3"),
                 _doc("YouTube Analytics API", "https://developers.google.com/youtube/analytics")),
        "note": BRIDGE_NOTE,
    },
    {
        "id": "linkedin", "name": "LinkedIn", "category": "social", "initials": "in",
        "description": "Organization page posts and statistics through LinkedIn's marketing APIs.",
        "kind": "bridge", "server_name": "linkedin", "auth": "bearer",
        "api": "LinkedIn Community Management API",
        "capabilities": (_cap("Read organization posts and statistics"), _cap("Publish posts", "public")),
        "credentials": (_cred("LINKEDIN_ACCESS_TOKEN", "LinkedIn access token",
                              "Requires an approved LinkedIn developer app. Used by a local bridge."),),
        "docs": (_doc("LinkedIn API docs", "https://learn.microsoft.com/en-us/linkedin/"),),
        "note": BRIDGE_NOTE,
    },
    # ---- Messaging --------------------------------------------------------------
    {
        "id": "zalo-oa", "name": "Zalo Official Account", "category": "messaging", "initials": "Za",
        "description": "Follower, message and article statistics for a Zalo OA through the official OA Open API.",
        "kind": "bridge", "server_name": "zalo-oa", "auth": "bearer",
        "api": "Zalo Official Account Open API (REST)",
        "capabilities": (_cap("Read follower and interaction statistics"), _cap("Read OA conversations"),
                         _cap("Send messages to followers", "messages"), _cap("Publish articles", "public")),
        "credentials": (_cred("ZALO_OA_ACCESS_TOKEN", "Zalo OA access token",
                              "Not the bot's ZALO_BOT_TOKEN. OA access tokens expire; the bridge must refresh them "
                              "with the app's refresh token."),),
        "docs": (_doc("Zalo for Developers", "https://developers.zalo.me/"),),
        "note": ("Zalo publishes an official REST API for Official Accounts but no official MCP server. Run a bridge "
                 "on this server that wraps the OA API (start with read-only statistics), then install its URL here."),
    },
    {
        "id": "telegram-channel", "name": "Telegram channel analytics", "category": "messaging", "initials": "Tg",
        "description": "Member counts and posts of a Telegram channel where a separate bot is an administrator.",
        "kind": "bridge", "server_name": "telegram-channel", "auth": "bearer",
        "api": "Telegram Bot API (member counts, channel posts); full statistics need the MTProto API",
        "capabilities": (_cap("Read member count and posts the bot receives"), _cap("Publish channel posts", "public")),
        "credentials": (_cred("TELEGRAM_CHANNEL_BOT_TOKEN", "Channel bot token",
                              "Use a separate bot that administers the channel, not the assistant's own "
                              "TELEGRAM_BOT_TOKEN."),),
        "docs": (_doc("Telegram Bot API", "https://core.telegram.org/bots/api"),
                 _doc("Channel statistics (MTProto)", "https://core.telegram.org/api/stats")),
        "note": ("The Bot API cannot read channel view or growth statistics; those require the MTProto API with a "
                 "user account. " + BRIDGE_NOTE),
    },
    # ---- Analytics --------------------------------------------------------------
    {
        "id": "google-analytics", "name": "Google Analytics", "category": "analytics", "initials": "GA",
        "description": "GA4 reports and realtime data through Google's official Analytics MCP server.",
        "kind": "local", "server_name": "google-analytics", "auth": "none",
        "api": "Google Analytics Admin and Data APIs",
        "capabilities": (_cap("Run GA4 reports"), _cap("Read realtime data"), _cap("Read account and property details")),
        "credentials": (_cred("GOOGLE_APPLICATION_CREDENTIALS", "Application default credentials file",
                              "A file path on the server, set by the operator. Not editable here.", writable=False),
                        _cred("GOOGLE_PROJECT_ID", "Google Cloud project ID",
                              "Set by the operator together with the credentials file.", writable=False)),
        "docs": (_doc("google-analytics-mcp", "https://github.com/googleanalytics/google-analytics-mcp"),),
        "snippet": ("mcp_servers:\n"
                    "  google-analytics:\n"
                    "    command: pipx\n"
                    "    args: [run, analytics-mcp]\n"
                    "    env:\n"
                    "      GOOGLE_APPLICATION_CREDENTIALS: ${GOOGLE_APPLICATION_CREDENTIALS}\n"
                    "      GOOGLE_PROJECT_ID: ${GOOGLE_PROJECT_ID}\n"
                    "    enabled: false\n"),
        "note": "A local (stdio) server. Install it on the server; check the project's README for the current command.",
    },
    {
        "id": "search-console", "name": "Google Search Console", "category": "analytics", "initials": "SC",
        "description": "Search queries, clicks and impressions for your sites.",
        "kind": "bridge", "server_name": "search-console", "auth": "bearer",
        "api": "Google Search Console API",
        "capabilities": (_cap("Read search performance"), _cap("Read sitemaps and indexing status")),
        "credentials": (_cred("GOOGLE_APPLICATION_CREDENTIALS", "Service account credentials file",
                              "A file path on the server, set by the operator. Not editable here.", writable=False),),
        "docs": (_doc("Search Console API", "https://developers.google.com/webmaster-tools"),),
        "note": BRIDGE_NOTE,
    },
    # ---- Productivity -----------------------------------------------------------
    {
        "id": "gmail", "name": "Gmail", "category": "productivity", "initials": "M",
        "description": "Search and summarize mail, draft replies.",
        "kind": "bridge", "server_name": "gmail", "auth": "bearer",
        "api": "Gmail API",
        "capabilities": (_cap("Search and read mail"), _cap("Create drafts", "write"), _cap("Send email", "messages")),
        "credentials": _GOOGLE_OAUTH,
        "docs": (_doc("Gmail API", "https://developers.google.com/gmail/api"),),
        "note": ("Community MCP servers for Google Workspace exist; review their code before running one. "
                 + BRIDGE_NOTE),
    },
    {
        "id": "google-calendar", "name": "Google Calendar", "category": "productivity", "initials": "31",
        "description": "Upcoming events, free time and scheduling.",
        "kind": "bridge", "server_name": "google-calendar", "auth": "bearer",
        "api": "Google Calendar API",
        "capabilities": (_cap("Read calendars and events"), _cap("Create or change events", "write"),
                         _cap("Invite attendees (sends email)", "messages")),
        "credentials": _GOOGLE_OAUTH,
        "docs": (_doc("Calendar API", "https://developers.google.com/calendar/api"),),
        "note": BRIDGE_NOTE,
    },
    {
        "id": "google-drive", "name": "Google Drive & Sheets", "category": "productivity", "initials": "Dr",
        "description": "Find documents and read or update spreadsheets.",
        "kind": "bridge", "server_name": "google-drive", "auth": "bearer",
        "api": "Google Drive API and Google Sheets API",
        "capabilities": (_cap("Search and read files"), _cap("Read spreadsheet ranges"),
                         _cap("Write cells and create files", "write")),
        "credentials": _GOOGLE_OAUTH,
        "docs": (_doc("Drive API", "https://developers.google.com/drive/api"),
                 _doc("Sheets API", "https://developers.google.com/sheets/api")),
        "note": BRIDGE_NOTE,
    },
    {
        "id": "notion", "name": "Notion", "category": "productivity", "initials": "N",
        "description": "Search and read pages and databases, add notes, through Notion's official MCP server.",
        "kind": "local", "server_name": "notion", "auth": "none",
        "api": "Notion API",
        "capabilities": (_cap("Search and read pages and databases"), _cap("Create and update pages", "write")),
        "credentials": (_cred("NOTION_TOKEN", "Notion integration token",
                              "Internal integration secret. Share only the pages the assistant should see."),),
        "docs": (_doc("notion-mcp-server", "https://github.com/makenotion/notion-mcp-server"),
                 _doc("Notion API", "https://developers.notion.com/")),
        "snippet": ("mcp_servers:\n"
                    "  notion:\n"
                    "    command: npx\n"
                    "    args: [-y, \"@notionhq/notion-mcp-server\"]\n"
                    "    env:\n"
                    "      NOTION_TOKEN: ${NOTION_TOKEN}\n"
                    "    enabled: false\n"),
        "note": ("A local (stdio) server: install it on the server. Notion's hosted MCP server signs in with OAuth, "
                 "which this console does not run."),
    },
    # ---- Developer --------------------------------------------------------------
    {
        "id": "github", "name": "GitHub", "category": "developer", "initials": "GH",
        "description": "Repositories, issues and pull requests through GitHub's official MCP server.",
        "kind": "remote", "server_name": "github", "auth": "bearer",
        "url": "https://api.githubcopilot.com/mcp/",
        "api": "GitHub MCP Server (hosted)",
        "capabilities": (_cap("Read repositories, issues and pull requests"),
                         _cap("Create issues, comments and pull requests", "write")),
        "credentials": (),
        "docs": (_doc("github-mcp-server", "https://github.com/github/github-mcp-server"),),
        "note": ("Use a fine-grained personal access token with the narrowest scopes as the bearer token. The server "
                 "documents a read-only mode; prefer it for reports. Check its README for access requirements."),
    },
    # ---- Custom -----------------------------------------------------------------
    {
        "id": "custom", "name": "Custom MCP server", "category": "custom", "initials": "+",
        "description": "Any MCP server that speaks Streamable HTTP, including bridges you run yourself.",
        "kind": "custom", "server_name": "", "auth": "bearer",
        "api": "Model Context Protocol (Streamable HTTP)",
        "capabilities": (_cap("Depends on the server: review its tools before enabling", "unknown"),),
        "credentials": (),
        "docs": (_doc("Model Context Protocol", "https://modelcontextprotocol.io/"),),
        "note": "https URLs only (http only on localhost). Local command servers are added by the operator on the host.",
    },
)


def connection_guide(template):
    """Presentation metadata, not a claim that an app is authenticated or usable."""
    tid, kind = template["id"], template["kind"]
    owner = {"facebook-pages": "Meta", "instagram": "Meta", "threads": "Meta", "x-twitter": "X",
             "tiktok": "TikTok", "youtube": "Google", "linkedin": "LinkedIn", "zalo-oa": "Zalo",
             "telegram-channel": "Telegram", "google-analytics": "Google", "search-console": "Google",
             "gmail": "Google", "google-calendar": "Google", "google-drive": "Google", "notion": "Notion",
             "github": "GitHub"}.get(tid, "Your server")
    if kind == "bridge":
        method, hint = "bridge_http", "Your bridge · URL + optional token"
        steps = ["Ask your operator to run an MCP bridge for this app using the linked API documentation.",
                 "Authorize the app inside that bridge. Provider credentials belong in the bridge's configuration.",
                 "Enter the bridge MCP URL here, and its bearer token if required. Saving an API key alone does not create a bridge."]
    elif kind == "local":
        method, hint = "operator_stdio", "Local server · operator setup"
        steps = ["Ask your operator to install the documented server package in the bot's environment.",
                 "Add the supplied configuration to this bot's config.yaml and set the required credentials.",
                 "Refresh this page, review channel access, then enable the server and restart the gateway."]
    elif kind == "remote":
        method, hint = "bearer_http", "Hosted MCP · personal access token"
        steps = ["Create a token using the provider's documentation. Grant only the permissions you need.",
                 "Save the hosted MCP URL and token here. The token is write-only.",
                 "Test the saved server, review its tools, then enable it and restart the gateway."]
    else:
        method, hint = "custom_http", "MCP URL · token or no authentication"
        steps = ["Use a trusted Streamable HTTP MCP endpoint. Local commands are configured by an operator.",
                 "Choose no authentication or enter the endpoint's bearer token. Browser OAuth is not available in this Console.",
                 "Test the saved server before enabling it. Review channel access and restart the gateway to apply changes."]
    return {"method": method, "hint": hint, "publisher": ("Your bridge · " + owner + " API") if kind == "bridge" else owner,
            "steps": steps, "web_oauth_supported": False,
            "oauth_note": "Hermes supports MCP OAuth in its CLI and its separate Dashboard. This Console does not expose their authorization/callback routes; gateway API Chat does not provide them."}


def _section(en, vi, guide):
    return {"en": en, "vi": vi, "guide": guide}


SOCIAL = ("facebook-pages", "instagram", "threads", "x-twitter", "tiktok", "youtube", "linkedin")

WORK = ("gmail", "google-calendar", "google-drive", "notion", "github")
KPI = ("google-analytics", "search-console", "facebook-pages", "instagram", "youtube", "tiktok", "zalo-oa",
       "telegram-channel")
ALL_CONNECTORS = tuple(item["id"] for item in CONNECTORS)

# Featured templates come first: they are offered in the "New report" flow.
REPORTS = (
    {
        "id": "daily-social-summary", "name": "Daily social summary", "short": "Daily social", "featured": True,
        "description": "Yesterday's reach, engagement and top posts across your social accounts.",
        "connectors": SOCIAL, "requires": "any",
        "schedule": "0 8 * * *", "period": "the previous 24 hours",
        "focus": "reach, engagement rate, new followers, top posts",
        "sections": (
            _section("Summary", "Tóm tắt", "three bullet points with the most important changes"),
            _section("Key metrics", "Chỉ số chính", "a table: metric | value | change vs the previous period"),
            _section("Top content", "Nội dung nổi bật", "the best and weakest posts with one reason each"),
            _section("Needs attention", "Cần chú ý", "drops, spikes, negative comments or anything unusual"),
            _section("Suggested actions", "Đề xuất", "at most three concrete next steps"),
            _section("Data gaps", "Thiếu dữ liệu", "tools that were missing or failed, and what could not be checked"),
        ),
    },
    {
        "id": "weekly-work-digest", "name": "Weekly work digest", "short": "Work digest", "featured": True,
        "description": "Last week's meetings, documents, tasks and open issues, with what needs a decision.",
        "connectors": WORK, "requires": "any",
        "account_label": "Team or project", "account_help": "Shown in the report title and job name.",
        "schedule": "0 8 * * 1", "period": "the previous 7 days",
        "focus": "decisions needed, overdue tasks, upcoming deadlines, open issues",
        "sections": (
            _section("Summary", "Tóm tắt", "three bullet points about the week"),
            _section("Needs a decision", "Cần quyết định", "items waiting for the owner, with who is waiting"),
            _section("Done last week", "Đã xong tuần trước", "completed work, grouped by project"),
            _section("Coming up", "Sắp tới", "meetings and deadlines in the next 7 days"),
            _section("Risks", "Rủi ro", "overdue or blocked work and why"),
            _section("Data gaps", "Thiếu dữ liệu", "tools that were missing or failed"),
        ),
    },
    {
        "id": "mentions-to-reply", "name": "Mentions watch", "short": "Mentions to reply", "featured": True,
        "description": "New comments, mentions and messages that deserve a reply, most urgent first.",
        "connectors": SOCIAL + ("zalo-oa",), "requires": "any",
        "schedule": "0 9,17 * * *", "period": "since the previous run (at most 24 hours)",
        "focus": "questions, complaints, sales leads",
        "sections": (
            _section("Reply first", "Cần trả lời ngay", "urgent items with platform, link or identifier and why"),
            _section("Reply today", "Trả lời trong ngày", "normal-priority items"),
            _section("Suggested replies", "Gợi ý trả lời", "short draft replies for the owner to review; never post them"),
            _section("Skipped", "Bỏ qua", "spam or items that need no reply, counted"),
            _section("Data gaps", "Thiếu dữ liệu", "tools that were missing or failed"),
        ),
    },
    {
        "id": "kpi-snapshot", "name": "KPI snapshot", "short": "KPI snapshot", "featured": True,
        "description": "One table of the numbers you track across website, social and messaging, with the trend.",
        "connectors": KPI, "requires": "any",
        "account_label": "Business or brand", "account_help": "Shown in the report title and job name.",
        "schedule": "0 8 * * *", "period": "the previous 24 hours compared with the same day last week",
        "focus": "visitors, followers, engagement, messages, conversions",
        "sections": (
            _section("Headline", "Điểm chính", "one sentence: better, worse or flat, and why"),
            _section("KPI table", "Bảng chỉ số", "a table: KPI | value | change | source"),
            _section("Outliers", "Bất thường", "any KPI that moved more than 20%"),
            _section("Data gaps", "Thiếu dữ liệu", "tools that were missing or failed"),
        ),        "extra_rules": (
            "After writing the report, also save the KPI table for the console briefing: overwrite the file "
            "console/kpis.json in the Hermes home directory ($HERMES_HOME) with UTF-8 JSON in exactly this shape: "
            '{"period": "Last 7 days", "items": [{"id": "fb-reach", "platform": "facebook", "label": "Page reach", '
            '"value": 18420, "previous": 16390, "unit": "", "good_when": "up", "series": [2100, 2300, 2250]}]}. '
            "platform is one of facebook, zalo, telegram, x, instagram, tiktok, youtube, work or custom; good_when is "
            "up, down or neutral; value, previous and series are plain numbers (series: at most 30, oldest first); "
            "at most 8 items; leave out any metric you could not read instead of guessing. This file is the only "
            "thing you may write.",
        ),
    },
    {
        "id": "custom", "name": "Custom report", "short": "Custom", "featured": True,
        "description": "Describe what you want to know; the assistant reports on it with any enabled connector.",
        "connectors": ALL_CONNECTORS, "requires": "optional",
        "account_label": "Report name", "account_help": "A short name, for example \"Pricing watch\".",
        "notes_label": "What should the report cover?", "notes_required": True,
        "schedule": "0 8 * * *", "period": "since the previous run (at most 7 days)",
        "focus": "what changed and what needs the owner's attention",
        "sections": (
            _section("Summary", "Tóm tắt", "three bullet points"),
            _section("Details", "Chi tiết", "what the owner asked for, with numbers and sources"),
            _section("Suggested actions", "Đề xuất", "at most three concrete next steps"),
            _section("Data gaps", "Thiếu dữ liệu", "tools that were missing or failed"),
        ),
    },
    {
        "id": "zalo-oa-weekly", "name": "Weekly Zalo OA engagement report", "short": "Zalo OA weekly",
        "description": "Followers, messages and article performance of your Zalo Official Account over the week.",
        "connectors": ("zalo-oa",), "requires": "any",
        "schedule": "every monday 8am", "period": "the previous 7 days",
        "focus": "new and lost followers, messages received, response time, article views",
        "sections": (
            _section("Summary", "Tóm tắt", "three bullet points about the week"),
            _section("Followers", "Người quan tâm", "new, lost and net followers with the trend"),
            _section("Conversations", "Tin nhắn", "message volume, unanswered threads and common topics (no personal data)"),
            _section("Articles and broadcasts", "Bài viết và thông báo", "views and interactions per item"),
            _section("Suggested actions", "Đề xuất", "at most three concrete next steps"),
            _section("Data gaps", "Thiếu dữ liệu", "tools that were missing or failed"),
        ),
    },
    {
        "id": "competitor-watch", "name": "Competitor watch", "short": "Competitor watch",
        "description": "What named competitors published and how their audience reacted.",
        "connectors": SOCIAL, "requires": "optional",
        "account_label": "Competitors to watch",
        "account_help": "Names, pages or handles, separated by commas.",
        "schedule": "every friday 4pm", "period": "the previous 7 days",
        "focus": "posting frequency, campaigns, offers, audience reaction",
        "sections": (
            _section("Summary", "Tóm tắt", "three bullet points"),
            _section("By competitor", "Theo đối thủ", "for each competitor: what they published and notable reactions"),
            _section("Trends", "Xu hướng", "themes or formats that are working"),
            _section("Opportunities", "Cơ hội", "ideas we could act on"),
            _section("Sources", "Nguồn", "links or tool names used for every claim"),
        ),
    },
    {
        "id": "content-calendar-check", "name": "Content calendar check", "short": "Content calendar",
        "description": "Checks next week's planned posts for gaps, clashes and missing assets.",
        "connectors": ("google-calendar", "google-drive", "notion"), "requires": "any",
        "schedule": "every thursday 3pm", "period": "the next 7 days",
        "focus": "missing posts, duplicate topics, unapproved drafts, deadlines",
        "sections": (
            _section("Summary", "Tóm tắt", "is next week ready? one sentence and a status"),
            _section("Planned content", "Nội dung đã lên lịch", "a table: date | channel | topic | status"),
            _section("Gaps and clashes", "Khoảng trống và trùng lặp", "empty days, duplicate topics, overloaded days"),
            _section("To do", "Việc cần làm", "missing drafts, assets or approvals with owners if known"),
            _section("Data gaps", "Thiếu dữ liệu", "tools that were missing or failed"),
        ),
    },
    {
        "id": "website-traffic", "name": "Website traffic digest", "short": "Website traffic",
        "description": "Weekly visitors, sources and search performance for your website.",
        "connectors": ("google-analytics", "search-console"), "requires": "any",
        "schedule": "every monday 9am", "period": "the previous 7 days",
        "focus": "users, sessions, top pages, traffic sources, search clicks",
        "sections": (
            _section("Summary", "Tóm tắt", "three bullet points"),
            _section("Key metrics", "Chỉ số chính", "a table: metric | value | change vs the previous week"),
            _section("Top pages and sources", "Trang và nguồn truy cập", "top five of each"),
            _section("Search", "Tìm kiếm", "top queries, clicks and position changes"),
            _section("Suggested actions", "Đề xuất", "at most three concrete next steps"),
            _section("Data gaps", "Thiếu dữ liệu", "tools that were missing or failed"),
        ),
    },
)
