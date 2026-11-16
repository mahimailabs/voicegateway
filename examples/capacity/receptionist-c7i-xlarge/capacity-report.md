# Capacity: demo-runner

**16 calls at once, every call answered. Past that, it refused new calls (worker full).**

Agent server: c7i.xlarge (4 vCPU, 8 GB)

- At 32 calls, 8 never got the agent, 1 was dropped, 23 got it.
- The real limit is between 16 and 31; this run did not test the numbers in between.
- More agent workers or bigger servers raise it.
- For 100 calls at once: 7 servers like these, at the measured limit with no headroom.

| Calls at once | Result | Got the agent | Median answer | p95 answer |
|---:|---|---:|---:|---:|
| 1 | passed | 1 of 1 | 0.58 s | 0.94 s |
| 2 | passed | 2 of 2 | 0.61 s | 1.87 s |
| 4 | passed | 4 of 4 | 0.58 s | 1.31 s |
| 8 | passed | 8 of 8 | 0.62 s | 1.20 s |
| 16 | passed | 16 of 16 | 0.61 s | 1.30 s |
| 32 | failed | 23 of 32 | 0.72 s | 1.77 s |

<details><summary>All measurements</summary>

| Calls | Refused | Dropped | Answers | Over 1 s | Slowest | Slowest join | Tester CPU |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 0 | 0 | 8 | 0% | 0.94 s | 0.30 s | 2% |
| 2 | 0 | 0 | 18 | 11% | 1.87 s | 0.29 s | 2% |
| 4 | 0 | 0 | 36 | 17% | 1.45 s | 0.33 s | 2% |
| 8 | 0 | 0 | 72 | 22% | 1.25 s | 0.56 s | 3% |
| 16 | 0 | 0 | 138 | 23% | 2.74 s | 1.79 s | 3% |
| 32 | 8 | 1 | 7 | 29% | 1.77 s | 1.97 s | 4% |

Each step holds that many calls for 60 s. Callers say one line after each answer; answer time runs from the end of the line to the agent's first sound. Not measured: the agent servers' CPU and memory, time per stage (speech to text, model, voice), phone (SIP) calls, answer quality.

</details>

15 Nov 2026, 14:49 to 14:54 UTC · [Measured with VoiceGateway](https://voicegateway.dev) 1.0.0-preview.11 · run 319eabb4-2216-4d33-86cb-86de7cdecd60
