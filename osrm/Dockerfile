# Single merged-OSRM image serving every supported city from one dataset.
#
# Three stages so we never apt-install into the osrm-backend image (it's built on
# Debian stretch, whose apt mirrors are now 404):
#   1. merger  — modern Debian + osmium: download, clip-to-bbox, and merge the
#                city PBFs into one combined.osm.pbf.
#   2. builder — the osrm-backend image (ships osrm-extract/partition/customize):
#                process the merged PBF into a foot/MLD dataset.
#   3. runtime — lean osrm-backend that just serves the prebuilt dataset.
# Data is baked in at build time, so the container starts instantly (no boot-time
# download or processing).

# Stage 1: merge city extracts with osmium
FROM debian:bookworm-slim AS merger
RUN apt-get update && apt-get install -y --no-install-recommends \
      osmium-tool wget ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY build-merged.sh /build/build-merged.sh
RUN chmod +x /build/build-merged.sh && WORK_DIR=/build OUT_DIR=/merged /build/build-merged.sh

# Stage 2: OSRM extract/partition/customize (foot profile, MLD algorithm)
FROM osrm/osrm-backend:v5.25.0 AS builder
WORKDIR /data
COPY --from=merger /merged/combined.osm.pbf /data/combined.osm.pbf
# Use our pinned foot.lua (kept in sync with server/foot_profile.py) instead of the
# image's stock /opt/foot.lua. It require()s lib/* which still ship at /opt/lib/.
COPY foot.lua /opt/foot.lua
RUN osrm-extract -p /opt/foot.lua /data/combined.osm.pbf \
    && osrm-partition /data/combined.osrm \
    && osrm-customize /data/combined.osrm \
    && rm -f /data/combined.osm.pbf

# Stage 3: runtime
FROM osrm/osrm-backend:v5.25.0
WORKDIR /data
COPY --from=builder /data/combined.osrm* /data/

# Cloud Run injects $PORT (8080); local compose leaves it unset -> default 5000.
# osrm-routed must bind 0.0.0.0 so the container is reachable.
ENV OSRM_DATASET=/data/combined.osrm
CMD ["sh", "-c", "exec osrm-routed --algorithm mld --ip 0.0.0.0 --port ${PORT:-5000} \"$OSRM_DATASET\""]
