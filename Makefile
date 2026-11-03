ASM_URL := https://raw.githubusercontent.com/realdmx/c64_6581_sid_players/f215174fc4c983674808fcd47bc506f96b3d2d04/Hubbard_Rob/Hubbard_Rob_Commando.asm
CC := mos-c64-clang
CFLAGS := -Os -Wall -Wextra -Werror
B := build

all: $(B)/commando.prg

$(B)/commando.asm:
	mkdir -p $(B)
	curl -fsSL -o $@ $(ASM_URL)

$(B)/ref.sid $(B)/ref.labels &: $(B)/commando.asm
	acme -l $(B)/ref.labels -o $(B)/ref.sid $<

$(B)/commando_data.h: $(B)/ref.sid $(B)/ref.labels tools/gen_data.py
	python3 tools/gen_data.py $(B)/ref.sid $(B)/ref.labels $@

$(B)/commando.prg: src/main.c src/commando.c src/commando.h $(B)/commando_data.h
	$(CC) $(CFLAGS) -I$(B) -o $@ src/main.c src/commando.c

check: $(B)/commando.prg
	python3 tools/sidtrace.py $(B)/ref.sid $< $(B)/ref.labels

test: $(B)/commando.prg
	python3 -m pytest -n auto --cov=tools --cov-report=term-missing tests

lint:
	clang-format --style=LLVM --dry-run -Werror src/*.c src/*.h
	black --check tools tests
	pylint tools tests

clean:
	rm -rf $(B)

.PHONY: all check test lint clean
