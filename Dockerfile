FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      bash \
      bats \
      ca-certificates \
      git \
      python3 \
      python3-pytest \
      ripgrep \
      shellcheck \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /work
COPY . /work
CMD ["./scripts/test.sh"]
