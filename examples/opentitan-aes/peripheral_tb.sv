`timescale 1ns/1ps

// Earl Grey AES TL-UL smoke test using the upstream bus and register packages.
module peripheral_tb;
  import aes_reg_pkg::*;
  import tlul_pkg::*;
  import edn_pkg::*;

  bit clk = 0;
  bit rst_n = 0;
  tl_h2d_t request = TL_H2D_DEFAULT;
  tl_d2h_t response;
  edn_req_t entropy_request;
  edn_rsp_t entropy = EDN_RSP_DEFAULT;
  prim_mubi_pkg::mubi4_t idle;
  prim_alert_pkg::alert_tx_t [NumAlerts-1:0] alerts;
  int unsigned cycle = 0;
  int unsigned entropy_words = 0;
  logic [31:0] entropy_word = 32'h41_aed_a12;

  aes dut (
    .clk_i(clk), .rst_ni(rst_n), .rst_shadowed_ni(rst_n),
    .idle_o(idle), .lc_escalate_en_i(lc_ctrl_pkg::Off),
    .clk_edn_i(clk), .rst_edn_ni(rst_n), .edn_o(entropy_request), .edn_i(entropy),
    .keymgr_key_i('0), .tl_i(request), .tl_o(response),
    .alert_rx_i({NumAlerts{prim_alert_pkg::ALERT_RX_DEFAULT}}), .alert_tx_o(alerts)
  );

  always #5 clk = ~clk;

  always @(negedge clk) begin
    entropy.edn_ack = rst_n && entropy_request.edn_req && !entropy.edn_ack;
    entropy.edn_fips = 1;
    if (entropy.edn_ack) begin
      entropy.edn_bus = entropy_word;
      entropy_word = {entropy_word[30:0],
                      entropy_word[31] ^ entropy_word[21] ^ entropy_word[1] ^ entropy_word[0]};
      entropy_words++;
    end
  end

  always @(posedge clk) begin
    if (rst_n) begin
      $display("AEDA_AES_TRACE %0d %h", cycle,
               {idle, alerts, entropy_request, response.a_ready, response.d_valid,
                response.d_valid ? response.d_error : 1'b0,
                response.d_valid && response.d_opcode == AccessAckData ?
                  response.d_data : 32'd0});
      cycle++;
    end
  end

  task automatic fail(input string why);
    $display("AEDA_AES_FAIL %s", why);
    $fatal(1, "%s", why);
  endtask

  task automatic access_register(
    input bit write,
    input logic [31:0] address,
    input logic [31:0] data,
    input bit invalid_address,
    output logic [31:0] result
  );
    bit accepted = 0;
    bit received = 0;
    @(negedge clk);
    #1;
    request = TL_H2D_DEFAULT;
    request.a_valid = 1;
    request.a_opcode = write ? PutFullData : Get;
    request.a_size = 2;
    request.a_address = address;
    request.a_mask = '1;
    request.a_data = data;
    request.a_user.cmd_intg = get_cmd_intg(request);
    request.a_user.data_intg = get_data_intg(data);
    request.d_ready = 0;
    for (int n = 0; n < 20; n++) begin
      @(posedge clk);
      if (response.a_ready) begin
        accepted = 1;
        break;
      end
    end
    if (!accepted) fail("TL-UL request timed out");
    @(negedge clk);
    #1;
    request.a_valid = 0;
    for (int n = 0; n < 20; n++) begin
      @(posedge clk);
      if (response.d_valid) begin
        received = 1;
        result = response.d_data;
        if (response.d_error !== invalid_address) fail("unexpected TL-UL d_error");
        if (response.d_opcode !== (write ? AccessAck : AccessAckData))
          fail("unexpected TL-UL d_opcode");
        break;
      end
    end
    if (!received) fail("TL-UL response timed out");
    // Hold d_ready low for another cycle and verify the buffered result is stable.
    @(posedge clk);
    if (!response.d_valid || response.d_data !== result)
      fail("response changed under backpressure");
    @(negedge clk);
    #1;
    request.d_ready = 1;
    @(posedge clk);
  endtask

  task automatic wr(input logic [31:0] address, input logic [31:0] value);
    logic [31:0] ignored;
    access_register(1, address, value, 0, ignored);
  endtask

  task automatic rd(input logic [31:0] address, output logic [31:0] value);
    access_register(0, address, 0, 0, value);
  endtask

  task automatic wait_status(input int index);
    logic [31:0] value;
    for (int i = 0; i < 200; i++) begin
      rd(32'(AES_STATUS_OFFSET), value);
      if (value[index]) return;
    end
    fail($sformatf("STATUS bit %0d timed out", index));
  endtask

  task automatic reset_peripheral;
    @(negedge clk);
    #1;
    request = TL_H2D_DEFAULT;
    rst_n = 0;
    repeat (6) @(negedge clk);
    #1;
    rst_n = 1;
    wait_status(0); // IDLE
  endtask

  task automatic ecb(
    input bit decrypt,
    input logic [127:0] data,
    input logic [127:0] expected
  );
    // FIPS 197 AES-128 example, packed as little-endian register words.
    localparam logic [127:0] Key = 128'h0f0e0d0c0b0a09080706050403020100;
    logic [31:0] actual;
    logic [31:0] control_word;
    control_word = 32'h00001104 | (decrypt ? 32'd2 : 32'd1);
    wait_status(0);
    wr(32'(AES_CTRL_SHADOWED_OFFSET), control_word);
    wr(32'(AES_CTRL_SHADOWED_OFFSET), control_word);
    rd(32'(AES_CTRL_SHADOWED_OFFSET), actual);
    if (actual !== control_word) fail("committed shadowed control value differs");
    for (int i = 0; i < 8; i++) begin
      wr(32'(AES_KEY_SHARE0_0_OFFSET) + 4*i, i < 4 ? Key[32*(i%4) +: 32] : 0);
      wr(32'(AES_KEY_SHARE1_0_OFFSET) + 4*i, 0);
    end
    for (int i = 0; i < 4; i++) wr(32'(AES_DATA_IN_0_OFFSET) + 4*i, data[32*i +: 32]);
    wait_status(3); // OUTPUT_VALID
    for (int i = 0; i < 4; i++) begin
      rd(32'(AES_DATA_OUT_0_OFFSET) + 4*i, actual);
      if (actual !== expected[32*i +: 32]) fail($sformatf("AES result word %0d differs", i));
    end
    rd(32'(AES_STATUS_OFFSET), actual);
    if (actual[3]) fail("OUTPUT_VALID remained set after all words were read");
  endtask

  initial begin
    logic [31:0] actual;
    localparam logic [127:0] Plaintext = 128'hffeeddccbbaa99887766554433221100;
    localparam logic [127:0] Ciphertext = 128'h5ac5b47080b7cdd830047b6ad8e0c469;
    reset_peripheral();
    ecb(0, Plaintext, Ciphertext);
    reset_peripheral();
    ecb(1, Ciphertext, Plaintext);
    access_register(0, 32'h000000f0, 0, 1, actual);
    if (!entropy_words) fail("No EDN transfers observed");
    $display("AEDA_AES_PASS");
    $finish;
  end
endmodule
