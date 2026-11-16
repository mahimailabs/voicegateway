# Capacity run: a receptionist agent on one c7i.xlarge

A real capacity test, run on 15 Nov 2026 from 14:49 to 14:54 UTC (run `319eabb4-2216-4d33-86cb-86de7cdecd60`, VoiceGateway 1.0.0-preview.11).

- **Agent:** a receptionist voice agent built on LiveKit Agents, dispatched as `demo-runner`, unchanged for the test. It ran on one AWS c7i.xlarge server (4 vCPU, 8 GB) in ca-central-1, with a self-hosted LiveKit server 1.9.11.
- **Tester:** a separate c7i.2xlarge server (8 vCPU) ran the callers, at 2 to 4% CPU.
- **Test:** steps of 1, 2, 4, 8, 16 and 32 calls at once, 60 s each. The 32-call step ended after 3.9 s, when the agent refused new calls, so its answer times cover a much shorter window than the other steps.
- **Result:** 16 calls at once, every call answered, median answer 0.61 s. At 32 the agent refused 8 new calls (worker full) and dropped 1.

This run came before the re-test step existed, so its limit is a range (16 to 31). The command now tests the numbers in between and reports one number.

## Files

- [capacity-report.html](capacity-report.html): the report page. Download it and open it in a browser. It works offline, in light and dark.
- [capacity-report.md](capacity-report.md): the same report as Markdown.
- [capacity-report.json](capacity-report.json): every measurement, including each answer and join time.
- [terminal.txt](terminal.txt): the summary the command prints when the run ends.

The files were written by the report code on `main` from the run's stored result, with `--agent-server "c7i.xlarge (4 vCPU, 8 GB)" --agent-servers 1 --target-calls 100`. The HTML and Markdown match the report sent on the day byte for byte. The LiveKit host is left out.
