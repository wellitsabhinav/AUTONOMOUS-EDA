module logic_tb;
  logic a, b, y;
  logic expected [0:3];
  logic_unit dut (.a(a), .b(b), .y(y));

  initial begin
    {expected[0], expected[1], expected[2], expected[3]} = 4'b0110;
    for (integer i = 0; i < 4; i = i + 1) begin
      {a, b} = i[1:0];
      #1;
      if (y !== expected[i]) begin
        $display("LOGIC_MISMATCH");
        $display("a=%b b=%b expected=%b observed=%b", a, b, expected[i], y);
        $fatal(1, "logic.sv output mismatch");
      end
    end
    $display("LOGIC_PASS");
    $finish;
  end
endmodule
