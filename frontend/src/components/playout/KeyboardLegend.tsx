import { PLAYOUT_KEY_LEGEND, TRANSPORT_KEY_LEGEND } from "../../lib/playout";

/** The playout keyboard shortcuts, opened with `?`; the media-control keys need a clip on air. */
export function KeyboardLegend({
  transportEnabled,
  onClose,
}: {
  transportEnabled: boolean;
  onClose: () => void;
}) {
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
          <tr>
            <th colSpan={2}>
              Media controls{" "}
              <span className="muted">
                {transportEnabled ? "(these keys act on air)" : "(with a clip on air)"}
              </span>
            </th>
          </tr>
          {TRANSPORT_KEY_LEGEND.map(([keys, meaning]) => (
            <tr key={keys} className={transportEnabled ? "" : "muted"}>
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
