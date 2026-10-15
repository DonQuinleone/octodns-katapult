#
#
#

from octodns.equality import EqualityTupleMixin
from octodns.record import Record, ValueMixin, ValuesMixin
from octodns.record.validator import ValidationReason, ValueValidator

REDIRECT_URL_SCHEMES = ('http://', 'https://')
REDIRECT_STATUSES = (301, 302, 307, 308)


def _is_redirect_url(url):
    if not isinstance(url, str):
        return False
    lowered = url.lower()
    return any(
        lowered.startswith(scheme) and len(url) > len(scheme)
        for scheme in REDIRECT_URL_SCHEMES
    )


class KatapultHttpRedirectValueValidator(ValueValidator):
    def validate(self, value_cls, data, _type):
        if not isinstance(data, dict):
            return [ValidationReason('missing value', validator_id=self.id)]
        return self._url_reasons(data) + self._status_reasons(data)

    def _url_reasons(self, data):
        url = data.get('url')
        if not url:
            return [ValidationReason('missing url', validator_id=self.id)]
        if isinstance(url, str) and url != url.strip():
            return [
                ValidationReason(
                    f'invalid url "{url}"; remove the surrounding whitespace',
                    validator_id=self.id,
                )
            ]
        if not _is_redirect_url(url):
            return [
                ValidationReason(
                    f'invalid url "{url}"; must start with http:// or '
                    'https://',
                    validator_id=self.id,
                )
            ]
        return []

    def _status_reasons(self, data):
        if 'http_status' not in data:
            return [
                ValidationReason('missing http_status', validator_id=self.id)
            ]
        status = data['http_status']
        try:
            code = int(status)
        except (TypeError, ValueError):
            return [
                ValidationReason(
                    f'invalid http_status "{status}"', validator_id=self.id
                )
            ]
        if code not in REDIRECT_STATUSES:
            return [
                ValidationReason(
                    f'invalid http_status "{status}"; must be 301, 302, 307 '
                    'or 308',
                    validator_id=self.id,
                )
            ]
        return []


class KatapultHttpRedirectValue(EqualityTupleMixin, dict):
    VALIDATORS = [
        KatapultHttpRedirectValueValidator('katapult-http-redirect-value')
    ]

    @classmethod
    def process(cls, value):
        return cls(value)

    def __init__(self, value):
        super().__init__(
            {'url': value['url'], 'http_status': int(value['http_status'])}
        )

    @property
    def url(self):
        return self['url']

    @property
    def http_status(self):
        return self['http_status']

    def _equality_tuple(self):
        return (self.url, self.http_status)

    def __repr__(self):
        return f'{self.http_status} {self.url}'


class KatapultHttpRedirectRecord(ValueMixin, Record):
    '''
    A redirect served by Katapult: the name answers HTTP requests with a
    redirect to `url` using `http_status`.

    Only Katapult understands this type; other providers skip it.
    '''

    _type = 'KatapultProvider/HTTPRedirect'
    _value_type = KatapultHttpRedirectValue


def _is_trimmed_text(value):
    return (
        isinstance(value, str)
        and value.strip() != ''
        and value == value.strip()
    )


class KatapultVirtualMachineValueValidator(ValueValidator):
    def validate(self, value_cls, data, _type):
        if not data:
            return [ValidationReason('missing value(s)', validator_id=self.id)]
        return [
            ValidationReason(
                f'invalid virtual machine "{value}"', validator_id=self.id
            )
            for value in data
            if not _is_trimmed_text(value)
        ]


class KatapultVirtualMachineValue(str):
    VALIDATORS = [
        KatapultVirtualMachineValueValidator('katapult-virtual-machine-value')
    ]

    @classmethod
    def process(cls, values):
        return [cls(value) for value in values]


class KatapultVirtualMachineRecord(ValuesMixin, Record):
    '''
    Addresses that follow a Katapult virtual machine: Katapult answers with
    the current IP addresses of each listed virtual machine.

    Only Katapult understands this type; other providers skip it.
    '''

    _type = 'KatapultProvider/VirtualMachine'
    _value_type = KatapultVirtualMachineValue


Record.register_type(KatapultHttpRedirectRecord)
Record.register_type(KatapultVirtualMachineRecord)
