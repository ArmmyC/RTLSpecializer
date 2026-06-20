# Benchmark configurations

Copy `verilog_eval_local_models.example.json` before editing it. Give each model a filesystem-safe `name`, then replace the model identifiers and loopback endpoints with values for services you operate locally.

Keep configuration values safe to commit: do not put API keys, tokens, URL credentials, private hostnames, or other secrets in JSON. If authentication is needed, use an `api_key_env` environment-variable name and keep the value only in your environment.

Start with the network-free dry-run and `--limit 3`. Before a real run, remember that even a local model service can read every submitted prompt and its RTL content.

Benchmark summaries, candidates, raw responses, evaluator results, and review/release workspaces are generated local artifacts. Keep them out of git; inspect `git status` before committing.

See the [first local benchmark runbook](../../docs/eval/first_local_benchmark_runbook.md) for the complete workflow.
