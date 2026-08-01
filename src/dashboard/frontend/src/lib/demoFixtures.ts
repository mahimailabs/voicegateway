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
  CallDetail,
  CallsResponse,
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

// Runs are shaped exactly as livekit_diag.service.RealProbes returns them, check
// by check: `agents` carries the in-room rows plus the heartbeat roster, `sfu` /
// `sfu_load` carry the baseline, the ramp, the knee, the budget the knee was
// computed against and the prober's own resource sample, and `latency` carries
// per-agent stats (seconds) plus the split read back from the agent's own rows.
//
// Four deliberate properties, so the demo shows the honest cases and not just
// the happy one:
//   - One run died before recording anything: `status: 'failed'`, `results:
//     null`, `verdict: null`, one error string. That is exactly what the backend
//     stores on that path. It is deliberately NOT the newest: the tabs only ever
//     render the first run, and leading the public demo with four "no result"
//     cards would sell the product short. It sits in the run history, which is
//     enough to prove the failed path is stored and listed honestly.
//   - One completed run stores the verdict PASS while one probed agent answered
//     nothing. That was `_verdict`'s reading, and `_verdict` is gone: `gates`
//     replaced it and would call the same payload UNKNOWN, because a probe that
//     measured nothing has not passed. This page renders the STORED verdict and
//     never recomputes one, so the value here is a display fixture and not a
//     claim about what today's gates return. The Errors tab surfaces the
//     no-reply either way.
//   - The two oldest runs each carry one real failure string, so the error-class
//     bars have something to group: a per-check timeout and a dispatch that found
//     no worker.
//   - The oldest run has `roster: []` (a configured collector reporting nothing)
//     against the populated roster of the run above it: two different answers
//     that must never collapse into "0 workers".
const DIAG_RUNS: DiagnosticRun[] = [
  {
    run_id: 'diag_run_003',
    status: 'done',
    checks: ['agents', 'latency', 'sfu', 'sfu_load'],
    config: { target_ms: 1500, ramp: [2, 10, 25], duration: 10, trials: 3 },
    results: {
      checks: {
        agents: {
          ok: true,
          result: {
            agents: [
              {
                agent_name: 'support-voice',
                room: 'support-call-7f21',
                identity: 'agent-support-01',
                state: 'active',
                humans: 1,
                age_s: 132,
              },
              {
                agent_name: 'reception',
                room: 'reception-inbound-a903',
                identity: 'agent-reception-02',
                state: 'active',
                humans: 1,
                age_s: 54,
              },
              {
                // Assigned by LiveKit but not joined: a dispatch nobody took yet.
                agent_name: 'reception',
                room: 'reception-inbound-a903',
                identity: null,
                state: 'dispatched',
                humans: 1,
                age_s: null,
              },
            ],
            roster: [
              {
                agent_id: 'support-voice',
                agent_name: 'support-voice',
                status: 'busy',
                region: 'us-east-1',
                host: 'worker-01',
                version: '0.20.1',
                active_sessions: 2,
                last_seen: BASE_EPOCH - 45,
              },
              {
                agent_id: 'reception',
                agent_name: 'reception',
                status: 'busy',
                region: 'us-east-1',
                host: 'worker-02',
                version: '0.20.1',
                active_sessions: 1,
                last_seen: BASE_EPOCH - 12,
              },
              {
                // Idle: registered and heartbeating, in no room, so LiveKit's
                // server API cannot see it at all. This row is the whole point.
                agent_id: 'outbound-sales',
                agent_name: 'outbound-sales',
                status: 'idle',
                region: 'us-west-2',
                host: 'worker-03',
                version: '0.20.0',
                active_sessions: 0,
                last_seen: BASE_EPOCH - 22 * MIN,
              },
              {
                agent_id: 'nightly-batch',
                agent_name: 'nightly-batch',
                status: 'offline',
                region: 'us-west-2',
                host: 'worker-04',
                version: '0.20.0',
                active_sessions: 0,
                last_seen: BASE_EPOCH - 9 * HOUR,
              },
            ],
          },
        },
        sfu: {
          ok: true,
          result: {
            baseline: { rtt_ms: 18.4, loss_pct: 0.0, quality: 'Excellent' },
            ramp: [],
            knee: null,
            target_rtt_ms: 50.0,
            resource: null, // nothing is sampled without a ramp
          },
        },
        sfu_load: {
          ok: true,
          result: {
            baseline: { rtt_ms: 19.1, loss_pct: 0.0, quality: 'Excellent' },
            ramp: [
              { clients: 2, rtt_ms: 14.6, loss_pct: 0.0, quality: 'Excellent' },
              { clients: 10, rtt_ms: 31.2, loss_pct: 0.0, quality: 'Good' },
              { clients: 25, rtt_ms: 68.9, loss_pct: 0.0, quality: 'Poor' },
            ],
            // 25 clients broke the 50 ms budget, so the last healthy tier is 10.
            knee: 10,
            target_rtt_ms: 50.0,
            resource: {
              cpu_peak: 71.4,
              mem_peak_mb: 842.6,
              net_kbps_up: 4180.5,
              saturated: false,
              per_client: { cpu_pct: 2.856, kbps_up: 167.2 },
              sustainable_n: 29,
            },
          },
        },
        latency: {
          ok: true,
          result: {
            agents: [
              {
                // Times are SECONDS. p95 is present on the wire and deliberately
                // not rendered: 3 trials cannot support a percentile.
                agent: 'support-voice',
                stats: {
                  avg: 0.912,
                  p50: 0.884,
                  p95: 1.043,
                  min: 0.83,
                  max: 1.043,
                  trials: 3,
                },
                components: {
                  eou: 0.412,
                  stt: 0.168,
                  stt_ttfp: 0.168,
                  stt_transcription_delay: 0.334,
                  llm_ttft: 0.402,
                  tts: 0.214,
                },
              },
              {
                // Answered nothing: every number is 0 with trials 0, which the UI
                // must render as "not measured", never as an instant reply.
                agent: 'reception',
                stats: { avg: 0, p50: 0, p95: 0, min: 0, max: 0, trials: 0 },
                components: null,
              },
            ],
          },
        },
      },
      verdict: 'PASS',
    },
    verdict: 'PASS',
    error: null,
    created_at: iso(BASE_EPOCH - 12 * MIN),
    started_at: iso(BASE_EPOCH - 12 * MIN),
    ended_at: iso(BASE_EPOCH - 12 * MIN + 96),
  },
  {
    // A run that ended without recording anything. `_execute` wraps the whole
    // run in `asyncio.wait_for(..., 360s)`; when that fires, `run.results` was
    // never assigned and no verdict was ever computed, so the stored row is
    // `results: null` + `verdict: null` + the error string verbatim. It is NOT a
    // run of zeros: a zero would claim a measurement nobody took.
    //
    // Deliberately NOT the newest. The tabs and the exportable report only ever
    // render the first run, and voicegateway.dev/demo is a shop window: leading
    // with four 'no result' cards would sell the product short. It sits in the
    // run history instead, where it still proves the failed path is stored and
    // listed honestly. Make the history rows selectable and this becomes
    // reachable in the demo without costing the default view.
    //
    // `latency` is left out of `checks` so the four per-check tabs cover both
    // resultless states: three that were asked for and recorded nothing, and one
    // that was never part of the run at all.
    run_id: 'diag_run_004',
    status: 'failed',
    // A 4-tier ramp held 30s per tier does not fit inside the six-minute cap,
    // which is why this run hit it.
    checks: ['agents', 'sfu', 'sfu_load'],
    config: { target_ms: 1500, ramp: [2, 10, 25, 50], duration: 30, trials: 3 },
    results: null,
    verdict: null,
    // Verbatim from the TimeoutError branch of `_execute`.
    error: 'run timed out',
    created_at: iso(BASE_EPOCH - 26 * MIN),
    started_at: iso(BASE_EPOCH - 26 * MIN),
    // Started plus the 360s cap: the run was killed, it did not finish early.
    ended_at: iso(BASE_EPOCH - 20 * MIN),
  },
  {
    run_id: 'diag_run_002',
    status: 'done',
    checks: ['agents', 'sfu_load'],
    config: { target_ms: 1500, ramp: [2, 10, 25], duration: 10, trials: 2 },
    results: {
      checks: {
        agents: {
          ok: true,
          result: {
            agents: [
              {
                agent_name: 'support-voice',
                room: 'support-call-1c04',
                identity: 'agent-support-01',
                state: 'active',
                humans: 1,
                age_s: 61,
              },
            ],
            roster: [
              {
                agent_id: 'support-voice',
                agent_name: 'support-voice',
                status: 'busy',
                region: 'us-east-1',
                host: 'worker-01',
                version: '0.20.1',
                active_sessions: 1,
                last_seen: BASE_EPOCH - 90 * MIN,
              },
            ],
          },
        },
        // The per-check timeout string, verbatim from service.execute_run.
        sfu_load: { ok: false, error: 'check timed out' },
      },
      verdict: 'FAIL',
    },
    verdict: 'FAIL',
    error: null,
    created_at: iso(BASE_EPOCH - 90 * MIN),
    started_at: iso(BASE_EPOCH - 90 * MIN),
    ended_at: iso(BASE_EPOCH - 90 * MIN + 128),
  },
  {
    run_id: 'diag_run_001',
    status: 'done',
    checks: ['agents', 'latency'],
    config: { target_ms: 1500, ramp: [2, 10, 25], duration: 10, trials: 1 },
    results: {
      checks: {
        agents: {
          ok: true,
          result: {
            agents: [],
            // Configured collector, nothing reported: NOT the same as null.
            roster: [],
          },
        },
        latency: {
          ok: false,
          error:
            "dispatched to 'outbound-sales' but no worker joined within 8s: that name is how the worker registered (register_worker / @server.rtc_session agent_name); check a worker with that name is running",
        },
      },
      verdict: 'FAIL',
    },
    verdict: 'FAIL',
    error: null,
    created_at: iso(BASE_EPOCH - 1 * DAY),
    started_at: iso(BASE_EPOCH - 1 * DAY),
    ended_at: iso(BASE_EPOCH - 1 * DAY + 19),
  },
];

