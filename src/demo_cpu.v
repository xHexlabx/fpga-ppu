`default_nettype none

module demo_cpu (
    input  wire        clk,
    input  wire        rst,
    input  wire        frame_start,
    input  wire        sprite_overflow,
    input  wire [11:0] pad,      // held: {b, y, select, start, up, down, left, right, a, x, l, r}
    input  wire [4:0]  sw,       // held: {row, right, left, down, up}
    output reg         we,
    output reg  [12:0] waddr,
    output reg  [31:0] wdata
);

    reg [9:0] map_rom [0:4095];
    reg [5:0] pal_rom [0:31];
    initial $readmemh("demo_map.mem", map_rom);
    initial $readmemh("palette.mem", pal_rom);

    reg [12:0] c;              // step counter inside a state
    reg [12:0] c_d;            // c one clock ago (ROM reads take a clock)
    reg        v_d;
    reg [9:0]  map_q;
    reg [5:0]  pal_q;

    always @(posedge clk) begin
        map_q <= map_rom[c[11:0]];
        pal_q <= pal_rom[c[4:0]];
    end

    localparam S_BOOT_VRAM = 4'd0, S_BOOT_PAL = 4'd1, S_BOOT_OBJ = 4'd2,
               S_WAIT      = 4'd3, S_INPUT    = 4'd4, S_OBJ      = 4'd5,
               S_ANIM      = 4'd6, S_HUD      = 4'd7, S_REGS     = 4'd8;
    reg [3:0] st;

    reg [8:0] ox  [0:63];      // object x (screen)
    reg [7:0] oy  [0:63];      // object y (screen)
    reg       odx [0:63];      // 1 = moving left
    reg       ody [0:63];      // 1 = moving up

    reg [15:0] lfsr;
    reg [8:0]  cam_x;
    reg [7:0]  cam_y;
    reg [7:0]  fcnt;
    reg        flick, behind, row_mode, hud_on, auto_pan;
    reg [1:0]  pal_shift;
    reg [5:0]  rot;
    reg [1:0]  wphase;
    reg [5:0]  w0, w1, w2;     // the three water colours
    reg        ovf_seen;
    reg [11:0] pad_prev;

    wire [11:0] pad_edge = pad & ~pad_prev;
    wire row_on = row_mode ^ sw[4];
    wire d_up    = pad[7] | sw[0];
    wire d_down  = pad[6] | sw[1];
    wire d_left  = pad[5] | sw[2];
    wire d_right = pad[4] | sw[3];
    wire _unused = &{c_d[12], pad_edge[7:4], pad_edge[1:0], 1'b0};

    wire [5:0] i    = c[5:0];
    wire       big  = (i[5:4] == 2'b00);
    wire [8:0] x0   = ox[i];
    wire [7:0] y0   = oy[i];
    wire [8:0] vx   = i[0] ? 9'd2 : 9'd1;
    wire [7:0] vy   = i[1] ? 8'd2 : 8'd1;
    wire [8:0] xmax = big ? 9'd304 : 9'd312;
    wire [7:0] ymax = big ? 8'd224 : 8'd232;

    reg [8:0] nx;
    reg [7:0] ny;
    reg       ndx, ndy;
    always @(*) begin
        ndx = odx[i];
        ndy = ody[i];
        if (!odx[i]) begin
            if (x0 + vx >= xmax) begin nx = xmax; ndx = 1'b1; end
            else                       nx = x0 + vx;
        end else begin
            if (x0 <= vx)        begin nx = 9'd0; ndx = 1'b0; end
            else                       nx = x0 - vx;
        end
        if (!ody[i]) begin
            if (y0 + vy >= ymax) begin ny = ymax; ndy = 1'b1; end
            else                       ny = y0 + vy;
        end else begin
            if (y0 <= 8'd16 + vy) begin ny = 8'd16; ndy = 1'b0; end
            else                        ny = y0 - vy;
        end
        if (row_on && big) begin
            nx = 9'd8 + {i[3:0], 4'b0000} + {4'b0000, i[3:0], 1'b0} + {5'd0, i[3:0]};
            ny = 8'd112;
        end
    end

    wire       anim  = fcnt[4] ^ i[2];
    wire [1:0] sfr   = fcnt[4:3] + i[3:2];
    wire [7:0] tile  = big ? {4'b0000, i[1:0], anim, 1'b0}    // creature k, frame f
                           : {4'b0100, i[1:0], sfr};          // item t, frame 0..3
    wire [1:0] opal  = (big ? i[1:0] : i[3:2]) + pal_shift;
    wire       hflip = big & ndx;                             // art faces right
    wire       vflip = !big && (i[1:0] == 2'd2) && !ndy;      // arrow art points up
    wire [31:0] oam_entry = {1'b0, big, behind, vflip, hflip, opal, tile, ny, nx};
    wire [5:0]  slot      = i + rot;

    function [7:0] hexc;
        input [3:0] n;
        hexc = (n < 4'd10) ? (8'h30 + {4'd0, n}) : (8'h37 + {4'd0, n});
    endfunction

    function [7:0] onoff;          // "ON " / "OFF", character j
        input       on;
        input [1:0] j;
        begin
            if (j == 2'd0)      onoff = "O";
            else if (j == 2'd1) onoff = on ? "N" : "F";
            else                onoff = on ? " " : "F";
        end
    endfunction

    reg [4:0]  hud_row;
    reg [5:0]  hud_col;
    reg [7:0]  hud_chr;
    wire [4:0] k = c[4:0];
    always @(*) begin
        hud_row = 5'd0;
        hud_col = 6'd0;
        hud_chr = " ";
        case (k)
            5'd0:  begin hud_col = 6'd24; hud_chr = hexc({3'd0, cam_x[8]}); end
            5'd1:  begin hud_col = 6'd25; hud_chr = hexc(cam_x[7:4]); end
            5'd2:  begin hud_col = 6'd26; hud_chr = hexc(cam_x[3:0]); end
            5'd3:  begin hud_col = 6'd30; hud_chr = hexc(cam_y[7:4]); end
            5'd4:  begin hud_col = 6'd31; hud_chr = hexc(cam_y[3:0]); end
            5'd5:  begin hud_col = 6'd37; hud_chr = ovf_seen ? "Y" : "N"; end
            5'd6:  begin hud_col = 6'd38; hud_chr = ovf_seen ? "E" : "O"; end
            5'd7:  begin hud_col = 6'd39; hud_chr = ovf_seen ? "S" : " "; end
            5'd8, 5'd9, 5'd10: begin
                hud_row = 5'd1; hud_col = 6'd8 + {1'b0, k} - 6'd8;
                hud_chr = onoff(flick, k[1:0] - 2'd0);
            end
            5'd11, 5'd12, 5'd13: begin
                hud_row = 5'd1; hud_col = 6'd21 + {1'b0, k} - 6'd11;
                hud_chr = onoff(behind, k[1:0] - 2'd3);
            end
            5'd14, 5'd15, 5'd16: begin
                hud_row = 5'd1; hud_col = 6'd31 + {1'b0, k} - 6'd14;
                hud_chr = onoff(row_on, k[1:0] - 2'd2);
            end
            default: begin
                hud_row = 5'd1; hud_col = 6'd38; hud_chr = hexc({2'd0, pal_shift});
            end
        endcase
    end

    function [5:0] water;
        input [1:0] j;
        input [1:0] ph;
        input [5:0] a, b, d;
        reg   [2:0] t;
        begin
            t = {1'b0, j} + {1'b0, ph};
            if (t >= 3'd3) t = t - 3'd3;
            water = (t == 3'd0) ? a : (t == 3'd1) ? b : d;
        end
    endfunction

    always @(posedge clk) begin
        we <= 1'b0;
        if (rst) begin
            st        <= S_BOOT_VRAM;
            c         <= 13'd0;
            v_d       <= 1'b0;
            lfsr      <= 16'hACE1;
            cam_x     <= 9'd0;
            cam_y     <= 8'd0;
            fcnt      <= 8'd0;
            flick     <= 1'b0;
            behind    <= 1'b0;
            row_mode  <= 1'b0;
            hud_on    <= 1'b1;
            auto_pan  <= 1'b1;
            pal_shift <= 2'd0;
            rot       <= 6'd0;
            wphase    <= 2'd0;
            ovf_seen  <= 1'b0;
            pad_prev  <= 12'd0;
        end else begin
            case (st)
                S_BOOT_VRAM: begin         // demo_map.mem -> VRAM, 4096 words
                    c   <= c + 13'd1;
                    c_d <= c;
                    v_d <= !c[12];
                    if (v_d) begin
                        we    <= 1'b1;
                        waddr <= {1'b0, c_d[11:0]};
                        wdata <= {22'd0, map_q};
                    end
                    if (c == 13'd4097) begin
                        st  <= S_BOOT_PAL;
                        c   <= 13'd0;
                        v_d <= 1'b0;
                    end
                end

                S_BOOT_PAL: begin          // palette.mem -> palette RAM
                    c   <= c + 13'd1;
                    c_d <= c;
                    v_d <= !c[5];
                    if (v_d) begin
                        we    <= 1'b1;
                        waddr <= 13'h1040 | {8'd0, c_d[4:0]};
                        wdata <= {26'd0, pal_q};
                        if (c_d[4:0] == 5'd13) w0 <= pal_q;
                        if (c_d[4:0] == 5'd14) w1 <= pal_q;
                        if (c_d[4:0] == 5'd15) w2 <= pal_q;
                    end
                    if (c == 13'd33) begin
                        st <= S_BOOT_OBJ;
                        c  <= 13'd0;
                    end
                end

                S_BOOT_OBJ: begin          // scatter the objects
                    lfsr   <= {lfsr[14:0], lfsr[15] ^ lfsr[13] ^ lfsr[12] ^ lfsr[10]};
                    ox[i]  <= {1'b0, lfsr[7:0]} + {4'd0, lfsr[12:8]};
                    oy[i]  <= 8'd16 + {1'b0, lfsr[14:8]} + {2'd0, lfsr[5:0]};
                    odx[i] <= lfsr[3];
                    ody[i] <= lfsr[9];
                    c      <= c + 13'd1;
                    if (i == 6'd63) st <= S_WAIT;
                end

                S_WAIT: begin
                    if (frame_start) st <= S_INPUT;
                end

                S_INPUT: begin             // once per frame
                    pad_prev <= pad;
                    if (pad_edge[3])  flick     <= ~flick;
                    if (pad_edge[11]) behind    <= ~behind;
                    if (pad_edge[10]) row_mode  <= ~row_mode;
                    if (pad_edge[2])  pal_shift <= pal_shift + 2'd1;
                    if (pad_edge[9])  hud_on    <= ~hud_on;
                    if (pad_edge[8])  auto_pan  <= ~auto_pan;

                    if (d_up)        cam_y <= cam_y - 8'd2;
                    else if (d_down) cam_y <= cam_y + 8'd2;
                    if (d_left)       cam_x <= cam_x - 9'd2;
                    else if (d_right) cam_x <= cam_x + 9'd2;
                    if (auto_pan && !(d_up || d_down || d_left || d_right)) begin
                        cam_x <= cam_x + 9'd1;
                        if (fcnt[1:0] == 2'd0) cam_y <= cam_y + 8'd1;
                    end

                    fcnt     <= fcnt + 8'd1;
                    rot      <= flick ? (rot + 6'd23) : 6'd0;
                    ovf_seen <= sprite_overflow;
                    if (fcnt[2:0] == 3'd0)
                        wphase <= (wphase == 2'd2) ? 2'd0 : (wphase + 2'd1);
                    st <= S_OBJ;
                    c  <= 13'd0;
                end

                S_OBJ: begin               // move object i, write it to OAM
                    ox[i]  <= nx;
                    oy[i]  <= ny;
                    odx[i] <= ndx;
                    ody[i] <= ndy;
                    we     <= 1'b1;
                    waddr  <= 13'h1000 | {7'd0, slot};
                    wdata  <= oam_entry;
                    c      <= c + 13'd1;
                    if (i == 6'd63) begin
                        st <= S_ANIM;
                        c  <= 13'd0;
                    end
                end

                S_ANIM: begin              // water colour cycling: entries 13..15
                    we    <= 1'b1;
                    waddr <= 13'h104D + {11'd0, c[1:0]};
                    wdata <= {26'd0, water(c[1:0], wphase, w0, w1, w2)};
                    c     <= c + 13'd1;
                    if (c[1:0] == 2'd2) begin
                        st <= S_HUD;
                        c  <= 13'd0;
                    end
                end

                S_HUD: begin               // the HUD's value fields, page 1
                    we    <= 1'b1;
                    waddr <= {2'b01, hud_row, hud_col};
                    wdata <= {22'd0, 2'b01, hud_chr};
                    c     <= c + 13'd1;
                    if (k == 5'd17) begin
                        st <= S_REGS;
                        c  <= 13'd0;
                    end
                end

                default: begin             // S_REGS: scroll A, scroll B, split, control
                    we    <= 1'b1;
                    waddr <= 13'h1060 + {11'd0, c[1:0]};
                    case (c[1:0])
                        2'd0:    wdata <= {14'd0, 1'b1, 8'd0, 9'd0};       // HUD: page 1 at 0,0
                        2'd1:    wdata <= {14'd0, 1'b0, cam_y, cam_x};     // world: page 0
                        2'd2:    wdata <= hud_on ? 32'd16 : 32'd0;
                        default: wdata <= 32'd7;                           // all on
                    endcase
                    c <= c + 13'd1;
                    if (c[1:0] == 2'd3) st <= S_WAIT;
                end
            endcase
        end
    end

endmodule

`default_nettype wire
