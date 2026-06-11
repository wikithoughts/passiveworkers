FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY council/ council/
RUN pip install --no-cache-dir -e .
ENV PW_SERVE_HOST=0.0.0.0
EXPOSE 8770
CMD ["pw", "serve"]
