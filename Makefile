# Console and Go platform targets.
DASHBOARD := dashboard
COMPOSE := docker compose -f deploy/compose.yaml

.DEFAULT_GOAL := help
.PHONY: help dev test lint dashboard-test dashboard-e2e dashboard-lint typecheck up down preflight user-add project-add go-test go-lint sqlc api-lint api-sources api-types api-types-check api-check

help: ## List the targets
	@grep -E '^[a-z][a-z0-9_-]*:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-15s %s\n", $$1, $$2}'

dev: ## Start the dashboard dev server
	npm --prefix $(DASHBOARD) run dev

test: go-test dashboard-test ## Run the Go tests (race detector) and the dashboard tests

lint: go-lint dashboard-lint ## Run Go lint and the dashboard lint and TypeScript checks

dashboard-test: ## Run the dashboard tests
	npm --prefix $(DASHBOARD) test

dashboard-e2e: ## Build the preview and run shell and accessibility browser tests
	cd $(DASHBOARD) && npx --no-install tsc -p e2e/tsconfig.json --noEmit
	VITE_MOCK_API=on npm --prefix $(DASHBOARD) run build
	cd $(DASHBOARD) && npx --no-install playwright test

dashboard-lint: ## Run the dashboard lint and TypeScript checks
	npm --prefix $(DASHBOARD) run lint
	$(MAKE) typecheck

typecheck: ## Run the dashboard TypeScript check
	cd $(DASHBOARD) && npx --no-install tsc -b --noEmit

up: ## Start the six-service stack; creates deploy/.env with random secrets on first run. Pulls VOICEGATEWAY_IMAGE (kept in deploy/.env) instead of building when set
	tools/scripts/dev-env.sh deploy/.env
	@if grep -q '^VOICEGATEWAY_IMAGE=.' deploy/.env; then \
		$(COMPOSE) --env-file deploy/.env pull migrate web worker && \
		$(COMPOSE) --env-file deploy/.env up -d --no-build --wait; \
	else \
		$(COMPOSE) --env-file deploy/.env up -d --build --wait; \
	fi

down: ## Stop the stack (data volumes are kept)
	$(COMPOSE) --env-file deploy/.env down

preflight: ## Check that every dependency of the running stack answers
	$(COMPOSE) --env-file deploy/.env run --rm --no-deps web preflight

go-test: ## Run the Go tests with the race detector; container tests need Docker
	go test -race $(GO_TEST_FLAGS) ./...

go-lint: ## Lint and format-check the Go code, and check go.mod and sqlc output are current
	go tool golangci-lint run ./...
	go tool golangci-lint fmt --diff ./...
	go mod tidy -diff
	cd internal/store/pgstore && go tool sqlc diff

sqlc: ## Regenerate the typed Postgres queries in internal/store/pgstore
	cd internal/store/pgstore && go tool sqlc generate

user-add: ## Add the first admin to the running stack: make user-add EMAIL=you@example.com (asks for the password, or reads VOICEGATEWAY_USER_PASSWORD)
	@test -n "$(EMAIL)" || { echo 'EMAIL is required, for example: make user-add EMAIL=you@example.com'; exit 1; }
	$(COMPOSE) --env-file deploy/.env run --rm --no-deps -e VOICEGATEWAY_USER_PASSWORD web user add --email "$(EMAIL)" --role "$(or $(ROLE),admin)"

project-add: ## Add a LiveKit project to the running stack: make project-add NAME="Production" (uses LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET; SKIP_VERIFY=1 skips the LiveKit check)
	@test -n "$(NAME)" || { echo 'NAME is required, for example: make project-add NAME="Production"'; exit 1; }
	$(COMPOSE) --env-file deploy/.env run --rm --no-deps -e LIVEKIT_API_SECRET web project add --name "$(NAME)" --livekit-url "$(LIVEKIT_URL)" --livekit-api-key "$(LIVEKIT_API_KEY)"$(if $(SKIP_VERIFY), --skip-verify)

api-lint: ## Validate the pinned OpenAPI contract
	cd $(DASHBOARD) && REDOCLY_TELEMETRY=off npx --no-install redocly lint ../docs/api/openapi.yaml --config ../redocly.yaml

api-sources: ## Check source attribution, units and unknown values
	node $(DASHBOARD)/scripts/check-sources.ts

api-types: ## Regenerate the console API types from the contract
	npm --prefix $(DASHBOARD) run api:types

api-types-check: api-types ## Fail when the generated API types are out of date
	git diff --exit-code -- $(DASHBOARD)/src/lib/api-types
	test -z "$$(git ls-files --others --exclude-standard -- $(DASHBOARD)/src/lib/api-types)"

api-check: api-lint api-sources api-types-check ## Validate the console read contract

.PHONY: installer-test
installer-test: ## Check installer shell syntax and host behavior
	shellcheck install.sh collector.sh tools/tests/test_installers.sh
	bash tools/tests/test_installers.sh
