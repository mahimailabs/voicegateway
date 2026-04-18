import { defineConfig } from 'vitepress'

export default defineConfig({
  title: 'VoiceGateway',
  description: 'Self-hosted inference gateway for voice AI. STT, LLM, TTS with agent-native MCP management.',

  head: [
    ['link', { rel: 'icon', type: 'image/svg+xml', href: '/favicon.svg' }],
    ['meta', { name: 'og:type', content: 'website' }],
    ['meta', { name: 'og:image', content: 'https://vg.openrtc.tech/og-image.png' }],
    ['meta', { name: 'theme-color', content: '#C67B3C' }],
  ],

  cleanUrls: true,
  lastUpdated: true,
  ignoreDeadLinks: [/localhost/],

  markdown: {
    theme: {
      light: 'github-light',
      dark: 'github-dark',
    },
    lineNumbers: true,
  },

  themeConfig: {
    logo: '/logo.svg',
    siteTitle: 'VoiceGateway',

    nav: [
      { text: 'Guide', link: '/guide/quick-start', activeMatch: '/guide/' },
      { text: 'Config', link: '/configuration/voicegw-yaml', activeMatch: '/configuration/' },
      { text: 'API', link: '/api/', activeMatch: '/api/' },
      { text: 'CLI', link: '/cli/', activeMatch: '/cli/' },
      { text: 'MCP', link: '/mcp/', activeMatch: '/mcp/' },
      {
        text: 'More',
        items: [
          { text: 'Architecture', link: '/architecture/' },
          { text: 'Examples', link: '/examples/' },
          { text: 'Migration', link: '/migration/from-litellm' },
          { text: 'Contributing', link: '/contributing/' },
          { text: 'FAQ', link: '/reference/faq' },
          { text: 'Changelog', link: '/reference/changelog' },
        ],
      },
    ],

    sidebar: {
      '/guide/': [
        {
          text: 'Getting Started',
          items: [
            { text: 'What is VoiceGateway?', link: '/guide/what-is-voicegateway' },
            { text: 'Quick Start', link: '/guide/quick-start' },
            { text: 'Installation', link: '/guide/installation' },
            { text: 'Your First Agent', link: '/guide/first-agent' },
            { text: 'Core Concepts', link: '/guide/core-concepts' },
          ],
        },
      ],
      '/configuration/': [
        {
          text: 'Configuration',
          items: [
            { text: 'voicegw.yaml', link: '/configuration/voicegw-yaml' },
            { text: 'Providers', link: '/configuration/providers' },
            { text: 'Models', link: '/configuration/models' },
            { text: 'Stacks', link: '/configuration/stacks' },
            { text: 'Projects', link: '/configuration/projects' },
            { text: 'Observability', link: '/configuration/observability' },
            { text: 'Environment Variables', link: '/configuration/environment-variables' },
          ],
        },
      ],
      '/api/': [
        {
          text: 'API Reference',
          items: [
            { text: 'Overview', link: '/api/' },
            { text: 'Python SDK', link: '/api/python-sdk' },
            { text: 'HTTP API', link: '/api/http-api' },
            { text: 'Dashboard API', link: '/api/dashboard-api' },
          ],
        },
      ],
      '/cli/': [
        {
          text: 'CLI Reference',
          items: [
            { text: 'Overview', link: '/cli/' },
            { text: 'init', link: '/cli/init' },
            { text: 'status', link: '/cli/status' },
            { text: 'costs', link: '/cli/costs' },
            { text: 'projects', link: '/cli/projects' },
            { text: 'logs', link: '/cli/logs' },
            { text: 'serve', link: '/cli/serve' },
            { text: 'dashboard', link: '/cli/dashboard' },
            { text: 'mcp', link: '/cli/mcp' },
          ],
        },
      ],
      '/mcp/': [
        {
          text: 'MCP Server',
          items: [
            { text: 'Overview', link: '/mcp/' },
            { text: 'Setup', link: '/mcp/setup' },
            { text: 'Transports', link: '/mcp/transports' },
            { text: 'Authentication', link: '/mcp/authentication' },
          ],
        },
        {
          text: 'Tools',
          items: [
            { text: 'Observability', link: '/mcp/tools/observability' },
            { text: 'Providers', link: '/mcp/tools/providers' },
            { text: 'Models', link: '/mcp/tools/models' },
            { text: 'Projects', link: '/mcp/tools/projects' },
          ],
        },
      ],
      '/architecture/': [
        {
          text: 'Architecture',
          items: [
            { text: 'System Overview', link: '/architecture/' },
            { text: 'Gateway Core', link: '/architecture/gateway-core' },
            { text: 'Provider Abstraction', link: '/architecture/provider-abstraction' },
            { text: 'Middleware', link: '/architecture/middleware' },
            { text: 'Storage', link: '/architecture/storage' },
            { text: 'Config Layers', link: '/architecture/config-layers' },
            { text: 'Security', link: '/architecture/security' },
          ],
        },
      ],
      '/examples/': [
        {
          text: 'Examples',
          items: [
            { text: 'Overview', link: '/examples/' },
            { text: 'Basic Voice Agent', link: '/examples/basic-voice-agent' },
            { text: 'Multi-Project Setup', link: '/examples/multi-project' },
            { text: 'Budget Enforcement', link: '/examples/budget-enforcement' },
            { text: 'Fallback Chains', link: '/examples/fallback-chains' },
            { text: 'Local-Only Stack', link: '/examples/local-only' },
            { text: 'Claude Code Integration', link: '/examples/claude-code-integration' },
            { text: 'Docker Deployment', link: '/examples/docker-deployment' },
            { text: 'Fly.io Deployment', link: '/examples/fly-deployment' },
          ],
        },
      ],
      '/migration/': [
        {
          text: 'Migration',
          items: [
            { text: 'From LiteLLM', link: '/migration/from-litellm' },
            { text: 'From LiveKit Inference', link: '/migration/from-livekit-inference' },
            { text: 'Version Upgrades', link: '/migration/version-upgrades' },
          ],
        },
      ],
      '/contributing/': [
        {
          text: 'Contributing',
          items: [
            { text: 'Overview', link: '/contributing/' },
            { text: 'Development Setup', link: '/contributing/development-setup' },
            { text: 'Adding a Provider', link: '/contributing/adding-a-provider' },
            { text: 'Code Style', link: '/contributing/code-style' },
            { text: 'Testing', link: '/contributing/testing' },
          ],
        },
      ],
      '/reference/': [
        {
          text: 'Reference',
          items: [
            { text: 'Troubleshooting', link: '/reference/troubleshooting' },
            { text: 'FAQ', link: '/reference/faq' },
            { text: 'Changelog', link: '/reference/changelog' },
          ],
        },
      ],
    },

    socialLinks: [
      { icon: 'github', link: 'https://github.com/mahimailabs/voicegateway' },
    ],

    editLink: {
      pattern: 'https://github.com/mahimailabs/voicegateway/edit/main/docs/:path',
      text: 'Edit this page on GitHub',
    },

    footer: {
      message: 'Released under the MIT License.',
      copyright: 'Copyright 2026 Mahimai Labs',
    },

    search: {
      provider: 'local',
    },

    outline: {
      level: [2, 3],
      label: 'On this page',
    },
  },
})
