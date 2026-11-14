module io_demo(input a, output y);
  sky130_fd_sc_hd__buf_1 u_buf (.A(a), .X(y));
endmodule
