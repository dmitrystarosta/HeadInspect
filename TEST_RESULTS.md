# Результаты автоматических тестов — HeadInspect

Обновлено: 28 августа 2026 (после независимой проверки на реальных
зависимостях и исправления найденного тестового бага — см.
`CHANGELOG.md`, раздел "Исправление после независимой проверки").

Backend-прогон ниже по-прежнему выполнен через локальные заглушки
fastapi/httpx/pydantic (в этой среде нет сети для `pip install`), но
заглушка pydantic была **ужесточена** по итогам независимой проверки:
теперь она, как и настоящий Pydantic, требует обязательные поля и
отклоняет `None` для не-`Optional` полей (`str`, `int` и т.д. без
`| None`). Это именно то поведение, из-за которого настоящий Pydantic
на реальном окружении нашёл баг в одном тесте — заглушка теперь
воспроизводит его.

Независимая проверка на **настоящих** зависимостях (Python 3.12.10,
`backend/requirements-dev.txt`, команда `python -m pytest tests -v`)
дала: **50 passed, 1 failed** — единственное падение было в тесте, не в
production-коде (см. CHANGELOG). Тест исправлен; итог после
исправления и повторной проверки заглушками — **53 passed, 0 failed**
(51 исходных + 2 новых теста, добавленных по итогам этой же проверки —
см. CHANGELOG).

**Просьба повторить настоящий `pytest`-прогон на реальном окружении
после замены файлов** — ожидаемый результат: **53 passed**.

---

## Backend (`backend/tests/`, pytest-совместимые файлы)

