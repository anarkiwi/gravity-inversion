TUNES := $(sort $(dir $(wildcard tunes/*/Makefile)))

all check fixture clean:
	for t in $(TUNES); do $(MAKE) -C $$t $@ || exit 1; done

test: all
	python3 -m pytest -n auto --cov=tools --cov=tunes --cov-report=term-missing

lint:
	clang-format --style=LLVM --dry-run -Werror $(wildcard tunes/*/src/*.[ch])
	black --check tools tests tunes
	pylint tools tests tunes

.PHONY: all check fixture clean test lint
