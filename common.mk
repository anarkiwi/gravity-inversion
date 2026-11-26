# Shared rules for tunes/<name>/Makefile. A tune sets FIXTURE, URL and MD5; with
# GEN set before the include, also PSID and CHECK_ARGS (its sidtrace comparison).
ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
FIX := $(ROOT)/fixtures
B := build
ifeq ($(origin CC),default)
CC := mos-c64-clang
endif
CFLAGS := -Os -Wall -Wextra -Werror
PY := PYTHONPATH=$(ROOT)/tools$(if $(PYTHONPATH),:$(PYTHONPATH)) python3
UNIT := -flto -Xclang -disable-llvm-passes -c
MERGE := -flto -r -nostdlib -Wl,--lto-O0,--lto-emit-llvm
DRIVER := $(ROOT)/c64/driver.c $(ROOT)/c64/driver.h
SONGLENGTHS := $(FIX)/DOCUMENTS/Songlengths.md5
SONGLENGTHS_URL := https://www.hvsc.c64.org/download/C64Music/DOCUMENTS/Songlengths.md5
SONGLENGTHS_MD5 := 792a3e49c08f3c9908d3a5f097a6fb3b
HVSC := https://www.hvsc.c64.org/download/C64Music

# $(call fetch,file,url,md5)
define fetch
	mkdir -p $(dir $(1))
	curl -fsSL -o $(1).tmp '$(2)'
	echo '$(3)  $(1).tmp' | md5sum -c --quiet
	mv $(1).tmp $(1)
endef

# $(call mosbuild,out.prg,include dir,sources): C64 PRG with the shared driver
define mosbuild
	$(CC) $(CFLAGS) $(CFLAGS_TUNE) -I$(2) -I$(ROOT)/c64 -o $(1) $(3) $(ROOT)/c64/driver.c
endef

fixture: $(FIX)/$(FIXTURE)

# The PSID the generated translation is lifted from.
psid: $(PSID)

$(FIX)/$(FIXTURE):
	$(call fetch,$@,$(URL),$(MD5))

$(SONGLENGTHS):
	$(call fetch,$@,$(SONGLENGTHS_URL),$(SONGLENGTHS_MD5))

ifdef GEN
TUNE := $(notdir $(CURDIR))

all: $(B)/gen.prg

# The installed lifter's identity, rewritten only when it changes: a pin or wheel change
# regenerates the C.
$(B)/lifter.stamp: FORCE
	$(PY) $(ROOT)/tools/lifterstamp.py $@

# The translation units, their paths listed in $(B)/gen/units.
$(B)/gen/units $(B)/gen/$(TUNE).h $(B)/gen/cgen_trap.c $(B)/gen/overruns.json &: $(PSID) $(ROOT)/tools/transliterate.py $(B)/lifter.stamp
	$(PY) $(ROOT)/tools/transliterate.py $< $(B)/gen --header $(TUNE).h --list $(B)/gen/units --overruns $(B)/gen/overruns.json

# Each unit's bitcode, left for the whole program's optimization, linked into one module
# (tools/transliterate.py UNIT and MERGE): the PRG is optimized as one file is.
$(B)/gen.bc: $(B)/gen/units $(B)/gen/$(TUNE).h
	for u in $$(cat $(B)/gen/units); do \
	  $(CC) $(CFLAGS) $(CFLAGS_TUNE) -I$(B)/gen -I$(ROOT)/c64 $(UNIT) -o $$u.bc $$u || exit 1; \
	done
	$(CC) $(MERGE) -o $@ $$(sed 's/$$/.bc/' $(B)/gen/units)

$(B)/gen.prg: $(B)/gen.bc $(B)/gen/cgen_trap.c $(DRIVER)
	$(call mosbuild,$@,$(B)/gen,$< $(B)/gen/cgen_trap.c)

check: check-gen

# The hand C at -O0: each C read of its data is one load (tests/test_eventlog.py).
all: $(B)/hand-O0.prg

$(B)/hand-O0.prg: src/$(TUNE).c $(B)/$(TUNE)_data.h $(DRIVER)
	$(CC) -O0 -Wall -Wextra -Werror $(CFLAGS_TUNE) -I$(B) -I$(ROOT)/c64 -o $@ $< $(ROOT)/c64/driver.c

check-gen: $(B)/gen.prg $(B)/gen/overruns.json
	$(PY) $(ROOT)/tools/sidtrace.py $(PSID) $< $(CHECK_ARGS) --overruns $(B)/gen/overruns.json \
	  $(if $(wildcard overruns_allowed.json),--allowed overruns_allowed.json)

.PHONY: check-gen
endif

clean:
	rm -rf $(B)

.PHONY: all check fixture clean psid FORCE
FORCE:
