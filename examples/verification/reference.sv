module logic_unit(input logic a, b, output logic y);
  assign y = (a & ~b) | (~a & b);
endmodule
