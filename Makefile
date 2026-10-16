# Console targets. Backend targets arrive with Phase 2.
DASHBOARD := dashboard

.DEFAULT_GOAL := help
.PHONY: help dev test lint typecheck landing-dev landing-check landing-deploy

help: ## List the targets
	@grep -E '^[a-z][a-z0-9_-]*:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-15s %s\n", $$1, $$2}'

dev: ## Start the dashboard dev server
	npm --prefix $(DASHBOARD) run dev

test: ## Run dashboard helper regression tests
	npm --prefix $(DASHBOARD) test

lint: ## Run dashboard lint and TypeScript checks
	npm --prefix $(DASHBOARD) run lint
	$(MAKE) typecheck

typecheck: ## Run the dashboard TypeScript check
	cd $(DASHBOARD) && npx --no-install tsc -b --noEmit

landing-dev: ## Start the landing dev server
	pnpm --dir site/landing dev

landing-check: ## Test, build and type-check the landing
	pnpm --dir site/landing test
	pnpm --dir site/landing build
	pnpm --dir site/landing typecheck

landing-deploy: landing-check ## Deploy the checked landing to Cloudflare Pages
	pnpm --dir site/landing exec wrangler pages deploy out
