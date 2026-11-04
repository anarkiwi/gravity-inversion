FIXTURE := Emomyst.sid
URL = $(HVSC)/MUSICIANS/H/Hermit/Emomyst.sid
MD5 := 09ea86c3abf40489ddb72e0a2c8362b1
PSID = $(FIX)/$(FIXTURE)
CHECK_ARGS = --hvsc $(FIX) --times 2
GEN := 1
include ../../common.mk

fixture check-gen: $(SONGLENGTHS)

all: $(B)/emomyst.prg

$(B)/emomyst_data.h: $(PSID) gen_emomyst.py
	mkdir -p $(B)
	$(PY) gen_emomyst.py $< $@

$(B)/emomyst.prg: src/emomyst.c $(B)/emomyst_data.h $(DRIVER)
	$(call mosbuild,$@,$(B),src/emomyst.c)

check: $(B)/emomyst.prg $(SONGLENGTHS)
	$(PY) $(ROOT)/tools/sidtrace.py $(PSID) $< --hvsc $(FIX)
	$(PY) $(ROOT)/tools/sidtrace.py $(PSID) $< $(CHECK_ARGS)
