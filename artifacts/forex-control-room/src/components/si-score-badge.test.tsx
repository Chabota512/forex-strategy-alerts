import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { SiScoreBadge } from './si-score-badge';

afterEach(cleanup);

describe('System Intelligence score badge', () => {
  it('renders a bounded score to two decimal places', () => {
    render(<SiScoreBadge score={0.734} />);
    expect(screen.getByTestId('si-score-badge').textContent).toBe('SI 0.73');
    expect(screen.getByLabelText('System Intelligence score 0.73')).toBeTruthy();
  });

  it('omits the badge when no score is available', () => {
    const { container } = render(<SiScoreBadge score={null} />);
    expect(container.firstChild).toBeNull();
  });
});
