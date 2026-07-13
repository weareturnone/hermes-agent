# Turn.One patch disposition for upstream sync 2026-07

Upstream base: `bd740f203b44237dbc5c27a2de4d86ef32af4dde` (`upstream/main`, 2026-07-13).

| Patch SHA | Title | Decision | Evidence |
|---|---|---|---|
| `99d474fdcb1557a1e9fb6dbbb5324391408dc48d` | fix(gateway): compact large persisted tool results | DROPPED | Upstream `65e24c942`, `77c5bc9da`, `1965d5621`, and `08ec60277` provide per-result and per-turn tool-output budgets before canonical messages are persisted. |
| `e50a62b8e717353069cfb90246fbcf67508b5a94` | fix(gateway): stop no-progress compression retry loops | DROPPED | Upstream `b18490b89`, `47b6b4cf8`, `90f84144e`, and the `32f30d2a4`..`83000c729` anti-thrash series supersede the local retry-loop guard. |
| `20a7551e6cebff57cdf1eafd4d3265fa47dc492c` | fix(gateway): honor compression threshold in hygiene | CARRIED | Re-applied as `c701a6dbbd5229c52ae2d60c02d6083a99cdf2f8`; upstream gateway hygiene still used a fixed `0.85` trigger and ignored `compression.threshold`. |
| `69352fd8f487d785057af8aed2ae680fd587587a` | fix(gateway): close compaction hygiene review gaps | CARRIED | Re-applied as `dcdfec57497746f19c2d5b1eb25d74d9fd02107a`; retained the shared effective-threshold resolution gap while resolving superseded persistence and retry-loop hunks to upstream. |
| `8ba1116c6a9c690b7c438f346213dfbca1b9fff0` | fix(compression): reject ineffective compaction | CARRIED | Re-applied as `cd44a1ae887dec69b6971dcdea2994ce5ae7fd9a`; current upstream tracked post-boundary anti-thrash verdicts but still accepted a candidate whose message estimate grew. |

## `99d474fdc` — DROPPED

This patch replaced a tool/function result larger than 64 KiB with a metadata placeholder at `SessionStore`, direct agent-flush, transcript-rewrite, and copy-session persistence boundaries. Upstream already routes tool results through `tools/tool_result_storage.py::maybe_persist_tool_result()` before `make_tool_result_message()` in both execution paths in `agent/tool_executor.py`; `65e24c942` introduced that persisted-preview layer, `77c5bc9da` made its per-result and per-turn budgets configurable, `1965d5621` scales the budget to the active context window, and `08ec60277` made large writes reliable by sending content over stdin. Consequently the canonical message that later reaches `SessionDB` contains a bounded preview plus a sandbox path rather than the raw oversized result, while preserving the full output for follow-up reads. Carrying the older persistence-boundary placeholder would duplicate this protection, discard recoverability, and introduce a second non-secret environment setting.

## `e50a62b8e` — DROPPED

This patch stopped a post-tool compression loop when `_last_compress_aborted` was set or message count failed to shrink. Upstream now covers the same failure class more completely: `b18490b89` records empty compression windows as ineffective, `47b6b4cf8` recognizes token reduction even when message count is unchanged, `90f84144e` sends pre-API compression through the canonical cooldown/anti-thrash guard chain, and `32f30d2a4`, `d17244562`, `7f9485707`, `2c6e5877a`, and `83000c729` score a completed boundary against the next real provider usage and suppress repeated futile attempts. Current `agent/conversation_loop.py` also caps payload/context compression attempts and returns `compression_exhausted`, while `agent/conversation_compression.py` skips rotation and rewrite when the compressor returns the original object. That is broader than the local one-site message-count test and avoids rejecting valid same-count token reductions.

## `20a7551e6` — CARRIED

This patch makes gateway session hygiene read `compression.hygiene_threshold` when explicitly set and otherwise follow `compression.threshold`. At the pinned upstream base, `gateway/run.py` still initialized `_hyg_threshold_pct = 0.85` and deliberately read only `compression.enabled`, so a deployment configured at (for example) `0.95` compressed gateway history earlier than the agent loop. The patch was re-applied over the current async context-length resolver, with its full regression file retained, as `c701a6dbbd5229c52ae2d60c02d6083a99cdf2f8`.

## `69352fd8f` — CARRIED

This follow-up centralized threshold resolution so gateway hygiene also honors model-specific thresholds rather than only the global setting. Its persistence-boundary changes were resolved out because the upstream tool-result storage layer described above supersedes them; its message-count progress check was resolved out because `47b6b4cf8` and the later real-usage anti-thrash series supersede it. The remaining threshold resolver was adapted to current upstream model routing: it passes the resolved provider, preserves the `compression.codex_gpt55_autoraise` opt-out, keeps Codex gpt-5.4/5.5/5.6 and spark autoraises raise-only, and retains the unconditional Arcee Trinity override. The carried commit is `dcdfec57497746f19c2d5b1eb25d74d9fd02107a`.

## `8ba1116c6` — CARRIED

This patch rejects an automatically triggered compaction candidate when its post-compaction message estimate is not smaller than its pre-compaction message estimate and the candidate itself is at least the minimum supported context size, returning the original transcript instead of rotating into a larger summary state. Upstream's July anti-thrash series correctly judges completed compactions using the next real provider prompt count, but current `ContextCompressor.compress()` still returned a large candidate when `new_estimate >= pre_estimate`. The re-application keeps upstream's like-for-like message estimate and real-usage verdict ownership, records one ineffective strike because a rejected boundary will receive no provider verdict, sets `_last_compress_aborted`, and leaves `compression_count` unchanged. The carried commit is `cd44a1ae887dec69b6971dcdea2994ce5ae7fd9a`.
