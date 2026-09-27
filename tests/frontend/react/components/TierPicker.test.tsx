/**
 * TierPicker: Hook-Reihenfolge, Tastaturbedienung, beide Varianten.
 *
 * 🛑 Der wichtigste Test hier ist `it('survives a switch from pills to compact')`.
 * Er haelt den Fehler fest, der die Komponente bis 2026-09-27 latent kaputt
 * machte: `useRef` und `useEffect` standen HINTER dem vorzeitigen `return` fuer
 * `variant === 'compact'`. React fuehrt Hooks ueber die AUFRUFREIHENFOLGE — eine
 * Instanz, die die Variante wechselt, ruft dann zwei Hooks weniger auf und React
 * wirft „Rendered fewer hooks than expected".
 *
 * Latent blieb das nur, weil alle vier Aufrufstellen die Variante als Literal
 * setzen. Genau deshalb braucht es diesen Test: ein Fehler, den keine bestehende
 * Aufrufstelle ausloest, verschwindet sonst wieder, sobald jemand die Variante
 * an einen Zustand haengt.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import TierPicker from '../../../../src/frontend/src/components/TierPicker';

describe('TierPicker', () => {
  it('renders all five tiers as pills', () => {
    render(<TierPicker value={2} onChange={vi.fn()} />);
    expect(screen.getAllByRole('radio')).toHaveLength(5);
  });

  it('renders a select in the compact variant', () => {
    render(<TierPicker value={1} onChange={vi.fn()} variant="compact" />);
    const select = screen.getByRole('combobox') as HTMLSelectElement;
    expect(select.value).toBe('1');
    expect(select.querySelectorAll('option')).toHaveLength(5);
  });

  it('survives a switch from pills to compact', () => {
    // Der eigentliche Regressionswaechter. Vor der Korrektur wirft das
    // Neu-Rendern „Rendered fewer hooks than expected", weil die compact-Variante
    // vor `useRef`/`useEffect` zurueckkehrte.
    const { rerender } = render(<TierPicker value={2} onChange={vi.fn()} />);
    expect(screen.getAllByRole('radio')).toHaveLength(5);

    expect(() =>
      rerender(<TierPicker value={2} onChange={vi.fn()} variant="compact" />),
    ).not.toThrow();
    expect(screen.getByRole('combobox')).toBeInTheDocument();

    // Und zurueck — die Gegenrichtung ruft ZWEI Hooks MEHR auf, was React
    // ebenso bemerkt.
    expect(() =>
      rerender(<TierPicker value={3} onChange={vi.fn()} variant="pills" />),
    ).not.toThrow();
    expect(screen.getAllByRole('radio')).toHaveLength(5);
  });

  it('moves the selection with the arrow keys', () => {
    const onChange = vi.fn();
    render(<TierPicker value={2} onChange={onChange} />);
    fireEvent.keyDown(screen.getAllByRole('radio')[2], { key: 'ArrowRight' });
    expect(onChange).toHaveBeenCalledWith(3);
  });

  it('does not move when disabled', () => {
    const onChange = vi.fn();
    render(<TierPicker value={2} onChange={onChange} disabled />);
    fireEvent.keyDown(screen.getAllByRole('radio')[2], { key: 'ArrowRight' });
    expect(onChange).not.toHaveBeenCalled();
  });

  it('clamps at both ends of the ladder', () => {
    const onChange = vi.fn();
    const { rerender } = render(<TierPicker value={0} onChange={onChange} />);
    fireEvent.keyDown(screen.getAllByRole('radio')[0], { key: 'ArrowLeft' });
    expect(onChange).toHaveBeenCalledWith(0);

    onChange.mockClear();
    rerender(<TierPicker value={4} onChange={onChange} />);
    fireEvent.keyDown(screen.getAllByRole('radio')[4], { key: 'ArrowRight' });
    expect(onChange).toHaveBeenCalledWith(4);
  });
});
