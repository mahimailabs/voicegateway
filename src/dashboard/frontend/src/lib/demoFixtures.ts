// Seeded demo fixtures for the VoiceGateway dashboard.
//
// When DEMO_MODE is on (VITE_DEMO=1 / `npm run build:demo`), lib/api.ts's
// fetchJson() dynamically imports this module and routes every request through
// `demoFetch` instead of the network. That turns the real React SPA into a
// no-login, read-only "command center" running entirely on static JSON: no
// backend, no LiveKit, no providers. Everything below is hand-seeded to look
// like a small but real fleet so the dashboard renders with data on first paint.
//
// Rules this file follows so the demo is honest and deterministic:
//   - Every fixture is typed with the imported dashboard types, so a shape drift
//     surfaces at `tsc` time rather than as a blank card in the browser.
//   - All timestamps are fixed literals anchored on BASE_EPOCH (never Date.now(),
//     never Math.random()) so screenshots are stable across runs.
//   - Probe times are SECONDS (matching AgentProbeResult); latency_ms / TTFB
//     fields are milliseconds; CostByDayPoint.day and last_seen are epoch seconds.
//   - Numbers are plausible, not round-zeros where a real value belongs, and no
//     fixture impersonates a real customer (generic agent + project names only).
//   - This is READ ONLY: any mutating method throws, and so does the probe POST
//     (a probe is a billed call — disabled in the demo).

import type {
  AgentRow,
  AgentsResponse,
  ApiKey,
  CostByDayPoint,
  CostsResponse,
  DiagnosticRun,
  DiagnosticsCreds,
  LatencyResponse,
  MetricsAggregate,
  OverviewResponse,
  ProjectBrandingResponse,
  ReplayResponse,
  ServerOverview,
  SessionDetail,
  SessionRow,
  StatusResponse,
  TurnRow,
} from './types';

// Fixed clock anchor. 1785000000 is 2026-07-24T02:40:00Z; every timestamp below
// is this base plus/minus a literal offset so the demo never drifts.
const BASE_EPOCH = 1785000000; // epoch seconds
const MIN = 60;
const HOUR = 3600;
const DAY = 86400;

// ---------------------------------------------------------------------------
// auth-status: MUST be false so the demo skips the login gate entirely.
// ---------------------------------------------------------------------------

const AUTH_STATUS = { auth_required: false } as const;

// ---------------------------------------------------------------------------
// status + overview
// ---------------------------------------------------------------------------

const STATUS: StatusResponse = {
  version: '0.20.1',
  providers: {
    openai: { configured: true, type: 'llm' },
    deepgram: { configured: true, type: 'stt' },
    cartesia: { configured: true, type: 'tts' },
    anthropic: { configured: true, type: 'llm' },
    elevenlabs: { configured: true, type: 'tts' },
    assemblyai: { configured: true, type: 'stt' },
    groq: { configured: false, type: 'llm' },
  },
  models: {
    'deepgram/nova-3': { modality: 'stt', provider: 'deepgram' },
    'assemblyai/universal': { modality: 'stt', provider: 'assemblyai' },
    'openai/gpt-4o-mini': { modality: 'llm', provider: 'openai' },
    'anthropic/claude-haiku-4-5': { modality: 'llm', provider: 'anthropic' },
    'cartesia/sonic-2': { modality: 'tts', provider: 'cartesia' },
    'elevenlabs/eleven_turbo_v2': { modality: 'tts', provider: 'elevenlabs' },
  },
  fallbacks: {
    'openai/gpt-4o-mini': ['anthropic/claude-haiku-4-5'],
  },
};

const OVERVIEW: OverviewResponse = {
  total_requests: 48213,
  total_cost_today: 7.42,
  total_cost_all: 318.96,
  active_models: 6,
  providers_configured: 6,
};

// ---------------------------------------------------------------------------
// Fleet — the centerpiece. Four distinct agents across different provider
// stacks, costs, and latencies, showcasing what VoiceGateway measures.
// ---------------------------------------------------------------------------

// support-voice: busy, full telemetry + a healthy cached probe.
const AGENT_SUPPORT: AgentRow = {
  agent_id: 'support-voice',
  agent_name: 'support-voice',
  request_count: 3184,
  total_cost_usd: 2.9137,
  last_seen: BASE_EPOCH - 45, // ~45s ago
  error_rate: 0.006,
  p95_latency_ms: 812,
  cpu_pct: 22,
  memory_pct: 41,
  resources: {
    cpu_pct: 22,
    memory_pct: 41,
    memory_rss_bytes: 512 * 1024 * 1024, // ~512 MB
    memory_total_bytes: 4 * 1024 * 1024 * 1024, // 4 GB
  },
  models: {
    stt: 'deepgram/nova-3',
    llm: 'openai/gpt-4o-mini',
    tts: 'cartesia/sonic-2',
  },
  latency_ms: { stt: 168, llm: 402, tts: 214 },
  fleet_status: 'busy',
  probe: {
    eligible: false,
    dispatch_name: null,
    mode: null,
    reason: 'Probing is disabled in the demo',
  },
  latency_probe: {
    components: {
      eou: 0.121,
      stt: 0.164,
      stt_ttfp: 0.142,
      stt_transcription_delay: 0.088,
      llm_ttft: 0.396,
      tts: 0.211,
    },
    e2e: { avg: 0.94, p50: 0.9, p95: 1.28, min: 0.71, max: 1.44, trials: 3 },
    cost_usd: 0.0021,
    models: {
      stt: 'deepgram/nova-3',
      llm: 'openai/gpt-4o-mini',
      tts: 'cartesia/sonic-2',
    },
    mode: 'explicit',
    dispatch_name: 'support-voice',
    error: null,
    created_at: BASE_EPOCH - 2 * HOUR,
  },
};

// outbound-sales: idle, premium stack (Anthropic + ElevenLabs), probe NEVER run.
const AGENT_SALES: AgentRow = {
  agent_id: 'outbound-sales',
  agent_name: 'outbound-sales',
  request_count: 1276,
  total_cost_usd: 4.1082,
  last_seen: BASE_EPOCH - 22 * MIN, // ~22m ago
  error_rate: 0.014,
  p95_latency_ms: 1043,
  cpu_pct: 9,
  memory_pct: 33,
  resources: {
    cpu_pct: 9,
    memory_pct: 33,
    memory_rss_bytes: 386 * 1024 * 1024, // ~386 MB
    memory_total_bytes: 2 * 1024 * 1024 * 1024, // 2 GB
  },
  models: {
    stt: 'assemblyai/universal',
    llm: 'anthropic/claude-haiku-4-5',
    tts: 'elevenlabs/eleven_turbo_v2',
  },
  latency_ms: { stt: 241, llm: 588, tts: 302 },
  fleet_status: 'idle',
  probe: {
    eligible: false,
    dispatch_name: null,
    mode: null,
    reason: 'Probing is disabled in the demo',
  },
  // Never probed: shows the honest "no cached sample" state.
  latency_probe: null,
};

// reception: busy, mixed stack, probe cached but ERRORED (worker didn't join).
const AGENT_RECEPTION: AgentRow = {
  agent_id: 'reception',
  agent_name: 'reception',
  request_count: 5921,
  total_cost_usd: 1.4368,
  last_seen: BASE_EPOCH - 12, // ~12s ago
  error_rate: 0.021,
  p95_latency_ms: 736,
  cpu_pct: 37,
  memory_pct: 58,
  resources: {
    cpu_pct: 37,
    memory_pct: 58,
    memory_rss_bytes: 742 * 1024 * 1024, // ~742 MB
    memory_total_bytes: 4 * 1024 * 1024 * 1024, // 4 GB
  },
  models: {
    stt: 'deepgram/nova-3',
    llm: 'anthropic/claude-haiku-4-5',
    tts: 'elevenlabs/eleven_turbo_v2',
  },
  latency_ms: { stt: 155, llm: 471, tts: 268 },
  fleet_status: 'busy',
  probe: {
    eligible: false,
    dispatch_name: null,
    mode: null,
    reason: 'Probing is disabled in the demo',
  },
  // Errored probe: the honest "we tried, it did not complete" state.
  latency_probe: {
    components: null,
    e2e: null,
    cost_usd: null,
    models: {
      stt: 'deepgram/nova-3',
      llm: 'anthropic/claude-haiku-4-5',
      tts: 'elevenlabs/eleven_turbo_v2',
    },
    mode: 'automatic',
    dispatch_name: 'reception',
    error: 'worker did not join in time',
    created_at: BASE_EPOCH - 5 * HOUR,
  },
};

// survey-bot: telemetry-only (no heartbeat) — null resources, offline/null
// presence, and a cached probe from a remote-sink agent (no split measured here).
const AGENT_SURVEY: AgentRow = {
  agent_id: 'survey-bot',
  agent_name: null, // telemetry-only: UI falls back to agent_id
  request_count: 842,
  total_cost_usd: 0.5216,
  last_seen: BASE_EPOCH - 3 * HOUR, // ~3h ago
  error_rate: 0.0,
  p95_latency_ms: 690,
  cpu_pct: null,
  memory_pct: null,
  // resources omitted (undefined): not a heartbeating worker, so
  // ResourceMeter renders the honest "not sampled" state. The AgentRow type
  // allows undefined (optional) but not null here.
  models: {
    stt: 'assemblyai/universal',
    llm: 'openai/gpt-4o-mini',
    tts: 'cartesia/sonic-2',
  },
  latency_ms: { stt: 198, llm: 351, tts: 176 },
  fleet_status: null, // telemetry-only, no roster presence
  probe: {
    eligible: false,
    dispatch_name: null,
    mode: null,
    reason: 'Probing is disabled in the demo',
  },
  latency_probe: {
    components: {
      stt: 0.196,
      stt_ttfp: 0.161,
      llm_ttft: 0.348,
      tts: 0.174,
    },
    e2e: { avg: 0.81, p50: 0.79, p95: 1.02, min: 0.66, max: 1.11, trials: 2 },
    cost_usd: 0.0009,
    models: {
      stt: 'assemblyai/universal',
      llm: 'openai/gpt-4o-mini',
      tts: 'cartesia/sonic-2',
    },
    mode: 'automatic',
    dispatch_name: 'survey-bot',
    error: null,
    created_at: BASE_EPOCH - 8 * HOUR,
  },
};

