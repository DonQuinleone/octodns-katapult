#
#
#

from json import load
from os.path import dirname, join
from unittest import TestCase

from requests_mock import mock as requests_mock

from octodns_katapult.client import (
    KatapultClient,
    KatapultClientException,
    KatapultClientNotFound,
    KatapultClientUnauthorized,
)

BASE = 'https://api.katapult.io/core/v1'


def fixture(name):
    with open(join(dirname(__file__), 'fixtures', name)) as fh:
        return load(fh)


def error(code, description, detail=None):
    return {
        'error': {
            'code': code,
            'description': description,
            'detail': detail or {},
        }
    }


class TestKatapultClient(TestCase):
    def setUp(self):
        self.client = KatapultClient('secret', 'octodns-test/1')

    def test_sends_bearer_token_and_user_agent(self):
        with requests_mock() as mock:
            mock.get(f'{BASE}/dns_zones/_', json=fixture('katapult-zone.json'))

            self.client.zone('unit.tests')

            headers = mock.last_request.headers
            self.assertEqual('Bearer secret', headers['Authorization'])
            self.assertEqual('octodns-test/1', headers['User-Agent'])

    def test_zone_looks_up_by_name(self):
        with requests_mock() as mock:
            mock.get(f'{BASE}/dns_zones/_', json=fixture('katapult-zone.json'))

            zone = self.client.zone('unit.tests')

            self.assertEqual('dnszone_k75eFc4UBOgeE5Zy', zone['id'])
            self.assertEqual(
                {'dns_zone[name]': ['unit.tests']}, mock.last_request.qs
            )

    def test_not_found_raises_not_found(self):
        with requests_mock() as mock:
            mock.get(
                f'{BASE}/dns_zones/_',
                status_code=404,
                json=error(
                    'dns_zone_not_found',
                    'No DNS zone was found matching any of the criteria '
                    'provided in the arguments',
                ),
            )

            with self.assertRaises(KatapultClientNotFound) as ctx:
                self.client.zone('unit.tests')

            self.assertEqual(
                '404 dns_zone_not_found: No DNS zone was found matching any '
                'of the criteria provided in the arguments',
                str(ctx.exception),
            )

    def test_forbidden_raises_unauthorized(self):
        with requests_mock() as mock:
            mock.get(
                f'{BASE}/dns_zones/_',
                status_code=403,
                json=error(
                    'invalid_api_token', 'The API token provided was not valid'
                ),
            )

            with self.assertRaises(KatapultClientUnauthorized) as ctx:
                self.client.zone('unit.tests')

            self.assertEqual(
                '403 invalid_api_token: The API token provided was not valid',
                str(ctx.exception),
            )

    def test_validation_error_lists_each_error(self):
        with requests_mock() as mock:
            mock.post(
                f'{BASE}/dns_zones/_/records',
                status_code=422,
                json=error(
                    'validation_error',
                    'A validation error occurred with the object',
                    {'errors': ['TTL is too low', 'Name is invalid']},
                ),
            )

            with self.assertRaises(KatapultClientException) as ctx:
                self.client.record_create('unit.tests', {'type': 'A'})

            self.assertEqual(
                '422 validation_error: A validation error occurred with the '
                'object (TTL is too low; Name is invalid)',
                str(ctx.exception),
            )

    def test_error_without_json_body_reports_status(self):
        with requests_mock() as mock:
            mock.get(
                f'{BASE}/dns_zones/_',
                status_code=502,
                reason='Bad Gateway',
                text='<html>upstream down</html>',
            )

            with self.assertRaises(KatapultClientException) as ctx:
                self.client.zone('unit.tests')

            self.assertEqual('502 Bad Gateway', str(ctx.exception))

    def test_retry_configuration_only_retries_rate_limits(self):
        adapter = self.client._sess.get_adapter(BASE)
        retry = adapter.max_retries

        self.assertEqual(frozenset((429,)), frozenset(retry.status_forcelist))
        self.assertIsNone(retry.allowed_methods)
        self.assertTrue(retry.respect_retry_after_header)
        self.assertFalse(retry.raise_on_status)
        self.assertGreater(retry.status, 0)
        self.assertEqual(0, retry.connect)
        self.assertEqual(0, retry.read)
        self.assertEqual(0, retry.other)

    def test_malformed_error_body_reports_status(self):
        for body in ({'error': 'Internal error'}, {'error': {'code': 'x'}}):
            with requests_mock() as mock:
                mock.get(
                    f'{BASE}/dns_zones/_',
                    status_code=500,
                    reason='Internal Server Error',
                    json=body,
                )

                with self.assertRaises(KatapultClientException) as ctx:
                    self.client.zone('unit.tests')

                self.assertEqual(
                    '500 Internal Server Error', str(ctx.exception)
                )

    def test_zones_follows_total_pages(self):
        with requests_mock() as mock:
            url = f'{BASE}/organizations/_/dns_zones'
            mock.get(
                f'{url}?page=1', json=fixture('katapult-zones-page-1.json')
            )
            mock.get(
                f'{url}?page=2', json=fixture('katapult-zones-page-2.json')
            )

            zones = self.client.zones('acme')

            self.assertEqual(
                ['alpha.tests', 'unit.tests', 'beta.tests'],
                [zone['name'] for zone in zones],
            )
            self.assertEqual(
                {'organization[sub_domain]': ['acme'], 'page': ['2']},
                mock.last_request.qs,
            )

    def test_zones_without_page_count_stops_on_short_page(self):
        first = fixture('katapult-zones-page-1.json')
        first['pagination']['total_pages'] = None
        second = fixture('katapult-zones-page-2.json')
        second['pagination']['total_pages'] = None
        with requests_mock() as mock:
            url = f'{BASE}/organizations/_/dns_zones'
            mock.get(f'{url}?page=1', json=first)
            mock.get(f'{url}?page=2', json=second)

            zones = self.client.zones('acme')

            self.assertEqual(3, len(zones))
            self.assertEqual(2, mock.call_count)

    def test_zone_create_names_the_organization(self):
        with requests_mock() as mock:
            mock.post(
                f'{BASE}/organizations/_/dns_zones',
                status_code=201,
                json=fixture('katapult-zone.json'),
            )

            zone = self.client.zone_create('acme', 'unit.tests')

            self.assertEqual('unit.tests', zone['name'])
            self.assertEqual(
                {
                    'organization': {'sub_domain': 'acme'},
                    'properties': {'name': 'unit.tests'},
                },
                mock.last_request.json(),
            )

    def test_records_lists_the_zone(self):
        with requests_mock() as mock:
            mock.get(
                f'{BASE}/dns_zones/_/records',
                json=fixture('katapult-records.json'),
            )

            records = self.client.records('unit.tests')

            self.assertEqual(22, len(records))
            self.assertEqual(
                {'dns_zone[name]': ['unit.tests']}, mock.last_request.qs
            )

    def test_record_create_posts_properties(self):
        properties = {'name': 'www', 'type': 'A', 'ttl': 60}
        with requests_mock() as mock:
            mock.post(
                f'{BASE}/dns_zones/_/records',
                json={'dns_record': {'id': 'dnsrec_new'}},
            )

            record = self.client.record_create('unit.tests', properties)

            self.assertEqual('dnsrec_new', record['id'])
            self.assertEqual(
                {'dns_zone': {'name': 'unit.tests'}, 'properties': properties},
                mock.last_request.json(),
            )

    def test_record_update_patches_by_id(self):
        properties = {'ttl': 120}
        with requests_mock() as mock:
            mock.patch(
                f'{BASE}/dns_records/_',
                json={'dns_record': {'id': 'dnsrec_0001'}},
            )

            record = self.client.record_update('dnsrec_0001', properties)

            self.assertEqual('dnsrec_0001', record['id'])
            self.assertEqual(
                {'dns_record': {'id': 'dnsrec_0001'}, 'properties': properties},
                mock.last_request.json(),
            )

    def test_record_delete_deletes_by_id(self):
        with requests_mock() as mock:
            mock.delete(f'{BASE}/dns_records/_', json={'deleted': True})

            self.client.record_delete('dnsrec_0001')

            self.assertEqual(
                {'dns_record': {'id': 'dnsrec_0001'}}, mock.last_request.json()
            )
