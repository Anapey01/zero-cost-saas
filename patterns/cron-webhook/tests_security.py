"""
Security tests for the cron webhook endpoint.

Verifies:
  - Valid Bearer token is accepted
  - Missing token returns 401
  - Wrong token returns 401
  - Query-string token is rejected (even if correct value)
  - Missing CRON_SECRET config returns 401, not 500
"""
from django.test import TestCase, override_settings


@override_settings(CRON_SECRET='test-secret-value-for-tests')
class CronEndpointSecurityTest(TestCase):
    url = '/api/v1/orders/cron/daily/'

    def test_valid_bearer_is_accepted(self):
        response = self.client.get(
            self.url,
            HTTP_AUTHORIZATION='Bearer test-secret-value-for-tests',
        )
        # 200 means auth passed (tasks may fail, that's a separate concern)
        self.assertNotEqual(response.status_code, 401)
        self.assertNotEqual(response.status_code, 403)

    def test_missing_auth_header_returns_401(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 401)

    def test_wrong_secret_returns_401(self):
        response = self.client.get(
            self.url,
            HTTP_AUTHORIZATION='Bearer wrong-secret',
        )
        self.assertEqual(response.status_code, 401)

    def test_query_string_secret_is_rejected(self):
        """Even a correct secret in the query string must be rejected."""
        response = self.client.get(
            f'{self.url}?secret=test-secret-value-for-tests',
            HTTP_AUTHORIZATION='Bearer test-secret-value-for-tests',
        )
        self.assertEqual(response.status_code, 401)

    def test_non_bearer_scheme_rejected(self):
        response = self.client.get(
            self.url,
            HTTP_AUTHORIZATION='Token test-secret-value-for-tests',
        )
        self.assertEqual(response.status_code, 401)

    @override_settings(CRON_SECRET='')
    def test_unconfigured_secret_returns_401_not_500(self):
        """A misconfigured server must fail closed, not with a traceback."""
        response = self.client.get(
            self.url,
            HTTP_AUTHORIZATION='Bearer test-secret-value-for-tests',
        )
        self.assertEqual(response.status_code, 401)
        # Ensure no Django debug traceback leaks
        self.assertNotIn(b'Traceback', response.content)
        self.assertNotIn(b'Exception', response.content)
