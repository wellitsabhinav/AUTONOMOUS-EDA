# Model gateway

The provider interface accepts a bounded request and returns structured selection text with optional token usage and reported model identity. The supervisor supplies a task-specific schema and revalidates all IDs. The accepted response selects a constrained interpretation and optional hypotheses and measurements from the supplied list. Every number, gate conclusion, and citation in the rendered result is supplied by the evidence builder. The journal records the selected IDs and output. Hidden reasoning and raw provider responses are discarded.

| Adapter | Behavior |
| --- | --- |
| `disabled` | Observe remains available. Explain reports `inference_disabled` with any verified partial observation. |
| `mock` | Deterministic fixture selection. Results carry `fixture=true`. |
| `local` | HTTP POST to an explicit `127.0.0.1` or `[::1]` port at `/v1/chat/completions`. Requests use the schema form documented by [llama.cpp](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md). |
| `openai` | HTTPS POST to the fixed OpenAI `/v1/responses` endpoint with `store=false` and strict `text.format`. See the [official OpenAI documentation](https://developers.openai.com/api/reference/resources/responses/methods/create). |

## Run a local model

Install a trusted CPU-capable server and model through your normal local provisioning process. The supervisor connects to an already running server and does not download models. The acceptance run used llama.cpp `b12361` with Qwen2.5-0.5B-Instruct Q4_K_M.

```sh
llama-server -m /path/to/qwen2.5-0.5b-instruct-q4_k_m.gguf --alias qwen2.5-0.5b --host 127.0.0.1 --port 18116 -c 8192 -t 6 -ngl 0 --parallel 1
```

Configure `adapter = "local"`, `model = "qwen2.5-0.5b"`, and `endpoint = "http://127.0.0.1:18116/v1/chat/completions"`. Other local models can be configured when the server supports the same schema format. The client connects directly to the numeric loopback address. There is no DNS, proxy, redirect, or external API request on the local route. Use a trusted local server with its own network and logging configuration. Default CI uses deterministic or mock HTTP responses.

## Hosted opt-in and accounting

Set `hosted_enabled = true` in the policy, configure `adapter = "openai"`, supply a model ID that supports the Responses API's Structured Outputs, and set `OPENAI_API_KEY` through the process environment. The per-request `explain --consent-hosted` flag authorizes private engineering data for that workflow. The workflow treats all prompts as private engineering data, including user questions, measurements, derived path fields, and approved reference excerpts. No raw RTL is collected for inference. Review applicable provider retention terms before enabling hosted use. Hosted behavior was validated with fixtures.

Set `input_microdollars_per_million`, `output_microdollars_per_million`, and a nonzero `cost_budget_microdollars` from your provider pricing and intended budget. Use current configured rates for estimates. Before sending, the gateway reserves serialized request bytes plus 256 framing tokens and the configured output allowance. It settles accounting from reported usage when both counts are available and otherwise keeps the reservation. Cost accounting is an estimate under those rates, and is independent of the provider's billing controls. Each attempt is bounded by request time, workflow deadline, maximum output tokens, and a 64 KiB response body. Transient errors and malformed selections may retry within the same budget. Hosted timeouts and uncertain interrupted hosted dispatches stop without a retry.

The journal stores context and input digests, authorized question text, normalized evidence output, selection IDs, accounting and sanitized status codes. Protect it as private engineering data. API credentials are read only at the transport boundary and are excluded from the journal. Secret detection is heuristic. Keep credentials out of user questions, source material, model aliases and policies.
