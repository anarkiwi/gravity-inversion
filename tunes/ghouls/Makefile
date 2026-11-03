FIXTURE := Ghouls_n_Ghosts.sid
URL := $(HVSC)/MUSICIANS/F/Follin_Tim/Ghouls_n_Ghosts.sid
MD5 := 87e0a687004730aaf3d2e6f0d450dec7
include ../../common.mk

all: $(B)/ghouls.prg

fixture: $(SONGLENGTHS)

$(B)/ghouls_data.h: $(FIX)/$(FIXTURE) gen_ghouls.py
	mkdir -p $(B)
	$(PY) gen_ghouls.py $< $@

$(B)/ghouls.prg: src/ghouls.c $(B)/ghouls_data.h $(DRIVER)
	$(call mosbuild,$@,$(B),src/ghouls.c)

check: $(B)/ghouls.prg $(SONGLENGTHS)
	$(PY) $(ROOT)/tools/sidtrace.py $(FIX)/$(FIXTURE) $< --songs 0-31 --songlengths $(SONGLENGTHS)
