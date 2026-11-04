FIXTURE := Incoherent_Nightmare_tune_2.sid
URL = $(HVSC)/MUSICIANS/L/Linus/Incoherent_Nightmare_tune_2.sid
MD5 := c7706a9ce8f49179d8815846c6c21946
PSID = $(FIX)/$(FIXTURE)
CHECK_ARGS = --hvsc $(FIX)
GEN := 1
include ../../common.mk

all: $(B)/incoherent.prg

$(B)/incoherent_data.h: $(FIX)/$(FIXTURE) gen_incoherent.py
	mkdir -p $(B)
	$(PY) gen_incoherent.py $< $@

$(B)/incoherent.prg: src/incoherent.c $(B)/incoherent_data.h $(DRIVER)
	$(call mosbuild,$@,$(B),src/incoherent.c)

fixture check-gen: $(SONGLENGTHS)

check: $(B)/incoherent.prg $(SONGLENGTHS)
	$(PY) $(ROOT)/tools/sidtrace.py $(FIX)/$(FIXTURE) $< --hvsc $(FIX)
