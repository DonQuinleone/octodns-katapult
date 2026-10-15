#
#
#

from unittest import TestCase

from octodns.record import Record, ValidationError
from octodns.zone import Zone

from octodns_katapult.record import (
    KatapultHttpRedirectRecord,
    KatapultVirtualMachineRecord,
)

ZONE = Zone('unit.tests.', [])


def redirect(value):
    return Record.new(
        ZONE,
        'go',
        {'type': 'KatapultProvider/HTTPRedirect', 'ttl': 300, 'value': value},
    )


def virtual_machines(values):
    return Record.new(
        ZONE,
        'app',
        {
            'type': 'KatapultProvider/VirtualMachine',
            'ttl': 300,
            'values': values,
        },
    )


class TestKatapultHttpRedirectRecord(TestCase):
    def test_registered_under_provider_namespace(self):
        self.assertIs(
            KatapultHttpRedirectRecord,
            Record.registered_types()['KatapultProvider/HTTPRedirect'],
        )

    def test_value_exposes_url_and_status(self):
        record = redirect({'url': 'https://example.com/', 'http_status': 301})

        self.assertEqual('https://example.com/', record.value.url)
        self.assertEqual(301, record.value.http_status)
        self.assertEqual('301 https://example.com/', repr(record.value))

    def test_scheme_is_case_insensitive(self):
        record = redirect({'url': 'HTTPS://example.com/', 'http_status': 301})

        self.assertEqual('HTTPS://example.com/', record.value.url)

    def test_status_given_as_text_is_an_integer(self):
        record = redirect({'url': 'https://example.com/', 'http_status': '302'})

        self.assertEqual(302, record.value.http_status)

    def test_accepts_each_redirect_status(self):
        for status in (301, 302, 307, 308):
            with self.subTest(status=status):
                record = redirect(
                    {'url': 'https://example.com/', 'http_status': status}
                )

                self.assertEqual(status, record.value.http_status)

    def test_round_trips_through_data(self):
        record = redirect({'url': 'https://example.com/', 'http_status': 301})

        data = record.data

        self.assertEqual(300, data['ttl'])
        self.assertEqual(
            {'url': 'https://example.com/', 'http_status': 301},
            dict(data['value']),
        )

    def test_status_change_is_an_update(self):
        current = redirect({'url': 'https://example.com/', 'http_status': 301})
        desired = redirect({'url': 'https://example.com/', 'http_status': 302})

        self.assertIsNotNone(current.changes(desired, None))
        self.assertIsNone(current.changes(current, None))

    def test_rejects_invalid_values(self):
        cases = (
            (None, 'missing value'),
            ({'http_status': 301}, 'missing url'),
            (
                {'url': 'ftp://example.com/', 'http_status': 301},
                'invalid url "ftp://example.com/"; must start with http:// '
                'or https://',
            ),
            (
                {'url': 123, 'http_status': 301},
                'invalid url "123"; must start with http:// or https://',
            ),
            (
                {'url': 'https://', 'http_status': 301},
                'invalid url "https://"; must start with http:// or https://',
            ),
            (
                {'url': ' https://example.com/', 'http_status': 301},
                'invalid url " https://example.com/"; remove the surrounding '
                'whitespace',
            ),
            ({'url': 'https://example.com/'}, 'missing http_status'),
            (
                {'url': 'https://example.com/', 'http_status': 'moved'},
                'invalid http_status "moved"',
            ),
            (
                {'url': 'https://example.com/', 'http_status': 200},
                'invalid http_status "200"; must be 301, 302, 307 or 308',
            ),
            (
                {'url': 'https://example.com/', 'http_status': 303},
                'invalid http_status "303"; must be 301, 302, 307 or 308',
            ),
        )
        for value, reason in cases:
            with self.subTest(value=value):
                with self.assertRaises(ValidationError) as ctx:
                    redirect(value)

                self.assertEqual([reason], ctx.exception.reasons)


class TestKatapultVirtualMachineRecord(TestCase):
    def test_registered_under_provider_namespace(self):
        self.assertIs(
            KatapultVirtualMachineRecord,
            Record.registered_types()['KatapultProvider/VirtualMachine'],
        )

    def test_values_are_sorted(self):
        record = virtual_machines(['vm_beta', 'vm_alpha'])

        self.assertEqual(['vm_alpha', 'vm_beta'], record.values)

    def test_change_in_machines_is_an_update(self):
        current = virtual_machines(['vm_alpha'])
        desired = virtual_machines(['vm_alpha', 'vm_beta'])

        self.assertIsNotNone(current.changes(desired, None))
        self.assertIsNone(current.changes(current, None))

    def test_round_trips_through_data(self):
        record = virtual_machines(['vm_beta', 'vm_alpha'])

        self.assertEqual(
            {'ttl': 300, 'values': ['vm_alpha', 'vm_beta']}, record.data
        )

    def test_rejects_an_empty_list(self):
        with self.assertRaises(ValidationError) as ctx:
            virtual_machines([])

        self.assertEqual(['missing value(s)'], ctx.exception.reasons)

    def test_rejects_blank_or_non_text_values(self):
        for value in ('', '  ', 42, ' vm_alpha'):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError) as ctx:
                    virtual_machines([value])

                self.assertEqual(
                    [f'invalid virtual machine "{value}"'],
                    ctx.exception.reasons,
                )