```

=== test_fetcher_pinning.py ===
PASS: test_fetcher_pinning.py::test_safe_fetch_pins_the_validated_ip_and_preserves_host_and_sni
PASS: test_fetcher_pinning.py::test_safe_fetch_revalidates_and_repins_on_every_redirect_hop

=== test_global_semaphore.py ===
PASS: test_global_semaphore.py::test_global_semaphore_caps_real_concurrency_across_many_callers
PASS: test_global_semaphore.py::test_global_max_concurrent_fetches_equals_page_concurrency_today

=== test_job_lifecycle.py ===
PASS: test_job_lifecycle.py::test_get_missing_job_raises_404_with_stable_contract
Audit timeout-job timed out after 0 seconds: ip=unknown url=https://example.ru/, checked=2
PASS: test_job_lifecycle.py::test_audit_timeout_with_partial_results_becomes_completed_partial
Audit timeout-job-2 timed out after 0 seconds: ip=unknown url=https://example.ru/, checked=0
PASS: test_job_lifecycle.py::test_audit_timeout_with_zero_checked_pages_stays_a_plain_failure
PASS: test_job_lifecycle.py::test_single_blocked_page_does_not_stop_the_whole_audit
Audit blocked-entry-job stopped at entry URL: HTTP 403 blocks automated access, ip=unknown url=https://example.ru/
PASS: test_job_lifecycle.py::test_entry_page_access_blocked_still_stops_before_any_page_checks
Audit model-serialization-job timed out after 0 seconds: ip=unknown url=https://example.ru/, checked=2
PASS: test_job_lifecycle.py::test_status_and_results_model_serialize_with_real_pydantic
PASS: test_job_lifecycle.py::test_status_model_with_mid_audit_block_fields_populated

=== test_jobs_block_detection.py ===
PASS: test_jobs_block_detection.py::test_single_403_among_healthy_pages_is_not_a_block
Audit j2: site appears to have started blocking HeadInspect mid-audit (HTTP 403), stopping further requests after 40 checked pages, ip=unknown
PASS: test_jobs_block_detection.py::test_mass_403_after_good_pages_is_detected_as_a_block
PASS: test_jobs_block_detection.py::test_site_that_403s_everything_from_the_start_is_not_flagged
Audit j4: site appears to have started blocking HeadInspect mid-audit (HTTP 403), stopping further requests after 40 checked pages, ip=unknown
PASS: test_jobs_block_detection.py::test_partial_status_reason_mentions_the_blocking_code

=== test_regressions.py ===
PASS: test_regressions.py::test_redirect_page_never_creates_a_false_meta_duplicate
PASS: test_regressions.py::test_genuine_duplicate_title_across_two_distinct_pages_is_still_flagged
PASS: test_regressions.py::test_check_failed_pages_are_excluded_from_duplicate_detection
PASS: test_regressions.py::test_run_pages_stop_event_halts_further_fetches

=== test_security.py ===
PASS: test_security.py::test_normalize_public_url_adds_https_scheme
PASS: test_security.py::test_normalize_public_url_lowercases_host
PASS: test_security.py::test_normalize_public_url_strips_trailing_dot
PASS: test_security.py::test_normalize_public_url_rejects_non_http_scheme
PASS: test_security.py::test_normalize_public_url_rejects_credentials
PASS: test_security.py::test_normalize_public_url_rejects_nonstandard_ports
PASS: test_security.py::test_is_forbidden_ip_matrix[{'literal': '127.0.0.1', 'expected': True}]
PASS: test_security.py::test_is_forbidden_ip_matrix[{'literal': '10.0.0.5', 'expected': True}]
PASS: test_security.py::test_is_forbidden_ip_matrix[{'literal': '172.16.0.5', 'expected': True}]
PASS: test_security.py::test_is_forbidden_ip_matrix[{'literal': '192.168.1.1', 'expected': True}]
PASS: test_security.py::test_is_forbidden_ip_matrix[{'literal': '169.254.169.254', 'expected': True}]
PASS: test_security.py::test_is_forbidden_ip_matrix[{'literal': '100.100.100.200', 'expected': True}]
PASS: test_security.py::test_is_forbidden_ip_matrix[{'literal': '224.0.0.1', 'expected': True}]
PASS: test_security.py::test_is_forbidden_ip_matrix[{'literal': '0.0.0.0', 'expected': True}]
PASS: test_security.py::test_is_forbidden_ip_matrix[{'literal': '::1', 'expected': True}]
PASS: test_security.py::test_is_forbidden_ip_matrix[{'literal': 'fe80::1', 'expected': True}]
PASS: test_security.py::test_is_forbidden_ip_matrix[{'literal': '::ffff:127.0.0.1', 'expected': True}]
PASS: test_security.py::test_is_forbidden_ip_matrix[{'literal': '8.8.8.8', 'expected': False}]
PASS: test_security.py::test_is_forbidden_ip_matrix[{'literal': '1.1.1.1', 'expected': False}]
PASS: test_security.py::test_is_forbidden_ip_matrix[{'literal': '93.184.216.34', 'expected': False}]
PASS: test_security.py::test_resolve_and_validate_host_blocks_dns_rebinding

=== test_sitemap_parsing.py ===
PASS: test_sitemap_parsing.py::test_image_loc_does_not_become_a_page
PASS: test_sitemap_parsing.py::test_video_loc_does_not_become_a_page
PASS: test_sitemap_parsing.py::test_plain_urlset_keeps_working
PASS: test_sitemap_parsing.py::test_sitemap_index_keeps_working
PASS: test_sitemap_parsing.py::test_gzip_sitemap_is_transparently_decompressed
PASS: test_sitemap_parsing.py::test_corrupted_gzip_raises_an_explanatory_error_not_a_silent_fallback
PASS: test_sitemap_parsing.py::test_gzip_bomb_is_rejected
PASS: test_sitemap_parsing.py::test_non_gzip_bytes_pass_through_unchanged
PASS: test_sitemap_parsing.py::test_unsupported_root_element_still_rejected
PASS: test_sitemap_parsing.py::test_invalid_xml_still_rejected

=== test_www_nonwww.py ===
PASS: test_www_nonwww.py::test_discover_urls_accepts_the_explicit_site_host
PASS: test_www_nonwww.py::test_discover_urls_rejects_unrelated_subdomain_even_with_matching_etld1
PASS: test_www_nonwww.py::test_discover_audit_urls_uses_entry_pages_actual_redirect_target_as_site_host


TOTAL: 53 passed, 0 failed
```

---

## Frontend (`tests/frontend/`, `node --test`)

