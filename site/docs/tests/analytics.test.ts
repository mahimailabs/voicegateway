import { afterEach, describe, expect, it, vi } from 'vitest';

afterEach(() => { vi.unstubAllGlobals(); vi.resetModules(); vi.clearAllMocks(); });

describe('docs analytics', () => {
  it('stays off everywhere but docs.voicegateway.dev', async () => {
    const init = vi.fn(); const capture = vi.fn();
    vi.doMock('posthog-js', () => ({ default: { init, capture, register: vi.fn() } }));
    const { track } = await import('../lib/analytics');
    for (const hostname of ['localhost', 'voicegateway.dev', 'voicegateway-docs.mahimai.workers.dev']) {
      vi.stubGlobal('window', { location: { hostname, origin: `https://${hostname}` } });
      await track('code_copy');
    }
    expect(init).not.toHaveBeenCalled();
    expect(capture).not.toHaveBeenCalled();
  });

  it('tags every event as the docs site and keeps ids in memory', async () => {
    const init = vi.fn(); const capture = vi.fn();
    vi.doMock('posthog-js', () => ({ default: { init, capture, register: vi.fn() } }));
    vi.stubGlobal('window', { location: { hostname: 'docs.voicegateway.dev', origin: 'https://docs.voicegateway.dev' } });
    const { track, trackPageview } = await import('../lib/analytics');
    await Promise.all([trackPageview('/install'), trackPageview('/install')]);
    await track('docs_search', { query: 'sip', results: 0 });
    expect(init).toHaveBeenCalledTimes(1);
    expect(init.mock.calls[0][1]).toMatchObject({ persistence: 'memory', autocapture: false, capture_pageview: false, disable_session_recording: true });
    expect(capture.mock.calls.filter((call) => call[0] === '$pageview')).toHaveLength(1);
    expect(capture).toHaveBeenLastCalledWith('docs_search', { query: 'sip', results: 0, site: 'voicegateway-docs' });
  });
});

describe('linkEvent', () => {
  it.each([
    ['https://github.com/mahimailabs/voicegateway', 'github_click'],
    ['https://dash.voicegateway.dev/calls', 'console_click'],
    ['https://www.voicegateway.dev/', 'landing_click'],
    ['https://claude.ai/new?q=x', 'ask_ai_click'],
    ['https://discord.gg/ysFaF4uSB', 'outbound_click'],
  ])('%s is %s', async (href, name) => {
    const { linkEvent } = await import('../lib/analytics');
    expect(linkEvent(href, 'https://docs.voicegateway.dev')?.name).toBe(name);
  });

  it('ignores links within the docs and non-web links', async () => {
    const { linkEvent } = await import('../lib/analytics');
    for (const href of ['/install', 'https://docs.voicegateway.dev/api', 'mailto:hi@voicegateway.dev']) {
      expect(linkEvent(href, 'https://docs.voicegateway.dev')).toBeNull();
    }
  });
});
