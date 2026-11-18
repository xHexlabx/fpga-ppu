`default_nettype none

module chr_rom_bg (
    input  wire        clk,
    input  wire [10:0] addr,
    output reg  [15:0] data
);
    reg [15:0] mem [0:2047];
    initial $readmemh("chr_bg.mem", mem);
    always @(posedge clk) data <= mem[addr];
endmodule

module chr_rom_spr (
    input  wire        clk,
    input  wire [10:0] addr,
    output reg  [15:0] data
);
    reg [15:0] mem [0:2047];
    initial $readmemh("chr_spr.mem", mem);
    always @(posedge clk) data <= mem[addr];
endmodule

`default_nettype wire
