#!/bin/bash -ex

PBF_DIR=/nominatim/data
PBF_FILE=$PBF_DIR/odessa_oblast.pbf

PBF_MIRRORS=(
  "https://geo2day.com/europe/ukraine/odessa_oblast.pbf"
  "https://download.openstreetmap.fr/extracts/europe/ukraine/odessa_oblast.osm.pbf"
)

mkdir -p $PBF_DIR

if [ ! -f "$PBF_FILE" ]; then
  echo "PBF not found at $PBF_FILE, downloading..."
  downloaded=false
  for mirror in "${PBF_MIRRORS[@]}"; do
    echo "Trying $mirror ..."
    if curl -L -A "nominatim-docker" --fail-with-body -o "$PBF_FILE" "$mirror"; then
      echo "Downloaded successfully from $mirror"
      downloaded=true
      break
    else
      echo "Failed to download from $mirror"
      rm -f "$PBF_FILE"
    fi
  done
  if [ "$downloaded" = false ]; then
    echo "FATAL: Could not download PBF from any mirror"
    exit 1
  fi
else
  echo "PBF already exists at $PBF_FILE, skipping download"
fi

export PBF_PATH=$PBF_FILE
unset PBF_URL

exec /app/start.sh
