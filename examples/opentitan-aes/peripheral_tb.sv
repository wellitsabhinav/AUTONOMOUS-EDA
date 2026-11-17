`timescale 1ns/1ps

// Earl Grey AES TL-UL smoke test using the upstream bus and register packages.
module peripheral_tb;
  import aes_reg_pkg::*;
  import aes_pkg::AES_128, aes_pkg::AES_192, aes_pkg::AES_256, aes_pkg::AES_ECB;
  import tlul_pkg::*;
  import edn_pkg::*;

  bit clk = 0;
  bit edn_clk = 0;
  bit rst_n = 0;
  bit shadow_rst_n = 0;
  bit edn_rst_n = 0;
  int clk_half = 5, clk_phase = 0, edn_half = 5, edn_phase = 0;
  int reset_cycles = 6, edn_release_cycles = 0, shadow_release_cycles = 0;
  int edn_delay = 0, edn_jitter = 0, tl_jitter = 0;
  int unsigned seed = 41;
  bit extended;
  tl_h2d_t request = TL_H2D_DEFAULT;
  tl_d2h_t response;
  edn_req_t entropy_request;
  edn_rsp_t entropy = EDN_RSP_DEFAULT;
  prim_mubi_pkg::mubi4_t idle;
  prim_alert_pkg::alert_tx_t [NumAlerts-1:0] alerts;
  prim_alert_pkg::alert_rx_t [NumAlerts-1:0] alert_rx =
    {NumAlerts{prim_alert_pkg::ALERT_RX_DEFAULT}};
  keymgr_pkg::hw_key_req_t sideload_key = '0;
  int unsigned cycle = 0;
  int unsigned edn_cycle = 0;
  int unsigned tl_count = 0;
  int unsigned entropy_words = 0;
  int unsigned edn_wait_edges = 0;
  int remaining = -1;
  logic [31:0] entropy_word;
  logic [31:0] tl_random;

  localparam logic [255:0] Key =
    256'h1f1e1d1c1b1a191817161514131211100f0e0d0c0b0a09080706050403020100;
  localparam logic [127:0] Plaintext = 128'hffeeddccbbaa99887766554433221100;
  localparam logic [127:0] Cipher128 = 128'h5ac5b47080b7cdd830047b6ad8e0c469;
  localparam logic [127:0] Cipher192 = 128'h91710deca070af6ee0df4c86a47ca9dd;
  localparam logic [127:0] Cipher256 = 128'h8960494b9049fceabf456751cab7a28e;

  aes dut (
    .clk_i(clk), .rst_ni(rst_n), .rst_shadowed_ni(shadow_rst_n),
    .idle_o(idle), .lc_escalate_en_i(lc_ctrl_pkg::Off),
    .clk_edn_i(edn_clk), .rst_edn_ni(edn_rst_n), .edn_o(entropy_request), .edn_i(entropy),
    .keymgr_key_i(sideload_key), .tl_i(request), .tl_o(response),
    .alert_rx_i(alert_rx), .alert_tx_o(alerts)
  );

  function automatic logic [31:0] next_random(logic [31:0] value);
    return {value[30:0], value[31] ^ value[21] ^ value[1] ^ value[0]};
  endfunction

  initial begin
    void'($value$plusargs("clk_half=%d", clk_half));
    void'($value$plusargs("clk_phase=%d", clk_phase));
    if (clk_phase != 0) #(clk_phase);
    forever #(clk_half) clk = ~clk;
  end

  initial begin
    void'($value$plusargs("edn_half=%d", edn_half));
    void'($value$plusargs("edn_phase=%d", edn_phase));
    if (edn_phase != 0) #(edn_phase);
    forever #(edn_half) edn_clk = ~edn_clk;
  end

  always @(negedge edn_clk) begin
    if (!edn_rst_n || entropy.edn_ack || !entropy_request.edn_req) begin
      entropy.edn_ack = 0;
      remaining = -1;
    end else begin
      if (remaining < 0) remaining = edn_delay + int'(entropy_word % (edn_jitter + 1));
      if (remaining == 0) begin
        entropy = '{edn_ack: 1, edn_fips: 1, edn_bus: entropy_word};
        entropy_word = next_random(entropy_word);
      end else begin
        remaining--;
        edn_wait_edges++;
      end
    end
  end

  always @(posedge edn_clk) begin
    if (edn_rst_n && entropy.edn_ack) begin
      if (!entropy_request.edn_req) fail("EDN acknowledgment without request");
      entropy_words++;
    end
    if (extended) begin
      if (edn_cycle < 2) $display("AEDA_AES_EDGE EDN %0d %0d", edn_cycle, $time);
      $display("AEDA_AES_EDN %0d %h", edn_cycle, {edn_rst_n, entropy_request, entropy});
      edn_cycle++;
    end
  end

  always @(posedge clk) begin
    if (rst_n || extended) begin
      if (extended && cycle < 2) $display("AEDA_AES_EDGE CORE %0d %0d", cycle, $time);
      $display("AEDA_AES_TRACE %0d %h", cycle,
               {rst_n, shadow_rst_n, sideload_key, idle, alerts, alert_rx, request, response.a_ready,
                response.d_valid ? response : tl_d2h_t'('0)});
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
    output logic [31:0] result,
    input bit bad_integrity = 0
  );
    bit accepted = 0;
    bit received = 0;
    tl_d2h_t held_response;
    int unsigned stalls;
    tl_random = next_random(tl_random);
    repeat (tl_random % (tl_jitter + 1)) @(negedge clk);
    @(negedge clk);
    #1;
    request = TL_H2D_DEFAULT;
    request.a_valid = 1;
    request.a_opcode = write ? PutFullData : Get;
    request.a_size = 2;
    request.a_address = address;
    request.a_source = top_pkg::TL_AIW'(tl_count);
    request.a_mask = '1;
    request.a_data = data;
    request.a_user.cmd_intg = get_cmd_intg(request);
    if (bad_integrity) request.a_user.cmd_intg ^= 1;
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
        held_response = response;
        result = response.d_data;
        if (response.d_error !== (invalid_address || bad_integrity))
          fail("unexpected TL-UL d_error");
        if (response.d_opcode !== (write ? AccessAck : AccessAckData))
          fail("unexpected TL-UL d_opcode");
        if (response.d_source !== request.a_source || response.d_size !== request.a_size)
          fail("TL-UL response ordering or size differs");
        break;
      end
    end
    if (!received) fail("TL-UL response timed out");
    stalls = 1 + (tl_random >> 8) % (tl_jitter + 1);
    repeat (stalls) begin
      @(posedge clk);
      if (!response.d_valid || response !== held_response)
        fail("response changed under backpressure");
    end
    @(negedge clk);
    #1;
    request.d_ready = 1;
    @(posedge clk);
    if (!response.d_valid) fail("response was withdrawn before the TL handshake");
    tl_count++;
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
    shadow_rst_n = 0;
    edn_rst_n = 0;
    alert_rx = {NumAlerts{prim_alert_pkg::ALERT_RX_DEFAULT}};
    sideload_key = '0;
    repeat (reset_cycles) @(negedge clk);
    #1;
    fork
      begin
        if (edn_release_cycles != 0) begin
          repeat (edn_release_cycles) @(negedge edn_clk);
          #1;
        end
        edn_rst_n = 1;
      end
      begin
        if (shadow_release_cycles != 0) begin
          repeat (shadow_release_cycles) @(negedge clk);
          #1;
        end
        shadow_rst_n = 1;
      end
    join
    // Release the primary reset after both supporting domains are out of reset.
    if (edn_release_cycles != 0 || shadow_release_cycles != 0) begin
      @(negedge clk);
      #1;
    end
    rst_n = 1;
    wait_status(0); // IDLE
    begin
      logic [31:0] status;
      rd(32'(AES_STATUS_OFFSET), status);
      if (status[6] || status[3]) fail("fatal status or output valid survived reset");
    end
  endtask

  function automatic logic [31:0] control(input logic [1:0] op, input logic [2:0] length,
                                          input bit sideload);
    return {19'd0, 2'b10, sideload, length, AES_ECB, op};
  endfunction

  task automatic configure(input logic [31:0] word, input logic [31:0] expected);
    logic [31:0] actual;
    wait_status(0);
    wr(32'(AES_CTRL_SHADOWED_OFFSET), word);
    wr(32'(AES_CTRL_SHADOWED_OFFSET), word);
    rd(32'(AES_CTRL_SHADOWED_OFFSET), actual);
    if (actual !== expected) fail($sformatf("shadowed control %h != %h", actual, expected));
  endtask

  task automatic software_key;
    for (int i = 0; i < 8; i++) begin
      wr(32'(AES_KEY_SHARE0_0_OFFSET) + 4*i, Key[32*i +: 32]);
      wr(32'(AES_KEY_SHARE1_0_OFFSET) + 4*i, 0);
    end
  endtask

  task automatic write_block(input logic [127:0] data);
    wait_status(4); // INPUT_READY
    for (int i = 0; i < 4; i++) wr(32'(AES_DATA_IN_0_OFFSET) + 4*i, data[32*i +: 32]);
  endtask

  task automatic check_block(input string name, input logic [127:0] expected);
    logic [127:0] actual;
    logic [31:0] status;
    wait_status(3); // OUTPUT_VALID
    for (int i = 0; i < 4; i++) rd(32'(AES_DATA_OUT_0_OFFSET) + 4*i, actual[32*i +: 32]);
    $display("AEDA_AES_VECTOR %s %032h %032h", name, expected, actual);
    if (actual !== expected) fail($sformatf("AES result differs for %s", name));
    rd(32'(AES_STATUS_OFFSET), status);
    if (status[3] || status[6]) fail("unexpected OUTPUT_VALID or fatal status after the read");
  endtask

  task automatic ecb(input bit decrypt, input logic [127:0] data, input logic [127:0] expected);
    logic [31:0] word;
    word = control(decrypt ? 2 : 1, AES_128, 0);
    configure(word, word);
    software_key();
    write_block(data);
    check_block(decrypt ? "software_decrypt_128" : "software_encrypt_128", expected);
  endtask

  task automatic set_sideload(input logic [255:0] key, input bit valid);
    localparam logic [255:0] Share = {8{32'h91e0427b}};
    @(negedge clk);
    #1;
    sideload_key.valid = valid;
    sideload_key.key[0] = key ^ Share;
    sideload_key.key[1] = Share;
  endtask

  task automatic sideload_cases;
    logic [31:0] word, status;
    set_sideload(Key, 1);
    word = control(1, AES_128, 1);
    configure(word, word);
    write_block(Plaintext);
    check_block("sideload_encrypt_128", Cipher128);
    set_sideload('0, 0);
    write_block(Plaintext);
    repeat (32) @(posedge clk);
    rd(32'(AES_STATUS_OFFSET), status);
    if (!status[0] || status[3]) fail("automatic sideload started with invalid key");
    $display("AEDA_AES_CASE sideload_invalid_key");
    // A key becomes available on the pins, but only a committed control write loads it.
    set_sideload('0, 1);
    check_block("sideload_pins_changed_old_key", Cipher128);
    configure(word, word);
    write_block('0);
    check_block("sideload_update_128", 128'h2e2b34ca59fa4c883b2c8aefd44be966);
    set_sideload(Key, 1);
    word = control(1, AES_192, 1);
    configure(word, word);
    write_block(Plaintext);
    check_block("sideload_encrypt_192", Cipher192);
    // Unsupported op/length encodings sanitize to encrypt/256 in the pinned RTL.
    configure(control(0, 0, 1), control(1, AES_256, 1));
    write_block(Plaintext);
    check_block("sideload_sanitized_encrypt_256", Cipher256);
    word = control(2, AES_256, 1);
    configure(word, word);
    write_block(Cipher256);
    check_block("sideload_decrypt_256", Plaintext);
    write_block(Cipher256);
    if (idle !== prim_mubi_pkg::MuBi4False) fail("reset test failed to reach busy state");
    reset_peripheral();
    $display("AEDA_AES_CASE active_reset");
    ecb(0, Plaintext, Cipher128);
  endtask

  task automatic expect_alert(input int index);
    for (int n = 0; n < 64 && !alerts[index].alert_p; n++) begin
      @(negedge clk);
      #1;
    end
    if (alerts[index] !== 2'b10) fail("expected differential alert assertion");
    @(negedge clk);
    #1;
    alert_rx[index].ack_p = 1;
    alert_rx[index].ack_n = 0;
    for (int n = 0; n < 64 && alerts[index].alert_p; n++) begin
      @(negedge clk);
      #1;
    end
    if (alerts[index] !== 2'b01) fail("expected differential alert deassertion after ACK");
    alert_rx[index].ack_p = 0;
    alert_rx[index].ack_n = 1;
    repeat (6) @(negedge clk);
  endtask

  task automatic error_cases;
    logic [31:0] word, status;
    word = control(1, AES_128, 0);
    configure(word, word);
    wr(32'(AES_CTRL_SHADOWED_OFFSET), control(2, AES_128, 0));
    wr(32'(AES_CTRL_SHADOWED_OFFSET), control(1, AES_192, 0));
    expect_alert(0);
    rd(32'(AES_STATUS_OFFSET), status);
    if (!status[5] || status[6]) fail("shadow update did not report recoverable status");
    $display("AEDA_AES_CASE shadow_update_error");
    wr(32'(AES_ALERT_TEST_OFFSET), 2);
    expect_alert(1);
    $display("AEDA_AES_CASE fatal_alert_test_handshake");
    access_register(0, 32'(AES_STATUS_OFFSET), 0, 0, status, 1);
    expect_alert(1);
    rd(32'(AES_STATUS_OFFSET), status);
    if (!status[6]) fail("command integrity fault did not set fatal status");
    $display("AEDA_AES_CASE command_integrity_fault");
    reset_peripheral();
    ecb(1, Cipher128, Plaintext);
    $display("AEDA_AES_CASE error_reset_recovery");
  endtask

  initial begin
    logic [31:0] actual;
    extended = $test$plusargs("extended");
    void'($value$plusargs("schedule_seed=%d", seed));
    void'($value$plusargs("reset_cycles=%d", reset_cycles));
    void'($value$plusargs("edn_release_cycles=%d", edn_release_cycles));
    void'($value$plusargs("shadow_release_cycles=%d", shadow_release_cycles));
    void'($value$plusargs("edn_delay=%d", edn_delay));
    void'($value$plusargs("edn_jitter=%d", edn_jitter));
    void'($value$plusargs("tl_jitter=%d", tl_jitter));
    entropy_word = 32'h041a_eda1 ^ seed;
    tl_random = 32'h1edb_3456 ^ seed;
    reset_peripheral();
    ecb(0, Plaintext, Cipher128);
    reset_peripheral();
    $display("AEDA_AES_CASE idle_reset");
    ecb(1, Cipher128, Plaintext);
    access_register(0, 32'h000000f0, 0, 1, actual);
    $display("AEDA_AES_CASE illegal_register");
    if ($test$plusargs("exercise_sideload")) sideload_cases();
    if ($test$plusargs("exercise_errors")) error_cases();
    if (!entropy_words) fail("No EDN transfers observed");
    if (edn_delay > 0 && !edn_wait_edges) fail("No delayed EDN requests observed");
    $display("AEDA_AES_COUNTS %0d %0d %0d", tl_count, entropy_words, edn_wait_edges);
    $display("AEDA_AES_PASS");
    $finish;
  end
endmodule
