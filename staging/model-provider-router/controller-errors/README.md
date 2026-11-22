# Local inference admission errors

The controller retains its existing bounded queue and shared cold-start behavior.
At an admission timeout it now reports the observed state:

| HTTP | Code | Meaning | Retry-After |
| --- | --- | --- | --- |
| 429 | `concurrency_limit_exceeded` | Another request or GPU lease occupies the generation slot | 15 seconds |
| 503 | `resource_not_ready` | Shared startup is still running | 15 seconds |
| 503 | `resource_not_ready` | Stop/profile transition is still running | 15 seconds |
| 409 | `resource_busy` | Other GPU work or desktop policy prevents admission | Omitted; free resources |
| 409 | `resource_conflict` | Existing profile conflicts with desktop session | Omitted; resolve conflict |
| 502 | `initialization_failed` | Startup failed; inspect local status/logs | Remaining startup cooldown, when positive |
| 503 | `resource_unavailable` | No more specific observed state | 15 seconds |

Startup diagnostics remain in authenticated controller status and local logs.
Public error bodies never include the raw startup exception or command output.

`../admission_errors.py` is shared by the PC controller and VPS router. The native
Messages handler can omit upstream codes and retry headers. For exact known local
status/message pairs, the router restores the code and a conservative retry delay
for busy/loading/transitioning responses. Existing retry headers are retained;
unknown provider errors are unchanged. It does not invent a startup-failure retry
delay if the native handler omitted it.

The source-pinned `prepare.py` creates the controller candidate. `deploy.py` waits
for an idle GPU and worker lock before updating the controller, shared helper and
verified source pins, then restarts the controller without stopping the model.
Backups are retained. The router and its helper are installed on the VPS.

Validation: 33 CPU-only controller HTTP tests and 26 router/native-binary tests
pass in network namespaces with only loopback enabled. The controller test
snapshot also updates three stale assertions to the existing 92k desktop profile,
context length and unified stack launcher; these runtime behaviors are unchanged.

The application codes use generic resource/failure terms. Loading and profile
transitions share `resource_not_ready`, with distinct human-readable messages.
`concurrency_limit_exceeded` identifies the retryable generation-slot limit;
`resource_busy` identifies a GPU/desktop resource conflict. HTTP statuses and
retry behavior retain their meanings. `deploy_codes.py` installs the renamed
controller catalog in an idle window and updates its verified source pin.
