FROM python:3.13-slim AS executable_builder
WORKDIR /app

RUN --mount=type=cache,sharing=locked,target=/var/cache/apt \
    --mount=type=cache,sharing=locked,target=/var/lib/apt/lists \
    apt-get update && \
    apt-get install -y cargo

RUN \
    --mount=type=bind,source=.,target=.,rw \
    --mount=type=cache,sharing=private,target=/app/target \
    --mount=type=cache,target=/usr/local/cargo/git/db \
    --mount=type=cache,target=/usr/local/cargo/registry/ \
    cargo build --release && \
    cp target/release/any-board /anyboard

FROM python:3.13-slim AS runtime
WORKDIR /app

COPY --from=executable_builder /anyboard /anyboard

RUN --mount=type=cache,sharing=private,target=/root/.cache/pip \
    --mount=type=bind,source=requirements.txt,target=requirements.txt \
    pip3 install -r requirements.txt

CMD ["/anyboard"]