const FLEET: AgentRow[] = [
  AGENT_SUPPORT,
  AGENT_SALES,
  AGENT_RECEPTION,
  AGENT_SURVEY,
];

const AGENTS_RESPONSE: AgentsResponse = {
  agents: FLEET,
  unattributed: {
    request_count: 217,
    total_cost_usd: 0.1834,
    last_seen: BASE_EPOCH - 6 * HOUR,
    error_rate: 0.009,
  },
};

// ---------------------------------------------------------------------------
// costs
// ---------------------------------------------------------------------------

const COSTS: CostsResponse = {
  period: 'week',
  project: null,
  total: 42.87,
  by_provider: {
    openai: { cost: 11.94, requests: 18422 },
    anthropic: { cost: 14.02, requests: 9310 },
    deepgram: { cost: 6.71, requests: 14880 },
    assemblyai: { cost: 2.38, requests: 4102 },
    cartesia: { cost: 4.12, requests: 12006 },
    elevenlabs: { cost: 3.7, requests: 6740 },
  },
  by_model: {
    'openai/gpt-4o-mini': {
      cost: 11.94,
      requests: 18422,
      pricing_source: 'voice-prices',
    },
    'anthropic/claude-haiku-4-5': {
      cost: 14.02,
      requests: 9310,
      pricing_source: 'voice-prices',
    },
    'deepgram/nova-3': {
      cost: 6.71,
      requests: 14880,
      pricing_source: 'voice-prices',
    },
    'assemblyai/universal': {
      cost: 2.38,
      requests: 4102,
      pricing_source: 'voice-prices',
    },
    'cartesia/sonic-2': {
      cost: 4.12,
      requests: 12006,
      pricing_source: 'voice-prices',
    },
    'elevenlabs/eleven_turbo_v2': {
      cost: 3.7,
      requests: 6740,
      pricing_source: 'voice-prices',
    },
  },
  by_project: {
    'customer-support': { cost: 21.4, requests: 29880 },
    'sales-outreach': { cost: 15.83, requests: 12440 },
    default: { cost: 5.64, requests: 5140 },
  },
  pricing_sources: {
    llm: 'voice-prices',
    stt: 'voice-prices',
    tts: 'voice-prices',
  },
};

// Seven daily points, gently rising. day = start-of-day epoch SECONDS.
const COSTS_BY_DAY: CostByDayPoint[] = [
  { day: BASE_EPOCH - 6 * DAY, cost: 4.18, requests: 6120 },
  { day: BASE_EPOCH - 5 * DAY, cost: 4.62, requests: 6540 },
  { day: BASE_EPOCH - 4 * DAY, cost: 5.03, requests: 6980 },
  { day: BASE_EPOCH - 3 * DAY, cost: 5.71, requests: 7410 },
  { day: BASE_EPOCH - 2 * DAY, cost: 6.24, requests: 7860 },
  { day: BASE_EPOCH - 1 * DAY, cost: 6.98, requests: 8330 },
  { day: BASE_EPOCH - 0 * DAY, cost: 7.42, requests: 8790 },
];

// ---------------------------------------------------------------------------
// latency: Record<modelName, LatencyStats>. TTFB fields are milliseconds.
// ---------------------------------------------------------------------------

const LATENCY: LatencyResponse = {
  'deepgram/nova-3': {
    avg_ttfb_ms: 164,
    avg_latency_ms: 292,
    request_count: 14880,
    ttfb_percentiles: { p50: 152, p95: 238, p99: 301 },
    latency_percentiles: { p50: 274, p95: 388, p99: 452 },
  },
  'assemblyai/universal': {
    avg_ttfb_ms: 236,
    avg_latency_ms: 371,
    request_count: 4102,
    ttfb_percentiles: { p50: 221, p95: 318, p99: 402 },
    latency_percentiles: { p50: 352, p95: 486, p99: 560 },
  },
  'openai/gpt-4o-mini': {
    avg_ttfb_ms: 398,
    avg_latency_ms: 742,
    request_count: 18422,
    ttfb_percentiles: { p50: 372, p95: 561, p99: 690 },
    latency_percentiles: { p50: 706, p95: 1024, p99: 1288 },
  },
  'anthropic/claude-haiku-4-5': {
    avg_ttfb_ms: 486,
    avg_latency_ms: 903,
    request_count: 9310,
    ttfb_percentiles: { p50: 452, p95: 648, p99: 812 },
    latency_percentiles: { p50: 862, p95: 1210, p99: 1466 },
  },
  'cartesia/sonic-2': {
    avg_ttfb_ms: 208,
    avg_latency_ms: 361,
    request_count: 12006,
    ttfb_percentiles: { p50: 194, p95: 292, p99: 358 },
    latency_percentiles: { p50: 342, p95: 474, p99: 548 },
  },
  'elevenlabs/eleven_turbo_v2': {
    avg_ttfb_ms: 296,
    avg_latency_ms: 468,
    request_count: 6740,
    ttfb_percentiles: { p50: 278, p95: 402, p99: 498 },
    latency_percentiles: { p50: 446, p95: 612, p99: 704 },
  },
};

