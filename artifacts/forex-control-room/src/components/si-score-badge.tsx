type SiScoreBadgeProps = {
  score?: number | null;
};

export function SiScoreBadge({ score }: SiScoreBadgeProps) {
  if (score == null || !Number.isFinite(score)) return null;
  const normalized = Math.min(1, Math.max(0, score));
  return (
    <span
      className="si-score-badge"
      title="SYSTEM INTELLIGENCE pre-trade score"
      aria-label={`System Intelligence score ${normalized.toFixed(2)}`}
      data-testid="si-score-badge"
    >
      SI {normalized.toFixed(2)}
    </span>
  );
}
