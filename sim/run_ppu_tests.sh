#!/bin/sh
set -e
cd "$(dirname "$0")"
python3 gen_ppu_cases.py
iverilog -g2005 -Wall -Wno-timescale -o out/tb_ppu.vvp tb_ppu.v ../src/ppu.v ../src/vga_timing.v
rm -f out/tb_results.txt out/case*_rtl.hex out/case*_cmp.png
vvp -n out/tb_ppu.vvp > out/vvp.log 2>&1 || { cat out/vvp.log; exit 1; }
cat out/vvp.log
python3 ppu_model.py "$@"
