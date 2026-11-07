# Shared rules for tunes/<name>/Makefile. A tune sets FIXTURE, URL and MD5; with
# GEN set before the include, also PSID and CHECK_ARGS (its sidtrace comparison).
ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
FIX := $(ROOT)/fixtures
B := build
ifeq ($(origin CC),default)
CC := mos-c64-clang
endif
CFLAGS := -Os -Wall -Wextra -Werror
PY := PYTHONPATH=$(ROOT)/tools python3
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

$(FIX)/$(FIXTURE):
	$(call fetch,$@,$(URL),$(MD5))

$(SONGLENGTHS):
	$(call fetch,$@,$(SONGLENGTHS_URL),$(SONGLENGTHS_MD5))

ifdef GEN
TUNE := $(notdir $(CURDIR))

all: $(B)/gen.prg

$(B)/gen/$(TUNE).c $(B)/gen/$(TUNE).h &: $(PSID) $(ROOT)/tools/transliterate.py
	mkdir -p $(B)/gen
	$(PY) $(ROOT)/tools/transliterate.py $< $(B)/gen/$(TUNE).c --header $(B)/gen/$(TUNE).h

$(B)/gen.prg: $(B)/gen/$(TUNE).c $(B)/gen/$(TUNE).h $(DRIVER)
	$(call mosbuild,$@,$(B)/gen,$<)

check: check-gen

# The hand C at -O0: each C read of its data is one load (tests/test_eventlog.py).
all: $(B)/hand-O0.prg

$(B)/hand-O0.prg: src/$(TUNE).c $(B)/$(TUNE)_data.h $(DRIVER)
	$(CC) -O0 -Wall -Wextra -Werror $(CFLAGS_TUNE) -I$(B) -I$(ROOT)/c64 -o $@ $< $(ROOT)/c64/driver.c

check-gen: $(B)/gen.prg
	$(PY) $(ROOT)/tools/sidtrace.py $(PSID) $< $(CHECK_ARGS)

.PHONY: check-gen
endif

clean:
	rm -rf $(B)

.PHONY: all check fixture clean
