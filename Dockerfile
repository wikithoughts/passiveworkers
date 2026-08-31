FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY passiveworkers/ passiveworkers/
# [extract] = trafilatura, for real main-content/date extraction on page evidence. The bare
# install degrades silently to a regex strip — this image is the README-recommended "just
# works" path (research-desk AND coordinator both build from it), so it ships the better
# extractor by default.
RUN pip install --no-cache-dir -e '.[extract]'
ENV PW_SERVE_HOST=0.0.0.0
EXPOSE 8770
CMD ["pworkers", "serve"]
