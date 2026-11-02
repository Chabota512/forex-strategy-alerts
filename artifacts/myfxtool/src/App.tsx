import { type ReactNode, useEffect, useMemo, useRef, useState } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { getGetFxRatesQueryKey, useGetFxRates } from '@workspace/api-client-react';
import type { GetFxRatesParams } from '@workspace/api-client-react';
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import {
  ArrowDownRight,
  ArrowRight,
  ArrowUpRight,
  Check,
  ChevronDown,
  CircleAlert,
  Clock3,
  Download,
  Moon,
  Printer,
  RefreshCw,
  Sun,
  TrendingUp,
} from 'lucide-react';
import { ErrorBoundary } from '@/components/error-boundary';
import { Toaster } from '@/components/ui/toaster';
import { TooltipProvider } from '@/components/ui/tooltip';
import NotFound from '@/pages/not-found';
import { Route, Switch, useLocation, Router as WouterRouter } from 'wouter';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5 * 60 * 1000,
      refetchOnWindowFocus: false,
    },
  },
});

const currencies = [
  { code: 'USD', name: 'US dollar' },
  { code: 'ZMW', name: 'Zambian kwacha' },
  { code: 'EUR', name: 'Euro' },
  { code: 'GBP', name: 'British pound' },
  { code: 'JPY', name: 'Japanese yen' },
  { code: 'CHF', name: 'Swiss franc' },
  { code: 'CAD', name: 'Canadian dollar' },
  { code: 'AUD', name: 'Australian dollar' },
  { code: 'ZAR', name: 'South African rand' },
  { code: 'KES', name: 'Kenyan shilling' },
  { code: 'INR', name: 'Indian rupee' },
  { code: 'CNY', name: 'Chinese yuan' },
  { code: 'NZD', name: 'New Zealand dollar' },
  { code: 'SGD', name: 'Singapore dollar' },
];

const refreshIntervals = [
  { label: 'Every 5 minutes', value: 5 * 60 * 1000 },
  { label: 'Every 15 minutes', value: 15 * 60 * 1000 },
  { label: 'Every hour', value: 60 * 60 * 1000 },
];

const ranges = [
  { label: '30D', days: 30 },
  { label: '90D', days: 90 },
  { label: '180D', days: 180 },
  { label: '1Y', days: 365 },
];

