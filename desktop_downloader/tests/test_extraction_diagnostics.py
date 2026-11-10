from __future__ import annotations

import unittest

from media_core.engine import extraction_failure_hint


class ExtractionDiagnosticsTests(unittest.TestCase):
    def test_rate_limit_recommends_lower_concurrency(self):
        message = extraction_failure_hint(["Attempt 1: HTTP Error 429: Too Many Requests"])
        self.assertIn("Reduce simultaneous downloads", message)

    def test_private_link_explains_access_failure(self):
        message = extraction_failure_hint(["Attempt 2: HTTP Error 403: Forbidden"])
        self.assertIn("authorized browser session", message)

    def test_expired_url_is_classified(self):
        message = extraction_failure_hint(["Attempt 1: HTTP Error 404: Not Found"])
        self.assertIn("expired", message)

    def test_network_tls_error_is_classified(self):
        message = extraction_failure_hint(["Attempt 1: SSL connection reset"])
        self.assertIn("connection or TLS", message)

    def test_drm_is_not_treated_as_downloadable(self):
        message = extraction_failure_hint(["Attempt 1: DRM protected content"])
        self.assertIn("cannot be downloaded", message)

    def test_missing_stream_has_actionable_hint(self):
        message = extraction_failure_hint([])
        self.assertIn("interactive player", message)


if __name__ == "__main__":
    unittest.main()
