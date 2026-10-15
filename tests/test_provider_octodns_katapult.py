#
#
#

from json import load
from os.path import dirname, join
from unittest import TestCase
from unittest.mock import Mock, call

from octodns.provider import ProviderException, SupportsException
from octodns.provider.plan import Plan
from octodns.provider.yaml import YamlProvider
from octodns.record import Create, Delete, Record, Update, ValidationError
from octodns.zone import Zone

from octodns_katapult import KatapultProvider, __version__
from octodns_katapult.client import (
    KatapultClientNotFound,
    KatapultClientUnauthorized,
)


def fixture(name):
    with open(join(dirname(__file__), 'fixtures', name)) as fh:
        return load(fh)


def records_fixture():
    return fixture('katapult-records.json')['dns_records']


def zone_fixture(**overrides):
    return {**fixture('katapult-zone.json')['dns_zone'], **overrides}


def katapult_record(record_id, name, _type, attrs, ttl=None, priority=None):
    full_name = f'{name}.unit.tests' if name else 'unit.tests'
    return {
        'id': record_id,
        'name': name or None,
        'full_name': full_name,
        'ttl': ttl,
        'type': _type,
        'priority': priority,
        'content': '',
        'content_attributes': {_type: attrs},
    }


class TestKatapultProvider(TestCase):
    expected = Zone('unit.tests.', [])
    source = YamlProvider(
        'test', join(dirname(__file__), 'config'), escaped_semicolons=False
    )
    source.populate(expected)

    def provider(self, records=None, zone=None, **kwargs):
        provider = KatapultProvider('test', 'token', **kwargs)
        client = Mock()
        client.zone.return_value = zone or zone_fixture()
        client.records.return_value = (
            records_fixture() if records is None else records
        )
        provider._client = client
        return provider

    def populated(self, provider):
        zone = Zone('unit.tests.', [])
        provider.populate(zone, target=True)
        return zone

    def plan(self, provider, existing, desired, changes):
        return Plan(existing, desired, changes, True)

    def test_user_agent_names_versions(self):
        provider = KatapultProvider('test', 'token')

        user_agent = provider._client._sess.headers['User-Agent']

        self.assertIn(f'octodns-katapult/{__version__}', user_agent)
        self.assertTrue(user_agent.startswith('octodns/'))

    def test_populate_matches_expected_zone(self):
        provider = self.provider()

        zone = self.populated(provider)

        self.assertEqual(14, len(zone.records))
        self.assertEqual([], self.expected.changes(zone, provider))

    def test_populate_reads_each_type(self):
        zone = self.populated(self.provider())
        records = {(r.name, r._type): r for r in zone.records}

        self.assertEqual(3600, records[('', 'CAA')].ttl)
        self.assertEqual(300, records[('', 'A')].ttl)
        self.assertEqual(
            ['with semi\\; colons', 'v=spf1 -all'],
            sorted(records[('', 'TXT')].values, reverse=True),
        )
        self.assertEqual(
            ['ns1.example.com.', 'ns2.example.com.'],
            records[('sub', 'NS')].values,
        )
        self.assertEqual('unit.tests.', records[('cname', 'CNAME')].value)

    def test_populate_caches_records(self):
        provider = self.provider()

        self.populated(provider)
        again = self.populated(provider)

        self.assertEqual(14, len(again.records))
        provider._client.records.assert_called_once_with('unit.tests')
        provider._client.zone.assert_called_once_with('unit.tests')

    def test_populate_reports_exists(self):
        provider = self.provider()

        exists = provider.populate(Zone('unit.tests.', []))

        self.assertTrue(exists)

    def test_populate_missing_zone_is_empty(self):
        provider = self.provider()
        provider._client.zone.side_effect = KatapultClientNotFound('missing')
        zone = Zone('unit.tests.', [])

        exists = provider.populate(zone)

        self.assertFalse(exists)
        self.assertEqual(set(), zone.records)
        provider._client.records.assert_not_called()

    def test_populate_raises_client_errors(self):
        provider = self.provider()
        provider._client.zone.side_effect = KatapultClientUnauthorized('no')

        with self.assertRaises(KatapultClientUnauthorized):
            provider.populate(Zone('unit.tests.', []))

    def test_populate_skips_katapult_only_types(self):
        provider = self.provider()

        with self.assertLogs('KatapultProvider[test]', 'DEBUG') as logs:
            self.populated(provider)

        warnings = [
            r.getMessage() for r in logs.records if r.levelname == 'WARNING'
        ]
        self.assertEqual(
            ['populate: skipping unsupported IPS record'], warnings
        )

    def test_populate_reads_alias_at_apex(self):
        records = [
            katapult_record(
                'dnsrec_1', '', 'ALIAS', {'hostname': 'lb.example.com'}
            )
        ]
        zone = self.populated(self.provider(records=records))

        (record,) = zone.records
        self.assertEqual('ALIAS', record._type)
        self.assertEqual('lb.example.com.', record.value)

    def test_list_zones_needs_an_organization(self):
        provider = self.provider()

        with self.assertRaises(ProviderException) as ctx:
            provider.list_zones()

        self.assertIn('organization', str(ctx.exception))

    def test_list_zones_returns_sorted_fqdns(self):
        provider = self.provider(organization='acme')
        provider._client.zones.return_value = (
            fixture('katapult-zones-page-1.json')['dns_zones']
            + fixture('katapult-zones-page-2.json')['dns_zones']
        )

        zones = provider.list_zones()

        self.assertEqual(['alpha.tests.', 'beta.tests.', 'unit.tests.'], zones)
        provider._client.zones.assert_called_once_with('acme')

    def test_apply_creates_missing_zone(self):
        provider = self.provider(records=[], organization='acme')
        provider._client.zone.side_effect = KatapultClientNotFound('missing')
        provider._client.zone_create.return_value = zone_fixture(verified=False)
        existing = Zone('unit.tests.', [])
        provider.populate(existing, target=True)
        changes = existing.changes(self.expected, provider)

        with self.assertLogs('KatapultProvider[test]', 'WARNING') as logs:
            provider.apply(
                self.plan(provider, existing, self.expected, changes)
            )

        provider._client.zone_create.assert_called_once_with(
            'acme', 'unit.tests'
        )
        self.assertIn('not verified', logs.records[0].getMessage())
        self.assertEqual(20, provider._client.record_create.call_count)

    def test_apply_missing_zone_needs_an_organization(self):
        provider = self.provider(records=[])
        provider._client.zone.side_effect = KatapultClientNotFound('missing')
        existing = Zone('unit.tests.', [])
        provider.populate(existing, target=True)
        changes = existing.changes(self.expected, provider)

        with self.assertRaises(ProviderException) as ctx:
            provider.apply(
                self.plan(provider, existing, self.expected, changes)
            )

        self.assertIn('organization', str(ctx.exception))
        provider._client.record_create.assert_not_called()

    def test_apply_create_sends_katapult_properties(self):
        provider = self.provider(records=[])
        existing = Zone('unit.tests.', [])
        provider.populate(existing, target=True)
        changes = existing.changes(self.expected, provider)

        provider.apply(self.plan(provider, existing, self.expected, changes))

        sent = [c.args for c in provider._client.record_create.call_args_list]
        self.assertTrue(all(zone_name == 'unit.tests' for zone_name, _ in sent))
        properties = [p for _, p in sent]
        wanted = [
            {
                'name': '@',
                'type': 'A',
                'ttl': 300,
                'content': {'A': {'ip_address': '1.2.3.4'}},
            },
            {
                'name': '@',
                'type': 'CAA',
                'ttl': 3600,
                'content': {
                    'CAA': {
                        'flag': '0',
                        'tag': 'issue',
                        'value': 'ca.unit.tests',
                    }
                },
            },
            {
                'name': '@',
                'type': 'MX',
                'ttl': 3600,
                'priority': 10,
                'content': {'MX': {'hostname': 'mx1.unit.tests'}},
            },
            {
                'name': '@',
                'type': 'NS',
                'ttl': 3600,
                'content': {'NS': {'hostname': 'ns1.unit.tests'}},
            },
            {
                'name': '@',
                'type': 'SSHFP',
                'ttl': 3600,
                'content': {
                    'SSHFP': {
                        'algorithm': '1',
                        'fingerprint_type': '1',
                        'fingerprint': 'bf6b6825d2977c511a475bbefb88aad54a92ac73',
                    }
                },
            },
            {
                'name': '@',
                'type': 'TXT',
                'ttl': 3600,
                'content': {'TXT': {'content': 'with semi; colons'}},
            },
            {
                'name': '*',
                'type': 'A',
                'ttl': 3600,
                'content': {'A': {'ip_address': '2.2.3.6'}},
            },
            {
                'name': '_srv._tcp',
                'type': 'SRV',
                'ttl': 600,
                'priority': 10,
                'content': {
                    'SRV': {
                        'weight': '20',
                        'port': '30',
                        'target': 'foo-1.unit.tests',
                    }
                },
            },
            {
                'name': 'aaaa',
                'type': 'AAAA',
                'ttl': 600,
                'content': {
                    'AAAA': {
                        'ip_address': '2601:644:500:e210:62f8:1dff:feb8:947a'
                    }
                },
            },
            {
                'name': 'cname',
                'type': 'CNAME',
                'ttl': 300,
                'content': {'CNAME': {'hostname': 'unit.tests'}},
            },
            {
                'name': 'ptr',
                'type': 'PTR',
                'ttl': 3600,
                'content': {'PTR': {'hostname': 'foo.bar.com'}},
            },
            {
                'name': 'sub',
                'type': 'NS',
                'ttl': 3600,
                'content': {'NS': {'hostname': 'ns1.example.com'}},
            },
            {
                'name': 'redirect',
                'type': 'HTTPRedirect',
                'ttl': 3600,
                'content': {
                    'HTTPRedirect': {
                        'url': 'https://example.com/',
                        'http_status': '301',
                    }
                },
            },
            {
                'name': 'vm',
                'type': 'VirtualMachine',
                'ttl': 3600,
                'content': {
                    'VirtualMachine': {'virtual_machine': 'vm_example'}
                },
            },
        ]
        for want in wanted:
            self.assertIn(want, properties)
        second_values = {
            ('@', 'A'): {'A': {'ip_address': '1.2.3.5'}},
            ('@', 'MX'): {'MX': {'hostname': 'mx2.unit.tests'}},
            ('@', 'NS'): {'NS': {'hostname': 'ns2.unit.tests'}},
            ('@', 'TXT'): {'TXT': {'content': 'v=spf1 -all'}},
            ('_srv._tcp', 'SRV'): {
                'SRV': {
                    'weight': '20',
                    'port': '30',
                    'target': 'foo-2.unit.tests',
                }
            },
            ('sub', 'NS'): {'NS': {'hostname': 'ns2.example.com'}},
        }
        for (name, _type), content in second_values.items():
            self.assertIn(
                content,
                [
                    p['content']
                    for p in properties
                    if p['name'] == name and p['type'] == _type
                ],
            )
        self.assertEqual(20, len(properties))

    def test_apply_create_alias(self):
        provider = self.provider(records=[])
        desired = Zone('unit.tests.', [])
        record = Record.new(
            desired,
            '',
            {'type': 'ALIAS', 'ttl': 60, 'value': 'lb.example.com.'},
        )
        desired.add_record(record)

        provider.apply(self.plan(provider, desired, desired, [Create(record)]))

        provider._client.record_create.assert_called_once_with(
            'unit.tests',
            {
                'name': '@',
                'type': 'ALIAS',
                'ttl': 60,
                'content': {'ALIAS': {'hostname': 'lb.example.com'}},
            },
        )

    def test_apply_delete_removes_every_value(self):
        provider = self.provider()
        existing = self.populated(provider)
        record = next(
            r for r in existing.records if r.name == '' and r._type == 'A'
        )

        provider.apply(
            self.plan(provider, existing, existing, [Delete(record)])
        )

        self.assertEqual(
            [call('dnsrec_0001'), call('dnsrec_0002')],
            provider._client.record_delete.call_args_list,
        )

    def update(self, provider, name, _type, data):
        existing = self.populated(provider)
        current = next(
            r for r in existing.records if r.name == name and r._type == _type
        )
        desired = Record.new(existing, name, data)
        change = Update(current, desired)
        provider.apply(self.plan(provider, existing, existing, [change]))
        return provider._client

    def test_update_ttl_only_patches_in_place(self):
        client = self.update(
            self.provider(),
            '',
            'A',
            {'type': 'A', 'ttl': 120, 'values': ['1.2.3.4', '1.2.3.5']},
        )

        self.assertEqual(
            [
                call(
                    'dnsrec_0001',
                    {
                        'name': '@',
                        'type': 'A',
                        'ttl': 120,
                        'content': {'A': {'ip_address': '1.2.3.4'}},
                    },
                ),
                call(
                    'dnsrec_0002',
                    {
                        'name': '@',
                        'type': 'A',
                        'ttl': 120,
                        'content': {'A': {'ip_address': '1.2.3.5'}},
                    },
                ),
            ],
            client.record_update.call_args_list,
        )
        client.record_create.assert_not_called()
        client.record_delete.assert_not_called()

    def test_update_reuses_records_for_changed_values(self):
        client = self.update(
            self.provider(),
            '',
            'A',
            {'type': 'A', 'ttl': 300, 'values': ['1.2.3.5', '1.2.3.6']},
        )

        client.record_update.assert_called_once_with(
            'dnsrec_0001',
            {
                'name': '@',
                'type': 'A',
                'ttl': 300,
                'content': {'A': {'ip_address': '1.2.3.6'}},
            },
        )
        client.record_create.assert_not_called()
        client.record_delete.assert_not_called()

    def test_update_single_value_patches_one_record(self):
        client = self.update(
            self.provider(),
            'cname',
            'CNAME',
            {'type': 'CNAME', 'ttl': 300, 'value': 'www.example.com.'},
        )

        client.record_update.assert_called_once_with(
            'dnsrec_0017',
            {
                'name': 'cname',
                'type': 'CNAME',
                'ttl': 300,
                'content': {'CNAME': {'hostname': 'www.example.com'}},
            },
        )

    def test_update_deletes_removed_values(self):
        client = self.update(
            self.provider(),
            '',
            'MX',
            {
                'type': 'MX',
                'ttl': 3600,
                'value': {'preference': 20, 'exchange': 'mx2.unit.tests.'},
            },
        )

        client.record_delete.assert_called_once_with('dnsrec_0004')
        client.record_update.assert_not_called()
        client.record_create.assert_not_called()

    def test_update_creates_added_values(self):
        client = self.update(
            self.provider(),
            '_srv._tcp',
            'SRV',
            {
                'type': 'SRV',
                'ttl': 600,
                'values': [
                    {
                        'priority': 10,
                        'weight': 20,
                        'port': 30,
                        'target': 'foo-1.unit.tests.',
                    },
                    {
                        'priority': 12,
                        'weight': 20,
                        'port': 30,
                        'target': 'foo-2.unit.tests.',
                    },
                    {
                        'priority': 14,
                        'weight': 20,
                        'port': 30,
                        'target': 'foo-3.unit.tests.',
                    },
                ],
            },
        )

        client.record_create.assert_called_once_with(
            'unit.tests',
            {
                'name': '_srv._tcp',
                'type': 'SRV',
                'ttl': 600,
                'priority': 14,
                'content': {
                    'SRV': {
                        'weight': '20',
                        'port': '30',
                        'target': 'foo-3.unit.tests',
                    }
                },
            },
        )
        client.record_update.assert_not_called()
        client.record_delete.assert_not_called()

    def test_update_keeps_reuses_and_deletes_in_one_change(self):
        records = [
            katapult_record(f'dnsrec_{i}', 'www', 'A', {'ip_address': ip}, 300)
            for i, ip in enumerate(('1.1.1.1', '2.2.2.2', '3.3.3.3', '4.4.4.4'))
        ]
        client = self.update(
            self.provider(records=records),
            'www',
            'A',
            {'type': 'A', 'ttl': 120, 'values': ['1.1.1.1', '5.5.5.5']},
        )

        self.assertEqual(
            [
                call(
                    'dnsrec_0',
                    {
                        'name': 'www',
                        'type': 'A',
                        'ttl': 120,
                        'content': {'A': {'ip_address': '1.1.1.1'}},
                    },
                ),
                call(
                    'dnsrec_1',
                    {
                        'name': 'www',
                        'type': 'A',
                        'ttl': 120,
                        'content': {'A': {'ip_address': '5.5.5.5'}},
                    },
                ),
            ],
            client.record_update.call_args_list,
        )
        self.assertEqual(
            [call('dnsrec_2'), call('dnsrec_3')],
            client.record_delete.call_args_list,
        )
        client.record_create.assert_not_called()

    def test_populate_rejects_records_outside_the_zone(self):
        records = [
            katapult_record('dnsrec_1', 'www', 'A', {'ip_address': '1.1.1.1'})
        ]
        records[0]['full_name'] = 'www.xunit.tests'

        with self.assertRaises(ProviderException) as ctx:
            self.populated(self.provider(records=records))

        self.assertIn('www.xunit.tests', str(ctx.exception))

    def test_populate_encodes_unicode_full_names(self):
        provider = self.provider(
            records=[
                {
                    **katapult_record(
                        'dnsrec_1', 'www', 'A', {'ip_address': '1.1.1.1'}
                    ),
                    'full_name': 'www.bücher.example',
                }
            ],
            zone=zone_fixture(name='xn--bcher-kva.example'),
        )
        zone = Zone('xn--bcher-kva.example.', [])

        provider.populate(zone)

        self.assertEqual(['www'], [r.name for r in zone.records])

    def test_update_redirect_patches_in_place(self):
        client = self.update(
            self.provider(),
            'redirect',
            'KatapultProvider/HTTPRedirect',
            {
                'type': 'KatapultProvider/HTTPRedirect',
                'ttl': 3600,
                'value': {'url': 'https://example.com/', 'http_status': 302},
            },
        )

        client.record_update.assert_called_once_with(
            'dnsrec_0021',
            {
                'name': 'redirect',
                'type': 'HTTPRedirect',
                'ttl': 3600,
                'content': {
                    'HTTPRedirect': {
                        'url': 'https://example.com/',
                        'http_status': '302',
                    }
                },
            },
        )
        client.record_create.assert_not_called()
        client.record_delete.assert_not_called()

    def test_delete_virtual_machine_removes_its_record(self):
        provider = self.provider()
        existing = self.populated(provider)
        record = next(
            r for r in existing.records if r._type.endswith('VirtualMachine')
        )

        provider.apply(
            self.plan(provider, existing, existing, [Delete(record)])
        )

        provider._client.record_delete.assert_called_once_with('dnsrec_0022')

    def test_update_virtual_machines_adds_and_removes_values(self):
        records = [
            katapult_record(
                'dnsrec_1', 'app', 'VirtualMachine', {'virtual_machine': 'vm_a'}
            ),
            katapult_record(
                'dnsrec_2', 'app', 'VirtualMachine', {'virtual_machine': 'vm_b'}
            ),
            katapult_record(
                'dnsrec_3', 'app', 'VirtualMachine', {'virtual_machine': 'vm_c'}
            ),
        ]
        client = self.update(
            self.provider(records=records),
            'app',
            'KatapultProvider/VirtualMachine',
            {
                'type': 'KatapultProvider/VirtualMachine',
                'ttl': 3600,
                'values': ['vm_a', 'vm_d'],
            },
        )

        client.record_update.assert_called_once_with(
            'dnsrec_2',
            {
                'name': 'app',
                'type': 'VirtualMachine',
                'ttl': 3600,
                'content': {'VirtualMachine': {'virtual_machine': 'vm_d'}},
            },
        )
        client.record_delete.assert_called_once_with('dnsrec_3')
        client.record_create.assert_not_called()

    def test_populate_rejects_a_non_numeric_redirect_status(self):
        records = [
            katapult_record(
                'dnsrec_1',
                'go',
                'HTTPRedirect',
                {'url': 'https://example.com/', 'http_status': 'permanent'},
            )
        ]

        with self.assertRaises(ValidationError) as ctx:
            self.populated(self.provider(records=records))

        self.assertEqual(
            ['invalid http_status "permanent"'], ctx.exception.reasons
        )

    def test_populate_warns_about_extra_single_value_records(self):
        records = [
            katapult_record(
                f'dnsrec_{i}',
                'go',
                'HTTPRedirect',
                {'url': f'https://example.com/{i}', 'http_status': '301'},
            )
            for i in (1, 2)
        ]

        with self.assertLogs('KatapultProvider[test]', 'WARNING') as logs:
            zone = self.populated(self.provider(records=records))

        (record,) = zone.records
        self.assertEqual('https://example.com/1', record.value.url)
        self.assertEqual(
            [
                'populate: go.unit.tests. has 2 KatapultProvider/HTTPRedirect '
                'records but takes one value; using the first'
            ],
            [r.getMessage() for r in logs.records],
        )

    def test_update_matches_mixed_case_and_dotted_hostnames(self):
        client = self.update(
            self.provider(),
            'sub',
            'NS',
            {'type': 'NS', 'ttl': 3600, 'values': ['ns2.example.com.']},
        )

        client.record_delete.assert_called_once_with('dnsrec_0019')
        client.record_update.assert_not_called()

    def low_ttl_zone(self):
        desired = Zone('unit.tests.', [])
        desired.add_record(
            Record.new(
                desired, 'low', {'type': 'A', 'ttl': 30, 'value': '1.2.3.4'}
            )
        )
        desired.add_record(
            Record.new(
                desired, 'ok', {'type': 'A', 'ttl': 60, 'value': '1.2.3.5'}
            )
        )
        return desired

    def test_plan_rejects_ttl_below_minimum_when_strict(self):
        provider = self.provider(records=[])

        with self.assertRaises(SupportsException) as ctx:
            provider.plan(self.low_ttl_zone())

        self.assertEqual(
            'test: TTL 30 for low.unit.tests. is below the Katapult minimum '
            'of 60',
            str(ctx.exception),
        )

    def test_plan_raises_ttl_to_minimum_when_lenient(self):
        provider = self.provider(records=[], strict_supports=False)

        with self.assertLogs('KatapultProvider[test]', 'WARNING') as logs:
            plan = provider.plan(self.low_ttl_zone())

        ttls = {c.new.name: c.new.ttl for c in plan.changes}
        self.assertEqual({'low': 60, 'ok': 60}, ttls)
        self.assertTrue(any('using 60' in m for m in logs.output))

    def srv_zone(self, *values):
        desired = Zone('unit.tests.', [])
        desired.add_record(
            Record.new(
                desired,
                '_sip._tcp',
                {'type': 'SRV', 'ttl': 3600, 'values': list(values)},
            )
        )
        return desired

    null_srv = {'priority': 0, 'weight': 0, 'port': 0, 'target': '.'}
    port_zero_srv = {
        'priority': 5,
        'weight': 0,
        'port': 0,
        'target': 'sip0.unit.tests.',
    }
    sip_srv = {
        'priority': 10,
        'weight': 20,
        'port': 5060,
        'target': 'sip1.unit.tests.',
    }

    def test_plan_keeps_supported_srv_values(self):
        provider = self.provider(records=[])

        plan = provider.plan(self.srv_zone(self.sip_srv))

        (change,) = plan.changes
        self.assertEqual(
            ['sip1.unit.tests.'], [v.target for v in change.new.values]
        )

    def test_plan_rejects_null_srv_when_strict(self):
        provider = self.provider(records=[])

        with self.assertRaises(SupportsException) as ctx:
            provider.plan(self.srv_zone(self.null_srv))

        self.assertEqual(
            'test: SRV values with port 0 or the null target "." are not '
            'supported for _sip._tcp.unit.tests.',
            str(ctx.exception),
        )

    def test_plan_omits_unsupported_srv_values_when_lenient(self):
        provider = self.provider(records=[], strict_supports=False)

        with self.assertLogs('KatapultProvider[test]', 'WARNING') as logs:
            plan = provider.plan(
                self.srv_zone(self.null_srv, self.port_zero_srv, self.sip_srv)
            )

        (change,) = plan.changes
        self.assertEqual(
            ['sip1.unit.tests.'], [v.target for v in change.new.values]
        )
        self.assertTrue(any('omitting those values' in m for m in logs.output))

    def test_plan_omits_srv_with_only_unsupported_values_when_lenient(self):
        provider = self.provider(records=[], strict_supports=False)

        with self.assertLogs('KatapultProvider[test]', 'WARNING') as logs:
            plan = provider.plan(self.srv_zone(self.null_srv))

        self.assertIsNone(plan)
        self.assertTrue(any('omitting record' in m for m in logs.output))

    def mx_zone(self, *values):
        desired = Zone('unit.tests.', [])
        desired.add_record(
            Record.new(
                desired,
                'nomail',
                {'type': 'MX', 'ttl': 3600, 'values': list(values)},
            )
        )
        return desired

    def test_plan_rejects_null_mx_when_strict(self):
        provider = self.provider(records=[])

        with self.assertRaises(SupportsException) as ctx:
            provider.plan(self.mx_zone({'preference': 0, 'exchange': '.'}))

        self.assertEqual(
            'test: MX values with the null exchange "." are not supported '
            'for nomail.unit.tests.',
            str(ctx.exception),
        )

    def test_plan_omits_null_mx_values_when_lenient(self):
        provider = self.provider(records=[], strict_supports=False)

        with self.assertLogs('KatapultProvider[test]', 'WARNING') as logs:
            plan = provider.plan(
                self.mx_zone(
                    {'preference': 0, 'exchange': '.'},
                    {'preference': 10, 'exchange': 'mx1.unit.tests.'},
                )
            )

        (change,) = plan.changes
        self.assertEqual(
            ['mx1.unit.tests.'], [v.exchange for v in change.new.values]
        )
        self.assertTrue(any('omitting those values' in m for m in logs.output))

    def test_apply_clears_record_cache(self):
        provider = self.provider()
        existing = self.populated(provider)
        record = next(iter(existing.records))

        provider.apply(
            self.plan(provider, existing, existing, [Delete(record)])
        )
        self.populated(provider)

        self.assertEqual(2, provider._client.records.call_count)
        self.assertEqual(2, provider._client.zone.call_count)
