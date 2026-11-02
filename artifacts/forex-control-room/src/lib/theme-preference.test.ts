import { describe, expect, it } from 'vitest';
import {
  normalizeThemePreference,
  resolveThemePreference,
} from './theme-preference';

describe('theme preference', () => {
  it('defaults missing or invalid saved values to system', () => {
    expect(normalizeThemePreference(null)).toBe('system');
    expect(normalizeThemePreference('sepia')).toBe('system');
  });

  it('keeps explicit light and dark preferences', () => {
    expect(normalizeThemePreference('light')).toBe('light');
    expect(normalizeThemePreference('dark')).toBe('dark');
  });

  it('follows the operating system only in system mode', () => {
    expect(resolveThemePreference('system', true)).toBe('dark');
    expect(resolveThemePreference('system', false)).toBe('light');
    expect(resolveThemePreference('light', true)).toBe('light');
    expect(resolveThemePreference('dark', false)).toBe('dark');
  });
});
