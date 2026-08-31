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

# Minimum time between two audits *of the same site*, regardless of who
# requests them or from which IP - protects the audited site's own server
# from repeated full crawls in quick succession. Independent of (and in
# addition to) RATE_LIMIT_AUDITS above, which limits one client's request
# rate but does nothing to stop two different visitors (or the same one
# from two IPs) from both launching a full crawl of the same site back to
# back. See JobManager._cooldown_site_key / JobManager.create.
DOMAIN_COOLDOWN_SECONDS = 10 * 60

# Hard ceiling on how many Job objects the backend keeps in memory at once,
# independent of JOB_TTL_SECONDS (a job can be evicted for being over this
# count before it is old enough for TTL, or vice versa - both apply).
# Safely-finished jobs are evicted oldest-first once this is reached; queued
# and running jobs are never touched (see JobManager.cleanup). Kept well
# above MAX_QUEUED_AUDITS + MAX_CONCURRENT_AUDITS so it is never reached by
# active jobs alone under today's other limits.
MAX_JOBS = 200

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
