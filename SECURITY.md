# Security policy

## Reporting a vulnerability

Report vulnerabilities privately through GitHub: open the repository's **Security** tab and choose **Report a vulnerability**, or go straight to [the private reporting form](https://github.com/DonQuinleone/octodns-katapult/security/advisories/new).

Please do not open a public issue, pull request or discussion for a vulnerability.

A useful report includes:

- the version of octodns-katapult, and whether you used the PyPI package or the GHCR image
- what an attacker can do, and what they need first
- steps or a configuration that reproduce it, with any tokens or account details removed

## Supported versions

| Version | Supported |
|---|:---:|
| Latest stable release | ✅ |
| Latest pre-release, until its stable release ships | ✅ |
| Anything older | ❌ |

## Scope

In scope: the code in this repository and the artefacts it publishes, which are the `octodns-katapult` package on PyPI and the `ghcr.io/donquinleone/octodns-katapult` image.

Out of scope, and best reported elsewhere:

- vulnerabilities in the Katapult platform or its API: report them to Krystal
- vulnerabilities in octoDNS itself: follow the [octoDNS security policy](https://github.com/octodns/octodns/security)

octodns-katapult is an independent project, not made, endorsed or supported by Krystal.
