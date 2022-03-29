#!/usr/bin/env bash

# Start Docker container

# Bash strict-mode
set -o errexit
set -o nounset
set -o pipefail

cmd="${@:-bash}"

docker run --rm -it -v ${PWD}/config:/bugzilla2gitlab/config -v ${PWD}/../bugzilla2gitlab:/bugzilla2gitlab test/bugzilla2gitlab sh -c "pip install -e . && ${cmd}"