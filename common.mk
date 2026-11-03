# Shared rules for tunes/<name>/Makefile. A tune sets FIXTURE, URL and MD5.
ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
FIX := $(ROOT)/fixtures
B := build
ifeq ($(origin CC),default)
CC := mos-c64-clang
endif
CFLAGS := -Os -Wall -Wextra -Werror
PY := PYTHONPATH=$(ROOT)/tools python3

fixture: $(FIX)/$(FIXTURE)

$(FIX)/$(FIXTURE):
	mkdir -p $(FIX)
	curl -fsSL -o $@.tmp '$(URL)'
	echo '$(MD5)  $@.tmp' | md5sum -c --quiet
	mv $@.tmp $@

clean:
	rm -rf $(B)

.PHONY: all check fixture clean