// ---------------------------------------------------------------------------
// metrics (voice-conversation aggregate)
// ---------------------------------------------------------------------------

const METRICS: MetricsAggregate = {
  window: {
    days: 7,
    since: '2026-07-17T00:00:00Z',
    until: '2026-07-24T00:00:00Z',
  },
  filter: { project: null },
  session_count: 1842,
  measured_session_count: 1691,
  per_minute_cost_usd_avg: 0.0231,
  response_speed_ms: { p50: 812, p95: 1284 },
  talk_over_rate: 0.037,
  dead_air_event_count: 24,
};

// ---------------------------------------------------------------------------
// server overview: live LiveKit topology + fleet, cost-annotated.
// ---------------------------------------------------------------------------

const SERVER_OVERVIEW: ServerOverview = {
  generated_at: BASE_EPOCH - 8,
  connection: {
    configured: true,
    url: 'wss://demo-voicegw.livekit.cloud',
    reachable: true,
  },
  rooms: {
    ok: true,
    error: null,
    rooms: [
      {
        name: 'support-call-7f21',
        humans: 1,
        agents: [
          {
            agent_name: 'support-voice',
            identity: 'agent-support-01',
            state: 'active',
            age_s: 132,
          },
        ],
        cost_usd: 0.0142,
        request_count: 38,
        p95_latency_ms: 804,
      },
      {
        name: 'reception-inbound-a903',
        humans: 1,
        agents: [
          {
            agent_name: 'reception',
            identity: 'agent-reception-02',
            state: 'active',
            age_s: 54,
          },
          {
            agent_name: 'reception',
            identity: null,
            state: 'dispatched',
            age_s: null,
          },
        ],
        cost_usd: 0.0088,
        request_count: 21,
        p95_latency_ms: 712,
      },
    ],
  },
  egress: {
    ok: true,
    error: null,
    items: [
      {
        egress_id: 'EG_demo7a21',
        status: 'EGRESS_ACTIVE',
        source_type: 'ROOM_COMPOSITE',
        room_name: 'support-call-7f21',
        started_at: (BASE_EPOCH - 130) * 1_000_000_000, // unix nanoseconds
      },
    ],
  },
  ingress: {
    ok: true,
    error: null,
    items: [
      {
        ingress_id: 'IN_demo4c88',
        name: 'pstn-bridge',
        input_type: 'RTMP_INPUT',
        room_name: 'reception-inbound-a903',
        status: 'ENDPOINT_PUBLISHING',
      },
    ],
  },
  sip: {
    ok: true,
    error: null,
    inbound: [
      {
        trunk_id: 'ST_inbound_demo',
        name: 'main-line',
        numbers: ['+15195550101'],
      },
    ],
    outbound: [
      {
        trunk_id: 'ST_outbound_demo',
        name: 'sales-dialer',
        address: 'sip.demo-carrier.example.com',
        transport: 'SIP_TRANSPORT_TCP',
        numbers: ['+15195550188'],
      },
    ],
    dispatch_rules: [
      {
        rule_id: 'SDR_demo_01',
        name: 'route-to-reception',
        trunk_ids: ['ST_inbound_demo'],
      },
    ],
  },
  fleet: {
    ok: true,
    error: null,
    workers: [
      {
        agent_id: 'support-voice',
        agent_name: 'support-voice',
        region: 'us-east-1',
        host: 'worker-01',
        version: '0.20.1',
        status: 'busy',
        active_sessions: 2,
        last_seen: BASE_EPOCH - 45,
        memory_pct: 41,
      },
      {
        agent_id: 'reception',
        agent_name: 'reception',
        region: 'us-east-1',
        host: 'worker-02',
        version: '0.20.1',
        status: 'busy',
        active_sessions: 1,
        last_seen: BASE_EPOCH - 12,
        memory_pct: 58,
      },
      {
        agent_id: 'outbound-sales',
        agent_name: 'outbound-sales',
        region: 'us-west-2',
        host: 'worker-03',
        version: '0.20.0',
        status: 'idle',
        active_sessions: 0,
        last_seen: BASE_EPOCH - 22 * MIN,
        memory_pct: 33,
      },
      {
        agent_id: 'nightly-batch',
        agent_name: 'nightly-batch',
        region: 'us-west-2',
        host: 'worker-04',
        version: '0.20.0',
        status: 'offline',
        active_sessions: 0,
        last_seen: BASE_EPOCH - 9 * HOUR,
        memory_pct: null,
      },
    ],
    counts: { total: 4, idle: 1, busy: 2, offline: 1 },
  },
};

// ---------------------------------------------------------------------------
// projects
// ---------------------------------------------------------------------------

interface ProjectEntry {
  id: string;
  name: string;
  description: string;
  daily_budget: number;
  budget_action: string;
  tags: string[];
  default_stack: string;
  accent: string;
  source?: string;
}

interface ProjectStats {
  requests_today: number;
  cost_today: number;
}

