import os

APP_NAME = "HeadInspect API"
APP_VERSION = "0.5.0"

USER_AGENT = os.getenv(
    "HEADINSPECT_USER_AGENT",
    "HeadInspectBot/0.5 (+https://headinspect.ru/)",
)

CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 10.0
DNS_TIMEOUT = 8.0

# Per-page liveness bound: a single page's own fetch+analyze work may take at
# most this long (applied inside audit.analyze_page, around the real work only,
# never around the semaphore wait). Guarantees one hung/slow page can never
# hold a worker forever - it is turned into a check_failed "timeout" result and
# the worker moves on.
PAGE_TIMEOUT = 30.0

# --- Job execution budget ------------------------------------------------
# The audit is NOT bounded by a single fixed wall-clock over discovery+crawl.
# That old model (one 90 s AUDIT_TIMEOUT around everything) truncated healthy
# large sites purely because legitimate work takes time: at ~2.8 s/page and
# PAGE_CONCURRENCY workers, throughput is bounded, so a fixed 90 s could only
# ever reach ~70-80 pages regardless of the 500-page functional limit. Instead
# the two phases are bounded independently, and the crawl budget scales with
# the amount of real work while staying capped:
#
#   * DISCOVERY_TIMEOUT bounds only discovery (entry + robots + sitemaps), so a
#     slow/stalled discovery can't silently consume the crawl's budget. Real
#     discovery finishes well under it; only a stall hits it.
DISCOVERY_TIMEOUT = 30.0
#
#   * The page crawl gets a deadline that scales with the number of discovered
#     pages, capped by a hard maximum:
#         crawl_deadline = min(CRAWL_MAX_SECONDS,
#                              CRAWL_BASE_SECONDS + CRAWL_PER_PAGE_SECONDS * n)
#     Sizing: measured slow sites answer in ~2.8 s/page; with PAGE_CONCURRENCY=4
#     that is ~0.77 s of wall-clock per discovered page. CRAWL_PER_PAGE_SECONDS
#     is set ~1.4x above that so a healthy crawl finishes comfortably inside its
#     deadline instead of racing it, and a full 500-page slow-but-healthy site
#     still completes (500 * ~0.8 s ≈ 400 s < CRAWL_MAX_SECONDS). A small
#     pathological site gets a correspondingly small budget and is cut quickly.
CRAWL_BASE_SECONDS = 30.0
CRAWL_PER_PAGE_SECONDS = 1.1
CRAWL_MAX_SECONDS = 600.0
#
#   * A progress watchdog stops the crawl if NOT ONE page completes for this
#     long, even before the deadline. It must exceed PAGE_TIMEOUT so a run of
#     legitimately slow-but-alive pages (each up to PAGE_TIMEOUT) never trips
#     it - only a genuine, total stall (network black hole, mass hang) does.
CRAWL_STALL_TIMEOUT = 60.0
MAX_REDIRECTS = 5

MAX_AUDIT_URLS = 500

# --- Concurrency model ---------------------------------------------------
# Three limits with distinct jobs, sized so that (a) several audits run at once,
# (b) no single audit can monopolise the server, and (c) total load on the small
# VPS is hard-capped regardless of how many users arrive:
#
#   PAGE_CONCURRENCY  - PER-AUDIT cap: the size of each audit's page-worker pool
#     (see audit.run_pages). One audit therefore issues at most PAGE_CONCURRENCY
#     concurrent requests, which also keeps us polite to any single site (never
#     more than this many simultaneous requests to one host). This is the
#     structural fairness guarantee: because an audit can never hold more than
#     PAGE_CONCURRENCY of the global fetch permits below, a 500-page audit and a
#     5-page audit compete on equal footing - the big one just has more pages to
#     get through, it can't grab a disproportionate share of the pool.
PAGE_CONCURRENCY = 4
#
#   GLOBAL_MAX_CONCURRENT_FETCHES - GLOBAL cap: the real resource limiter. Every
#     outbound request from every audit (discovery + page crawl) funnels through
#     one process-wide semaphore (fetcher._global_fetch_semaphore), so the total
#     number of in-flight third-party requests - and hence peak sockets, HTML/
#     image buffers and parse CPU - is bounded by this number no matter how many
#     audits or pages exist. Sized for the VPS, and >= a few multiples of
#     PAGE_CONCURRENCY so multiple audits can progress in parallel: with 12 and a
#     per-audit cap of 4, up to 3 audits run at full speed with no contention,
#     and more than that share the pool fairly (each still capped at 4).
GLOBAL_MAX_CONCURRENT_FETCHES = 12
#
#   MAX_CONCURRENT_AUDITS - how many audits may be *in flight* (discovery+crawl)
#     at once before new ones queue. This is deliberately NOT the primary
#     resource cap and is set well above GLOBAL_MAX_CONCURRENT_FETCHES: real load
#     is already bounded by that global pool, which the active audits simply
#     share, so accepted audits should progress concurrently rather than wait for
#     each other. Its only job is to bound the cheap per-audit bookkeeping (live
#     page-worker pools: <= MAX_CONCURRENT_AUDITS * PAGE_CONCURRENCY mostly-idle
#     tasks, plus each audit's Job state), so the whole-audit queue below is only
#     an emergency valve under genuinely heavy load - not something a 7th user
#     hits. Under contention many audits share 12 fetch slots: small ones still
#     finish fast; large ones may return an honest partial (crawl deadline).
MAX_CONCURRENT_AUDITS = 24
MAX_QUEUED_AUDITS = 12

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

# --- Limits for extracting the *contents* of Schema.org (JSON-LD) objects.
# These bound how much per-page Schema data the backend stores in a job and
# returns in /results, so a page with pathologically large structured data
# cannot blow up memory or the response for an audit of up to MAX_AUDIT_URLS
# (500) pages. They are intentionally generous: ordinary, correct Schema.org
# markup fits well within them and is never truncated - the limits only bite
# on genuinely unusual pages, and when they do the UI says so honestly rather
# than silently cutting data.
#
# SCHEMA_MAX_VALUE_CHARS is 1000 (not a few hundred): real `description`,
# `articleBody` excerpts and similar legitimate fields routinely exceed a few
# hundred characters, and clipping them would make normal, valid Schema look
# broken. 1000 keeps whole descriptions intact while still capping a single
# runaway string. The per-page byte budget below is the real backstop against
# a page that stacks many such values.
SCHEMA_MAX_OBJECTS_PER_PAGE = 25
SCHEMA_MAX_PROPS_PER_OBJECT = 40
SCHEMA_MAX_VALUE_CHARS = 1000
SCHEMA_MAX_ARRAY_ITEMS = 20
SCHEMA_MAX_DEPTH = 4
# Total budget (in characters, a close and cheap proxy for bytes) for all
# normalized object data of a single page. ~24 KB comfortably holds several
# rich entities with full descriptions; worst case is ~24 KB x 500 pages =
# ~12 MB per job, a ceiling that realistic sites never approach.
SCHEMA_MAX_CHARS_PER_PAGE = 24 * 1024

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
