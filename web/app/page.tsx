'use client';

import { useEffect, useRef, useState, type FormEvent } from 'react';
import { ArrowDownToLine, ArrowRight, AudioLines, Check, Link2, LoaderCircle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Progress } from '@/components/ui/progress';
import type { FFmpeg } from '@ffmpeg/ffmpeg';

type Stage = 'idle' | 'fetching' | 'loading' | 'converting' | 'done' | 'error';
const labels: Record<Stage, string> = {
  idle: 'Ready when you are', fetching: 'Retrieving source audio…',
  loading: 'Loading the browser converter…', converting: 'Converting to MP3 in your browser…',
  done: 'Your MP3 is ready', error: 'Couldn’t convert this link',
};

export default function Home() {
  const [url, setUrl] = useState('');
  const [stage, setStage] = useState<Stage>('idle');
  const [error, setError] = useState('');
  const [progress, setProgress] = useState(0);
  const [result, setResult] = useState<{ url: string; name: string; size: string } | null>(null);
  const engine = useRef<FFmpeg | null>(null);
  const resultURL = useRef<string | null>(null);
  const controller = useRef<AbortController | null>(null);
  const active = useRef(false);
  const currentStatus = useRef({ stage, error, result });
  const busy = ['fetching', 'loading', 'converting'].includes(stage);

  useEffect(() => { currentStatus.current = { stage, error, result }; }, [stage, error, result]);

  useEffect(() => {
    const context = (document as Document & { modelContext?: {
      registerTool: (tool: object, options: { signal: AbortSignal }) => void | Promise<void>;
    } }).modelContext;
    if (!context?.registerTool) return;
    const lifecycle = new AbortController();
    try {
      void Promise.resolve(context.registerTool({
        name: 'get_conversion_status',
        description: 'Read the current conversion status and, when ready, the MP3 download link. Does not start a conversion.',
        inputSchema: { type: 'object', properties: {}, additionalProperties: false },
        annotations: { readOnlyHint: true, untrustedContentHint: true },
        execute: () => currentStatus.current,
      }, { signal: lifecycle.signal })).catch(() => {});
    } catch { /* Browsers without the optional registry still support conversion. */ }
    return () => lifecycle.abort();
  }, []);

  useEffect(() => () => {
    controller.current?.abort();
    engine.current?.terminate();
    if (resultURL.current) URL.revokeObjectURL(resultURL.current);
  }, []);

  async function convert(event: FormEvent) {
    event.preventDefault();
    if (active.current) return;
    try {
      const parsed = new URL(url.trim());
      const host = parsed.hostname;
      if (parsed.protocol !== 'https:' || parsed.username || parsed.password ||
          (parsed.port && parsed.port !== '443') ||
          !['youtube.com', 'youtu.be', 'soundcloud.com'].some(domain => host === domain || host.endsWith(`.${domain}`))) {
        throw new Error();
      }
    } catch {
      setError('Paste an HTTPS YouTube or SoundCloud track link.');
      setStage('error');
      return;
    }
    active.current = true;
    setError('');
    setResult(null);
    setProgress(0);
    if (resultURL.current) URL.revokeObjectURL(resultURL.current);
    resultURL.current = null;
    controller.current = new AbortController();
    const timer = setTimeout(() => controller.current?.abort(), 165_000);
    try {
      setStage('fetching');
      const response = await fetch('/api/source', {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Transmute-Client': 'web' },
        body: JSON.stringify({ url: url.trim() }), signal: controller.current.signal,
      });
      if (!response.ok) {
        const data = await response.json().catch(() => null);
        throw new Error(data && typeof data === 'object' && 'error' in data && typeof data.error === 'string'
          ? data.error : 'The source helper is unavailable. Please try again.');
      }
      const source = new Uint8Array(await response.arrayBuffer());
      clearTimeout(timer);
      if (!source.length || source.length > 50 * 1024 * 1024) throw new Error('Source audio must be under 50 MB.');
      const title = decodeURIComponent(response.headers.get('X-Track-Title') || 'Audio');
      setStage('loading');
      const { FFmpeg } = await import('@ffmpeg/ffmpeg');
      const ffmpeg = new FFmpeg();
      engine.current = ffmpeg;
      ffmpeg.on('progress', ({ progress: fraction }) => setProgress(Math.max(0, Math.min(99, Math.round(fraction * 100)))));
      const loadTimer = setTimeout(() => ffmpeg.terminate(), 90_000);
      try {
        await ffmpeg.load({ coreURL: '/ffmpeg/ffmpeg-core.js', wasmURL: '/ffmpeg/ffmpeg-core.wasm' });
      } finally {
        clearTimeout(loadTimer);
      }
      await ffmpeg.writeFile('source', source);
      setStage('converting');
      const code = await ffmpeg.exec(['-i', 'source', '-vn', '-map_metadata', '-1', '-c:a', 'libmp3lame', '-b:a', '320k', '-id3v2_version', '0', '-write_id3v1', '0', 'output.mp3'], 180_000);
      if (code !== 0) throw new Error('Conversion failed or took too long. Try a shorter track.');
      const data = await ffmpeg.readFile('output.mp3');
      if (typeof data === 'string' || !data.length) throw new Error('The converter returned an empty file.');
      const blob = new Blob([new Uint8Array(data)], { type: 'audio/mpeg' });
      const objectURL = URL.createObjectURL(blob);
      resultURL.current = objectURL;
      const name = (title.replace(/[<>:"/\\|?*\x00-\x1f]/g, '').trim().slice(0, 160) || 'Audio') + '.mp3';
      setResult({ url: objectURL, name, size: `${(blob.size / 1024 / 1024).toFixed(1)} MB` });
      setProgress(100);
      setStage('done');
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : '';
      setError(controller.current?.signal.aborted ? 'Source retrieval timed out. Try a shorter track.' :
        message.startsWith('Failed to fetch') ? 'Cannot reach the source helper. Check that it is running.' :
        message.includes('terminat') ? 'The browser converter could not start. Reload the page and try again.' :
        message.slice(0, 240) || 'Conversion failed. Reload the page and try again.');
      setStage('error');
    } finally {
      clearTimeout(timer);
      engine.current?.terminate();
      engine.current = null;
      active.current = false;
    }
  }

  return (
    <div className="page-shell">
      <header className="masthead">
        <a className="wordmark" href="/" aria-label="Transmute home"><AudioLines aria-hidden="true" size={24} />transmute<span className="wordmark-dot">.</span></a>
        <span className="edition">WEB / PROTOTYPE</span>
      </header>
      <main>
        <div className="eyebrow"><span /> YOUTUBE + SOUNDCLOUD</div>
        <h1>A link.<br />An MP3.<br /><span>That’s it.</span></h1>
        <p className="intro">Paste a track link. Get the audio.<br />No sign-in. No AI. No extra steps.</p>
        <form onSubmit={convert} aria-busy={busy}>
          <label htmlFor="track">Track link</label>
          <div className="input-wrap"><Link2 size={18} aria-hidden="true" /><Input id="track" type="url" value={url} onChange={event => setUrl(event.target.value)} placeholder="https://soundcloud.com/…" required maxLength={2048} disabled={busy} aria-describedby="track-help" aria-invalid={stage === 'error'} className="track-input" autoComplete="off" /></div>
          <p id="track-help" className="hint">One track · Up to 20 minutes · 50 MB source limit</p>
          <Button type="submit" disabled={busy || !url.trim()} className="convert-button">{busy ? <><LoaderCircle className="spin" />Working on it…</> : <>Convert to MP3<ArrowRight /></>}</Button>
        </form>
        <section className={`status-panel ${stage}`} aria-live="polite" aria-atomic="true">
          <div className="status-heading">{stage === 'done' ? <Check size={17} /> : busy ? <LoaderCircle size={17} className="spin" /> : <span className="status-dot" />}<span>{labels[stage]}</span>{stage === 'converting' && <span className="percentage">{progress}%</span>}</div>
          {busy && <Progress value={stage === 'converting' ? progress : null} aria-label={labels[stage]} className="conversion-progress" />}
          {stage === 'error' && <p role="alert" className="error-copy">{error}</p>}
          {result && stage === 'done' && <div className="result"><p className="filename">{result.name}</p><p className="hint">MP3 · {result.size}</p><a className="download-button" href={result.url} download={result.name}><ArrowDownToLine size={17} />Save MP3</a></div>}
        </section>
        <div className="details"><span>MP3 / 320 kbps</span><span>No added tags or artwork</span></div>
      </main>
      <footer><p>Audio is retrieved through the prototype’s local helper.<br />MP3 conversion happens in your browser.</p><a href="https://github.com/vznh/transmute" target="_blank" rel="noreferrer">Transmute on GitHub ↗</a></footer>
    </div>
  );
}
