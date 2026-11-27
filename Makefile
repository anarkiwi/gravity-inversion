TUNES := $(sort $(dir $(wildcard tunes/*/Makefile)))
GEN_TUNES := $(notdir $(patsubst %/Makefile,%,$(shell grep -lE '^GEN[[:space:]]*:?=[[:space:]]*1[[:space:]]*$$' tunes/*/Makefile)))

all check fixture clean psid:
	for t in $(TUNES); do $(MAKE) -C $$t $@ || exit 1; done

test: all
	python3 -m pytest -n auto --cov=tools --cov=tunes --cov-report=term-missing

readability: psid
	python3 tools/readability.py
	virtuous-cycles -m readability check $(if $(AGAINST),--against $(AGAINST))

prg: all
	virtuous-cycles -m prg check $(if $(AGAINST),--against $(AGAINST))

bounds-c: psid
	python3 tools/readability.py $(GEN_TUNES)

bounds: bounds-c
	python3 tools/eva.py $(if $(FRAMA_C),--frama-c $(FRAMA_C)) $(GEN_TUNES)
	virtuous-cycles -m bounds check $(if $(AGAINST),--against $(AGAINST))

lint:
	clang-format --style=LLVM --dry-run -Werror $(wildcard tunes/*/src/*.[ch])
	black --check tools tests tunes
	pylint tools tests tunes

.PHONY: all check fixture clean psid test lint readability prg bounds bounds-c
