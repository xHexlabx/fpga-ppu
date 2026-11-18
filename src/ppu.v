`default_nettype none

module ppu (
    input  wire        clk,          // 25.175 MHz pixel clock
    input  wire        rst,          // synchronous, active high

    input  wire        we,
    input  wire [12:0] waddr,
    input  wire [31:0] wdata,

    input  wire [11:0] nt_raddr,
    output reg  [9:0]  nt_rdata,

    output wire        frame_start,
    output wire        vblank,
    output reg         sprite_overflow,

    output wire [10:0] bg_chr_addr,
    input  wire [15:0] bg_chr_data,
    output wire [10:0] sp_chr_addr,
    input  wire [15:0] sp_chr_data,

    output wire [5:0]  rgb,          // {R[1:0], G[1:0], B[1:0]}
    output reg         hsync,
    output reg         vsync,
    output reg         de
);

    wire       t_hs, t_vs, t_blank;
    wire [9:0] x, y;

    vga_timing u_timing (
        .clk   (clk),
        .HS    (t_hs),
        .VS    (t_vs),
        .x     (x),
        .y     (y),
        .blank (t_blank)
    );

    assign frame_start = (x == 10'd0) && (y == 10'd480);
    assign vblank      = (y >= 10'd480);

    function [1:0] pick;
        input [15:0] row;
        input [2:0]  p;
        begin
            pick = {row[{~p, 1'b1}], row[{~p, 1'b0}]};
        end
    endfunction

    reg [9:0]  vram [0:4095];
    reg [31:0] oam  [0:63];
    reg [5:0]  pal  [0:31];

    reg [17:0] scroll_a, scroll_b;
    reg [7:0]  split;
    reg [2:0]  ctrl;

    wire wr_vram = we && !waddr[12];
    wire wr_oam  = we && (waddr[12:6] == 7'b1000000);
    wire wr_pal  = we && (waddr[12:5] == 8'b10000010);
    wire wr_reg  = we && (waddr[12:2] == 11'b10000011000);

    wire [11:0] bg_nt_addr;
    reg  [9:0]  bg_nt_q;

    always @(posedge clk) begin
        if (wr_vram) vram[waddr[11:0]] <= wdata[9:0];
        if (wr_oam)  oam[waddr[5:0]]   <= wdata;
        if (wr_pal)  pal[waddr[4:0]]   <= wdata[5:0];
        nt_rdata <= vram[nt_raddr];
        bg_nt_q  <= vram[bg_nt_addr];
    end

    always @(posedge clk) begin
        if (rst) begin
            scroll_a <= 18'd0;
            scroll_b <= 18'd0;
            split    <= 8'd0;
            ctrl     <= 3'd0;
        end else if (wr_reg) begin
            case (waddr[1:0])
                2'd0: scroll_a <= wdata[17:0];
                2'd1: scroll_b <= wdata[17:0];
                2'd2: split    <= wdata[7:0];
                default: ctrl  <= wdata[2:0];
            endcase
        end
    end

    wire disp_on = ctrl[0];
    wire bg_on   = ctrl[1];
    wire spr_on  = ctrl[2];

    localparam RS_IDLE = 2'd0, RS_EVAL = 2'd1, RS_BG = 2'd2, RS_SPR = 2'd3;

    wire       line_go = (x == 10'd0) && ((y == 10'd523) || (!y[0] && y <= 10'd476));
    wire [7:0] go_line = (y == 10'd523) ? 8'd0 : (y[8:1] + 8'd1);
    wire       go_a    = (go_line < split);
    wire [17:0] go_scroll = go_a ? scroll_a : scroll_b;

    reg [1:0] rs;
    reg [7:0] r_line;          // line being built; r_line[0] picks the buffer half
    reg       r_page;
    reg [8:0] r_sx;
    reg [7:0] r_wy;            // map y of this line

    reg [5:0]  ev_i;
    reg [3:0]  sec_n;
    reg [25:0] sec [0:7];
    reg        ovf_acc;

    wire [31:0] ev_e   = oam[ev_i];
    wire        unused_ev_b31 = ev_e[31];   // OAM bit 31 has no meaning
    wire [7:0]  ev_dy  = r_line - ev_e[16:9];
    wire        ev_big = ev_e[30];
    wire        ev_hit = spr_on && (ev_dy[7:4] == 4'd0) && (ev_big || !ev_dy[3]);
    wire [3:0]  ev_row = !ev_e[28] ? ev_dy[3:0] :
                         ev_big    ? ~ev_dy[3:0] : {1'b0, ~ev_dy[2:0]};

    reg  [8:0] bg_i;
    wire       bg_issue = (rs == RS_BG) && (bg_i < 9'd320);
    wire [8:0] bg_wx    = bg_i + r_sx;
    assign bg_nt_addr  = {r_page, r_wy[7:3], bg_wx[8:3]};
    assign bg_chr_addr = {bg_nt_q[7:0], r_wy[2:0]};

    reg       bg_v1, bg_v2;
    reg [8:0] bg_lx1, bg_lx2;
    reg [2:0] bg_px1, bg_px2;
    reg [1:0] bg_pal2;

    wire [1:0] bg_ci    = pick(bg_chr_data, bg_px2);
    wire       bg_solid = bg_on && (bg_ci != 2'd0);
    wire [5:0] bg_color = bg_solid ? pal[{1'b0, bg_pal2, bg_ci}] : pal[0];

    reg bgop [0:511];          // was the background opaque at x? (for "behind")

    reg       sp_run;
    reg [2:0] sp_s;
    reg [3:0] sp_p;

    wire [25:0] sp_e   = sec[sp_s];
    wire        sp_big = sp_e[25];
    wire [3:0]  sp_row = sp_e[3:0];
    wire [3:0]  sp_col = !sp_e[23] ? sp_p :
                         sp_big    ? ~sp_p : {1'b0, ~sp_p[2:0]};
    wire [7:0]  sp_tile = sp_e[20:13] + {3'b000, sp_row[3], 3'b000, sp_col[3]};
    wire [8:0]  sp_lx   = sp_e[12:4] + {5'd0, sp_p};
    wire        sp_last = (sp_p == (sp_big ? 4'd15 : 4'd7));
    assign sp_chr_addr = {sp_tile, sp_row[2:0]};

    reg       sp_v1, sp_beh1;
    reg [8:0] sp_lx1;
    reg [2:0] sp_col1;
    reg [1:0] sp_pal1;

    wire [1:0] sp_ci    = pick(sp_chr_data, sp_col1);
    wire       sp_write = sp_v1 && (sp_ci != 2'd0) && (sp_lx1 < 9'd320) &&
                          !(sp_beh1 && bgop[sp_lx1]);

    always @(posedge clk) begin
        if (rst) begin
            rs              <= RS_IDLE;
            sp_run          <= 1'b0;
            ovf_acc         <= 1'b0;
            sprite_overflow <= 1'b0;
        end else begin
            if (frame_start) begin
                sprite_overflow <= ovf_acc;
                ovf_acc         <= 1'b0;
            end

            case (rs)
                RS_IDLE: begin
                    if (line_go) begin
                        r_line <= go_line;
                        r_page <= go_scroll[17];
                        r_wy   <= go_line + go_scroll[16:9];
                        r_sx   <= go_scroll[8:0];
                        ev_i   <= 6'd0;
                        sec_n  <= 4'd0;
                        rs     <= RS_EVAL;
                    end
                end

                RS_EVAL: begin
                    if (ev_hit) begin
                        if (!sec_n[3]) begin
                            sec[sec_n[2:0]] <= {ev_big, ev_e[29], ev_e[27], ev_e[26:25],
                                                ev_e[24:17], ev_e[8:0], ev_row};
                            sec_n <= sec_n + 4'd1;
                        end else begin
                            ovf_acc <= 1'b1;
                        end
                    end
                    ev_i <= ev_i + 6'd1;
                    if (ev_i == 6'd63) begin
                        bg_i <= 9'd0;
                        rs   <= RS_BG;
                    end
                end

                RS_BG: begin
                    bg_i <= bg_i + 9'd1;
                    if (bg_i == 9'd321) begin
                        sp_run <= (sec_n != 4'd0);
                        sp_s   <= sec_n[2:0] - 3'd1;
                        sp_p   <= 4'd0;
                        rs     <= RS_SPR;
                    end
                end

                default: begin // RS_SPR
                    if (sp_run) begin
                        if (sp_last) begin
                            sp_p <= 4'd0;
                            if (sp_s == 3'd0) sp_run <= 1'b0;
                            else              sp_s   <= sp_s - 3'd1;
                        end else begin
                            sp_p <= sp_p + 4'd1;
                        end
                    end else if (!sp_v1) begin
                        rs <= RS_IDLE;
                    end
                end
            endcase
        end
    end

    always @(posedge clk) begin
        bg_v1   <= bg_issue;
        bg_lx1  <= bg_i;
        bg_px1  <= bg_wx[2:0];
        bg_v2   <= bg_v1;
        bg_lx2  <= bg_lx1;
        bg_px2  <= bg_px1;
        bg_pal2 <= bg_nt_q[9:8];

        sp_v1   <= sp_run && (rs == RS_SPR);
        sp_lx1  <= sp_lx;
        sp_col1 <= sp_col[2:0];
        sp_pal1 <= sp_e[22:21];
        sp_beh1 <= sp_e[24];
    end

    reg [5:0] linebuf [0:1023];    // {half, x}; x 0..319 used in each half
    reg [5:0] disp_q;

    always @(posedge clk) begin
        if (bg_v2) begin
            linebuf[{r_line[0], bg_lx2}] <= bg_color;
            bgop[bg_lx2]                 <= bg_solid;
        end else if (sp_write) begin
            linebuf[{r_line[0], sp_lx1}] <= pal[{1'b1, sp_pal1, sp_ci}];
        end
    end

    always @(posedge clk) begin
        disp_q <= disp_on ? linebuf[{y[1], x[9:1]}] : 6'd0;
        hsync  <= t_hs;
        vsync  <= t_vs;
        de     <= !t_blank;
    end

    assign rgb = de ? disp_q : 6'd0;

endmodule

`default_nettype wire
