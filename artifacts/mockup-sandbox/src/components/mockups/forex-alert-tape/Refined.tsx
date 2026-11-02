import './_group.css';

export function Refined() {
  return (
    <div className="alert-preview-shell refined">
      <div className="right-panel">
        <div className="right-panel-tabs" role="tablist" aria-label="Right-side workstation panels">
          <button role="tab" aria-selected="true" className="active">ALERT TAPE</button>
          <button role="tab" aria-selected="false">TRADE JOURNAL</button>
        </div>
        <aside className="alert-pane">
          <div className="alert-header">
            <div><strong>ALERT TAPE</strong><span>PRESS RUN TO BEGIN REPLAY</span></div>
            <b>00</b>
          </div>
          <div className="countline" aria-label="Revealed alert counts">
            <span>BUY <b>0</b></span><span>SELL <b>0</b></span>
            <span>STR <b>0</b></span><span>NEU <b>0</b></span><span>WEAK <b>0</b></span>
          </div>
          <div className="filters">
            <div className="alert-filters-heading"><span>FILTER ALERTS</span><small>6 AVAILABLE</small></div>
            <select aria-label="Direction filter"><option>ALL DIR</option></select>
            <select aria-label="Strength filter"><option>ALL STRENGTH</option></select>
            <select aria-label="Variant filter"><option>ALL VAR</option></select>
            <select aria-label="Wick pressure filter"><option>ALL WP</option></select>
            <select aria-label="Engulfing filter"><option>ALL ENGULF</option></select>
            <button disabled>MTF ALL</button>
            <button className="filter-ai">SYSTEM INTELLIGENCE OFF</button>
            <details className="si-advanced-settings" open>
              <summary><span>SCORING OPTIONS</span><span className="si-advanced-current">EVIDENCE · 0.00 MIN</span></summary>
              <div className="si-advanced-body">
                <div className="si-score-controls">
                  <label className="si-score-threshold">
                    <span>MINIMUM SI SCORE · AUTO</span><strong>0.00</strong>
                    <input type="range" min="0" max="0.8" step="0.01" value="0" readOnly aria-label="Minimum System Intelligence score" />
                    <small>AUTO 0.00 · Evidence is still calibrating</small>
                  </label>
                  <div className="si-score-actions">
                    <button className="si-threshold-save">SET OWNER OVERRIDE</button>
                  </div>
                </div>
                <div className="si-model-controls">
                  <label><span>SI SCORE MODEL</span><select><option>EVIDENCE</option></select></label>
                  <small>OWNER MODEL NEEDS 30 MORE CHOICES</small>
                </div>
              </div>
            </details>
          </div>
          <div className="alert-filter-stats">
            <span>0 ALERTS</span><span>0 CLOSED</span><span>WR —</span>
            <span>AVG R —</span><span>EXP — · $0.00</span><span>P&amp;L +$0.00</span>
          </div>
          <div className="alert-list">
            <div className="alert-sort-head">
              <span className="alert-column-label">DIR</span><span className="alert-column-label">ALERT</span>
              <span>WP</span><span>ENG</span><span>VAR</span><span>ACTION</span>
            </div>
            <div className="tape-empty">
              <span className="tape-empty-mark" aria-hidden="true">00</span>
              <strong>NO ALERTS YET</strong>
              <p>Run a replay and start playback to populate the tape.</p>
            </div>
          </div>
        </aside>
      </div>
    </div>
  );
}
