#!/usr/bin/env bash

# Run Docker container

# Bash strict-mode
set -o errexit
set -o nounset
set -o pipefail

IFS=$'\n\t'
SCRIPT_FOLDER="$(dirname "$(readlink -f "${0}")")"

CONFIG_DIR="${SCRIPT_FOLDER}/config"

if [[ ! -d "${SCRIPT_FOLDER}/config" ]]; then
  echo "Config dir not found. Initializing..."
  mkdir -p "${CONFIG_DIR}"
  cp "tests/test_data/config/defaults.yml" "${CONFIG_DIR}/"
  touch "${CONFIG_DIR}/component_mappings.yml"
  touch "${CONFIG_DIR}/bugs"
fi

./start_container.sh "bin/bugzilla2gitlab"