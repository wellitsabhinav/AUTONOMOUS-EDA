module counter (
    input  logic       clk,
    input  logic       reset_n,
    output logic [7:0] count
);
    always_ff @(posedge clk) begin
        if (!reset_n)
            count <= '0;
        else
            count <= count + 8'd1;
    end
endmodule
