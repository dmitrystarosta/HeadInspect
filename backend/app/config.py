import os

APP_NAME = "HeadInspect API"
APP_VERSION = "0.4.0"

USER_AGENT = os.getenv(
    "HEADINSPECT_USER_AGENT",
    "HeadInspectBot/0.4 (+https://headinspect.ru/)",
)

CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 10.0
DNS_TIMEOUT = 8.0
PAGE_TIMEOUT = 30.0
AUDIT_TIMEOUT = 90.0
MAX_REDIRECTS = 5

MAX_AUDIT_URLS = 500
PAGE_CONCURRENCY = 4
MAX_CONCURRENT_AUDITS = 1
MAX_QUEUED_AUDITS = 5

# Process-wide cap on simultaneous outbound HTTP requests to third-party
# sites, across *all* jobs combined (see fetcher.py::_global_fetch_semaphore).
# Kept equal to PAGE_CONCURRENCY today, which means it has no observable
# effect while MAX_CONCURRENT_AUDITS == 1 (a single job already can't exceed
# PAGE_CONCURRENCY concurrent requests). Its purpose is to make a future
# increase of MAX_CONCURRENT_AUDITS safe: without it, N concurrent jobs would
# each independently open up to PAGE_CONCURRENCY connections, so total
# outbound concurrency would grow unbounded with the number of running jobs.
GLOBAL_MAX_CONCURRENT_FETCHES = PAGE_CONCURRENCY

RATE_LIMIT_AUDITS = 3
RATE_LIMIT_WINDOW_SECONDS = 2 * 60

MAX_HTML_BYTES = 2 * 1024 * 1024
MAX_SITEMAP_BYTES = 5 * 1024 * 1024
MAX_ROBOTS_BYTES = 512 * 1024
MAX_OG_IMAGE_BYTES = 6 * 1024 * 1024

MAX_SITEMAP_DEPTH = 4
MAX_SITEMAPS = 50

# Conservative OG-image guidance for warnings.
RECOMMENDED_OG_WIDTH = 1200
RECOMMENDED_OG_HEIGHT = 630
MIN_OG_WIDTH = 600
MIN_OG_HEIGHT = 315
WARN_OG_IMAGE_BYTES = 1 * 1024 * 1024

# Pillow decompression-bomb protection.
MAX_IMAGE_PIXELS = 40_000_000

# v0.3 still stores jobs in memory.
JOB_TTL_SECONDS = 60 * 60
