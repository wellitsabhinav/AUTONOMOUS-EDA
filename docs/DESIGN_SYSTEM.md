# Documentation design system

Use one H1 per guide. Start major sections at H2 and use H3 for their subdivisions. Use sentence case and preserve names and acronyms. Leave a blank line around headings, lists, tables and fenced examples. Use inline code for identifiers, filenames and options.

Give each command or configuration block a language. Use `sh` for POSIX commands, `powershell` for Windows commands, and `toml` for manifests. Keep commands runnable in the environment named in the prose. Use repository-relative links and descriptive labels. In a new guide, link the related workflow and the evidence it consumes.

## Typography and color

GitHub supplies the typography for Markdown. Use ordinary emphasis, inline code and heading hierarchy. For a future HTML site, use `Inter, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif` for prose and `"JetBrains Mono", "Cascadia Code", Consolas, monospace` for technical text. Use locally installed fonts and system fallbacks.

Generated engineering charts use the shared Matplotlib theme. The canvas is white, labels are dark navy, blue identifies primary observations, and gray carries metadata. Green marks passes, amber marks blocked checks, and red marks failures. Write the status in text on the plot or in its caption. KLayout preserves the layer mapping and actual design geometry.

## Metrics and terminology

Spell out static timing analysis (STA), design rule checking (DRC), layout versus schematic (LVS), worst negative slack (WNS), power, performance, and area (PPA), and OpenROAD Flow Scripts (ORFS) at first substantive use in a standalone guide. Use RTL-to-GDSII and IO pad ring. Preserve identifiers exactly in machine-readable examples.

Put units in table column headings or axis labels. Use ns for timing, µm for length and µm² for area in prose and figures. Name the metric explicitly. Standard-cell area, core area and total die area describe different measurements. Mark measurements, estimates and unavailable values explicitly. Use consistent precision within a comparison and preserve published values.

## Status and evidence

| Status | Meaning |
| --- | --- |
| `PASSED` | The check completed with verified passing evidence. |
| `FAILED` | Evidence establishes a violation or mismatch. |
| `BLOCKED` | Required evidence or execution is unavailable or unresolved. |
| `UNKNOWN` | The evidence does not establish an outcome. |
| `NOT_APPLICABLE` | An explicit applicability rule excludes the check. |

Keep command output stable for automation. Use plain text in scripts and non-interactive sessions. Reserve color for an explicitly styled terminal view and honor `NO_COLOR`.

Use informative alt text, a short caption, and comparable display widths for gallery figures. Captions state the run, toolchain or corner where relevant, and the measured outcome. Link the source evidence and provenance. Published output and raw tool logs retain their original bytes. New renders carry their actual renderer settings and hashes.

## Maintenance

Run `python -m pytest -q tests/test_documentation.py tests/test_visual_artifacts.py` after editing docs or figures. The checks inspect maintained Markdown and publication manifests without rewriting evidence. Historical evidence is intentionally retained with its original rendering settings.

GitHub Markdown remains the primary entry point. The [README](../README.md) and [gallery](EXAMPLE_GALLERY.md) provide navigation without a site build or new dependencies.
