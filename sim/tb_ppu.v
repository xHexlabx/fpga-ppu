`timescale 1ns/1ns
`default_nettype none

module tb_ppu;

    reg clk = 1'b0;
    always #5 clk = ~clk;

    reg         rst      = 1'b1;
    reg         we       = 1'b0;
    reg  [12:0] waddr    = 13'd0;
    reg  [31:0] wdata    = 32'd0;
    reg  [11:0] nt_raddr = 12'd0;
    wire [9:0]  nt_rdata;
    wire        frame_start, vblank, sprite_overflow;
    wire [10:0] bg_chr_addr, sp_chr_addr;
    reg  [15:0] bg_chr_data, sp_chr_data;
    wire [5:0]  rgb;
    wire        hsync, vsync, de;

    ppu dut (
        .clk             (clk),
        .rst             (rst),
        .we              (we),
        .waddr           (waddr),
        .wdata           (wdata),
        .nt_raddr        (nt_raddr),
        .nt_rdata        (nt_rdata),
        .frame_start     (frame_start),
        .vblank          (vblank),
        .sprite_overflow (sprite_overflow),
        .bg_chr_addr     (bg_chr_addr),
        .bg_chr_data     (bg_chr_data),
        .sp_chr_addr     (sp_chr_addr),
        .sp_chr_data     (sp_chr_data),
        .rgb             (rgb),
        .hsync           (hsync),
        .vsync           (vsync),
        .de              (de)
    );

    reg [15:0] bg_rom [0:2047];
    reg [15:0] sp_rom [0:2047];

    always @(posedge clk) begin
        bg_chr_data <= bg_rom[bg_chr_addr];
        sp_chr_data <= sp_rom[sp_chr_addr];
    end

    reg [31:0] c_vram [0:4095];
    reg [31:0] c_oam  [0:63];
    reg [31:0] c_pal  [0:31];
    reg [31:0] c_regs [0:3];
    reg [31:0] ncases_mem [0:0];

    integer cyc = 0;
    always @(posedge clk) cyc <= cyc + 1;

    reg [5:0] pix [0:307199];
    reg       cap_on  = 1'b0;
    integer   cap_idx = 0;

    always @(negedge clk) begin
        if (cap_on && de === 1'b1) begin
            if (cap_idx < 307200) pix[cap_idx] = rgb;
            cap_idx = cap_idx + 1;
        end
    end

    integer sync_err   = 0;
    integer hs_run     = 0;  // clocks since last hsync change
    integer vs_run     = 0;
    integer de_run     = 0;
    integer hs_edges   = 0;
    integer vs_edges   = 0;
    integer de_edges   = 0;
    integer de_frame   = 0;  // de clocks since the last vsync fall
    integer de_lines   = 0;  // de runs since the last vsync fall
    integer frames_ok  = 0;
    integer last_hs_rise = -1;
    integer last_hs_fall = -1;
    integer last_vs_fall = -1;
    reg     first_de_in_frame = 1'b0;
    reg     hs_d = 1'b1, vs_d = 1'b1, de_d = 1'b0;
    reg     sync_live = 1'b0;

    task sync_fail;
        input [8*48-1:0] what;
        input integer    got;
        begin
            sync_err = sync_err + 1;
            if (sync_err <= 20)
                $display("SYNC ERROR @cyc %0d: %0s (got %0d)", cyc, what, got);
        end
    endtask

    always @(negedge clk) begin
        if (sync_live) begin
            if (de !== 1'b1 && rgb !== 6'd0) sync_fail("rgb not 0 outside de", rgb);
            if (hsync === 1'bx || vsync === 1'bx || de === 1'bx)
                sync_fail("X on hsync/vsync/de", 0);

            if (hsync !== hs_d) begin
                if (hs_edges > 0) begin
                    if (hs_d === 1'b0 && hs_run != 96)  sync_fail("hsync low length", hs_run);
                    if (hs_d === 1'b1 && hs_run != 704) sync_fail("hsync high length", hs_run);
                end
                if (hsync === 1'b1) last_hs_rise = cyc;
                else                last_hs_fall = cyc;
                hs_edges = hs_edges + 1;
                hs_run   = 1;
            end else begin
                hs_run = hs_run + 1;
            end

            if (vsync !== vs_d) begin
                if (vs_edges > 0) begin
                    if (vs_d === 1'b0 && vs_run != 1600)   sync_fail("vsync low length", vs_run);
                    if (vs_d === 1'b1 && vs_run != 418400) sync_fail("vsync high length", vs_run);
                end
                if (last_hs_fall >= 0 && cyc - last_hs_fall != 144)
                    sync_fail("vsync edge vs hsync fall", cyc - last_hs_fall);
                if (vsync === 1'b0) begin
                    if (last_vs_fall >= 0) begin
                        if (de_frame != 307200) sync_fail("de clocks per frame", de_frame);
                        if (de_lines != 480)    sync_fail("de lines per frame", de_lines);
                        frames_ok = frames_ok + 1;
                    end
                    last_vs_fall      = cyc;
                    de_frame          = 0;
                    de_lines          = 0;
                    first_de_in_frame = 1'b1;
                end
                vs_edges = vs_edges + 1;
                vs_run   = 1;
            end else begin
                vs_run = vs_run + 1;
            end

            if (de !== de_d) begin
                if (de_edges > 0) begin
                    if (de_d === 1'b1 && de_run != 640) sync_fail("de high length", de_run);
                    if (de_d === 1'b0 && de_run != 160 && de_run != 36160)
                        sync_fail("de low length", de_run);
                end
                if (de === 1'b1) begin
                    de_lines = de_lines + 1;
                    if (last_hs_rise >= 0 && cyc - last_hs_rise != 48)
                        sync_fail("de rise vs hsync rise (back porch)", cyc - last_hs_rise);
                    if (first_de_in_frame) begin
                        if (cyc - last_vs_fall != 28000)
                            sync_fail("first de vs vsync fall", cyc - last_vs_fall);
                        first_de_in_frame = 1'b0;
                    end
                end
                de_edges = de_edges + 1;
                de_run   = 1;
            end else begin
                de_run = de_run + 1;
            end
            if (de === 1'b1) de_frame = de_frame + 1;
        end
        hs_d = hsync;
        vs_d = vsync;
        de_d = de;
    end

    integer build_err = 0;
    integer bcount    = 0;
    integer max_build = 0;
    integer min_build = 1 << 30;
    integer builds    = 0;
    reg     building  = 1'b0;

    always @(posedge clk) begin
        if (!rst) begin
            if (dut.line_go) begin
                if (dut.rs !== 2'd0) begin
                    build_err = build_err + 1;
                    if (build_err <= 10)
                        $display("BUILD ERROR @cyc %0d: line_go at y=%0d but rs=%0d",
                                 cyc, dut.u_timing.y, dut.rs);
                end
                building = 1'b1;
                bcount   = 0;
            end else if (building) begin
                if (dut.rs === 2'd0) begin
                    building = 1'b0;
                    builds   = builds + 1;
                    if (bcount > max_build) max_build = bcount;
                    if (bcount < min_build) min_build = bcount;
                end else begin
                    bcount = bcount + 1;
                    if (bcount >= 1600) begin
                        build_err = build_err + 1;
                        if (build_err <= 10)
                            $display("BUILD ERROR @cyc %0d: build longer than 1600", cyc);
                        building = 1'b0;
                    end
                end
            end
            if (dut.u_timing.x == 10'd0 &&
                (dut.u_timing.y == 10'd478 || dut.u_timing.y == 10'd0) &&
                dut.rs !== 2'd0) begin
                build_err = build_err + 1;
                if (build_err <= 10)
                    $display("BUILD ERROR @cyc %0d: still building at y=%0d x=0",
                             cyc, dut.u_timing.y);
            end
        end
    end

    integer   ncases, c, i, lx, ly, base;
    integer   nonuni, rb_err, late, de_cnt, ovf, fd, fsum;
    reg [5:0] p00, p01, p10, p11;
    reg [8*96-1:0] fn;
    reg [31:0] w;

    task wait_frame_start;
        begin
            @(negedge clk);
            while (frame_start !== 1'b1) @(negedge clk);
        end
    endtask

    task bus_write;
        input [12:0] a;
        input [31:0] d;
        begin
            we    = 1'b1;
            waddr = a;
            wdata = d;
            @(negedge clk);
        end
    endtask

    task finish_case;
        input integer n;
        begin
            cap_on = 1'b0;
            de_cnt = cap_idx;
            nonuni = 0;
            $sformat(fn, "out/case%0d_rtl.hex", n);
            fd = $fopen(fn, "w");
            for (ly = 0; ly < 240; ly = ly + 1) begin
                for (lx = 0; lx < 320; lx = lx + 1) begin
                    base = (2 * ly) * 640 + 2 * lx;
                    p00 = pix[base];
                    p01 = pix[base + 1];
                    p10 = pix[base + 640];
                    p11 = pix[base + 641];
                    if (p00 !== p01 || p00 !== p10 || p00 !== p11) begin
                        nonuni = nonuni + 1;
                        if (nonuni <= 5)
                            $display("case %0d: 2x2 block (%0d,%0d) not uniform: %h %h / %h %h",
                                     n, lx, ly, p00, p01, p10, p11);
                    end
                    $fwrite(fd, "%h", p00);
                    if (lx < 319) $fwrite(fd, " ");
                end
                $fwrite(fd, "\n");
            end
            $fclose(fd);
        end
    endtask

    initial begin
        #1;
        #((ncases + 4) * 64'd420000 * 64'd10);
        $display("TIMEOUT");
        if (fsum != 0) begin
            $fdisplay(fsum, "global timeout 1");
            $fclose(fsum);
        end
        $finish;
    end

    initial begin
        fsum = 0;
        $readmemh("out/bg_chr.hex", bg_rom);
        $readmemh("out/sp_chr.hex", sp_rom);
        $readmemh("out/ncases.hex", ncases_mem);
        ncases = ncases_mem[0];
        fsum = $fopen("out/tb_results.txt", "w");
        $display("tb_ppu: %0d cases", ncases);

        repeat (4) @(negedge clk);
        rst = 1'b0;
        repeat (4) @(negedge clk);
        sync_live = 1'b1;

        wait_frame_start;
        for (c = 0; c <= ncases; c = c + 1) begin
            if (c > 0) finish_case(c - 1);
            if (c < ncases) begin
                $sformat(fn, "out/case%0d_vram.hex", c); $readmemh(fn, c_vram);
                $sformat(fn, "out/case%0d_oam.hex",  c); $readmemh(fn, c_oam);
                $sformat(fn, "out/case%0d_pal.hex",  c); $readmemh(fn, c_pal);
                $sformat(fn, "out/case%0d_regs.hex", c); $readmemh(fn, c_regs);
            end
            @(negedge clk);
            if (c > 0) begin
                ovf = sprite_overflow;
                $fdisplay(fsum, "case %0d overflow %0d nonuniform %0d de_count %0d readback_err %0d late_writes %0d",
                          c - 1, ovf, nonuni, de_cnt, rb_err, late);
                $display("case %0d done: overflow %0d, nonuniform %0d, de %0d, readback errors %0d, late %0d",
                         c - 1, ovf, nonuni, de_cnt, rb_err, late);
            end
            if (c < ncases) begin
                for (i = 0; i < 4096; i = i + 1) bus_write(i[12:0], c_vram[i]);
                for (i = 0; i < 64; i = i + 1)   bus_write(13'h1000 + i[12:0], c_oam[i]);
                for (i = 0; i < 32; i = i + 1)   bus_write(13'h1040 + i[12:0], c_pal[i]);
                for (i = 0; i < 4; i = i + 1)    bus_write(13'h1060 + i[12:0], c_regs[i]);
                we    = 1'b0;
                waddr = 13'd0;
                wdata = 32'd0;

                rb_err   = 0;
                nt_raddr = 12'd0;
                @(negedge clk);
                for (i = 1; i <= 4096; i = i + 1) begin
                    w = c_vram[i - 1];
                    if (nt_rdata !== w[9:0]) begin
                        rb_err = rb_err + 1;
                        if (rb_err <= 3)
                            $display("case %0d: VRAM read-back %h = %h, expected %h",
                                     c, i - 1, nt_rdata, w[9:0]);
                    end
                    if (i < 4096) nt_raddr = i[11:0];
                    @(negedge clk);
                end

                late = !(dut.u_timing.y >= 10'd480 && dut.u_timing.y < 10'd523);
                if (late) $display("case %0d: writes finished late, y=%0d", c, dut.u_timing.y);

                cap_idx = 0;
                cap_on  = 1'b1;
                wait_frame_start;
            end
        end

        while (vsync !== 1'b0) @(negedge clk);
        repeat (4) @(negedge clk);

        $fdisplay(fsum, "global cycles %0d frames_checked %0d sync_err %0d build_err %0d max_build %0d min_build %0d builds %0d",
                  cyc, frames_ok, sync_err, build_err, max_build, min_build, builds);
        $display("tb_ppu: frames %0d, sync errors %0d, build errors %0d, build length %0d..%0d clocks over %0d lines",
                 frames_ok, sync_err, build_err, min_build, max_build, builds);
        $fclose(fsum);
        fsum = 0;
        $finish;
    end

endmodule

`default_nettype wire