// ---------------------------------------------------------------------------
// calls + call_legs (the call itself, not the inference)
// ---------------------------------------------------------------------------

// Shaped exactly as `calls_repository.CallRow` / `CallLegRow` serialise, field
// for field, with one leg array per call.
//
// The five rows are chosen to be the five HONEST cases, not five happy ones,
// because this payload feeds the layer 1-6 waterfall and its whole job is to
// distinguish "measured" from "not measured":
//
//   1. webhook_proxy - the zero-instrumentation default. The caller's join is
//      millisecond-precise (ParticipantInfo.joined_at_ms) but the agent's
//      track_published time comes off the webhook's whole-second `created_at`,
//      so the ring time is real and coarse at once, and renders with its caveat.
//   2. agent_report  - both timestamps self-reported by the agent, so the same
//      subtraction earns millisecond rendering.
//   3. sipp_rtd      - a load worker reporting the true INVITE -> 200 OK wall
//      time. It is LONGER than the legs sum to, because the SIP setup before
//      the caller reached the room is inside the ring and layers 1-2 cannot
//      split it. The UI must not make these two numbers agree.
//   4. an agent that joined and never published: layer 3 measured, layer 4 not,
//      and NO ring time at all - the backend stores NULL rather than a zero.
//   5. a trunk failure with no room and no legs: `room_sid` and `room_name` are
//      both NULL, which is legitimate (a 503 on INVITE never creates a room)
//      and is the most important row in a load test.
//
// Whole-second timestamps below are whole seconds ON PURPOSE: that is what a
// webhook `created_at` gives you, and it is why the derived number is coarse.

