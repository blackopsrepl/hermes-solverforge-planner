# SolverForge Planner for Hermes — Makefile
# Engine (Rust) + Hermes plugin (Python) + Desktop half, with colorized output.

# ============== Colors & Symbols ==============
GREEN   := \033[92m
EMERALD := \033[38;2;16;185;129m
CYAN    := \033[96m
YELLOW  := \033[93m
RED     := \033[91m
GRAY    := \033[90m
BOLD    := \033[1m
RESET   := \033[0m

CHECK    := ✓
CROSS    := ✗
ARROW    := ▸
PROGRESS := →

# ============== Project Metadata ==============
VERSION        := $(shell grep -m1 '^version:' plugin.yaml | cut -d' ' -f2)
ENGINE_VERSION := $(shell grep -m1 '^version' engine/Cargo.toml | cut -d'"' -f2)
ENGINE_BIN     := engine/target/release/solverforge-planner
TARGETS        := x86_64-unknown-linux-musl aarch64-unknown-linux-musl \
                  x86_64-apple-darwin aarch64-apple-darwin x86_64-pc-windows-gnu
PYTHON         ?= 3.12
# The plugin's runtime libraries come from plugin.yaml, the same list Hermes installs.
PLUGIN_DEPS    := $(shell sed -n '/^python_dependencies:/,/^[a-z_]*:/s/^  - "\(.*\)"$$/--with "\1"/p' plugin.yaml)
PYTEST         := uv run --no-project --python $(PYTHON) --with pytest $(PLUGIN_DEPS) python -m pytest

# ============== Phony Targets ==============
.PHONY: banner help setup engine test test-engine test-plugin test-desktop lint fmt fmt-check clippy \
        validate check dist dist-all install-dev uninstall-dev version clean

.DEFAULT_GOAL := help

# ============== Banner ==============
banner:
	@if command -v figlet >/dev/null 2>&1; then \
		figlet -f small "SF Planner" | { printf "$(EMERALD)$(BOLD)"; cat; printf "$(RESET)"; }; \
	else \
		printf "$(EMERALD)$(BOLD)SolverForge Planner$(RESET)\n"; \
	fi
	@printf "  $(GRAY)plugin v$(VERSION) · engine v$(ENGINE_VERSION)$(RESET) $(EMERALD)for Hermes Agent$(RESET)\n\n"

# ============== Help ==============
help: banner
	@printf "$(CYAN)$(BOLD)Develop$(RESET)\n"
	@printf "  $(ARROW) $(BOLD)setup$(RESET)         Check the toolchain: Rust 1.95+, uv, Hermes\n"
	@printf "  $(ARROW) $(BOLD)engine$(RESET)        Build the engine binary (release)\n"
	@printf "  $(ARROW) $(BOLD)install-dev$(RESET)   Install this checkout into Hermes, using the local engine\n"
	@printf "  $(ARROW) $(BOLD)uninstall-dev$(RESET) Remove the plugin from Hermes\n\n"
	@printf "$(CYAN)$(BOLD)Verify$(RESET)\n"
	@printf "  $(ARROW) $(BOLD)check$(RESET)         The full gate: lint, engine tests, plugin tests, Hermes validation\n"
	@printf "  $(ARROW) $(BOLD)test$(RESET)          Engine, plugin and Desktop tests\n"
	@printf "  $(ARROW) $(BOLD)test-engine$(RESET)   Engine unit, incremental-score and CLI tests\n"
	@printf "  $(ARROW) $(BOLD)test-plugin$(RESET)   Plugin end-to-end tests against the real engine\n"
	@printf "  $(ARROW) $(BOLD)test-desktop$(RESET)  Render the Desktop half against a stand-in SDK (Node 22+)\n"
	@printf "  $(ARROW) $(BOLD)lint$(RESET)          rustfmt and clippy\n"
	@printf "  $(ARROW) $(BOLD)fmt$(RESET)           Format the engine\n"
	@printf "  $(ARROW) $(BOLD)validate$(RESET)      hermes plugins validate + doctor (catalog gate)\n\n"
	@printf "$(CYAN)$(BOLD)Release$(RESET)\n"
	@printf "  $(ARROW) $(BOLD)dist$(RESET)          Cross-build one release binary: TARGET=<triple>\n"
	@printf "  $(ARROW) $(BOLD)dist-all$(RESET)      Cross-build every release target into dist/\n"
	@printf "  $(ARROW) $(BOLD)version$(RESET)       Print plugin and engine versions\n"
	@printf "  $(ARROW) $(BOLD)clean$(RESET)         Remove build output\n\n"
	@printf "$(GRAY)Targets: $(TARGETS)$(RESET)\n"

# ============== Develop ==============
setup: banner
	@printf "$(ARROW) $(BOLD)Checking the toolchain...$(RESET)\n"
	@fail=0; \
	if command -v cargo >/dev/null 2>&1; then \
		printf "  $(GREEN)$(CHECK)$(RESET) cargo   $(GRAY)%s$(RESET)\n" "$$(rustc --version)"; \
	else printf "  $(RED)$(CROSS)$(RESET) cargo   $(GRAY)install Rust 1.95+ from https://rustup.rs$(RESET)\n"; fail=1; fi; \
	if command -v uv >/dev/null 2>&1; then \
		printf "  $(GREEN)$(CHECK)$(RESET) uv      $(GRAY)%s$(RESET)\n" "$$(uv --version)"; \
	else printf "  $(RED)$(CROSS)$(RESET) uv      $(GRAY)install from https://docs.astral.sh/uv$(RESET)\n"; fail=1; fi; \
	if command -v hermes >/dev/null 2>&1; then \
		printf "  $(GREEN)$(CHECK)$(RESET) hermes  $(GRAY)%s$(RESET)\n" "$$(hermes --version 2>/dev/null | head -1)"; \
	else printf "  $(YELLOW)!$(RESET) hermes  $(GRAY)optional: needed for validate and install-dev$(RESET)\n"; fi; \
	if command -v cargo-zigbuild >/dev/null 2>&1; then \
		printf "  $(GREEN)$(CHECK)$(RESET) zigbuild $(GRAY)%s$(RESET)\n" "$$(cargo-zigbuild --version)"; \
	else printf "  $(YELLOW)!$(RESET) zigbuild $(GRAY)optional: pip install ziglang && cargo install cargo-zigbuild (for dist)$(RESET)\n"; fi; \
	if [ $$fail -eq 0 ]; then printf "$(GREEN)$(CHECK) Toolchain ready$(RESET)\n"; else printf "$(RED)$(CROSS) Toolchain incomplete$(RESET)\n"; exit 1; fi

engine: banner
	@printf "$(ARROW) $(BOLD)Building the engine...$(RESET)\n"
	@cd engine && cargo build --release --locked && \
		printf "$(GREEN)$(CHECK) Built $(ENGINE_BIN)$(RESET)\n" || \
		(printf "$(RED)$(CROSS) Engine build failed$(RESET)\n" && exit 1)

install-dev: engine
	@printf "$(ARROW) $(BOLD)Installing this checkout into Hermes...$(RESET)\n"
	@hermes plugins install "file://$(CURDIR)" --force --enable >/dev/null && \
		hermes config set plugins.entries.solverforge-planner.settings.binary "$(CURDIR)/$(ENGINE_BIN)" >/dev/null && \
		printf "$(GREEN)$(CHECK) Installed; engine: $(ENGINE_BIN)$(RESET)\n$(GRAY)  Restart running sessions or the gateway to load it.$(RESET)\n" || \
		(printf "$(RED)$(CROSS) Install failed$(RESET)\n" && exit 1)

uninstall-dev: banner
	@printf "$(ARROW) $(BOLD)Removing the plugin from Hermes...$(RESET)\n"
	@hermes plugins remove solverforge-planner >/dev/null && \
		printf "$(GREEN)$(CHECK) Removed (planner data in plugin-data is kept)$(RESET)\n" || \
		(printf "$(RED)$(CROSS) Remove failed$(RESET)\n" && exit 1)

# ============== Verify ==============
fmt:
	@cd engine && cargo fmt

fmt-check:
	@printf "$(ARROW) $(BOLD)Checking formatting...$(RESET)\n"
	@cd engine && cargo fmt --check && \
		printf "$(GREEN)$(CHECK) Formatting valid$(RESET)\n" || \
		(printf "$(RED)$(CROSS) Formatting issues; run make fmt$(RESET)\n" && exit 1)

clippy:
	@printf "$(ARROW) $(BOLD)Running clippy...$(RESET)\n"
	@cd engine && cargo clippy --release --all-targets --locked -- -D warnings && \
		printf "$(GREEN)$(CHECK) Clippy passed$(RESET)\n" || \
		(printf "$(RED)$(CROSS) Clippy failed$(RESET)\n" && exit 1)

lint: fmt-check clippy

test-engine:
	@printf "$(ARROW) $(BOLD)Engine tests...$(RESET)\n"
	@cd engine && cargo test --release --locked --quiet && \
		printf "$(GREEN)$(CHECK) Engine tests passed$(RESET)\n" || \
		(printf "$(RED)$(CROSS) Engine tests failed$(RESET)\n" && exit 1)

test-plugin:
	@printf "$(ARROW) $(BOLD)Plugin end-to-end tests (Python $(PYTHON))...$(RESET)\n"
	@cd engine && cargo build --release --locked --quiet
	@SOLVERFORGE_PLANNER_BIN="$(CURDIR)/$(ENGINE_BIN)" $(PYTEST) -q && \
		printf "$(GREEN)$(CHECK) Plugin tests passed$(RESET)\n" || \
		(printf "$(RED)$(CROSS) Plugin tests failed$(RESET)\n" && exit 1)

test-desktop:
	@printf "$(ARROW) $(BOLD)Desktop render tests...$(RESET)\n"
	@cd tests/desktop && npm ci --no-audit --no-fund --silent && node --test && \
		printf "$(GREEN)$(CHECK) Desktop tests passed$(RESET)\n" || \
		(printf "$(RED)$(CROSS) Desktop tests failed$(RESET)\n" && exit 1)

test: test-engine test-plugin test-desktop

validate:
	@printf "$(ARROW) $(BOLD)Hermes catalog validation...$(RESET)\n"
	@if ! command -v hermes >/dev/null 2>&1; then \
		printf "$(YELLOW)! hermes not installed; skipped$(RESET)\n"; exit 0; fi; \
	tmp=$$(mktemp -d); trap 'rm -rf "$$tmp"' EXIT; \
	git archive HEAD | tar -x -C "$$tmp" && \
	hermes plugins validate "$$tmp" >"$$tmp.validate.log" 2>&1 && \
	hermes plugins doctor "$$tmp" --ci >"$$tmp.doctor.log" 2>&1 && \
	{ rm -f "$$tmp".*.log; printf "$(GREEN)$(CHECK) validate and doctor passed (committed tree)$(RESET)\n"; } || \
	{ cat "$$tmp".*.log; rm -f "$$tmp".*.log; printf "$(RED)$(CROSS) Validation failed$(RESET)\n"; exit 1; }

check: banner lint test validate
	@printf "\n$(GREEN)$(BOLD)$(CHECK) All checks passed$(RESET)\n"

# ============== Release ==============
dist: banner
	@test -n "$(TARGET)" || { printf "$(RED)$(CROSS) Pass TARGET=<triple>, one of: $(TARGETS)$(RESET)\n"; exit 1; }
	@printf "$(PROGRESS) $(BOLD)Cross-building $(TARGET)...$(RESET)\n"
	@scripts/build-engine.sh "$(TARGET)" >/dev/null && \
		printf "$(GREEN)$(CHECK) dist/solverforge-planner-$(TARGET)$(RESET)\n" || \
		(printf "$(RED)$(CROSS) $(TARGET) failed$(RESET)\n" && exit 1)

dist-all: banner
	@for t in $(TARGETS); do \
		printf "$(PROGRESS) $(BOLD)Cross-building $$t...$(RESET)\n"; \
		scripts/build-engine.sh "$$t" >/dev/null 2>&1 && \
			printf "$(GREEN)$(CHECK) $$t$(RESET)\n" || \
			{ printf "$(RED)$(CROSS) $$t failed (run: make dist TARGET=$$t)$(RESET)\n"; exit 1; }; \
	done
	@cd dist && sha256sum solverforge-planner-* > SHA256SUMS && \
		printf "$(GREEN)$(CHECK) dist/SHA256SUMS$(RESET)\n"

version:
	@printf "plugin $(VERSION)\nengine $(ENGINE_VERSION)\n"

clean:
	@printf "$(ARROW) $(BOLD)Cleaning...$(RESET)\n"
	@cd engine && cargo clean
	@rm -rf dist .pytest_cache
	@find . -name __pycache__ -type d -prune -exec rm -rf {} +
	@printf "$(GREEN)$(CHECK) Clean$(RESET)\n"