const PROJECTS: { projects: ProjectEntry[]; stats: Record<string, ProjectStats> } = {
  projects: [
    {
      id: 'customer-support',
      name: 'Customer Support',
      description: 'Inbound support line, deepgram + gpt-4o-mini + sonic',
      daily_budget: 25,
      budget_action: 'warn',
      tags: ['inbound', 'support'],
      default_stack: 'deepgram/nova-3 + openai/gpt-4o-mini + cartesia/sonic-2',
      accent: 'blue',
      source: 'yaml',
    },
    {
      id: 'sales-outreach',
      name: 'Sales Outreach',
      description: 'Outbound dialer, assemblyai + claude-haiku + elevenlabs',
      daily_budget: 20,
      budget_action: 'throttle',
      tags: ['outbound', 'sales'],
      default_stack:
        'assemblyai/universal + anthropic/claude-haiku-4-5 + elevenlabs/eleven_turbo_v2',
      accent: 'green',
      source: 'db',
    },
    {
      id: 'default',
      name: 'Default',
      description: 'Catch-all project for unattributed traffic',
      daily_budget: 10,
      budget_action: 'warn',
      tags: [],
      default_stack: 'deepgram/nova-3 + openai/gpt-4o-mini + cartesia/sonic-2',
      accent: 'orange',
      source: 'auto',
    },
  ],
  stats: {
    'customer-support': { requests_today: 4210, cost_today: 3.18 },
    'sales-outreach': { requests_today: 2044, cost_today: 3.62 },
    default: { requests_today: 612, cost_today: 0.62 },
  },
};

function projectBranding(projectId: string): ProjectBrandingResponse {
  return {
    project_id: projectId,
    branding: {
      logo_url: null,
      accent_color: null,
      product_name: null,
    },
  };
}

// ---------------------------------------------------------------------------
// sessions (Calls). started_at / ended_at are ISO 8601 UTC strings.
// ---------------------------------------------------------------------------

function iso(epochSeconds: number): string {
  return new Date(epochSeconds * 1000).toISOString();
}

const SESSIONS: SessionRow[] = [
  {
    id: 'sess-9a71c2',
    project: 'customer-support',
    started_at: iso(BASE_EPOCH - 4 * MIN),
    ended_at: iso(BASE_EPOCH - 4 * MIN + 214),
    modalities: ['stt', 'llm', 'tts'],
    total_cost_usd: 0.014218,
    request_count: 38,
    tenant_id: null,
    routed_llm: 'openai/gpt-4o-mini',
    routed_tts: 'cartesia/sonic-2',
    budget_ms: 1200,
    budget_overrun: false,
  },
  {
    id: 'sess-4f30bb',
    project: 'sales-outreach',
    started_at: iso(BASE_EPOCH - 31 * MIN),
    ended_at: iso(BASE_EPOCH - 31 * MIN + 178),
    modalities: ['stt', 'llm', 'tts'],
    total_cost_usd: 0.021884,
    request_count: 44,
    tenant_id: null,
    routed_llm: 'anthropic/claude-haiku-4-5',
    routed_tts: 'elevenlabs/eleven_turbo_v2',
    budget_ms: 1500,
    budget_overrun: true,
  },
  {
    id: 'sess-1d88a0',
    project: 'customer-support',
    started_at: iso(BASE_EPOCH - 52 * MIN),
    ended_at: iso(BASE_EPOCH - 52 * MIN + 96),
    modalities: ['stt', 'llm', 'tts'],
    total_cost_usd: 0.007312,
    request_count: 19,
    tenant_id: null,
    routed_llm: 'openai/gpt-4o-mini',
    routed_tts: 'cartesia/sonic-2',
    budget_ms: 1200,
    budget_overrun: false,
  },
  {
    id: 'sess-77b415',
    project: 'default',
    started_at: iso(BASE_EPOCH - 2 * HOUR),
    ended_at: iso(BASE_EPOCH - 2 * HOUR + 302),
    modalities: ['stt', 'llm', 'tts'],
    total_cost_usd: 0.018406,
    request_count: 51,
    tenant_id: null,
    routed_llm: 'openai/gpt-4o-mini',
    routed_tts: 'cartesia/sonic-2',
    budget_ms: null,
    budget_overrun: null,
  },
  {
    id: 'sess-c093e2',
    project: 'sales-outreach',
    started_at: iso(BASE_EPOCH - 3 * HOUR),
    ended_at: iso(BASE_EPOCH - 3 * HOUR + 143),
    modalities: ['stt', 'llm', 'tts'],
    total_cost_usd: 0.016978,
    request_count: 33,
    tenant_id: null,
    routed_llm: 'anthropic/claude-haiku-4-5',
    routed_tts: 'elevenlabs/eleven_turbo_v2',
    budget_ms: 1500,
    budget_overrun: false,
  },
  {
    id: 'sess-b2f6d9',
    project: 'customer-support',
    started_at: iso(BASE_EPOCH - 5 * HOUR),
    ended_at: iso(BASE_EPOCH - 5 * HOUR + 267),
    modalities: ['stt', 'llm', 'tts'],
    total_cost_usd: 0.012044,
    request_count: 41,
    tenant_id: null,
    routed_llm: 'openai/gpt-4o-mini',
    routed_tts: 'cartesia/sonic-2',
    budget_ms: 1200,
    budget_overrun: false,
  },
  {
    id: 'sess-30ac57',
    project: 'default',
    started_at: iso(BASE_EPOCH - 7 * HOUR),
    ended_at: iso(BASE_EPOCH - 7 * HOUR + 58),
    modalities: ['stt', 'llm'],
    total_cost_usd: 0.004102,
    request_count: 12,
    tenant_id: null,
    routed_llm: 'openai/gpt-4o-mini',
    routed_tts: null,
    budget_ms: null,
    budget_overrun: null,
  },
  {
    id: 'sess-e51882',
    project: 'sales-outreach',
    started_at: iso(BASE_EPOCH - 9 * HOUR),
    ended_at: iso(BASE_EPOCH - 9 * HOUR + 221),
    modalities: ['stt', 'llm', 'tts'],
    total_cost_usd: 0.023551,
    request_count: 47,
    tenant_id: null,
    routed_llm: 'anthropic/claude-haiku-4-5',
    routed_tts: 'elevenlabs/eleven_turbo_v2',
    budget_ms: 1500,
    budget_overrun: false,
  },
];

