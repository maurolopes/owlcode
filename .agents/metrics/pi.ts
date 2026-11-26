import type { ExtensionAPI } from '@earendil-works/pi-coding-agent';
import { appendFileSync, mkdirSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';

const runId = /^[A-Z][A-Z0-9]{0,9}-[0-9]{1,7}-[0-9]{8}T[0-9]{6}Z-[A-Za-z0-9.-]{1,63}$/u;
const startPrompt = /^\/(?:skill:)?implement ([A-Z][A-Z0-9]{0,9}-[0-9]{1,7}) start ([0-9]{8}T[0-9]{6}Z-[A-Za-z0-9.-]{1,63})(?:\s|$)/u;
const deltas = new Set(['text_delta', 'thinking_delta', 'toolcall_delta']);
const number = (value: unknown) => typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null;
const name = (value: unknown) => typeof value === 'string' && /^[\w./:@+-]{1,200}$/u.test(value) ? value : 'unknown';

type Stamp = { wall: number; tick: number };

export function recordCalls(
  pi: ExtensionAPI,
  folder: string,
  stamp = (): Stamp => ({ wall: Date.now(), tick: performance.now() }),
  reviewRun = process.env.AGENT_METRICS_RUN ?? '',
) {
  const review = runId.test(reviewRun);
  let run = review ? reviewRun : '';
  let started: Stamp | undefined;
  let first: Stamp | undefined;
  let status: number | null = null;
  let effort = 'unknown';

  pi.on('input', (event) => {
    if (run) return;
    const found = startPrompt.exec(event.text.trim());
    if (found) run = `${found[1]}-${found[2]}`;
  });
  pi.on('before_provider_request', (_event, ctx) => {
    if (!run) return;
    started = stamp();
    first = undefined;
    status = null;
    effort = name(ctx.thinkingLevel);
  });
  pi.on('after_provider_response', (event) => {
    status = event.status >= 400 ? event.status : null;
  });
  pi.on('message_update', ({ assistantMessageEvent: event }) => {
    if (started && !first && deltas.has(event.type) && 'delta' in event && event.delta) first = stamp();
  });
  pi.on('message_end', ({ message }, ctx) => {
    if (!started || message.role !== 'assistant') return;
    const ended = stamp();
    const request = started;
    started = undefined;
    const usage = message.usage;
    const failure = message.stopReason === 'error' ? status ? `http_${status}` : 'api_error'
      : message.stopReason === 'aborted' ? 'aborted' : null;
    const elapsed = first ? (ended.tick - first.tick) / 1000 : 0;
    const output = number(usage?.output);
    const input = number(usage?.input);
    const cached = number(usage?.cacheRead);
    const cacheWrite = number(usage?.cacheWrite);
    const priced = ctx.model?.provider === message.provider && ctx.model?.id === message.model
      && Object.values(ctx.model?.cost ?? {}).some((rate) => rate > 0);
    const total = number(usage?.cost?.total);
    const cost = total === 0 && !priced && !usage?.cost?.totalFromProvider ? null : total;
    const record = {
      role: review ? 'review' : 'run',
      provider: name(message.provider), model: name(message.model), effort,
      started_ms: request.wall, first_ms: first?.wall ?? null, ended_ms: ended.wall,
      ttft_s: first ? (first.tick - request.tick) / 1000 : null,
      tokens_per_s: !failure && output && elapsed > 0 ? output / elapsed : null,
      input: input === null ? null : input + (cached ?? 0) + (cacheWrite ?? 0),
      cached, output, reasoning: number(usage?.reasoning), cost, error: failure,
    };
    mkdirSync(folder, { recursive: true, mode: 0o700 });
    appendFileSync(join(folder, `${run}.jsonl`), `${JSON.stringify(record)}\n`, { mode: 0o600 });
  });
}

export default function metrics(pi: ExtensionAPI) {
  recordCalls(pi, join(homedir(), '.local', 'state', 'agent-metrics'));
}
