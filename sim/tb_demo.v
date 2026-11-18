`timescale 1ns/1ps
module tb_demo;

    reg        clk = 1'b0;
    always #20 clk = ~clk;

    reg        rst_n = 1'b0;
    reg  [7:0] ui_in = 8'h00;
    wire [7:0] uo_out, uio_out, uio_oe;

    tt_um_hextex_ppu_demo dut (
        .ui_in(ui_in), .uo_out(uo_out), .uio_in(8'h00), .uio_out(uio_out),
        .uio_oe(uio_oe), .ena(1'b1), .clk(clk), .rst_n(rst_n)
    );

    integer errors = 0;

    localparam [11:0] B_B = 12'h001, B_Y = 12'h002, B_SEL = 12'h004, B_START = 12'h008,
                      B_UP = 12'h010, B_DOWN = 12'h020, B_LEFT = 12'h040, B_RIGHT = 12'h080,
                      B_A = 12'h100, B_X = 12'h200;
    reg        gp_en = 0;
    reg [11:0] gp_value = 0;
    integer    gp_counter = 0, cycle, data_index;
    reg [23:0] data_reg;

    always @(posedge uo_out[7]) begin        // hsync going inactive: a line starts
        if (gp_en) begin
            cycle      = gp_counter % 400;
            gp_counter = gp_counter + 1;
            data_reg   = {gp_value, 12'h000};
            data_index = (cycle < 49) ? (cycle >> 1) : 0;
            ui_in[5]   = (cycle < 48) ? (cycle % 2) : 0;
            ui_in[6]   = data_reg[data_index];
            ui_in[4]   = (cycle == 49);
        end
    end

    task gamepad_hold(input [11:0] v, input integer packets);
        begin
            gp_value = v;
            repeat (packets + 1) @(posedge ui_in[4]);
            repeat (8) @(posedge clk);
        end
    endtask

    task press(input [11:0] v);              // press and release, ~3 frames
        begin
            gamepad_hold(v, 2);
            gamepad_hold(12'h000, 2);
        end
    endtask

    wire [9:0] vx = dut.u_ppu.x;
    wire [9:0] vy = dut.u_ppu.y;

    task wait_frame_start;
        begin
            @(posedge clk);
            while (!dut.u_ppu.frame_start) @(posedge clk);
        end
    endtask

    integer hs_low = 0, vs_low = 0, hs_bad = 0, vs_bad = 0, hs_seen = 0, unk = 0;
    always @(posedge clk) if (rst_n) begin
        if (^uo_out === 1'bx) unk = unk + 1;
        if (!uo_out[7]) hs_low = hs_low + 1;
        else if (hs_low) begin
            if (hs_low != 96) hs_bad = hs_bad + 1;
            hs_seen = hs_seen + 1; hs_low = 0;
        end
        if (!uo_out[3]) vs_low = vs_low + 1;
        else if (vs_low) begin
            if (vs_low != 2 * 800) vs_bad = vs_bad + 1;
            vs_low = 0;
        end
    end

    reg [5:0] frame [0:640*480-1];
    reg       capturing = 0;
    integer   cap_n = 0, cx = 0, cy = 0;

    always @(negedge clk) begin
        if (capturing && dut.u_ppu.de) begin
            frame[cy * 640 + cx] = {uo_out[0], uo_out[4], uo_out[1], uo_out[5], uo_out[2], uo_out[6]};
            cap_n = cap_n + 1;
            cx = cx + 1;
            if (cx == 640) begin cx = 0; cy = cy + 1; end
        end
    end

    integer res_fd;
    initial res_fd = $fopen("../sim/out/demo_results.txt", "w");

    reg [8*80:1] fname;

    task dump_state(input [8*24:1] name);
        integer fd, q;
        begin
            $sformat(fname, "../sim/out/%0s_vram.hex", name);
            fd = $fopen(fname, "w");
            for (q = 0; q < 4096; q = q + 1) $fwrite(fd, "%h\n", dut.u_ppu.vram[q]);
            $fclose(fd);
            $sformat(fname, "../sim/out/%0s_oam.hex", name);
            fd = $fopen(fname, "w");
            for (q = 0; q < 64; q = q + 1) $fwrite(fd, "%h\n", dut.u_ppu.oam[q]);
            $fclose(fd);
            $sformat(fname, "../sim/out/%0s_pal.hex", name);
            fd = $fopen(fname, "w");
            for (q = 0; q < 32; q = q + 1) $fwrite(fd, "%h\n", dut.u_ppu.pal[q]);
            $fclose(fd);
            $sformat(fname, "../sim/out/%0s_regs.hex", name);
            fd = $fopen(fname, "w");
            $fwrite(fd, "%h\n%h\n%h\n%h\n", dut.u_ppu.scroll_a, dut.u_ppu.scroll_b,
                    dut.u_ppu.split, dut.u_ppu.ctrl);
            $fclose(fd);
        end
    endtask

    task capture(input [8*24:1] name);
        integer fd, x, y, bad;
        reg [5:0] p;
        begin
            while (!(vy == 10'd523 && vx == 10'd0)) @(posedge clk);
            dump_state(name);
            while (!(vy == 10'd524 && vx == 10'd799)) @(posedge clk);
            cap_n = 0; cx = 0; cy = 0; capturing = 1;
            while (cap_n < 640 * 480) @(posedge clk);
            capturing = 0;
            bad = 0;
            $sformat(fname, "../sim/out/%0s.hex", name);
            fd = $fopen(fname, "w");
            for (y = 0; y < 480; y = y + 2)
                for (x = 0; x < 640; x = x + 2) begin
                    p = frame[y * 640 + x];
                    if (frame[y*640 + x + 1] !== p || frame[(y+1)*640 + x] !== p ||
                        frame[(y+1)*640 + x + 1] !== p)
                        bad = bad + 1;
                    $fwrite(fd, "%h\n", p);
                end
            $fclose(fd);
            if (bad) begin
                $display("FAIL %0s: %0d 2x2 blocks not one colour", name, bad);
                errors = errors + 1;
            end else
                $display("ok   captured %0s", name);
            wait_frame_start;
            #1 $fwrite(res_fd, "%0s %0d\n", name, dut.u_ppu.sprite_overflow);
            $fflush(res_fd);
        end
    endtask

    task expect_bit(input [8*40:1] what, input got, input want);
        if (got !== want) begin
            $display("FAIL %0s = %b, expected %b", what, got, want);
            errors = errors + 1;
        end else
            $display("ok   %0s = %b", what, got);
    endtask

    integer diff_px;
    reg [5:0] frame_a [0:320*240-1];
    task keep_frame;
        integer q;
        for (q = 0; q < 320*240; q = q + 1) frame_a[q] = frame[(q / 320) * 2 * 640 + (q % 320) * 2];
    endtask
    task diff_rows(input integer y0, input integer y1);
        integer q, yy;
        begin
            diff_px = 0;
            for (q = 0; q < 320*240; q = q + 1) begin
                yy = q / 320;
                if (yy >= y0 && yy < y1 &&
                    frame_a[q] !== frame[yy * 2 * 640 + (q % 320) * 2])
                    diff_px = diff_px + 1;
            end
        end
    endtask

    initial begin
        repeat (20) @(posedge clk);
        rst_n = 1'b1;
        gp_en = 1;

        wait_frame_start; wait_frame_start;
        expect_bit("display on after boot", dut.u_ppu.ctrl[0], 1'b1);
        capture("demo_default");

        press(B_START);                       // auto pan off: frames hold still
        expect_bit("auto pan off", dut.u_cpu.auto_pan, 1'b0);

        press(B_Y);                           // ROW
        expect_bit("row mode on", dut.u_cpu.row_mode, 1'b1);
        wait_frame_start; wait_frame_start;
        expect_bit("overflow with 16 on a line", dut.u_ppu.sprite_overflow, 1'b1);
        capture("demo_row");
        keep_frame;
        capture("demo_row_again");
        diff_rows(112, 128);
        $display("info row band, FLICK off: %0d pixels change frame to frame", diff_px);

        press(B_A);                           // FLICK
        expect_bit("flick on", dut.u_cpu.flick, 1'b1);
        capture("demo_flick1");
        keep_frame;
        capture("demo_flick2");
        diff_rows(112, 128);
        if (diff_px == 0) begin
            $display("FAIL flicker: the row looks the same two frames running");
            errors = errors + 1;
        end else
            $display("ok   flicker: %0d pixels of the row change between frames", diff_px);

        press(B_A);
        press(B_Y);
        expect_bit("row mode off", dut.u_cpu.row_mode, 1'b0);

        press(B_B);                           // BEHIND
        expect_bit("behind on", dut.u_cpu.behind, 1'b1);
        capture("demo_behind");
        press(B_B);

        press(B_X);                           // palette rotate
        if (dut.u_cpu.pal_shift !== 2'd1) begin
            $display("FAIL pal_shift = %0d, expected 1", dut.u_cpu.pal_shift);
            errors = errors + 1;
        end
        capture("demo_palette");

        press(B_SEL);                         // HUD off
        expect_bit("hud off", dut.u_cpu.hud_on, 1'b0);
        capture("demo_nohud");
        press(B_SEL);

        begin : scroll_test
            reg [8:0] x_before;
            x_before = dut.u_cpu.cam_x;
            gamepad_hold(B_LEFT, 8);
            gamepad_hold(12'h000, 2);
            if (dut.u_cpu.cam_x == x_before) begin
                $display("FAIL d-pad left did not scroll");
                errors = errors + 1;
            end else
                $display("ok   d-pad left scrolled from %0d to %0d", x_before, dut.u_cpu.cam_x);
        end

        begin : switch_test
            reg [8:0] x_before;
            x_before = dut.u_cpu.cam_x;
            ui_in[3] = 1'b1;
            wait_frame_start; wait_frame_start; wait_frame_start; wait_frame_start;
            ui_in[3] = 1'b0;
            wait_frame_start;
            if (dut.u_cpu.cam_x == x_before) begin
                $display("FAIL ui_in[3] did not scroll right");
                errors = errors + 1;
            end else
                $display("ok   ui_in[3] scrolled from %0d to %0d", x_before, dut.u_cpu.cam_x);
        end

        if (hs_bad || vs_bad || hs_seen == 0) begin
            $display("FAIL sync: %0d bad hsync, %0d bad vsync of %0d lines", hs_bad, vs_bad, hs_seen);
            errors = errors + 1;
        end else
            $display("ok   sync: %0d lines, every hsync 96 clocks, every vsync 2 lines", hs_seen);
        if (unk) begin
            $display("FAIL %0d clocks with x/z on uo_out", unk);
            errors = errors + 1;
        end else
            $display("ok   uo_out never unknown after reset");

        if (errors) $display("FAILED: %0d error(s)", errors);
        else        $display("PASS");
        $finish;
    end

endmodule