// Session detail extends a row with the per-modality breakdown + providers list.
function sessionDetail(id: string): SessionDetail {
  const row = SESSIONS.find((s) => s.id === id) ?? SESSIONS[0];
  const isPremium = row.project === 'sales-outreach';
  const sttCost = Number((row.total_cost_usd * 0.28).toFixed(6));
  const llmCost = Number((row.total_cost_usd * 0.46).toFixed(6));
  const ttsCost = Number((row.total_cost_usd * 0.26).toFixed(6));
  const by_modality: SessionDetail['by_modality'] = {
    stt: { cost: sttCost, request_count: Math.round(row.request_count * 0.4) },
    llm: { cost: llmCost, request_count: Math.round(row.request_count * 0.35) },
  };
  if (row.modalities.includes('tts')) {
    by_modality.tts = {
      cost: ttsCost,
      request_count: Math.round(row.request_count * 0.25),
    };
  }
  const providers = isPremium
    ? ['assemblyai', 'anthropic', 'elevenlabs']
    : ['deepgram', 'openai', 'cartesia'];
  return { ...row, by_modality, providers };
}

// A short realistic transcript for a session.
function sessionTurns(id: string): { session_id: string; turns: TurnRow[] } {
  const turns: TurnRow[] = [
    {
      session_id: id,
      turn_index: 0,
      caller_speak_start_ms: 1200,
      caller_speak_end_ms: 3400,
      agent_speak_start_ms: 4210,
      agent_speak_end_ms: 7180,
      response_speed_ms: 810,
    },
    {
      session_id: id,
      turn_index: 1,
      caller_speak_start_ms: 8900,
      caller_speak_end_ms: 11020,
      agent_speak_start_ms: 11760,
      agent_speak_end_ms: 14330,
      response_speed_ms: 740,
    },
    {
      session_id: id,
      turn_index: 2,
      caller_speak_start_ms: 16040,
      caller_speak_end_ms: 17880,
      agent_speak_start_ms: 18790,
      agent_speak_end_ms: 21260,
      response_speed_ms: 910,
    },
  ];
  return { session_id: id, turns };
}

function sessionReplay(id: string): ReplayResponse {
  // Minimal valid replay: empty events is acceptable and renders the
  // pre-v0.3.0 banner path rather than an error.
  return { session_id: id, events: [] };
}

// ---------------------------------------------------------------------------
// replay storage
// ---------------------------------------------------------------------------

const REPLAY_STORAGE: {
  total_replay_size_bytes: number;
  by_project: Array<{ project: string; replay_size_bytes: number }>;
} = {
  total_replay_size_bytes: 0,
  by_project: [],
};

// ---------------------------------------------------------------------------
// api keys (masked prefixes only — never a real secret)
// ---------------------------------------------------------------------------

const API_KEYS: { keys: ApiKey[] } = {
  keys: [
    {
      id: 1,
      key_prefix: 'vk_live_demo',
      name: 'production-collector',
      tenant_id: null,
      issued_by: 'ops@example.com',
      issued_at: iso(BASE_EPOCH - 40 * DAY),
      last_used_at: iso(BASE_EPOCH - 2 * HOUR),
      revoked_at: null,
    },
    {
      id: 2,
      key_prefix: 'vk_live_dash',
      name: 'dashboard-readonly',
      tenant_id: null,
      issued_by: 'ops@example.com',
      issued_at: iso(BASE_EPOCH - 120 * DAY),
      last_used_at: iso(BASE_EPOCH - 100 * DAY), // stale
      revoked_at: null,
    },
    {
      id: 3,
      key_prefix: 'vk_live_old0',
      name: 'legacy-agent',
      tenant_id: null,
      issued_by: 'admin@example.com',
      issued_at: iso(BASE_EPOCH - 200 * DAY),
      last_used_at: null,
      revoked_at: iso(BASE_EPOCH - 30 * DAY),
    },
  ],
};

// ---------------------------------------------------------------------------
// audit log (matches the AuditEntry shape Settings.tsx reads)
// ---------------------------------------------------------------------------

interface AuditEntry {
  id: number;
  timestamp: number; // epoch seconds (Settings multiplies by 1000)
  entity_type: string;
  entity_id: string;
  action: string;
  changes: Record<string, unknown> | null;
  source: string;
}

