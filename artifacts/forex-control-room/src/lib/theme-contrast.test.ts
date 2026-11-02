import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const css = readFileSync(resolve(process.cwd(), 'src/index.css'), 'utf8');
const evolveCss = readFileSync(
  resolve(process.cwd(), 'src/components/evolve-control.css'),
  'utf8',
);
const autoevolveCss = readFileSync(
  resolve(process.cwd(), 'src/components/autoevolve-control.css'),
  'utf8',
);

function declarations(selector: string): Record<string, string> {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const block = css.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`))?.[1] ?? '';
  const entries: Array<[string, string]> = [];
  for (const match of block.matchAll(/--([\w-]+):\s*(#[0-9a-fA-F]{6})\s*;/g)) {
    const key = match[1];
    const value = match[2];
    if (key && value) entries.push([key, value]);
  }
  return Object.fromEntries(entries);
}

function luminance(hex: string): number {
  const channels = hex.slice(1).match(/../g)!.map(value => parseInt(value, 16) / 255);
  const linear = channels.map(value => (
    value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4
  ));
  return 0.2126 * linear[0]! + 0.7152 * linear[1]! + 0.0722 * linear[2]!;
}

function contrastRatio(first: string, second: string): number {
  const [lighter, darker] = [luminance(first), luminance(second)].sort((a, b) => b - a);
  return (lighter + 0.05) / (darker + 0.05);
}

const mutedTokens = [
  'text-muted',
  'text-secondary',
  'text-dim',
  'text-subtle',
  'text-faint',
  'text-soft',
  'text-alert-date',
  'text-empty',
];

describe('theme text contrast', () => {
  it.each([
    ['dark', declarations('.terminal'), '#171c22'],
    ['light', declarations(".terminal[data-theme='light']"), '#f3f6f8'],
  ])('keeps muted text readable in the %s theme', (_theme, tokens, background) => {
    mutedTokens.forEach(token => {
      expect(tokens[token], `${token} must be defined`).toBeTruthy();
      expect(contrastRatio(tokens[token], background), token).toBeGreaterThanOrEqual(4.5);
    });
    if (_theme === 'light') {
      mutedTokens.forEach(token => {
        expect(contrastRatio(tokens[token], '#ffffff'), `${token} on white panels`)
          .toBeGreaterThanOrEqual(4.5);
      });
    }
  });

  it('uses high-contrast amber for warning text in both themes', () => {
    const dark = declarations('.terminal');
    const light = declarations(".terminal[data-theme='light']");
    expect(contrastRatio(dark['warning-text'], dark['warning-bg'])).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(light['warning-text'], light['warning-bg'])).toBeGreaterThanOrEqual(4.5);
  });

  it('uses red for errors and keeps persistent warnings amber', () => {
    const evolveError = evolveCss.match(
      /\.evolve-error, \.evolve-failure\s*\{([^}]*)\}/,
    )?.[1] ?? '';
    const persistentAlert = autoevolveCss.match(
      /\.autoevolve-persistent-alert\s*\{([^}]*)\}/,
    )?.[1] ?? '';
    const inlineError = autoevolveCss.match(
      /\.autoevolve-inline-error\s*\{([^}]*)\}/,
    )?.[1] ?? '';
    expect(evolveError).toContain('var(--negative-text)');
    expect(evolveError).toContain('var(--negative-bg)');
    expect(inlineError).toContain('var(--negative-text)');
    expect(persistentAlert).toContain('var(--warning-text)');
  });
});
