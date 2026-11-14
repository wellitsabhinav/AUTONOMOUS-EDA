# Verification evaluation

## Retained physical results

The pinned ORFS image and existing GCD and Ibex artifacts were read through the controller evidence contract. RTL, SDC and clock periods were preserved. The `physical` and `full` GCD campaign frontiers are empty.

| Run | Clock (ns) | Setup worst slack (ns) | Hold worst slack (ns) | Coverage | Acceptance |
| --- | ---: | ---: | ---: | --- | --- |
| GCD reference | 1.1 | −1.20488 | +0.429847 | BLOCKED | FAILED |
| GCD grid 30% | 1.1 | −1.17831 | +0.431021 | PASSED | FAILED |
| GCD grid 46% | 1.1 | −1.33046 | +0.453976 | PASSED | FAILED |
| GCD fallback 24% | 1.1 | −1.20969 | +0.432164 | PASSED | FAILED |
| GCD live 42% | 1.1 | −1.22107 | +0.421083 | PASSED | FAILED |
| Ibex reference | 8.0 | −0.377013 | +0.426791 | BLOCKED | FAILED |

All six retained runs show passing hold, router, platform DRC, LVS and independent GDS reopen evidence. Setup fails independently in each row. The four candidates completed all seven OpenSTA coverage checks on the final OpenDB, SPEF and SDC at `tt_025C_1v80`. A separate controller GCD checkpoint also completed the coverage probe. The historical reference indexes do not bind final OpenDB and SPEF. Their coverage therefore stays blocked even when the surrounding archive contains those files. The stored ORFS image digest identifies the OpenROAD build, which reports its version as `unknown`.

Full acceptance also requires configured RTL proofs and independent connectivity. The retained physical runs do not have qualifying evidence for those gates. Sequential proofs, formal properties, complex timing exceptions, multi-corner evaluation and hierarchical checks remain unresolved. No new physical implementation was required for this evaluation.

## Real RTL regression and proof

The [fixture](../examples/verification/manifest.toml) contains a two-input XOR design, an exhaustive Icarus oracle and a separately expressed XOR reference. In a disposable copy, replace `a ^ b` with `a | b` in `logic.sv`. Yosys structural elaboration still passes. Icarus 12.0 reports `LOGIC_MISMATCH` with `a=1 b=1 expected=0 observed=1`. Yosys 0.33 reports a SAT model with both inputs high and the mismatch trigger set. The failing input and observed output identify a Boolean behavior regression in the output assignment. Restoring XOR makes both checks pass and preserves the prior attempt receipts.

~~~sh
cp -R examples/verification experiments/logic-negative
sed -i 's/a \^ b/a | b/' experiments/logic-negative/logic.sv
autonomous-eda verify experiments/logic-negative --profile rtl-formal --render
cp examples/verification/logic.sv experiments/logic-negative/logic.sv
autonomous-eda resume experiments/logic-negative
~~~

The native Windows Icarus distribution initially failed to launch its compiler from a path containing spaces. The regression evidence was collected with Icarus 12.0 from Ubuntu's `12.0-2build2` package in an isolated workspace. The two-state proof ran with Ubuntu Yosys 0.33 and was also exercised with native Windows Yosys 0.30+48.

The retained [Icarus failure log](images/examples/verification-regression/simulate.stdout.log) and [Yosys SAT counterexample](images/examples/verification-regression/prove.stdout.log) are listed by SHA-256 in the [regression matrix](images/examples/verification-regression/acceptance.json).

See the [example gallery](EXAMPLE_GALLERY.md) for provenance-backed acceptance figures and the [optimization evaluation](OPTIMIZATION_EVALUATION.md) for the original physical campaign measurements.
