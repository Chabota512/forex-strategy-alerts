type SwingDirectionVersion = 'V1' | 'V2';

export function SwingDirectionSwitch({
  version,
  isGold,
  onChange,
}: {
  version: SwingDirectionVersion;
  isGold: boolean;
  onChange: (version: SwingDirectionVersion) => void;
}) {
  return (
    <div
      className="swing-version-switch"
      role="group"
      aria-label="Swing direction version"
      data-testid="swing-direction-version"
    >
      <span className="swing-version-caption">VERSION</span>
      <button
        type="button"
        className={`overlay-toggle${version === 'V1' ? ' active' : ''}`}
        aria-pressed={version === 'V1'}
        onClick={() => onChange('V1')}
        data-testid="button-swing-version-v1"
      >
        VERSION 1
      </button>
      <button
        type="button"
        className={`overlay-toggle${version === 'V2' ? ' active' : ''}`}
        aria-pressed={version === 'V2'}
        disabled={!isGold}
        title={isGold ? 'Use Swing Direction V2' : 'Version 2 is available for XAUUSD only'}
        onClick={() => onChange('V2')}
        data-testid="button-swing-version-v2"
      >
        VERSION 2
      </button>
    </div>
  );
}
