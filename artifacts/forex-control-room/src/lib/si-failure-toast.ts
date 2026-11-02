import { toast } from 'sonner';

const TOAST_COOLDOWN_MS = 5 * 60 * 1000;
const lastToastAt = new Map<string, number>();

const failureMessages: Record<string, string> = {
  quota: 'AI quota exhausted — update the key at console',
  rate_limit: 'AI busy — will retry automatically',
  'high-demand': 'AI busy — will retry automatically',
  auth: 'AI key invalid',
  network: 'Connection issue',
  model_not_found: 'AI model unavailable',
  unknown: 'AI request failed',
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

export function getSiFailureCategory(error: unknown): string {
  if (!isRecord(error)) return 'unknown';

  const payload = isRecord(error.data) ? error.data : null;
  const category = payload?.failureCategory ?? error.failureCategory;
  if (typeof category === 'string' && category in failureMessages) return category;

  const status = typeof error.status === 'number'
    ? error.status
    : isRecord(error.response) && typeof error.response.status === 'number'
      ? error.response.status
      : null;
  if (status === 401 || status === 403) return 'auth';
  if (status === 429) return 'rate_limit';
  if (status !== null && status >= 500) return 'network';
  if (error instanceof TypeError) return 'network';
  return 'unknown';
}

export function notifySiFailure(
  category: string | null | undefined,
  enabled: boolean,
  now = Date.now(),
): boolean {
  if (!enabled || !category) return false;
  const message = failureMessages[category];
  if (!message) return false;
  const lastShownAt = lastToastAt.get(category);
  if (lastShownAt !== undefined && now - lastShownAt < TOAST_COOLDOWN_MS) {
    return false;
  }

  lastToastAt.set(category, now);
  toast.warning(message, { className: 'si-warning-toast' });
  return true;
}
