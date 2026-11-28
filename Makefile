TUNES := $(sort $(dir $(wildcard tunes/*/Makefile)))
GT2 := fixtures/gt2_276.zip
GT2_URL := https://csdb.dk/getinternalfile.php/213485/GoatTracker_2.76.zip
GT2_SHA256 := c1b6b159ec0d37ae68599ac83be8934a71cd543e480eb5225f844b62151cea34

all check fixture clean psid:
	for t in $(TUNES); do $(MAKE) -C $$t $@ || exit 1; done

test: all
	python3 -m pytest -n auto --cov=tools --cov=tunes --cov-report=term-missing

fixture: $(GT2)

$(GT2):
	mkdir -p $(dir $@)
	curl -fsSL -o $@.tmp '$(GT2_URL)'
	echo '$(GT2_SHA256)  $@.tmp' | sha256sum -c --quiet
	mv $@.tmp $@

songs-smoke: $(GT2)
	PYTHONPATH=tools python3 tools/gensongs.py smoke out/songs-smoke

readability: psid
	python3 tools/readability.py
	virtuous-cycles -m readability check $(if $(AGAINST),--against $(AGAINST))

prg: all
	virtuous-cycles -m prg check $(if $(AGAINST),--against $(AGAINST))

lint:
	clang-format --style=LLVM --dry-run -Werror $(wildcard tunes/*/src/*.[ch])
	black --check tools tests tunes
	pylint tools tests tunes

.PHONY: all check fixture clean psid test lint readability prg songs-smoke
