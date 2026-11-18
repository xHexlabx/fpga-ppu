`default_nettype none

module tt_um_hextex_ppu_demo (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,      // 25.175 MHz
    input  wire       rst_n
);

    wire rst = ~rst_n;

    wire [11:0] pad;            // { b, y, select, start, up, down, left, right, a, x, l, r }
    wire        pad_present;

    gamepad_pmod_rx u_pad (
        .clk        (clk),
        .rst        (rst),
        .pmod_data  (ui_in[6]),
        .pmod_clk   (ui_in[5]),
        .pmod_latch (ui_in[4]),
        .btn        (pad),
        .present    (pad_present)
    );

    reg [4:0] sw_s1, sw_s2;     // {row, right, left, down, up}
    always @(posedge clk) begin
        sw_s1 <= {ui_in[7], ui_in[3:0]};
        sw_s2 <= sw_s1;
    end

    wire        we;
    wire [12:0] waddr;
    wire [31:0] wdata;
    wire        frame_start, vblank, sprite_overflow;
    wire [9:0]  nt_rdata;

    demo_cpu u_cpu (
        .clk             (clk),
        .rst             (rst),
        .frame_start     (frame_start),
        .sprite_overflow (sprite_overflow),
        .pad             (pad),
        .sw              (sw_s2),
        .we              (we),
        .waddr           (waddr),
        .wdata           (wdata)
    );

    wire [10:0] bg_chr_addr, sp_chr_addr;
    wire [15:0] bg_chr_data, sp_chr_data;
    wire [5:0]  rgb;
    wire        hsync, vsync, de;

    ppu u_ppu (
        .clk             (clk),
        .rst             (rst),
        .we              (we),
        .waddr           (waddr),
        .wdata           (wdata),
        .nt_raddr        (12'd0),
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

    chr_rom_bg  u_chr_bg  (.clk(clk), .addr(bg_chr_addr), .data(bg_chr_data));
    chr_rom_spr u_chr_spr (.clk(clk), .addr(sp_chr_addr), .data(sp_chr_data));

    assign uo_out  = {hsync, rgb[0], rgb[2], rgb[4], vsync, rgb[1], rgb[3], rgb[5]};
    assign uio_out = 8'h00;
    assign uio_oe  = 8'h00;

    wire _unused = &{ena, uio_in, ui_in[6:4], pad_present, vblank, de, nt_rdata, 1'b0};

endmodule

`default_nettype wire
