#
#
#

import logging
from collections import defaultdict
from json import dumps

from octodns import __version__ as octodns_version
from octodns.idna import idna_encode
from octodns.provider import ProviderException
from octodns.provider.base import BaseProvider
from octodns.record import Record, ValueMixin

from .client import (
    KatapultClient,
    KatapultClientException,
    KatapultClientNotFound,
    KatapultClientUnauthorized,
)
from .record import KatapultHttpRedirectRecord, KatapultVirtualMachineRecord

__version__ = '0.0.1'

__all__ = [
    'KatapultClientException',
    'KatapultClientNotFound',
    'KatapultClientUnauthorized',
    'KatapultHttpRedirectRecord',
    'KatapultProvider',
    'KatapultVirtualMachineRecord',
]

# Katapult takes `@` for the apex on write and returns the name as null
APEX_NAME = '@'

MIN_TTL = 60

# Katapult manages the SOA itself; it isn't worth a warning on every run
SILENTLY_SKIPPED_TYPES = frozenset(('SOA',))

# Katapult requires a real hostname and a non-zero port, so the RFC 7505 null
# MX and the RFC 2782 "service not available" SRV can't be expressed
UNSUPPORTED_VALUES = {
    'MX': (lambda v: v.exchange == '.', 'MX values with the null exchange "."'),
    'SRV': (
        lambda v: v.port == 0 or v.target == '.',
        'SRV values with port 0 or the null target "."',
    ),
}


OCTODNS_TYPES = {
    'HTTPRedirect': KatapultHttpRedirectRecord._type,
    'VirtualMachine': KatapultVirtualMachineRecord._type,
}
KATAPULT_TYPES = {v: k for k, v in OCTODNS_TYPES.items()}


def _octodns_type(katapult_type):
    return OCTODNS_TYPES.get(katapult_type, katapult_type)


def _katapult_type(octodns_type):
    return KATAPULT_TYPES.get(octodns_type, octodns_type)


def _fqdn(hostname):
    if hostname.endswith('.'):
        return hostname
    return f'{hostname}.'


def _hostname(fqdn):
    return fqdn.rstrip('.')