function dateInputValue(date: Date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function dateLabel(value: string | Date, options?: Intl.DateTimeFormatOptions) {
  const dateOnly = value instanceof Date ? value.toISOString().slice(0, 10) : value.slice(0, 10);
  const [year, month, day] = dateOnly.split('-').map(Number);
  const date = new Date(year, month - 1, day);
  if (Number.isNaN(date.getTime())) return '—';
  return new Intl.DateTimeFormat('en-US', options ?? { month: 'short', day: 'numeric', year: 'numeric' })
    .format(date);
}

function formatNumber(value: number, digits = 4) {
  return new Intl.NumberFormat('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value);
}

function currencyName(code: string) {
  return currencies.find((currency) => currency.code === code)?.name ?? code;
}

function LoadingChart() {
  return (
    <div className="chart-wrap flex flex-col justify-between" aria-label="Loading historical rates">
      <div className="skeleton h-full w-full" />
    </div>
  );
}

function RateTooltip({ active, payload, label }: {
  active?: boolean;
  payload?: Array<{ value?: number }>;
  label?: string;
}) {
  if (!active || !payload?.length || !label) return null;
  return (
    <div className="chart-tooltip">
      <div className="chart-tooltip-date">{dateLabel(label, { month: 'long', day: 'numeric', year: 'numeric' })}</div>
      <div className="chart-tooltip-value">{formatNumber(Number(payload[0].value), 5)}</div>
    </div>
  );
}

function Home() {
  const today = useMemo(() => new Date(), []);
  const [base, setBase] = useState('USD');
  const [quote, setQuote] = useState('ZMW');
  const [from, setFrom] = useState(() => dateInputValue(new Date(Date.now() - 90 * 86400000)));
  const [to, setTo] = useState(() => dateInputValue(today));
  const [activeRange, setActiveRange] = useState<number | null>(90);
  const [isDark, setIsDark] = useState(() => {
    try {
      return localStorage.getItem('myfxtool-theme') === 'dark';
    } catch {
      return false;
    }
  });
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [intervalMs, setIntervalMs] = useState(5 * 60 * 1000);
  const [menuOpen, setMenuOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const params = useMemo<GetFxRatesParams>(() => ({ base, quote, from, to }), [base, quote, from, to]);
  const query = useGetFxRates(params, {
    query: {
      queryKey: getGetFxRatesQueryKey(params),
      staleTime: 5 * 60 * 1000,
      refetchOnWindowFocus: false,
      refetchInterval: autoRefresh ? intervalMs : false,
    },
  });
  const data = query.data;
  const ratePoints = data?.rates ?? [];
  const hasRangeError = from > to;
  const isLoading = query.isLoading || query.isFetching;

  useEffect(() => {
    document.documentElement.classList.toggle('dark', isDark);
    try {
      localStorage.setItem('myfxtool-theme', isDark ? 'dark' : 'light');
    } catch {
      // Theme still works for this session when storage is unavailable.
    }
  }, [isDark]);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) setMenuOpen(false);
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  function applyRange(days: number) {
    const end = new Date();
    const start = new Date(end);
    start.setDate(start.getDate() - days);
    setFrom(dateInputValue(start));
    setTo(dateInputValue(end));
    setActiveRange(days);
  }

  function updateDate(setter: (value: string) => void, value: string) {
    setter(value);
    setActiveRange(null);
  }

  function exportCsv() {
    if (!data?.rates.length) return;
    const rows = [['date', `${data.base}_${data.quote}_reference_rate`], ...data.rates.map((point) => [point.date, String(point.rate)])];
    const content = rows.map((row) => row.map((cell) => `"${cell.replaceAll('"', '""')}"`).join(',')).join('\r\n');
    const blob = new Blob([content], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${data.base.toLowerCase()}-${data.quote.toLowerCase()}-reference-rates.csv`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }

  const firstRate = ratePoints[0]?.rate;
  const latestRate = ratePoints[ratePoints.length - 1]?.rate;
  const change = firstRate !== undefined && latestRate !== undefined ? latestRate - firstRate : null;
  const changePercent = change !== null && firstRate ? (change / firstRate) * 100 : null;
  const lastRefreshed = query.dataUpdatedAt
    ? new Intl.DateTimeFormat('en-US', { hour: 'numeric', minute: '2-digit', month: 'short', day: 'numeric' }).format(query.dataUpdatedAt)
    : null;
  const gridColor = isDark ? 'rgba(226,236,230,0.09)' : 'rgba(34,74,65,0.10)';
  const axisColor = isDark ? '#9caaa2' : '#7b8881';
  const lineColor = isDark ? '#7dc5a5' : '#286957';
  const titlePair = `${base} / ${quote}`;
  const errorMessage = query.error instanceof Error ? query.error.message : 'The historical rate service could not be reached.';

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="page-wrap flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="brand-mark" aria-hidden="true"><TrendingUp size={17} strokeWidth={2.3} /></div>
            <div className="brand-name">my<span>FX</span>tool</div>
          </div>
          <div className="flex items-center gap-5">
            <span className="topnav-label hidden sm:block">Reference rates · Daily history</span>
            <button
              className="icon-button print-hidden"
              type="button"
              aria-label={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
              data-testid="button-toggle-theme"
              onClick={() => setIsDark((value) => !value)}
            >
              {isDark ? <Sun size={17} /> : <Moon size={17} />}
            </button>
          </div>
        </div>
      </header>

      <main className="page-wrap main-content">
        <section className="mb-7 flex flex-col justify-between gap-6 sm:flex-row sm:items-end">
          <div className="fade-up">
            <div className="eyebrow">Currency history, clearly</div>
            <h1 className="page-title">A closer look at exchange rates.</h1>
            <p className="page-subtitle max-w-[580px]">
              Compare daily reference-rate movements across currencies and dates. Built for context, not trading.
            </p>
          </div>
          <div className="flex items-center gap-2 self-start sm:self-auto print-hidden fade-up delay-1">
            <div className="relative" ref={dropdownRef}>
              <div className="flex">
                <button
                  type="button"
                  className="action-button rounded-r-none"
                  disabled={query.isFetching || hasRangeError}
                  onClick={() => query.refetch()}
                  data-testid="button-refresh-rates"
                >
                  <RefreshCw size={15} className={query.isFetching ? 'animate-spin' : ''} />
                  Refresh
                </button>
                <button
                  type="button"
                  aria-label="Refresh settings"
                  aria-expanded={menuOpen}
                  className="action-button rounded-l-none border-l border-white/20 px-3"
                  onClick={() => setMenuOpen((open) => !open)}
                  data-testid="button-refresh-settings"
                >
                  <ChevronDown size={15} />
                </button>
              </div>
              {menuOpen && (
                <div className="refresh-menu">
                  <div className="menu-title">Refresh settings</div>
                  <button
                    className="menu-option"
                    type="button"
                    aria-pressed={autoRefresh}
                    onClick={() => setAutoRefresh((enabled) => !enabled)}
                    data-testid="toggle-auto-refresh"
                  >
                    <span>Automatic refresh</span>
                    <span className={`flex h-5 w-9 items-center rounded-full p-[2px] transition-colors ${autoRefresh ? 'bg-primary justify-end' : 'bg-muted justify-start'}`}>
                      <span className="h-4 w-4 rounded-full bg-background" />
                    </span>
                  </button>
                  <div className="mb-1 mt-3 px-2 text-[10px] text-muted-foreground">INTERVAL</div>
                  {refreshIntervals.map((interval) => (
                    <button
                      type="button"
                      key={interval.value}
                      className={`menu-option ${intervalMs === interval.value ? 'selected' : ''}`}
                      onClick={() => {
                        setIntervalMs(interval.value);
                        setAutoRefresh(true);
                      }}
                      data-testid={`option-refresh-${interval.value}`}
                    >
                      <span>{interval.label}</span>
                      {intervalMs === interval.value && <Check size={14} />}
                    </button>
                  ))}
                  <div className="mt-2 border-t border-border pt-2 text-[10px] leading-relaxed text-muted-foreground">
                    Automatic refresh is off by default. Minimum interval: 5 minutes.
                  </div>
                </div>
              )}
            </div>
            <button
              type="button"
              className="icon-button print-hidden"
              aria-label="Print or save as PDF"
              onClick={() => window.print()}
              data-testid="button-print"
            >
              <Printer size={16} />
            </button>
          </div>
        </section>

        <section className="data-card control-card fade-up delay-1" aria-label="Rate search controls">
          <div className="mb-4 flex items-center gap-2">
            <span className="eyebrow">01</span>
            <span className="text-[12px] font-semibold">Choose your comparison</span>
          </div>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-[1fr_36px_1fr_1fr_1fr] md:items-end">
            <label>
              <span className="control-label">Base currency</span>
              <select className="field" value={base} onChange={(event) => setBase(event.target.value)} data-testid="select-base-currency">
                {currencies.map((currency) => <option key={currency.code} value={currency.code}>{currency.code} — {currency.name}</option>)}
              </select>
            </label>
            <div className="hidden h-[42px] items-center justify-center text-muted-foreground md:flex" aria-hidden="true">
              <ArrowRight size={16} />
            </div>
            <label>
              <span className="control-label">Quote currency</span>
              <select className="field" value={quote} onChange={(event) => setQuote(event.target.value)} data-testid="select-quote-currency">
                {currencies.map((currency) => <option key={currency.code} value={currency.code}>{currency.code} — {currency.name}</option>)}
              </select>
            </label>
            <label>
              <span className="control-label">From</span>
              <input className="field" type="date" max={to} value={from} onChange={(event) => updateDate(setFrom, event.target.value)} data-testid="input-date-from" />
            </label>
            <label>
              <span className="control-label">Through</span>
              <input className="field" type="date" max={dateInputValue(new Date())} min={from} value={to} onChange={(event) => updateDate(setTo, event.target.value)} data-testid="input-date-to" />
            </label>
          </div>
          <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-1.5">
              <span className="mr-1 text-[10px] font-medium text-muted-foreground">QUICK RANGE</span>
              {ranges.map((range) => (
                <button
                  type="button"
                  key={range.days}
                  className={`preset ${activeRange === range.days ? 'active' : ''}`}
                  onClick={() => applyRange(range.days)}
                  data-testid={`preset-range-${range.days}`}
                >
                  {range.label}
                </button>
              ))}
            </div>
            <div className="source-note">
              {currencyName(base)} in {currencyName(quote)}
              {hasRangeError && <span className="ml-3 text-destructive">Start date must come before end date.</span>}
            </div>
          </div>
        </section>

        <section className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-3 fade-up delay-2" aria-label="Rate summary">
          <div className="data-card p-5">
            <div className="stat-label">Latest reference rate</div>
            {isLoading ? <div className="skeleton mt-3 h-7 w-36" /> : (
              <div className="stat-value mono" data-testid="text-latest-rate">{latestRate === undefined ? '—' : formatNumber(latestRate)}</div>
            )}
            <div className="stat-foot">
              {latestRate === undefined ? `Daily ${base}/${quote} reference history` : `1 ${base} = ${formatNumber(latestRate, 4)} ${quote} at the latest observation`}
            </div>
          </div>
          <div className="data-card p-5">
            <div className="stat-label">Movement in selected period</div>
            {isLoading ? <div className="skeleton mt-3 h-7 w-32" /> : (
              <div className="stat-value mono flex items-center gap-2" data-testid="text-period-movement">
                {change === null || changePercent === null ? '—' : (
                  <>
                    {change > 0 ? <ArrowUpRight size={19} className="text-[hsl(var(--primary))]" /> : change < 0 ? <ArrowDownRight size={19} className="text-[hsl(var(--accent))]" /> : null}
                    <span>{change > 0 ? '+' : ''}{formatNumber(change, 4)}</span>
                    <span className="text-[13px] font-medium text-muted-foreground">({changePercent > 0 ? '+' : ''}{changePercent.toFixed(2)}%)</span>
                  </>
                )}
              </div>
            )}
            <div className="stat-foot">{base} per 1 {quote} · first to latest observation</div>
          </div>
          <div className="data-card p-5">
            <div className="stat-label">Daily observations</div>
            {isLoading ? <div className="skeleton mt-3 h-7 w-20" /> : (
              <div className="stat-value mono" data-testid="text-observation-count">{ratePoints.length.toLocaleString()}</div>
            )}
            <div className="stat-foot">Reference rates in the selected date range</div>
          </div>
        </section>

        <section className="data-card rate-chart-card mt-5 fade-up delay-2" aria-label="Historical exchange rate chart">
          <div className="chart-topline flex flex-wrap items-start justify-between gap-4">
            <div>
              <div className="mb-2 flex items-center gap-2">
                <span className="eyebrow">02 / History</span>
                <span className="rounded-full bg-primary/10 px-2 py-1 text-[9px] font-medium uppercase tracking-[.1em] text-primary">Daily reference</span>
              </div>
              <h2 className="chart-heading">{titlePair} reference rate</h2>
              <p className="chart-subheading">{base} to {quote} · {from && to ? `${dateLabel(from)} — ${dateLabel(to)}` : 'Select dates'}</p>
            </div>
            <button
              type="button"
              className="quiet-button print-hidden"
              onClick={exportCsv}
              disabled={!ratePoints.length || isLoading}
              data-testid="button-export-csv"
            >
              <Download size={14} />
              Export CSV
            </button>
          </div>

          {hasRangeError ? (
            <div className="chart-wrap flex items-center justify-center">
              <div className="status-box max-w-md">
                <CircleAlert size={19} className="mt-0.5 shrink-0 text-destructive" />
                <div><div className="text-sm font-semibold">Check the date range</div><div className="mt-1 text-xs text-muted-foreground">The start date must be earlier than or equal to the end date.</div></div>
              </div>
            </div>
          ) : isLoading ? (
            <LoadingChart />
          ) : query.isError ? (
            <div className="chart-wrap flex items-center justify-center">
              <div className="status-box max-w-lg">
                <CircleAlert size={19} className="mt-0.5 shrink-0 text-destructive" />
                <div className="flex-1">
                  <div className="text-sm font-semibold">Rates are unavailable right now</div>
                  <div className="mt-1 text-xs leading-relaxed text-muted-foreground">{errorMessage}</div>
                  <button type="button" className="quiet-button mt-4" onClick={() => query.refetch()} data-testid="button-retry">
                    <RefreshCw size={13} /> Try again
                  </button>
                </div>
              </div>
            </div>
          ) : ratePoints.length === 0 ? (
            <div className="chart-wrap flex items-center justify-center">
              <div className="status-box max-w-lg">
                <Clock3 size={19} className="mt-0.5 shrink-0 text-muted-foreground" />
                <div><div className="text-sm font-semibold">No rates found for these dates</div><div className="mt-1 text-xs text-muted-foreground">Try a wider range or another currency pair.</div></div>
              </div>
            </div>
          ) : (
            <div className="chart-wrap" data-testid="chart-historical-rates">
              <ResponsiveContainer width="100%" height="100%" debounce={0}>
                <AreaChart data={ratePoints} margin={{ top: 8, right: 16, left: 4, bottom: 2 }}>
                  <defs>
                    <linearGradient id="rate-fill" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor={lineColor} stopOpacity={0.22} />
                      <stop offset="100%" stopColor={lineColor} stopOpacity={0.015} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid stroke={gridColor} strokeDasharray="2 5" vertical={false} />
                  <XAxis
                    dataKey="date"
                    tickFormatter={(value: string) => dateLabel(value, { month: 'short', day: 'numeric' })}
                    tick={{ fill: axisColor, fontSize: 10, fontFamily: 'DM Mono' }}
                    axisLine={{ stroke: gridColor }}
                    tickLine={false}
                    minTickGap={48}
                    tickMargin={12}
                  />
                  <YAxis
                    domain={['dataMin - (dataMax - dataMin) * 0.12', 'dataMax + (dataMax - dataMin) * 0.12']}
                    tickFormatter={(value: number) => Number(value).toFixed(2)}
                    tick={{ fill: axisColor, fontSize: 10, fontFamily: 'DM Mono' }}
                    axisLine={false}
                    tickLine={false}
                    width={58}
                    tickMargin={10}
                  />
                  <Tooltip content={<RateTooltip />} cursor={{ stroke: lineColor, strokeDasharray: '3 4', strokeOpacity: 0.45 }} isAnimationActive={false} />
                  <Area
                    type="monotone"
                    dataKey="rate"
                    name={`${base} / ${quote}`}
                    stroke={lineColor}
                    strokeWidth={2.2}
                    fill="url(#rate-fill)"
                    activeDot={{ r: 4, stroke: 'hsl(var(--card))', strokeWidth: 2, fill: lineColor }}
                    dot={false}
                    isAnimationActive={false}
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          )}

          <div className="chart-meta">
            <span>REFERENCE RATE · 1 {base} = {quote}</span>
            <span>{lastRefreshed ? `Updated ${lastRefreshed}` : 'Waiting for first update'}</span>
          </div>
        </section>

        {data && !isLoading && !query.isError && ratePoints.length > 0 && (
          <section className="data-card mt-5 overflow-hidden fade-up" aria-label="Recent daily reference rates">
            <div className="flex flex-wrap items-center justify-between gap-2 px-5 py-4">
              <div>
                <div className="eyebrow">Recent observations</div>
                <h2 className="mt-1 text-sm font-semibold">Daily reference rates</h2>
              </div>
              <span className="text-[10px] text-muted-foreground">Showing latest {Math.min(ratePoints.length, 6)} rows</span>
            </div>
            <div className="grid grid-cols-[1fr_1fr_1fr] px-5 pb-2 text-[9px] font-medium uppercase tracking-[.13em] text-muted-foreground">
              <span>Date</span><span>Pair</span><span className="text-right">Reference rate</span>
            </div>
            {[...ratePoints].slice(-6).reverse().map((point) => (
              <div className="table-row grid grid-cols-[1fr_1fr_1fr] px-5 py-3 text-xs" key={point.date} data-testid={`row-rate-${point.date}`}>
                <span>{dateLabel(point.date)}</span>
                <span className="mono text-muted-foreground">{data.base}/{data.quote}</span>
                <span className="mono text-right font-medium">{formatNumber(point.rate, 5)}</span>
              </div>
            ))}
          </section>
        )}

        <footer className="mt-6 flex flex-col gap-2 border-t border-border/70 pt-4 sm:flex-row sm:items-start sm:justify-between">
          <p className="source-note max-w-[760px]">
            <strong className="font-semibold text-foreground">Source note.</strong> {data?.source || 'Daily historical reference rates'}; these are indicative reference rates, not live trading quotes or executable prices. Rates are published for reference and may not match a provider’s transaction rate.
          </p>
          <p className="source-note shrink-0">{autoRefresh ? `Auto-refresh · ${refreshIntervals.find((item) => item.value === intervalMs)?.label}` : 'Auto-refresh off'}</p>
        </footer>
      </main>
    </div>
  );
}

function Router() {
  return (
    <RoutedErrorBoundary>
      <Switch>
        <Route path="/" component={Home} />
        <Route component={NotFound} />
      </Switch>
    </RoutedErrorBoundary>
  );
}

function RoutedErrorBoundary({ children }: { children: ReactNode }) {
  const [location] = useLocation();
  return <ErrorBoundary resetKey={location}>{children}</ErrorBoundary>;
}

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <WouterRouter base={import.meta.env.BASE_URL.replace(/\/$/, '')}>
          <Router />
        </WouterRouter>
        <Toaster />
      </TooltipProvider>
    </QueryClientProvider>
  );
}

export default App;
