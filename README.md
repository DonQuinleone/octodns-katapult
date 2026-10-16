## Katapult provider for octoDNS

[![CI](https://github.com/DonQuinleone/octodns-katapult/actions/workflows/main.yml/badge.svg?branch=master)](https://github.com/DonQuinleone/octodns-katapult/actions/workflows/main.yml)
[![PyPI version](https://img.shields.io/pypi/v/octodns-katapult)](https://pypi.org/project/octodns-katapult/)
[![Python versions](https://img.shields.io/pypi/pyversions/octodns-katapult)](https://pypi.org/project/octodns-katapult/)
[![Licence: MIT](https://img.shields.io/badge/licence-MIT-blue)](https://github.com/DonQuinleone/octodns-katapult/blob/master/LICENSE)

An [octoDNS](https://github.com/octodns/octodns/) provider that targets [Katapult](https://docs.katapult.io/docs/category/core-api), [Krystal](https://krystal.io)'s Cloud platform, through its Core API v1.

> **Disclaimer:** octodns-katapult is an independent project. It is not made, endorsed or supported by Krystal.
>
> Report problems with this provider here, not to Krystal's support team.

### Installation

#### Command line

```
pip install octodns-katapult
```

#### requirements.txt/setup.py

Pinning specific versions or SHAs is recommended to avoid unplanned upgrades.

##### Versions

```
octodns==1.22.0
octodns-katapult==1.0.0
```

##### SHAs

```
-e git+https://git@github.com/octodns/octodns.git@94bc80eb03f662565940dd2d4af5c20ccd61915e#egg=octodns
-e git+https://git@github.com/DonQuinleone/octodns-katapult.git@de30cd5b8775d8cf98ffcbff3eeac77929b043b1#egg=octodns_katapult
```

### Configuration

First, obtain an API token.

Log in at <https://cloud.krystal.io>, navigate to **Access > API Tokens** and create a new API token with the `Core: Manage DNS zones/records` scope, or `Core: Read-only access to DNS zones/records` where Katapult is only used as a source.

Then, establish your Organization's sub-domain.

This is the value after `/o/` in the URL. For example, it would be `acme` where the URL is `https://cloud.krystal.io/o/acme`. It's needed only to list zones and to create zones that don't exist yet.

Enter these values into your configuration file, preferably as environment variables:

```yaml
providers:
  katapult:
    class: octodns_katapult.KatapultProvider
    token: env/KATAPULT_API_TOKEN
    organization: env/KATAPULT_ORG
```

Or directly, keeping the file out of version control:

```yaml
providers:
  katapult:
    class: octodns_katapult.KatapultProvider
    token: <your API token>
    organization: <your Organization's sub-domain>
```

### Support Information

#### Records and features

- ✅ = Supported
- ⚠️ = Supported with limits (see the notes)
- ❌ = Not supported

#### Record types

| Record type | Supported | Notes |
| --- | :-: | --- |
| A | ✅ |  |
| AAAA | ✅ |  |
| ALIAS | ✅ | At the apex only |
| CAA | ✅ |  |
| CNAME | ✅ |  |
| DNAME | ❌ |  |
| DS | ❌ |  |
| HTTPS | ❌ |  |
| LOC | ❌ |  |
| MX | ⚠️ | Null MX (RFC 7505) records are not supported by Katapult |
| NAPTR | ❌ |  |
| NS | ✅ | Including at the zone apex |
| OPENPGPKEY | ❌ |  |
| PTR | ⚠️ | One value per name only |
| SPF | ❌ | Use TXT records instead |
| SRV | ⚠️ | Records with the root target `.` or port 0 (RFC 2782) are not supported by Katapult |
| SSHFP | ✅ |  |
| SVCB | ❌ |  |
| TLSA | ❌ |  |
| TXT | ✅ |  |
| URI | ❌ |  |
| URLFWD | ❌ |  |
| KatapultProvider/HTTPRedirect | ✅ | See **Katapult record types** below |
| KatapultProvider/VirtualMachine | ✅ | See **Katapult record types** below |
| SOA | ❌ | Managed by Katapult and skipped silently |

#### Features

| Feature | Supported | Notes |
| --- | :-: | --- |
| Root NS records | ✅ |  |
| DNSSEC | ❌ | Not offered by Katapult |
| Dynamic and geo records | ❌ | Fall back to their default values |
| Listing zones | ✅ |  |
| Creating missing zones | ✅ |  |

Katapult's minimum supported TTL is `60`.

Records marked ❌, outside a ⚠️ row's limits, or with a TTL under `60` fail the plan before anything reaches Katapult. With `strict_supports: false`, they're adjusted or dropped with a warning instead.

#### Katapult record types

- **HTTPRedirect**\
  Takes an `http_status` code (`301`, `302`, `307` or `308`) and a single `url` (`http` or `https`), and redirects visitors from the record's name to that URL.

```yaml
go:
  type: KatapultProvider/HTTPRedirect
  value:
    http_status: 301
    url: https://example.com/
```

- **VirtualMachine**\
  Takes one or more virtual machine IDs and answers with their IP addresses.

```yaml
app:
  type: KatapultProvider/VirtualMachine
  values:
    - vm_abcdEfg1h23IjK4l
    - vm_mnopQrs5t67UvW8x
```

#### Zone verification

Katapult serves a zone only once it's verified, by delegating the domain to Katapult's nameservers or by adding the `katapult-dns …` TXT record Katapult shows. If octoDNS also manages the apex TXT records at your current DNS provider, add that record to your config, or the next sync deletes it.

### Contributing

Contributions are welcome as pull requests against `master`. Report security vulnerabilities privately, as [SECURITY.md](SECURITY.md) describes, rather than in an issue.

#### Development

```
./script/bootstrap
```

This creates a virtual environment in `env/`, installs the pinned runtime and development requirements, and installs a pre-commit hook that runs the same checks as CI. The [scripts](/script/) follow the [Scripts to Rule Them All](https://github.com/github/scripts-to-rule-them-all) pattern:

| Script | What it does |
| --- | --- |
| `./script/test` | Runs the tests |
| `./script/coverage` | Runs the tests with branch coverage; anything under 100% fails |
| `./script/lint` | Checks the code with pyflakes |
| `./script/format` | Formats the code with isort and black |
| `./script/cibuild` | Runs everything CI runs |

The tests never call the Katapult API; responses are fixtures in `tests/fixtures`, shaped after the Core API's [OpenAPI schema](https://api.katapult.io/core/v1/schema/openapi.json). Check behaviour against the real API with a zone you can afford to break.

Every pull request needs a changelog entry, which CI checks. Use `none` for changes that don't belong in the release notes:

```
./script/changelog create -t <none|patch|minor|major> "What changed, as a release note"
```

#### Releasing

Versions follow [SemVer](https://semver.org/), with pre-releases written `1.0.0-beta.1` and `1.0.0-rc.1`. The version lives in `octodns_katapult/__init__.py` and the tag is `v` plus that version. PyPI shows pre-releases in Python's spelling (`1.0.0rc1`), and `pip` installs them only when asked with `--pre`.

1. Bump the version and write the changelog from the pending entries:

   ```
   ./script/changelog bump --version 1.0.0-rc.1 --make-changes
   ```

2. Open a pull request with the result, and merge it once CI passes.
3. From an up-to-date `master`, run `./script/release`. It refuses a dirty tree or a `HEAD` that isn't `origin/master`, then signs and pushes the tag.

The tag starts the release workflow, which waits for approval on the `pypi` environment before publishing:

| Step | Pre-release | Stable |
| --- | :-: | :-: |
| Checks the tag is on `master` and matches the version, then lints, tests and builds | ✅ | ✅ |
| Publishes to PyPI through trusted publishing | ✅ | ✅ |
| Pushes `ghcr.io/donquinleone/octodns-katapult:<version>` for amd64 and arm64 | ✅ | ✅ |
| Moves the image's `latest` tag | ❌ | ✅ |
| Creates a GitHub release with the changelog section and built files | ✅ | ✅ |
| Opens a pull request updating the pins under [Installation](#installation) | ❌ | ✅ |

Workflow-opened pull requests don't start CI on their own; close and reopen them to run the checks.

The image runs `octodns-sync` with `/config` as its working directory:

```
docker run --rm -v "$PWD:/config" -e KATAPULT_API_TOKEN ghcr.io/donquinleone/octodns-katapult:latest --config-file=config.yaml
```
