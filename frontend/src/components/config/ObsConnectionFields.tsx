/**
 * Which OBS an OBS crop camera's source is in (WP13 brief §4): this PC's
 * (playout's OBS, the default) or a remote OBS on a trackside laptop, with
 * its address, port and password, and a "Test connection" button that says
 * connected / unreachable / wrong password.
 *
 * The password is write-only, like playout's: a saved one comes back as
 * "********", and that mask saved (or tested) unchanged means "keep the
 * saved one". It is never shown or put anywhere else on the page.
 */

import { useState } from "react";

import { OBS_WEBSOCKET_PORT, type ObsConnection } from "../../lib/cameras";
import { Field, NumberField, SelectField, TextField } from "./fields";

/** The answer of the `test_obs_connection` command. */
export interface ObsTestResult {
  result: "connected" | "unreachable" | "wrong_password";
  message: string;
}

interface ObsConnectionFieldsProps {
  value: ObsConnection;
  onChange: (value: ObsConnection) => void;
  /** Server validation messages, keyed like "crop.obs.host". */
  errors: Record<string, string>;
  /** Runs `test_obs_connection` with the fields as they are now. */
  test: (obs: ObsConnection) => Promise<ObsTestResult>;
  /** Called after a test that connected (the page lists that OBS's sources again). */
  onConnected: () => void;
  /** What the studio says about this camera's connection (from its state), if anything. */
  note?: string;
}

export function ObsConnectionFields({
  value,
  onChange,
  errors,
  test,
  onConnected,
  note,
}: ObsConnectionFieldsProps) {
  const [tested, setTested] = useState<ObsTestResult | null>(null);
  const [testing, setTesting] = useState(false);
  const set = (change: Partial<ObsConnection>) => {
    setTested(null); // an old answer would no longer say anything about the new fields
    onChange({ ...value, ...change });
  };

  async function runTest() {
    setTesting(true);
    try {
      const result = await test(value);
      setTested(result);
      if (result.result === "connected") onConnected();
    } catch (error) {
      setTested({ result: "unreachable", message: (error as Error).message });
    } finally {
      setTesting(false);
    }
  }

  return (
    <>
      <div className="form-grid">
        <SelectField
          label="OBS"
          value={value.remote ? "remote" : "local"}
          options={[
            { value: "local", label: "This PC (playout's OBS)" },
            { value: "remote", label: "Remote OBS (a laptop beside the camera)" },
          ]}
          help="Where the 4K webcam's source and its Crop/Pad filter are."
          error={errors["crop.obs"]}
          onChange={(choice) => set({ remote: choice === "remote" })}
        />
        {value.remote && (
          <>
            <TextField
              label="OBS address"
              value={value.host}
              placeholder="192.168.10.50"
              help="The laptop's address on the studio network."
              error={errors["crop.obs.host"]}
              onChange={(host) => set({ host })}
            />
            <NumberField
              label="OBS port"
              value={value.port}
              min={1}
              max={65535}
              help="OBS → Tools → WebSocket Server Settings (4455 by default)."
              error={errors["crop.obs.port"]}
              onChange={(port) => set({ port: port ?? OBS_WEBSOCKET_PORT })}
            />
            <Field
              label="OBS password"
              help="The WebSocket server's password; left as it is, the saved one is kept."
              error={errors["crop.obs.password"]}
            >
              {(id) => (
                <input
                  id={id}
                  type="password"
                  autoComplete="new-password"
                  value={value.password}
                  onChange={(event) => set({ password: event.target.value })}
                />
              )}
            </Field>
          </>
        )}
      </div>
      {value.remote && (
        <div className="button-row">
          <button type="button" disabled={testing} onClick={() => void runTest()}>
            {testing ? "Testing…" : "Test connection"}
          </button>
          {tested && (
            <span
              role="status"
              className={tested.result === "connected" ? "obs-test obs-test-ok" : "obs-test"}
            >
              {tested.result === "connected" ? "✓ " : "✗ "}
              {tested.message}
            </span>
          )}
        </div>
      )}
      {note && <div className="notice notice-error">{note}</div>}
    </>
  );
}
