FIXTURE := Hubbard_Rob_Commando.asm
URL := https://raw.githubusercontent.com/realdmx/c64_6581_sid_players/f215174fc4c983674808fcd47bc506f96b3d2d04/Hubbard_Rob/Hubbard_Rob_Commando.asm
MD5 := 6d7610849565daaaac28c251f7c600e0
PSID = $(B)/ref.sid
CHECK_ARGS = --wrap $(B)/ref.labels posoff
GEN := 1
include ../../common.mk

all: $(B)/commando.prg

$(B)/ref.sid $(B)/ref.labels &: $(FIX)/$(FIXTURE)
	mkdir -p $(B)
	acme -l $(B)/ref.labels -o $(B)/ref.sid $<

$(B)/commando_data.h: $(B)/ref.sid $(B)/ref.labels gen_data.py
	$(PY) gen_data.py $(B)/ref.sid $(B)/ref.labels $@

$(B)/commando.prg: src/commando.c $(B)/commando_data.h $(DRIVER)
	$(call mosbuild,$@,$(B),src/commando.c)

check: $(B)/commando.prg
	$(PY) $(ROOT)/tools/sidtrace.py $(PSID) $< $(CHECK_ARGS)
