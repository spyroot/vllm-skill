SHELL := /bin/bash

PYTHON_BIN ?= python3
RUNTIME_DIR := runtime/daedalus-vllm

.PHONY: ci test coverage lint shellcheck bash-syntax runtime-test dry-run live-smoke

ci: lint test dry-run

test:
	PYTHON_BIN="$(PYTHON_BIN)" ./scripts/test.sh

coverage:
	PYTHON_BIN="$(PYTHON_BIN)" ./scripts/coverage.sh

lint: shellcheck bash-syntax

shellcheck:
	find scripts skills runtime -type f -name '*.sh' -print0 | xargs -0 shellcheck

bash-syntax:
	find scripts skills runtime -type f -name '*.sh' | sort | while read -r script_path; do \
		bash -n "$$script_path"; \
	done

runtime-test:
	cd "$(RUNTIME_DIR)" && ./run.sh test

dry-run:
	mkdir -p reports/rendered-manifests
	cd "$(RUNTIME_DIR)" && ./deploy-daedalus-openshift.sh --dry-run --manifest-only > ../../reports/rendered-manifests/daedalus-runtime.yaml
	cd "$(RUNTIME_DIR)" && ./build-deploy-daedalus-openshift.sh --dry-run > ../../reports/rendered-manifests/daedalus-build-deploy.txt
	cd "$(RUNTIME_DIR)" && ./deploy-daedalus-openshift.sh --profile example-local-gpu --dry-run --manifest-only > ../../reports/rendered-manifests/daedalus-example-local-gpu.yaml

live-smoke:
	./scripts/gitlab-live-smoke.sh
