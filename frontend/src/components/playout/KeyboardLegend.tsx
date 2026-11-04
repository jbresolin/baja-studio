import { PLAYOUT_KEY_LEGEND } from "../../lib/playout";

/** The playout keyboard shortcuts, opened with `?`. */
export function KeyboardLegend({ onClose }: { onClose: () => void }) {
  return (
    <div className="key-legend" role="dialog" aria-label="Keyboard shortcuts">
      <table className="dense-table">
        <tbody>
          {PLAYOUT_KEY_LEGEND.map(([keys, meaning]) => (
            <tr key={keys}>
              <td className="nowrap">
                <kbd>{keys}</kbd>
              </td>
              <td>{meaning}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <button onClick={onClose}>Close</button>
    </div>
  );
}
