# Repository entry points; each project keeps its own build and dependencies.
.DEFAULT_GOAL := all
SDL_ROOT ?= $(abspath $(CURDIR)/sdl)

.PHONY: all check test check-sdl check-ose check-chain test-sdl test-ose test-chain clean help
all:
	+$(MAKE) -C sdl all
check: check-sdl check-ose
test: test-sdl test-ose
check-sdl:
	+$(MAKE) -C sdl check
check-ose:
	+$(MAKE) -C ose check SDL_ROOT="$(SDL_ROOT)"
check-chain: check-ose
test-sdl:
	+$(MAKE) -C sdl test
test-ose:
	+$(MAKE) -C ose test SDL_ROOT="$(SDL_ROOT)"
test-chain: test-ose
clean:
	+$(MAKE) -C sdl clean
help:
	@echo 'make check       Run SDL and OSE checks'
	@echo 'make check-sdl   Run SDL backend checks'
	@echo 'make check-ose   Run OSE checks'
	@echo 'make test        Run both projects with verbose output'
	@echo 'Other targets are forwarded to the SDL project (for example test-java).'

# Retain the SDL commands previously invoked from the repository root.
%:
	+$(MAKE) -C sdl $@
