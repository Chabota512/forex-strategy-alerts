import './_group.css';
import { SimulationPanel } from './_shared/SimulationPanel';
import { demoSettings, demoSnapshot, demoTrades } from './_shared/mockData';

export function Current() {
  return <main className="terminal simulation-mock-page"><SimulationPanel
    symbol="XAUUSD" simulationCost={0.35} settings={demoSettings} snapshot={demoSnapshot}
    trades={demoTrades} filterScopeLabel="Simulating: all alerts · 4 of 26 alerts"
    onSettingsChange={() => undefined} sessionActive={false}
  /></main>;
}
