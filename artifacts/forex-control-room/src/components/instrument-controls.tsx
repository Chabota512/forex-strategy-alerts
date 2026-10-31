import type { InstrumentReviewInfo, InstrumentSymbol } from '@workspace/api-client-react';

export const INSTRUMENT_SYMBOLS: readonly InstrumentSymbol[] = [
  'XAUUSD',
  'GBPUSD',
  'USDJPY',
  'GBPJPY',
  'AUDJPY',
];

const MARKET_NAMES: Record<InstrumentSymbol, string> = {
  XAUUSD: 'GOLD / U.S. DOLLAR',
  GBPUSD: 'BRITISH POUND / U.S. DOLLAR',
  USDJPY: 'U.S. DOLLAR / JAPANESE YEN',
  GBPJPY: 'BRITISH POUND / JAPANESE YEN',
  AUDJPY: 'AUSTRALIAN DOLLAR / JAPANESE YEN',
};

const SYMBOL_SESSION_KEY = 'forex-strategy-lab-symbol';

export function instrumentLabel(symbol?: string | null): string | null {
  if (!symbol) return null;
  const marketName = MARKET_NAMES[symbol as InstrumentSymbol];
  return marketName ? `${symbol} · ${marketName}` : symbol;
}

export function readSessionSymbol(): InstrumentSymbol {
  try {
    const stored = window.sessionStorage.getItem(SYMBOL_SESSION_KEY);
    return INSTRUMENT_SYMBOLS.includes(stored as InstrumentSymbol)
      ? stored as InstrumentSymbol
      : 'XAUUSD';
  } catch {
    return 'XAUUSD';
  }
}

export function persistSessionSymbol(symbol: InstrumentSymbol): void {
  try {
    window.sessionStorage.setItem(SYMBOL_SESSION_KEY, symbol);
  } catch {
    // Selection remains active for the current render if storage is unavailable.
  }
}

export function InstrumentHeading({ symbol }: { symbol?: string | null }) {
  const pairLabel = instrumentLabel(symbol);
  return <div className="instrument" data-testid="instrument-heading">
    <strong className="app-name">Forex Strategy Lab</strong>
    {pairLabel && <span className="instrument-market" title={pairLabel} data-testid="selected-pair-label">{pairLabel}</span>}
    <span className="session-state" data-mode="historical"><i aria-hidden="true"/>HISTORICAL REPLAY</span>
  </div>;
}

export function SymbolSelect({
  symbol,
  reviews,
  onChange,
}: {
  symbol: InstrumentSymbol;
  reviews: InstrumentReviewInfo[];
  onChange: (symbol: InstrumentSymbol) => void;
}) {
  return <label className="compact-field symbol-field">
    <span>SYMBOL</span>
    <select
      aria-label="Symbol"
      value={symbol}
      onChange={event => onChange(event.target.value as InstrumentSymbol)}
      data-testid="select-symbol"
    >
      {INSTRUMENT_SYMBOLS.map(registeredSymbol => {
        const approved = reviews.find(review => review.symbol === registeredSymbol)?.ownerApproved === true;
        return <option key={registeredSymbol} value={registeredSymbol} disabled={!approved}>
          {registeredSymbol}{approved ? '' : ' · pending'}
        </option>;
      })}
    </select>
  </label>;
}