const AUDIT_LOG: AuditEntry[] = [
  {
    id: 3,
    timestamp: BASE_EPOCH - 3 * HOUR,
    entity_type: 'project',
    entity_id: 'sales-outreach',
    action: 'update',
    changes: { budget_action: 'throttle' },
    source: 'dashboard',
  },
  {
    id: 2,
    timestamp: BASE_EPOCH - 2 * DAY,
    entity_type: 'model',
    entity_id: 'anthropic/claude-haiku-4-5',
    action: 'create',
    changes: null,
    source: 'yaml',
  },
  {
    id: 1,
    timestamp: BASE_EPOCH - 5 * DAY,
    entity_type: 'provider',
    entity_id: 'deepgram',
    action: 'create',
    changes: null,
    source: 'yaml',
  },
];

// ---------------------------------------------------------------------------
// billing rate card
// ---------------------------------------------------------------------------

interface EffectiveRule {
  modality: string;
  provider: string;
  model: string;
  tenant: string | null;
  plan: string | null;
  kind: string;
  markup: number | null;
  unit_price_usd: number | null;
  unit: string | null;
  rule: string;
}

interface DbRule extends EffectiveRule {
  rule_id: string;
  created_at: number;
  updated_at: number;
}

interface ModelRow {
  modality: string;
  provider: string;
  model: string;
  unit: string | null;
  voice_price_usd: number | null;
  effective: {
    kind: string;
    markup: number | null;
    unit_price_usd: number | null;
    unit: string | null;
  } | null;
}

const RATE_CARD: { default_markup: number; rules: EffectiveRule[] } = {
  default_markup: 1.3,
  rules: [
    {
      modality: 'tts',
      provider: 'elevenlabs',
      model: '*',
      tenant: null,
      plan: null,
      kind: 'fixed',
      markup: null,
      unit_price_usd: 0.18,
      unit: '1k_char',
      rule: 'seed:rate_card.yaml#tts-elevenlabs',
    },
    {
      modality: 'llm',
      provider: 'openai',
      model: 'gpt-4o-mini',
      tenant: null,
      plan: null,
      kind: 'cost_plus',
      markup: 1.5,
      unit_price_usd: null,
      unit: null,
      rule: 'db:rule_demo_llm',
    },
  ],
};

const RATE_CARD_RULES: { rules: DbRule[] } = {
  rules: [
    {
      modality: 'llm',
      provider: 'openai',
      model: 'gpt-4o-mini',
      tenant: null,
      plan: null,
      kind: 'cost_plus',
      markup: 1.5,
      unit_price_usd: null,
      unit: null,
      rule: 'db:rule_demo_llm',
      rule_id: 'rule_demo_llm',
      created_at: BASE_EPOCH - 14 * DAY,
      updated_at: BASE_EPOCH - 14 * DAY,
    },
  ],
};

const RATE_CARD_MODELS: { models: ModelRow[] } = {
  models: [
    {
      modality: 'stt',
      provider: 'deepgram',
      model: 'deepgram/nova-3',
      unit: 'minute',
      voice_price_usd: 0.0043,
      effective: null,
    },
    {
      modality: 'llm',
      provider: 'openai',
      model: 'openai/gpt-4o-mini',
      unit: '1m_token',
      voice_price_usd: 0.6,
      effective: {
        kind: 'cost_plus',
        markup: 1.5,
        unit_price_usd: null,
        unit: null,
      },
    },
    {
      modality: 'llm',
      provider: 'anthropic',
      model: 'anthropic/claude-haiku-4-5',
      unit: '1m_token',
      voice_price_usd: 1.0,
      effective: null,
    },
    {
      modality: 'tts',
      provider: 'cartesia',
      model: 'cartesia/sonic-2',
      unit: '1k_char',
      voice_price_usd: 0.065,
      effective: null,
    },
    {
      modality: 'tts',
      provider: 'elevenlabs',
      model: 'elevenlabs/eleven_turbo_v2',
      unit: '1k_char',
      voice_price_usd: null, // not in catalog: shows the honest fallback
      effective: {
        kind: 'fixed',
        markup: null,
        unit_price_usd: 0.18,
        unit: '1k_char',
      },
    },
  ],
};

// ---------------------------------------------------------------------------
// diagnostics
// ---------------------------------------------------------------------------

const DIAG_CREDS: DiagnosticsCreds = {
  configured: true,
  url: 'wss://demo-voicegw.livekit.cloud',
};

const DIAG_RUNS: DiagnosticRun[] = [
  {
    run_id: 'diag_run_002',
    status: 'done',
    checks: ['agents', 'sfu'],
    config: {},
    results: {
      checks: {
        agents: { ok: true, result: { rooms: 2, agents: 3 } },
        sfu: { ok: true, result: { region: 'us-east-1', rtt_ms: 24 } },
      },
      verdict: 'PASS',
    },
    verdict: 'PASS',
    error: null,
    created_at: iso(BASE_EPOCH - 90 * MIN),
    started_at: iso(BASE_EPOCH - 90 * MIN),
    ended_at: iso(BASE_EPOCH - 90 * MIN + 6),
  },
  {
    run_id: 'diag_run_001',
    status: 'done',
    checks: ['agents'],
    config: {},
    results: {
      checks: {
        agents: { ok: true, result: { rooms: 1, agents: 1 } },
      },
      verdict: 'PASS',
    },
    verdict: 'PASS',
    error: null,
    created_at: iso(BASE_EPOCH - 1 * DAY),
    started_at: iso(BASE_EPOCH - 1 * DAY),
    ended_at: iso(BASE_EPOCH - 1 * DAY + 3),
  },
];

