#!/usr/bin/env bash
# Run the suite under coverage.
#
# HFIT_COVERAGE_SOURCE must be an absolute path: .coveragerc interpolates it, and
# every hook script is exercised as a subprocess whose cwd is a sandboxed tmp
# project directory. A relative `source = hooks` would resolve against that tmp
# dir and silently measure nothing.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export HFIT_COVERAGE_SOURCE="${repo_root}/hooks"

cd "${repo_root}"
python3 -m coverage erase
python3 -m coverage run -m pytest "$@"
python3 -m coverage combine
python3 -m coverage report
