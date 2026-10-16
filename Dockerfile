# Runner image: octoDNS with octodns-katapult installed, octodns-sync as the
# entrypoint. Built by the release workflow from the wheel it just tested.
FROM python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170

COPY requirements.txt /tmp/build/
COPY dist/*.whl /tmp/build/dist/
# Constrain dependencies to the versions the tests ran against
RUN pip install --no-cache-dir --constraint /tmp/build/requirements.txt /tmp/build/dist/*.whl \
    && rm -rf /tmp/build \
    && useradd --create-home --uid 10001 octodns

USER octodns
WORKDIR /config
ENTRYPOINT ["octodns-sync"]
CMD ["--help"]