/** Call 1 anchor: the room started 12 minutes before the demo's "now". */
const CALL_A_T0 = (BASE_EPOCH - 12 * MIN) * 1000;
const CALL_B_T0 = (BASE_EPOCH - 34 * MIN) * 1000;
const CALL_C_T0 = (BASE_EPOCH - 55 * MIN) * 1000;
const CALL_D_T0 = (BASE_EPOCH - 2 * HOUR) * 1000;
const CALL_E_T0 = (BASE_EPOCH - 3 * HOUR) * 1000;

const SIP_ATTRS = (number: string, callId: string): string =>
  JSON.stringify({
    'sip.callID': callId,
    'sip.callStatus': 'active',
    'sip.phoneNumber': number,
    'sip.trunkID': 'ST_demoinbound',
    'sip.ruleID': 'SDR_demoreception',
  });

const CALLS: CallDetail[] = [
  {
    // 1. webhook_proxy: 4.1 s of ring, 1.2 s of dispatch, 2.9 s of agent.
    id: 'ca_7f21demo0001',
    room_sid: 'RM_7f21demo',
    room_name: 'support-call-7f21',
    origin: 'webhook',
    attempt_id: null,
    run_id: null,
    project: 'default',
    tenant_id: null,
    agent_id: 'support-voice',
    channel: 'sip',
    direction: 'inbound',
    started_at_ms: CALL_A_T0,
    ended_at_ms: CALL_A_T0 + 182_000,
    duration_ms: 182_000,
    end_reason: 'CLIENT_INITIATED',
    num_legs: 2,
    is_probe: 0,
    answer_latency_ms: 4100,
    answer_latency_source: 'webhook_proxy',
    legs: [
      {
        id: 1,
        call_id: 'ca_7f21demo0001',
        participant_sid: 'PA_7f21caller',
        identity: 'sip_+15195550142',
        kind: 'SIP',
        region: 'us-east-1',
        joined_at_ms: CALL_A_T0 + 900,
        left_at_ms: CALL_A_T0 + 182_000,
        disconnect_reason: 'CLIENT_INITIATED',
        is_publisher: 1,
        attributes_json: SIP_ATTRS('+15195550142', 'sip_call_7f21'),
        first_audio_track_at_ms: CALL_A_T0 + 1000,
        audio_track_sid: 'TR_7f21caller',
        audio_codec: 'opus',
        joined_at_source: 'webhook',
        first_audio_track_at_source: 'webhook',
      },
      {
        id: 2,
        call_id: 'ca_7f21demo0001',
        participant_sid: 'PA_7f21agent',
        identity: 'agent-support-01',
        kind: 'AGENT',
        region: 'us-east-1',
        joined_at_ms: CALL_A_T0 + 2100,
        left_at_ms: CALL_A_T0 + 182_000,
        disconnect_reason: 'ROOM_CLOSED',
        is_publisher: 1,
        attributes_json: null,
        // Whole second: this came off the track_published webhook's created_at.
        first_audio_track_at_ms: CALL_A_T0 + 5000,
        audio_track_sid: 'TR_7f21agent',
        audio_codec: 'opus',
        joined_at_source: 'webhook',
        first_audio_track_at_source: 'webhook',
      },
    ],
  },
  {
    // 2. agent_report: both timestamps from inside the agent process.
    id: 'ca_a903demo0002',
    room_sid: 'RM_a903demo',
    room_name: 'reception-inbound-a903',
    origin: 'agent',
    attempt_id: null,
    run_id: null,
    project: 'default',
    tenant_id: null,
    agent_id: 'reception',
    channel: 'sip',
    direction: 'inbound',
    started_at_ms: CALL_B_T0,
    ended_at_ms: CALL_B_T0 + 96_000,
    duration_ms: 96_000,
    end_reason: 'CLIENT_INITIATED',
    num_legs: 2,
    is_probe: 0,
    answer_latency_ms: 1832,
    answer_latency_source: 'agent_report',
    legs: [
      {
        id: 3,
        call_id: 'ca_a903demo0002',
        participant_sid: 'PA_a903caller',
        identity: 'sip_+15195550188',
        kind: 'SIP',
        region: 'us-east-1',
        joined_at_ms: CALL_B_T0,
        left_at_ms: CALL_B_T0 + 96_000,
        disconnect_reason: 'CLIENT_INITIATED',
        is_publisher: 1,
        attributes_json: SIP_ATTRS('+15195550188', 'sip_call_a903'),
        first_audio_track_at_ms: CALL_B_T0 + 120,
        audio_track_sid: 'TR_a903caller',
        audio_codec: 'opus',
        joined_at_source: 'agent',
        first_audio_track_at_source: 'agent',
      },
      {
        id: 4,
        call_id: 'ca_a903demo0002',
        participant_sid: 'PA_a903agent',
        identity: 'agent-reception-02',
        kind: 'AGENT',
        region: 'us-east-1',
        joined_at_ms: CALL_B_T0 + 450,
        left_at_ms: CALL_B_T0 + 96_000,
        disconnect_reason: 'ROOM_CLOSED',
        is_publisher: 1,
        attributes_json: null,
        first_audio_track_at_ms: CALL_B_T0 + 1832,
        audio_track_sid: 'TR_a903agent',
        audio_codec: 'opus',
        joined_at_source: 'agent',
        first_audio_track_at_source: 'agent',
      },
    ],
  },
  {
    // 3. sipp_rtd: the reported INVITE -> 200 OK (2740 ms) is longer than the
    // legs sum to (610 + 1870 = 2480), because the SIP setup before the caller
    // reached the room is inside the ring and is not splittable.
    id: 'ca_load42demo003',
    room_sid: 'RM_load42demo',
    room_name: 'vg-load-att-0042',
    origin: 'loadgen',
    attempt_id: 'att_0042',
    run_id: 'load_run_01',
    project: 'default',
    tenant_id: null,
    agent_id: 'reception',
    channel: 'sip',
    direction: 'inbound',
    started_at_ms: CALL_C_T0,
    ended_at_ms: CALL_C_T0 + 30_000,
    duration_ms: 30_000,
    end_reason: 'CLIENT_INITIATED',
    num_legs: 2,
    is_probe: 1,
    answer_latency_ms: 2740,
    answer_latency_source: 'sipp_rtd',
    legs: [
      {
        id: 5,
        call_id: 'ca_load42demo003',
        participant_sid: 'PA_load42caller',
        identity: 'sip_+15195550001',
        kind: 'SIP',
        region: 'us-east-1',
        joined_at_ms: CALL_C_T0,
        left_at_ms: CALL_C_T0 + 30_000,
        disconnect_reason: 'CLIENT_INITIATED',
        is_publisher: 1,
        attributes_json: SIP_ATTRS('+15195550001', 'sip_call_load42'),
        first_audio_track_at_ms: CALL_C_T0 + 80,
        audio_track_sid: 'TR_load42caller',
        audio_codec: 'opus',
        joined_at_source: 'loadgen',
        first_audio_track_at_source: 'loadgen',
      },
      {
        id: 6,
        call_id: 'ca_load42demo003',
        participant_sid: 'PA_load42agent',
        identity: 'agent-reception-07',
        kind: 'AGENT',
        region: 'us-east-1',
        joined_at_ms: CALL_C_T0 + 610,
        left_at_ms: CALL_C_T0 + 30_000,
        disconnect_reason: 'ROOM_CLOSED',
        is_publisher: 1,
        attributes_json: null,
        first_audio_track_at_ms: CALL_C_T0 + 2480,
        audio_track_sid: 'TR_load42agent',
        audio_codec: 'opus',
        joined_at_source: 'loadgen',
        first_audio_track_at_source: 'loadgen',
      },
    ],
  },
  {
    // 4. The agent joined and never published. Layer 3 is measured, layer 4 is
    // not, and there is NO ring time: the caller never heard an answer, which
    // must not be stored or drawn as a zero.
    id: 'ca_dead11demo004',
    room_sid: 'RM_dead11demo',
    room_name: 'support-call-dead11',
    origin: 'webhook',
    attempt_id: null,
    run_id: null,
    project: 'default',
    tenant_id: null,
    agent_id: 'support-voice',
    channel: 'sip',
    direction: 'inbound',
    started_at_ms: CALL_D_T0,
    ended_at_ms: CALL_D_T0 + 24_000,
    duration_ms: 24_000,
    end_reason: 'CLIENT_INITIATED',
    num_legs: 2,
    is_probe: 0,
    answer_latency_ms: null,
    answer_latency_source: null,
    legs: [
      {
        id: 7,
        call_id: 'ca_dead11demo004',
        participant_sid: 'PA_dead11caller',
        identity: 'sip_+15195550233',
        kind: 'SIP',
        region: 'us-east-1',
        joined_at_ms: CALL_D_T0 + 400,
        left_at_ms: CALL_D_T0 + 24_000,
        disconnect_reason: 'CLIENT_INITIATED',
        is_publisher: 1,
        attributes_json: SIP_ATTRS('+15195550233', 'sip_call_dead11'),
        first_audio_track_at_ms: CALL_D_T0 + 1000,
        audio_track_sid: 'TR_dead11caller',
        audio_codec: 'opus',
        joined_at_source: 'webhook',
        first_audio_track_at_source: 'webhook',
      },
      {
        id: 8,
        call_id: 'ca_dead11demo004',
        participant_sid: 'PA_dead11agent',
        identity: 'agent-support-04',
        kind: 'AGENT',
        region: 'us-east-1',
        joined_at_ms: CALL_D_T0 + 3000,
        left_at_ms: CALL_D_T0 + 24_000,
        disconnect_reason: 'ROOM_CLOSED',
        is_publisher: 0,
        attributes_json: null,
        first_audio_track_at_ms: null,
        audio_track_sid: null,
        audio_codec: null,
        joined_at_source: 'webhook',
        first_audio_track_at_source: null,
      },
    ],
  },
  {
    // 5. Trunk failure. No room was ever created, so `room_sid` and `room_name`
    // are both NULL and the load attempt id is the only identifier.
    id: 'ca_load43demo005',
    room_sid: null,
    room_name: null,
    origin: 'loadgen',
    attempt_id: 'att_0043',
    run_id: 'load_run_01',
    project: 'default',
    tenant_id: null,
    agent_id: null,
    channel: 'sip',
    direction: 'inbound',
    started_at_ms: CALL_E_T0,
    ended_at_ms: CALL_E_T0 + 1200,
    duration_ms: 1200,
    end_reason: 'SIP_TRUNK_FAILURE',
    num_legs: 0,
    is_probe: 1,
    answer_latency_ms: null,
    answer_latency_source: null,
    legs: [],
  },
];

const CALLS_RESPONSE: CallsResponse = { calls: CALLS };

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
    case '/api/calls':
      return CALLS_RESPONSE as T;
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
