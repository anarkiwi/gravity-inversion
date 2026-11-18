# Ghidra headless with deity-informant's 6510 SLEIGH module (tools/ghidra_ref.py).
# The Ghidra download is its own cached stage; the run stage installs deity-informant
# at DEITY_INFORMANT (GI's pin) and emits the 6510 language into Ghidra, world-readable
# so a non-root --user can load it.

ARG GHIDRA_URL=https://github.com/NationalSecurityAgency/ghidra/releases/download/Ghidra_12.1.2_build/ghidra_12.1.2_PUBLIC_20260605.zip

FROM eclipse-temurin:24-jdk AS ghidra
ARG GHIDRA_URL
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl unzip ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL -o /tmp/ghidra.zip "$GHIDRA_URL" \
    && unzip -q /tmp/ghidra.zip -d /opt \
    && mv /opt/ghidra_* /opt/ghidra \
    && rm /tmp/ghidra.zip

FROM eclipse-temurin:24-jdk AS run
ARG DEITY_INFORMANT
RUN test -n "$DEITY_INFORMANT" \
    && apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghidra /opt/ghidra /opt/ghidra
ENV GHIDRA_INSTALL_DIR=/opt/ghidra
RUN pip3 install --no-cache-dir --break-system-packages "deity-informant==$DEITY_INFORMANT" \
    && deity-informant emit-sleigh \
        -o "$GHIDRA_INSTALL_DIR/Ghidra/Processors/6510/data/languages" \
    && chmod -R a+rX "$GHIDRA_INSTALL_DIR/Ghidra/Processors/6510"