// ---------------------------------------------------------------------------
// Routing + dispatch
// ---------------------------------------------------------------------------

const READ_ONLY_ERROR = 'This is a read-only demo.';

/** Mutating HTTP methods that a read-only demo refuses. */
const MUTATING = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);

/** Match `/api/agents/{id}`-style single-segment parameterized routes. */
function matchOne(pathname: string, prefix: string, suffix = ''): string | null {
  if (!pathname.startsWith(prefix)) return null;
  const rest = pathname.slice(prefix.length);
  if (suffix) {
    if (!rest.endsWith(suffix)) return null;
    const mid = rest.slice(0, rest.length - suffix.length);
    return mid.length > 0 && !mid.includes('/') ? decodeURIComponent(mid) : null;
  }
  return rest.length > 0 && !rest.includes('/') ? decodeURIComponent(rest) : null;
}

/**
 * Serve a seeded fixture for `path`, cast to `T`. GETs resolve immediately;
 * any mutating method (including the billed probe POST) throws the read-only
 * error; an unknown path throws so a missing fixture is loud, not a silent {}.
 */
export async function demoFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const method = (init?.method ?? 'GET').toUpperCase();
  // Strip the query string; the demo answers by pathname only.
  const pathname = path.split('?')[0];

  // Read-only backstop. The probe POST is billed traffic, so it is refused
  // just like any other mutation even though it is technically a "GET" of a
  // measurement — the UI disables the button; this is the safety net.
  const isProbe = /^\/api\/agents\/[^/]+\/probe$/.test(pathname);
  if (MUTATING.has(method) || isProbe) {
    throw new Error(READ_ONLY_ERROR);
  }

  // Exact-path fixtures.
  switch (pathname) {
    case '/api/auth-status':
      return AUTH_STATUS as T;
    case '/api/status':
      return STATUS as T;
    case '/api/overview':
      return OVERVIEW as T;
    case '/api/agents':
      return AGENTS_RESPONSE as T;
    case '/api/costs':
      return COSTS as T;
    case '/api/costs/by-day':
      return COSTS_BY_DAY as T;
    case '/api/latency':
      return LATENCY as T;
    case '/api/metrics':
      return METRICS as T;
    case '/api/server/overview':
      return SERVER_OVERVIEW as T;
    case '/api/projects':
      return PROJECTS as T;
    case '/api/sessions':
      return SESSIONS as T;
    case '/api/replay/storage':
      return REPLAY_STORAGE as T;
    case '/api/api_keys':
      return API_KEYS as T;
    case '/v1/audit-log':
      return AUDIT_LOG as T;
    case '/v1/billing/rate-card':
      return RATE_CARD as T;
    case '/v1/billing/rate-card/rules':
      return RATE_CARD_RULES as T;
    case '/v1/billing/rate-card/models':
      return RATE_CARD_MODELS as T;
    case '/api/diagnostics/creds':
      return DIAG_CREDS as T;
    case '/api/diagnostics/runs':
      return DIAG_RUNS as T;
  }

  // Parameterized-path fixtures.
  // /api/agents/{id}
  const agentId = matchOne(pathname, '/api/agents/');
  if (agentId !== null) {
    const agent = FLEET.find((a) => a.agent_id === agentId) ?? FLEET[0];
    return agent as T;
  }

  // /api/sessions/{id}/turns
  const turnsId = matchOne(pathname, '/api/sessions/', '/turns');
  if (turnsId !== null) return sessionTurns(turnsId) as T;

  // /api/sessions/{id}/dead_air
  const deadAirId = matchOne(pathname, '/api/sessions/', '/dead_air');
  if (deadAirId !== null) {
    return { session_id: deadAirId, events: [] } as T;
  }

  // /api/sessions/{id}/replay
  const replayId = matchOne(pathname, '/api/sessions/', '/replay');
  if (replayId !== null) return sessionReplay(replayId) as T;

  // /api/sessions/{id} (detail modal) — checked after the more specific
  // /turns, /dead_air, /replay so it only catches the bare id.
  const sessionId = matchOne(pathname, '/api/sessions/');
  if (sessionId !== null) return sessionDetail(sessionId) as T;

  // /api/projects/{id}/branding
  const brandingId = matchOne(pathname, '/api/projects/', '/branding');
  if (brandingId !== null) return projectBranding(brandingId) as T;

  // /api/diagnostics/runs/{id}
  const runId = matchOne(pathname, '/api/diagnostics/runs/');
  if (runId !== null) {
    const run = DIAG_RUNS.find((r) => r.run_id === runId) ?? DIAG_RUNS[0];
    return run as T;
  }

  throw new Error(`demo: no fixture for ${pathname}`);
}
