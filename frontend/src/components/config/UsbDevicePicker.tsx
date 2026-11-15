/**
 * The "USB device" field of the camera form (WP6a): which USB camera on the
 * studio PC this camera is, picked from the UVC child's list.
 *
 * Several S600s share one name, so each choice also shows the end of its
 * DirectShow path (one per USB port) and which camera already uses it. A
 * saved device that is not plugged in now stays selected and says so.
 */

import { useEffect, useState } from "react";

import type { UsbDeviceRef } from "../../lib/cameras";
import { shortPath, usbDeviceLabel, type UsbState } from "../../lib/imaging";
import { SelectField } from "./fields";

interface UsbDevicePickerProps {
  cameraId: string;
  value: UsbDeviceRef | null;
  /** Required for a uvc camera (the Pixy); optional otherwise (image settings only). */
  required: boolean;
  error?: string;
  listDevices: () => Promise<UsbState>;
  onChange: (device: UsbDeviceRef | null) => void;
}

export function UsbDevicePicker(props: UsbDevicePickerProps) {
  const { cameraId, value, listDevices, onChange } = props;
  const [usb, setUsb] = useState<UsbState | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [lookCount, setLookCount] = useState(0);

  useEffect(() => {
    listDevices()
      .then((found) => {
        setUsb(found);
        setProblem(null);
      })
      .catch((error: Error) => setProblem(error.message));
  }, [listDevices, lookCount]);

  const devices = usb?.devices ?? [];
  const sameName = (name: string) => devices.filter((device) => device.name === name).length;
  const options = devices.map((device) => ({
    value: device.path,
    label:
      `${usbDeviceLabel(device, sameName(device.name))} · ${shortPath(device.path)}` +
      (device.used_by && device.used_by !== cameraId ? ` (in use by ${device.used_by})` : ""),
  }));
  const savedMissing = value !== null && !devices.some((device) => device.path === value.path);
  const choices = [
    { value: "", label: props.required ? "— pick the camera —" : "none (no image settings)" },
    ...(savedMissing && value
      ? [
          {
            value: value.path,
            label: `${value.name || shortPath(value.path)} (not plugged in now)`,
          },
        ]
      : []),
    ...options,
  ];
  const help =
    problem ??
    (usb && usb.status !== "ok"
      ? `USB cameras: ${usb.message || usb.status}`
      : devices.length === 0 && usb
        ? "No USB cameras found."
        : "Gives the camera image settings (Image page).");

  return (
    <>
      <SelectField
        label="USB device"
        value={value?.path ?? ""}
        options={choices}
        help={help}
        error={props.error}
        onChange={(path) => {
          const device = devices.find((found) => found.path === path);
          if (!path) onChange(null);
          else if (device) onChange({ path, name: device.name, index: device.index });
        }}
      />
      <div className="field">
        <button type="button" onClick={() => setLookCount((count) => count + 1)}>
          Look for USB cameras again
        </button>
      </div>
    </>
  );
}
