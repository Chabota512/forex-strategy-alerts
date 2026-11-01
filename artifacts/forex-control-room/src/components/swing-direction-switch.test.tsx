import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SwingDirectionSwitch } from './swing-direction-switch';

afterEach(cleanup);

describe('SwingDirectionSwitch', () => {
  it('switches between versions for XAUUSD', () => {
    const onChange = vi.fn();
    render(<SwingDirectionSwitch version="V1" isGold onChange={onChange} />);

    fireEvent.click(screen.getByTestId('button-swing-version-v2'));

    expect(onChange).toHaveBeenCalledWith('V2');
    expect(screen.getByTestId('button-swing-version-v1').getAttribute('aria-pressed'))
      .toBe('true');
  });

  it('keeps Version 2 disabled outside XAUUSD', () => {
    const onChange = vi.fn();
    render(<SwingDirectionSwitch version="V1" isGold={false} onChange={onChange} />);

    expect((screen.getByTestId('button-swing-version-v2') as HTMLButtonElement).disabled)
      .toBe(true);
    fireEvent.click(screen.getByTestId('button-swing-version-v2'));
    expect(onChange).not.toHaveBeenCalled();
  });
});
