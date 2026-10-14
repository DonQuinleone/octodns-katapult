#
#
#

from requests import Session
from requests.adapters import HTTPAdapter, Retry

from octodns.provider import ProviderException


class KatapultClientException(ProviderException):
    pass


class KatapultClientNotFound(KatapultClientException):
    pass


class KatapultClientUnauthorized(KatapultClientException):
    pass


class KatapultClient(object):
    '''
    Thin wrapper around the DNS endpoints of the Katapult Core API v1.

    Katapult routes take the object being acted on as a lookup argument
    rather than a path segment, so every path uses the `_` placeholder and
    passes `dns_zone[name]`, `dns_record[id]` or `organization[sub_domain]`
    in the query string or JSON body.
    '''

    BASE = 'https://api.katapult.io/core/v1'
    RATE_LIMIT_RETRIES = 5
    RATE_LIMIT_BACKOFF_FACTOR = 1

    def __init__(self, token, user_agent):
        sess = Session()
        sess.headers.update(
            {'Authorization': f'Bearer {token}', 'User-Agent': user_agent}
        )
        # Katapult has no batch endpoint, so a large plan makes one call per
        # value and can hit the per-token rate limit. A 429 means the request
        # was not processed, so it's safe to retry for every method; other
        # failures are not retried, so a create is never sent twice.
        retry = Retry(
            total=self.RATE_LIMIT_RETRIES,
            connect=0,
            read=0,
            other=0,
            status=self.RATE_LIMIT_RETRIES,
            backoff_factor=self.RATE_LIMIT_BACKOFF_FACTOR,
            status_forcelist=(429,),
            allowed_methods=None,
            respect_retry_after_header=True,
            raise_on_status=False,
        )
        sess.mount('https://', HTTPAdapter(max_retries=retry))
        self._sess = sess

    def _request(self, method, path, params=None, data=None):
        url = f'{self.BASE}{path}'
        resp = self._sess.request(method, url, params=params, json=data)
        if resp.ok:
            return resp.json()
        message = self._error_message(resp)
        if resp.status_code == 404:
            raise KatapultClientNotFound(message)
        if resp.status_code in (401, 403):
            raise KatapultClientUnauthorized(message)
        raise KatapultClientException(message)

    def _error_message(self, resp):
        try:
            error = resp.json()['error']
            message = (
                f'{resp.status_code} {error["code"]}: {error["description"]}'
            )
            errors = (error.get('detail') or {}).get('errors')
        except (AttributeError, KeyError, TypeError, ValueError):
            return f'{resp.status_code} {resp.reason}'
        if errors:
            message = f'{message} ({"; ".join(errors)})'
        return message

    def zones(self, organization):
        params = {'organization[sub_domain]': organization}
        zones = []
        page = 1
        while True:
            data = self._request(
                'GET', '/organizations/_/dns_zones', {**params, 'page': page}
            )
            zones.extend(data['dns_zones'])
            if not self._has_next_page(data['pagination'], data['dns_zones']):
                return zones
            page += 1

    def _has_next_page(self, pagination, items):
        total_pages = pagination['total_pages']
        if total_pages is None:
            # Large sets omit the totals, so stop on the first short page
            return len(items) >= pagination['per_page']
        return pagination['current_page'] < total_pages

    def zone(self, name):
        params = {'dns_zone[name]': name}
        return self._request('GET', '/dns_zones/_', params)['dns_zone']

    def zone_create(self, organization, name):
        data = {
            'organization': {'sub_domain': organization},
            'properties': {'name': name},
        }
        return self._request('POST', '/organizations/_/dns_zones', data=data)[
            'dns_zone'
        ]

    def records(self, zone_name):
        params = {'dns_zone[name]': zone_name}
        return self._request('GET', '/dns_zones/_/records', params)[
            'dns_records'
        ]

    def record_create(self, zone_name, properties):
        data = {'dns_zone': {'name': zone_name}, 'properties': properties}
        return self._request('POST', '/dns_zones/_/records', data=data)[
            'dns_record'
        ]

    def record_update(self, record_id, properties):
        data = {'dns_record': {'id': record_id}, 'properties': properties}
        return self._request('PATCH', '/dns_records/_', data=data)['dns_record']

    def record_delete(self, record_id):
        data = {'dns_record': {'id': record_id}}
        self._request('DELETE', '/dns_records/_', data=data)
