# Base pinned by digest (the multi-arch index digest of python:3.12-slim, so amd64 and arm64 both
# resolve); the tag is kept for readability only. Refresh both together:
#   docker buildx imagetools inspect python:3.12-slim   → the top-level "Digest:" line
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY passiveworkers/ passiveworkers/
# [extract] = trafilatura, for real main-content/date extraction on page evidence. The bare
# install degrades silently to a regex strip — this image is the README-recommended "just
# works" path (research-desk AND coordinator both build from it), so it ships the better
# extractor by default. A regular (non-editable) install: the package lands in site-packages,
# owned by root and read-only to the runtime user below. (DL3013 wants a pinned version, but this
# installs the local project, not a registry package.)
# hadolint ignore=DL3013
RUN pip install --no-cache-dir '.[extract]'
# Run as the unprivileged user `pw`, by numeric ID so `runAsNonRoot`-style checks can verify it.
# UID/GID 1000 matches docker-compose.yml's PW_UID/PW_GID default, so bind-mounted
# ~/.passiveworkers files stay owned by the host user on Linux. /app/data is created here, owned by
# that user, so a fresh named volume mounted there (the coordinator's DB) inherits a writable owner
# instead of root.
RUN groupadd --gid 1000 pw \
    && useradd --uid 1000 --gid pw --create-home --shell /usr/sbin/nologin pw \
    && install -d -o pw -g pw -m 0700 /app/data
USER 1000:1000
ENV PW_SERVE_HOST=0.0.0.0
EXPOSE 8770
CMD ["pworkers", "serve"]