```
TAP version 13
# HeadInspect: unexpected error TypeError: Cannot set properties of null (setting 'textContent')
#     at TestContext.<anonymous> (file:///home/claude/work/edited/tests/frontend/common.test.mjs:129:22)
#     at Test.runInAsyncScope (node:async_hooks:214:14)
#     at Test.run (node:internal/test_runner/test:1047:25)
#     at Test.processPendingSubtests (node:internal/test_runner/test:744:18)
#     at Test.postRun (node:internal/test_runner/test:1173:19)
#     at Test.run (node:internal/test_runner/test:1101:12)
#     at async Test.processPendingSubtests (node:internal/test_runner/test:744:7)
# Subtest: uniqueContentPages: direct page wins over a redirect to the same final URL
ok 1 - uniqueContentPages: direct page wins over a redirect to the same final URL
  ---
  duration_ms: 3.451868
  type: 'test'
  ...
# Subtest: uniqueContentPages: keeps first redirect record if no direct record exists
ok 2 - uniqueContentPages: keeps first redirect record if no direct record exists
  ---
  duration_ms: 1.156832
  type: 'test'
  ...
# Subtest: uniqueContentPages: check_failed pages are never merged by url
ok 3 - uniqueContentPages: check_failed pages are never merged by url
  ---
  duration_ms: 0.878133
  type: 'test'
  ...
# Subtest: uniqueContentPages: pages without a final url are kept separately
ok 4 - uniqueContentPages: pages without a final url are kept separately
  ---
  duration_ms: 1.061227
  type: 'test'
  ...
# Subtest: sortRowsForDisplay: home page always first, then by severity, then path
ok 5 - sortRowsForDisplay: home page always first, then by severity, then path
  ---
  duration_ms: 2.441997
  type: 'test'
  ...
# Subtest: escapeHtml escapes dangerous characters and keeps an em dash placeholder for empty values
ok 6 - escapeHtml escapes dangerous characters and keeps an em dash placeholder for empty values
  ---
  duration_ms: 1.070572
  type: 'test'
  ...
# Subtest: pluralRu picks the correct Russian plural form
ok 7 - pluralRu picks the correct Russian plural form
  ---
  duration_ms: 1.134832
  type: 'test'
  ...
# Subtest: apiFetch throws ApiError with the server-provided detail on a non-2xx response
ok 8 - apiFetch throws ApiError with the server-provided detail on a non-2xx response
  ---
  duration_ms: 1.396806
  type: 'test'
  ...
# Subtest: apiFetch classifies a 404 with code 'not_found', and isJobNotFound recognizes it
ok 9 - apiFetch classifies a 404 with code 'not_found', and isJobNotFound recognizes it
  ---
  duration_ms: 1.522493
  type: 'test'
  ...
# Subtest: apiFetch wraps a network failure (not an AbortError) into a friendly ApiError
ok 10 - apiFetch wraps a network failure (not an AbortError) into a friendly ApiError
  ---
  duration_ms: 1.529283
  type: 'test'
  ...
# Subtest: describeError: an ApiError message is shown to the user as-is (item 4)
ok 11 - describeError: an ApiError message is shown to the user as-is (item 4)
  ---
  duration_ms: 1.008322
  type: 'test'
  ...
# Subtest: describeError: an unexpected JS error never leaks its raw message to the user (item 4)
ok 12 - describeError: an unexpected JS error never leaks its raw message to the user (item 4)
  ---
  duration_ms: 5.357596
  type: 'test'
  ...
# Subtest: describeError returns null for an AbortError (caller should silently stop)
ok 13 - describeError returns null for an AbortError (caller should silently stop)
  ---
  duration_ms: 0.886049
  type: 'test'
  ...
# Subtest: pollJobUntilDone resolves once the job reaches 'completed'
ok 14 - pollJobUntilDone resolves once the job reaches 'completed'
  ---
  duration_ms: 1823.766353
  type: 'test'
  ...
# Subtest: pollJobUntilDone resolves on 'completed_partial' too (items 8/9)
ok 15 - pollJobUntilDone resolves on 'completed_partial' too (items 8/9)
  ---
  duration_ms: 3.856595
  type: 'test'
  ...
# Subtest: pollJobUntilDone throws when the job status is 'failed'
ok 16 - pollJobUntilDone throws when the job status is 'failed'
  ---
  duration_ms: 2.973246
  type: 'test'
  ...
# Subtest: pollJobUntilDone propagates a 404 as an ApiError recognizable via isJobNotFound (item 7)
ok 17 - pollJobUntilDone propagates a 404 as an ApiError recognizable via isJobNotFound (item 7)
  ---
  duration_ms: 2.493958
  type: 'test'
  ...
# Subtest: renderAccessBlocked returns false and does nothing for a non-block status
ok 18 - renderAccessBlocked returns false and does nothing for a non-block status
  ---
  duration_ms: 2.64009
  type: 'test'
  ...
# Subtest: renderAccessBlocked renders the existing '403 blocked' screen and hides progress
ok 19 - renderAccessBlocked renders the existing '403 blocked' screen and hides progress
  ---
  duration_ms: 2.220066
  type: 'test'
  ...
# Subtest: renderJobExpired shows a normal-language message, never the raw API text (item 7)
ok 20 - renderJobExpired shows a normal-language message, never the raw API text (item 7)
  ---
  duration_ms: 2.402736
  type: 'test'
  ...
# Subtest: renderPartialNotice only renders for completed_partial and includes the backend's reason (items 8/9)
ok 21 - renderPartialNotice only renders for completed_partial and includes the backend's reason (items 8/9)
  ---
  duration_ms: 2.251086
  type: 'test'
  ...
# Subtest: app.js mapApiRow: page with no errors/warnings is 'success'
ok 22 - app.js mapApiRow: page with no errors/warnings is 'success'
  ---
  duration_ms: 6.560628
  type: 'test'
  ...
# Subtest: app.js mapApiRow: a check_failed page becomes 'unavailable' with a friendly message
ok 23 - app.js mapApiRow: a check_failed page becomes 'unavailable' with a friendly message
  ---
  duration_ms: 1.253669
  type: 'test'
  ...
# Subtest: meta.js mapApiRow: errors take priority over warnings
ok 24 - meta.js mapApiRow: errors take priority over warnings
  ---
  duration_ms: 2.264284
  type: 'test'
  ...
# Subtest: schema.js mapApiRow: reports JSON-LD counts in details
ok 25 - schema.js mapApiRow: reports JSON-LD counts in details
  ---
  duration_ms: 2.360364
  type: 'test'
  ...
# Subtest: sitemap.js mapApiRow: a redirected URL is a 'warning', not an 'error'
ok 26 - sitemap.js mapApiRow: a redirected URL is a 'warning', not an 'error'
  ---
  duration_ms: 3.71988
  type: 'test'
  ...
# Subtest: sitemap.js mapApiRow: a non-HTML content-type check_failed is a distinct 'error', not 'unavailable' (regression: image-in-sitemap UX)
ok 27 - sitemap.js mapApiRow: a non-HTML content-type check_failed is a distinct 'error', not 'unavailable' (regression: image-in-sitemap UX)
  ---
  duration_ms: 1.442455
  type: 'test'
  ...
# Subtest: sitemap.js mapApiRow: HTTP 4xx on a sitemap URL is an 'error'
ok 28 - sitemap.js mapApiRow: HTTP 4xx on a sitemap URL is an 'error'
  ---
  duration_ms: 7.875499
  type: 'test'
  ...
# Subtest: opening an existing job that no longer exists shows the job-expired screen, not raw API text (item 7)
ok 29 - opening an existing job that no longer exists shows the job-expired screen, not raw API text (item 7)
  ---
  duration_ms: 24.652817
  type: 'test'
  ...
# Subtest: opening an existing completed_partial job renders results plus the partial banner, no exception (items 8/9)
ok 30 - opening an existing completed_partial job renders results plus the partial banner, no exception (items 8/9)
  ---
  duration_ms: 23.547118
  type: 'test'
  ...
# Subtest: opening an existing job whose entry page was access-blocked shows the existing 403 screen unchanged
ok 31 - opening an existing job whose entry page was access-blocked shows the existing 403 screen unchanged
  ---
  duration_ms: 21.185825
  type: 'test'
  ...
# Subtest: home.js loads without throwing alongside common.js
ok 32 - home.js loads without throwing alongside common.js
  ---
  duration_ms: 4.843199
  type: 'test'
  ...
# Subtest: app.js loads without throwing alongside common.js
ok 33 - app.js loads without throwing alongside common.js
  ---
  duration_ms: 1.682487
  type: 'test'
  ...
# Subtest: meta.js loads without throwing alongside common.js
ok 34 - meta.js loads without throwing alongside common.js
  ---
  duration_ms: 1.42871
  type: 'test'
  ...
# Subtest: schema.js loads without throwing alongside common.js
ok 35 - schema.js loads without throwing alongside common.js
  ---
  duration_ms: 1.511001
  type: 'test'
  ...
# Subtest: sitemap.js loads without throwing alongside common.js
ok 36 - sitemap.js loads without throwing alongside common.js
  ---
  duration_ms: 2.942351
  type: 'test'
  ...
1..36
# tests 36
# suites 0
# pass 36
# fail 0
# cancelled 0
# skipped 0
# todo 0
# duration_ms 2270.987086
```
