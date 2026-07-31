{#
  Check-in messages sent by the replicate heartbeat loop
  (runner.py::_replicate_with_heartbeat) between resumed invocations of the
  same agent session. Rendered one `kind` at a time:

    continue      routine resume — keep going from where you left off
    stuck         resume after more than STALL_THRESHOLD ticks with no
                  objective progress
    wrap_up       the budget is spent; finalize evidence, do not start work
    fresh_session appended to the full session instructions when a resume
                  failed and a replacement session has to pick the run up
                  from disk with no memory of it

  These are agent-facing prompts and live here for the same reason every
  other one does: so they get reviewed as prompts. The shared paragraphs are
  macros — `continue` and `stuck` used to carry ~90 duplicated words each,
  which is exactly where drift starts.
#}
{%- macro no_one_answers() -%}
This is a non-interactive run: no one will read or answer a question you ask
{%- endmacro -%}

{%- macro no_background() -%}
Do not use the Monitor tool or run_in_background for anything you need the result of before ending your turn — verified by testing, 'I'll wait for it' followed by ending your turn silently abandons the work. If a step you started is still running detached, poll for its real completion with repeated separate foreground calls (e.g. `sleep 30; test -f <marker> && echo READY || echo NOT_READY`, issued again and again) rather than restarting it or assuming it's done.
{%- endmacro -%}

{%- if kind == "continue" -%}
Continue the replication plan from where you left off; do not repeat completed steps. {{ no_one_answers() }}. If the last attempt failed or was interrupted, decide how to proceed yourself (retry, adjust approach, or record the failure in that step's notes) rather than stopping to ask for guidance. {{ no_background() }}
{%- elif kind == "stuck" -%}
No progress across the last few check-ins. Don't repeat the same approach — try a genuinely different strategy for the current step, or move on if it's blocked, and say why in that step's notes. {{ no_one_answers() }}, so make the call yourself and record your reasoning in notes instead of stopping to ask. {{ no_background() }}
{%- elif kind == "wrap_up" -%}
You've reached the time budget. Don't start new work — finalize replication_log.json to reflect exactly what you completed. This is a non-interactive run and this is your last turn: no one will read or answer a question, and there is no next turn to poll in — do not background or defer anything now. If the last step failed or was interrupted, record that honestly in its notes rather than asking what to do — just write the file and stop.
{%- elif kind == "fresh_session" -%}
NOTE: an earlier agent session worked on this run and was interrupted; its memory is gone but its work is not. Before doing anything else, read replication_log.json and look at the codebase to see which steps already ran, and continue from there — do not redo completed steps and do not start the plan over. {{ no_one_answers() }}.
{%- endif -%}
