"""V1.5 memory layer — diff-since-last-research.

Persists each successful research run's findings dict to a per-account
JSONL file. On the next run for the same account, we load the most
recent snapshot, diff against the new findings (keyed by source_url),
and surface a "🆕 New since [date]" Block Kit section above the
standard research output.

Failure-mode contract: every entry point fails open. A snapshot read
or write that raises must not break the user-facing research response.
"""
