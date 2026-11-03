FIXTURE := Automatas.sid
URL = $(HVSC)/MUSICIANS/G/Goto80/Automatas.sid
MD5 := ececef3d224ba2af839cf1b8d4cd6760
CFLAGS_TUNE := -DFRAME_CIA
include ../../common.mk

all: $(B)/automatas.prg

$(B)/automatas_data.h: $(FIX)/$(FIXTURE) gen_automatas.py
	mkdir -p $(B)
	$(PY) gen_automatas.py $< $@

$(B)/automatas.prg: src/automatas.c $(B)/automatas_data.h $(DRIVER)
	$(call mosbuild,$@,$(B),src/automatas.c)

fixture: $(SONGLENGTHS)

check: $(B)/automatas.prg $(SONGLENGTHS)
	$(PY) $(ROOT)/tools/sidtrace.py $(FIX)/$(FIXTURE) $< --hvsc $(FIX)
