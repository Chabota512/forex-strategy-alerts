import { Router, type IRouter } from "express";
import {
  GetFxRatesQueryParams,
  GetFxRatesResponse,
} from "@workspace/api-zod";

const router: IRouter = Router();
const DATE_ONLY_PATTERN = /^\d{4}-\d{2}-\d{2}$/;
const MAX_RANGE_DAYS = 370;

function parseDateOnly(value: unknown): Date | null {
  if (typeof value !== "string" || !DATE_ONLY_PATTERN.test(value)) {
    return null;
  }

  const date = new Date(`${value}T00:00:00.000Z`);
  if (
    Number.isNaN(date.getTime()) ||
    date.toISOString().slice(0, 10) !== value
  ) {
    return null;
  }

  return date;
}

router.get("/fx/rates", async (req, res): Promise<void> => {
  const from = parseDateOnly(req.query.from);
  const to = parseDateOnly(req.query.to);
  const parsed = GetFxRatesQueryParams.safeParse({
    base: req.query.base,
    quote: req.query.quote,
    from,
    to,
  });

  if (!parsed.success) {
    res.status(400).json({ error: "Choose a valid currency pair and date range." });
    return;
  }

  const { base, quote } = parsed.data;
  if (base === quote) {
    res.status(400).json({ error: "Base and quote currencies must differ." });
    return;
  }

  const fromDate = parsed.data.from;
  const toDate = parsed.data.to;
  const rangeDays = Math.ceil(
    (toDate.getTime() - fromDate.getTime()) / (24 * 60 * 60 * 1000),
  );
  const today = new Date();
  today.setUTCHours(0, 0, 0, 0);

  if (
    fromDate > toDate ||
    rangeDays > MAX_RANGE_DAYS ||
    toDate > today
  ) {
    res.status(400).json({
      error: "The date range must be valid, end by today, and span no more than one year.",
    });
    return;
  }

  const fromText = fromDate.toISOString().slice(0, 10);
  const toText = toDate.toISOString().slice(0, 10);
  const providerUrl = new URL("https://api.frankfurter.dev/v2/rates");
  providerUrl.searchParams.set("base", base);
  providerUrl.searchParams.set("quotes", quote);
  providerUrl.searchParams.set("from", fromText);
  providerUrl.searchParams.set("to", toText);

  let providerResponse: Response;
  try {
    providerResponse = await fetch(providerUrl, {
      signal: AbortSignal.timeout(10_000),
      headers: { accept: "application/json" },
    });
  } catch (error) {
    req.log.warn({ error, base, quote }, "FX rate provider request failed");
    res.status(502).json({ error: "The exchange-rate provider is unavailable." });
    return;
  }

  if (!providerResponse.ok) {
    req.log.warn(
      { status: providerResponse.status, base, quote },
      "FX rate provider returned an error",
    );
    res.status(502).json({ error: "The exchange-rate provider is unavailable." });
    return;
  }

  const providerData: unknown = await providerResponse.json();
  if (!Array.isArray(providerData)) {
    req.log.warn({ base, quote }, "FX rate provider returned an unexpected payload");
    res.status(502).json({ error: "The exchange-rate provider returned invalid data." });
    return;
  }

  const rates = providerData.flatMap((row: unknown) => {
    if (!row || typeof row !== "object") return [];
    const record = row as Record<string, unknown>;
    if (
      typeof record.date !== "string" ||
      typeof record.rate !== "number" ||
      !Number.isFinite(record.rate)
    ) {
      return [];
    }
    return [{ date: record.date, rate: record.rate }];
  });

  if (rates.length === 0) {
    req.log.info({ base, quote, from: fromText, to: toText }, "No FX rates found for date range");
  }

  const result = GetFxRatesResponse.parse({
    base,
    quote,
    from: fromDate,
    to: toDate,
    source: "Frankfurter · central-bank reference rates",
    rates,
  });

  res.setHeader("Cache-Control", "public, max-age=300, stale-while-revalidate=60");
  res.json(result);
});

export default router;
