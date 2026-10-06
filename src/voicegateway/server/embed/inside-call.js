// VoiceGateway Inside your call. The host supplies an already-authorized summary.
// This component never fetches credentials or connects to an operator dashboard.
export class InsideCallElement extends HTMLElement {
  constructor() { super(); this.attachShadow({ mode: 'open' }); }
  set summary(value) { this.value = value; this.render(); }
  connectedCallback() { this.render(); }
  render() {
    const root = this.shadowRoot;
    root.replaceChildren();
    const style = document.createElement('style');
    style.textContent = `:host{display:block;color:var(--color-ink,#171717);font:inherit}*{box-sizing:border-box}h2{font:500 1.5rem var(--font-heading,sans-serif);margin:0}header{display:flex;flex-wrap:wrap;align-items:baseline;justify-content:space-between;gap:12px}a{color:var(--color-accent,#6f3fb0);text-underline-offset:4px;font-size:.875rem}a:focus-visible{outline:2px solid currentColor;outline-offset:4px}p{color:var(--color-muted,#55555c);line-height:1.6;font-size:.875rem}.stats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:24px;margin:24px 0}.stat strong{display:block;font:500 1.35rem var(--font-mono,monospace);font-variant-numeric:tabular-nums;margin-top:8px}.label{color:var(--color-muted,#55555c);font-size:.8125rem}.services{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:24px}.service{min-width:0}h3{font-size:1rem;font-weight:500;margin:0 0 8px}.service p{margin:4px 0;overflow-wrap:anywhere}.model{font-size:.75rem} @media(max-width:560px){.stats,.services{grid-template-columns:1fr;gap:20px}.stat strong{display:inline;margin-left:12px;font-size:1.1rem}}`;
    root.append(style);
    const el = (tag, text, parent = root, cls = '') => { const n = document.createElement(tag); n.textContent = text; if(cls)n.className=cls; parent.append(n); return n; };
    const header = el('header',''); el('h2','Inside your call',header);
    const link = el('a','Powered by VoiceGateway ↗',header); link.href='https://voicegateway.dev'; link.target='_blank'; link.rel='noopener noreferrer';
    const data = this.value;
    el('p',data ? (data.status === 'ended' ? 'Call ended. Late provider measurements may still arrive.' : 'Measurements from this call update as providers report them.') : 'Start a demo to see its duration, provider usage, and estimated cost.');
    const rows = Array.isArray(data?.services) ? data.services : [];
    const incomplete = rows.some(r => r.pricing_complete === false);
    const money = incomplete ? 'Incomplete' : rows.length ? '$'+(rows.reduce((n,r)=>n+r.cost_microusd,0)/1e6).toFixed(4) : 'Processing';
    const stats = el('div','',root,'stats');
    const stat = (label,value) => {const n=el('div','',stats,'stat'); el('span',label,n,'label');el('strong',value,n);};
    stat('Call duration', data ? `${Math.floor(data.elapsed_seconds/60)}:${String(data.elapsed_seconds%60).padStart(2,'0')}` : '—');
    stat('Estimated provider cost',data ? money : '—');
    stat('Measurements',data?.stale && data?.status !== 'ended' ? 'Delayed' : rows.length ? 'Received' : data ? 'Processing' : 'Waiting');
    const services=el('div','',root,'services');
    const isVoice = r => r.modality === 'llm' && (r.measurements?.audio_seconds != null || r.measurements?.realtime_audio_input != null);
    const groups = rows.some(isVoice) ? [['voice','Listens and speaks'],['llm','Handles tools']] : [['stt','Hears you'],['llm','Thinks through it'],['tts','Speaks back']];
    for(const [modality,title] of groups) {
      const box=el('section','',services,'service'); el('h3',title,box);
      const matches=rows.filter(r=>modality==='voice' ? isVoice(r) : r.modality===modality && !isVoice(r));
      if(!matches.length){el('p',data?'Processing':'No measurements yet',box);continue;}
      for(const row of matches){
        el('p',row.model,box,'model');
        const usage=modality==='voice' ? (row.measurements.audio_seconds != null ? `${row.measurements.audio_seconds.toFixed(1)} session seconds` : `${Math.round(row.measurements.realtime_audio_input)} input / ${Math.round(row.measurements.realtime_audio_output || 0)} audio tokens`) : modality==='stt' ? `${(row.input_units*60).toFixed(1)} audio seconds` : modality==='tts' ? `${Math.round(row.input_units)} characters` : `${Math.round(row.input_units)} input / ${Math.round(row.output_units)} output tokens`;
        el('p',usage,box);
        if(row.pricing_complete === false)el('p','Price not fully available',box);
        if(row.measurements?.cache_write)el('p',`${Math.round(row.measurements.cache_write)} cache-write tokens included`,box);
        if(modality!=='stt' && modality!=='voice')el('p',row.ttfb_ms==null ? 'Timing not reported' : `${Math.round(row.ttfb_ms)} ms average to first ${modality==='llm'?'token':'audio chunk'}`,box);
      }
    }
    el('p','Provider estimates exclude hosting and transport. First-token and first-audio timings are separate measurements, not end-to-end response latency.');
  }
}
if(!customElements.get('voicegateway-inside-call'))customElements.define('voicegateway-inside-call',InsideCallElement);