class KatapultProvider(BaseProvider):
    '''
    Katapult DNS provider

    katapult:
        class: octodns_katapult.KatapultProvider
        # API token with the `dns` scope (required)
        token: env/KATAPULT_API_TOKEN
        # Organization sub-domain, needed only to list zones and to create
        # zones that don't exist yet (optional)
        organization: acme
    '''

    SUPPORTS_GEO = False
    SUPPORTS_DYNAMIC = False
    SUPPORTS_ROOT_NS = True
    SUPPORTS = set(
        (
            'A',
            'AAAA',
            'ALIAS',
            'CAA',
            'CNAME',
            'MX',
            'NS',
            'PTR',
            'SRV',
            'SSHFP',
            'TXT',
            KatapultHttpRedirectRecord._type,
            KatapultVirtualMachineRecord._type,
        )
    )

    def __init__(self, id, token, organization=None, *args, **kwargs):
        self.log = logging.getLogger(f'KatapultProvider[{id}]')
        self.log.debug(
            '__init__: id=%s, token=***, organization=%s', id, organization
        )
        super().__init__(id, *args, **kwargs)
        self.organization = organization
        user_agent = f'octodns/{octodns_version} octodns-katapult/{__version__}'
        self._client = KatapultClient(token, user_agent)
        self._zones = {}
        self._zone_records = {}

    def _require_organization(self, action):
        if self.organization is None:
            raise ProviderException(
                f'{self.id}: organization is required to {action}; set it '
                'to the organization sub-domain in the provider config'
            )
        return self.organization

    def _process_desired_zone(self, desired):
        for record in desired.records:
            record = self._enforce_min_ttl(desired, record)
            if record._type in UNSUPPORTED_VALUES:
                self._omit_unsupported_values(desired, record)
        return super()._process_desired_zone(desired)

    def _enforce_min_ttl(self, desired, record):
        if record.ttl >= MIN_TTL:
            return record
        msg = (
            f'TTL {record.ttl} for {record.fqdn} is below the Katapult '
            f'minimum of {MIN_TTL}'
        )
        self.supports_warn_or_except(msg, f'using {MIN_TTL}')
        record = record.copy()
        record.ttl = MIN_TTL
        desired.add_record(record, replace=True)
        return record

    def _omit_unsupported_values(self, desired, record):
        is_unsupported, description = UNSUPPORTED_VALUES[record._type]
        supported = [v for v in record.values if not is_unsupported(v)]
        if len(supported) == len(record.values):
            return
        msg = f'{description} are not supported for {record.fqdn}'
        if not supported:
            self.supports_warn_or_except(msg, 'omitting record')
            desired.remove_record(record)
            return
        self.supports_warn_or_except(msg, 'omitting those values')
        record = record.copy()
        record.values = supported
        desired.add_record(record, replace=True)

    def _attributes(self, record):
        return record['content_attributes'][record['type']]

    def _data_for_multiple(self, _type, records):
        return {
            'ttl': records[0]['ttl'],
            'type': _type,
            'values': [self._attributes(r)['ip_address'] for r in records],
        }

    _data_for_A = _data_for_multiple
    _data_for_AAAA = _data_for_multiple

    def _data_for_single(self, _type, records):
        return {
            'ttl': records[0]['ttl'],
            'type': _type,
            'value': _fqdn(self._attributes(records[0])['hostname']),
        }

    _data_for_ALIAS = _data_for_single
    _data_for_CNAME = _data_for_single

    def _data_for_hostnames(self, _type, records):
        return {
            'ttl': records[0]['ttl'],
            'type': _type,
            'values': [_fqdn(self._attributes(r)['hostname']) for r in records],
        }

    _data_for_NS = _data_for_hostnames
    _data_for_PTR = _data_for_hostnames

    def _data_for_CAA(self, _type, records):
        values = []
        for record in records:
            attributes = self._attributes(record)
            values.append(
                {
                    'flags': int(attributes['flag']),
                    'tag': attributes['tag'],
                    'value': attributes['value'],
                }
            )
        return {'ttl': records[0]['ttl'], 'type': _type, 'values': values}

    def _data_for_MX(self, _type, records):
        values = []
        for record in records:
            values.append(
                {
                    'preference': record['priority'],
                    'exchange': _fqdn(self._attributes(record)['hostname']),
                }
            )
        return {'ttl': records[0]['ttl'], 'type': _type, 'values': values}

    def _data_for_SRV(self, _type, records):
        values = []
        for record in records:
            attributes = self._attributes(record)
            values.append(
                {
                    'priority': record['priority'],
                    'weight': int(attributes['weight']),
                    'port': int(attributes['port']),
                    'target': _fqdn(attributes['target']),
                }
            )
        return {'ttl': records[0]['ttl'], 'type': _type, 'values': values}

    def _data_for_SSHFP(self, _type, records):
        values = []
        for record in records:
            attributes = self._attributes(record)
            values.append(
                {
                    'algorithm': int(attributes['algorithm']),
                    'fingerprint_type': int(attributes['fingerprint_type']),
                    'fingerprint': attributes['fingerprint'],
                }
            )
        return {'ttl': records[0]['ttl'], 'type': _type, 'values': values}

    def _data_for_TXT(self, _type, records):
        # Katapult stores semicolons unescaped; octoDNS expects them escaped
        values = [
            self._attributes(r)['content'].replace(';', '\\;') for r in records
        ]
        return {'ttl': records[0]['ttl'], 'type': _type, 'values': values}

    def _data_for_HTTPRedirect(self, _type, records):
        attributes = self._attributes(records[0])
        return {
            'ttl': records[0]['ttl'],
            'type': _type,
            'value': {
                'url': attributes['url'],
                'http_status': attributes['http_status'],
            },
        }

    def _data_for_VirtualMachine(self, _type, records):
        return {
            'ttl': records[0]['ttl'],
            'type': _type,
            'values': [self._attributes(r)['virtual_machine'] for r in records],
        }

    def _data_for(self, katapult_type, records):
        data_for = getattr(self, f'_data_for_{katapult_type}')
        return data_for(_octodns_type(katapult_type), records)

    def _zone(self, zone_name):
        if zone_name not in self._zones:
            self._zones[zone_name] = self._client.zone(zone_name[:-1])
        return self._zones[zone_name]

    def _relative_name(self, full_name, domain):
        fqdn = idna_encode(full_name.rstrip('.')).lower()
        if fqdn == domain:
            return ''
        if fqdn.endswith(f'.{domain}'):
            return fqdn[: -len(domain) - 1]
        raise ProviderException(
            f'{self.id}: record {full_name} is outside zone {domain}'
        )

    def _normalise(self, record, domain, default_ttl):
        relative_name = self._relative_name(record['full_name'], domain)
        ttl = record['ttl'] if record['ttl'] is not None else default_ttl
        return {**record, 'relative_name': relative_name, 'ttl': ttl}

    def zone_records(self, zone):
        if zone.name not in self._zone_records:
            try:
                katapult_zone = self._zone(zone.name)
            except KatapultClientNotFound:
                return []
            domain = zone.name[:-1]
            self._zone_records[zone.name] = [
                self._normalise(record, domain, katapult_zone['default_ttl'])
                for record in self._client.records(domain)
            ]

        return self._zone_records[zone.name]

    def list_zones(self):
        self.log.debug('list_zones:')
        organization = self._require_organization('list zones')
        return sorted(f'{z["name"]}.' for z in self._client.zones(organization))

    def _skip(self, _type):
        level = (
            logging.DEBUG
            if _type in SILENTLY_SKIPPED_TYPES
            else logging.WARNING
        )
        self.log.log(level, 'populate: skipping unsupported %s record', _type)

    def _warn_if_extra_values(self, zone, name, katapult_type, records):
        _type = _octodns_type(katapult_type)
        record_class = Record.registered_types()[_type]
        if len(records) < 2 or not issubclass(record_class, ValueMixin):
            return
        fqdn = f'{name}.{zone.name}' if name else zone.name
        self.log.warning(
            'populate: %s has %d %s records but takes one value; using the '
            'first',
            fqdn,
            len(records),
            _type,
        )

    def populate(self, zone, target=False, lenient=False):
        self.log.debug(
            'populate: name=%s, target=%s, lenient=%s',
            zone.name,
            target,
            lenient,
        )

        values = defaultdict(lambda: defaultdict(list))
        for record in self.zone_records(zone):
            katapult_type = record['type']
            if _octodns_type(katapult_type) not in self.SUPPORTS:
                self._skip(katapult_type)
                continue
            values[record['relative_name']][katapult_type].append(record)

        before = len(zone.records)
        for name, types in values.items():
            for katapult_type, records in types.items():
                self._warn_if_extra_values(zone, name, katapult_type, records)
                record = Record.new(
                    zone,
                    name,
                    self._data_for(katapult_type, records),
                    source=self,
                    lenient=lenient,
                )
                zone.add_record(record, lenient=lenient)

        exists = zone.name in self._zone_records
        self.log.info(
            'populate:   found %s records, exists=%s',
            len(zone.records) - before,
            exists,
        )
        return exists

    def _properties(self, record, content, priority=None):
        katapult_type = _katapult_type(record._type)
        properties = {
            'name': record.name or APEX_NAME,
            'type': katapult_type,
            'ttl': record.ttl,
            'content': {katapult_type: content},
        }
        if priority is not None:
            properties['priority'] = priority
        return properties

    def _params_for_multiple(self, record):
        for value in record.values:
            yield self._properties(record, {'ip_address': value})

    _params_for_A = _params_for_multiple
    _params_for_AAAA = _params_for_multiple

    def _params_for_single(self, record):
        yield self._properties(record, {'hostname': _hostname(record.value)})

    _params_for_ALIAS = _params_for_single
    _params_for_CNAME = _params_for_single

    def _params_for_hostnames(self, record):
        for value in record.values:
            yield self._properties(record, {'hostname': _hostname(value)})

    _params_for_NS = _params_for_hostnames
    _params_for_PTR = _params_for_hostnames

    def _params_for_CAA(self, record):
        for value in record.values:
            content = {
                'flag': str(value.flags),
                'tag': value.tag,
                'value': value.value,
            }
            yield self._properties(record, content)

    def _params_for_MX(self, record):
        for value in record.values:
            content = {'hostname': _hostname(value.exchange)}
            yield self._properties(record, content, value.preference)

    def _params_for_SRV(self, record):
        for value in record.values:
            content = {
                'weight': str(value.weight),
                'port': str(value.port),
                'target': _hostname(value.target),
            }
            yield self._properties(record, content, value.priority)

    def _params_for_SSHFP(self, record):
        for value in record.values:
            content = {
                'algorithm': str(value.algorithm),
                'fingerprint_type': str(value.fingerprint_type),
                'fingerprint': value.fingerprint,
            }
            yield self._properties(record, content)

    def _params_for_TXT(self, record):
        for value in record.values:
            content = {'content': value.replace('\\;', ';')}
            yield self._properties(record, content)

    def _params_for_HTTPRedirect(self, record):
        content = {
            'url': record.value.url,
            'http_status': str(record.value.http_status),
        }
        yield self._properties(record, content)

    def _params_for_VirtualMachine(self, record):
        for value in record.values:
            yield self._properties(record, {'virtual_machine': value})

    def _params_for(self, record):
        katapult_type = _katapult_type(record._type)
        return getattr(self, f'_params_for_{katapult_type}')(record)

    def _existing_records(self, record):
        return [
            r
            for r in self.zone_records(record.zone)
            if r['relative_name'] == record.name
            and r['type'] == _katapult_type(record._type)
        ]

    def _value_key(self, properties):
        return dumps(
            [properties.get('priority'), properties['content']], sort_keys=True
        )

    def _existing_key(self, zone, existing):
        # Round-trip through octoDNS so both sides share one normalisation
        data = self._data_for(existing['type'], [existing])
        record = Record.new(zone, existing['relative_name'], data, lenient=True)
        return self._value_key(next(self._params_for(record)))

    def _match_existing(self, new, existing):
        unmatched = defaultdict(list)
        for record in existing:
            unmatched[self._existing_key(new.zone, record)].append(record)
        kept, pending = [], []
        for properties in self._params_for(new):
            matches = unmatched[self._value_key(properties)]
            if matches:
                kept.append((matches.pop(0), properties))
            else:
                pending.append(properties)
        spare = [r for records in unmatched.values() for r in records]
        return kept, pending, spare

    def _apply_Create(self, change):
        new = change.new
        for properties in self._params_for(new):
            self._client.record_create(new.zone.name[:-1], properties)

    def _apply_Update(self, change):
        # Patch records in place where possible so a name never goes without
        # its values mid-apply
        new = change.new
        existing = self._existing_records(change.existing)
        kept, pending, spare = self._match_existing(new, existing)
        for record, properties in kept:
            if record['ttl'] != new.ttl:
                self._client.record_update(record['id'], properties)
        for properties in pending:
            if spare:
                self._client.record_update(spare.pop(0)['id'], properties)
            else:
                self._client.record_create(new.zone.name[:-1], properties)
        for record in spare:
            self._client.record_delete(record['id'])

    def _apply_Delete(self, change):
        for record in self._existing_records(change.existing):
            self._client.record_delete(record['id'])

    def _ensure_zone(self, zone_name):
        try:
            katapult_zone = self._zone(zone_name)
        except KatapultClientNotFound:
            organization = self._require_organization(
                f'create zone {zone_name}'
            )
            self.log.debug('_apply:   no matching zone, creating')
            katapult_zone = self._client.zone_create(
                organization, zone_name[:-1]
            )
            self._zones[zone_name] = katapult_zone
        if not katapult_zone['verified']:
            self.log.warning(
                '_apply: zone %s is not verified; Katapult will not serve '
                'its records until the domain delegates to Katapult or '
                'carries the verification TXT record',
                zone_name,
            )

    def _apply(self, plan):
        desired = plan.desired
        changes = plan.changes
        self.log.debug(
            '_apply: zone=%s, len(changes)=%d', desired.name, len(changes)
        )

        self._ensure_zone(desired.name)

        for change in changes:
            class_name = change.__class__.__name__
            getattr(self, f'_apply_{class_name}')(change)

        # Clear out the cache if any
        self._zone_records.pop(desired.name, None)
        self._zones.pop(desired.name, None)
