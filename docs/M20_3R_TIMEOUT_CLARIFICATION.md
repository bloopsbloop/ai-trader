# M20.3R — Requests Timeout Clarification

## Accepted status

The accepted checkpoint status remains **OFFLINE_ADAPTER_VERIFIED**. This clarification narrows the timing claim; it does not change code, configuration, tests, protocols, prior reports, manifests or frozen artifacts.

## Exact timing scope

The Requests documentation states that connect and read timeouts are not wall-clock deadlines. A connect timeout applies to an individual connection attempt, while a read timeout limits the wait between bytes received from the server. The Requests quickstart also states that `timeout` is not a limit on the complete response download. Consequently, a response body that continues to deliver bytes slowly can keep the request running longer than the configured read-timeout value.

M20.3R executes the blocking Requests call in a worker and waits for that worker after coroutine cancellation. This prevents the adapter from returning while its HTTP work continues invisibly in the background. It does **not** guarantee a fixed return time because Python cancellation does not terminate the blocking Requests operation.

The configured 30-minute value is therefore the target active-capture limit. It is not a demonstrated upper bound on total process lifetime. Likewise, the two-second WebSocket cleanup budget limits WebSocket close handling; it is not a global HTTP-shutdown deadline and does not terminate a pending Requests worker.

A successful future Stage A run would demonstrate behavior for that run only. It would not prove that rare DNS, connection, TLS, proxy, operating-system or slowly streaming response stalls cannot occur in later runs. Any stronger hard wall-clock process bound would require a separately reviewed transport/process-isolation design and is outside M20.3/M20.3R.

## Sources

- [Requests Advanced Usage — Timeouts](https://requests.readthedocs.io/en/stable/user/advanced/#timeouts)
- [Requests Quickstart — Timeouts](https://requests.readthedocs.io/en/latest/user/quickstart/#timeouts)

No real REST/WebSocket connection or Stage A/B execution was performed for this clarification.
