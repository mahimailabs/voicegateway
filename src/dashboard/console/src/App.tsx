import { OpenOrcaProvider, OpenOrcaDashboard } from '@openorca-ui/react/theme';

// VoiceGateway serves the OpenOrca runtime contract natively at /openorca/*
// (same origin as this console), so point straight at it: the live fleet of
// agents, their calls as tasks, and guardrail events as interventions to
// resolve. In the default self-hosted (no static keys) setup those endpoints
// are reachable without credentials; keep the console on a trusted network or
// behind a credential-injecting proxy for a multi-tenant deployment.
const runtimeConfig = {
  snapshotUrl: '/openorca/snapshot',
  eventsUrl: '/openorca/events',
  resolveInterventionUrl: '/openorca/interventions/resolve',
  runtimeInfoUrl: '/openorca/runtime-info',
};

export default function App() {
  return (
    <OpenOrcaProvider>
      <OpenOrcaDashboard mode="runtime" runtimeConfig={runtimeConfig} />
    </OpenOrcaProvider>
  );
}
