/**
 * Image settings of every camera (WP6a): the shapes of `state.cameras.image`
 * and `state.cameras.usb` (server/baja_studio/imaging/), names for the
 * controls, and the small rules the Image page needs.
 */

/** One control on a card: a raw device number with its range. */
export interface ImageControlView {
  value: number;
  /** true/false: on automatic or not; null: the control has no automatic mode. */
  auto: boolean | null;
  min: number;
  max: number;
  step: number;
  /** The device's default (USB cameras); null for agent cameras. */
  default: number | null;
  /** Why the last write to this control did not apply ("" when it did). */
  error: string;
}

/** `state.cameras.image.<camera id>`. */
export interface ImageCardView {
  source: "usb" | "agent";
  status: "ok" | "offline";
  status_reason: string;
  device: { name: string; path: string; present: boolean; matched_by: string } | null;
  agent: string | null;
  /** In display order. */
  controls: Record<string, ImageControlView>;
}

/** One USB camera plugged into the studio PC, as the device picker lists it. */
export interface UsbDeviceView {
  name: string;
  path: string;
  /** Its place among devices with the same name (0 first). */
  index: number;
  used_by: string | null;
}

/** `state.cameras.usb`, and the answer of `list_usb_devices`. */
export interface UsbState {
  status: "starting" | "ok" | "unavailable" | "offline";
  message: string;
  devices: UsbDeviceView[];
}

/** One control change as `set_image` takes it. */
export interface ImageChange {
  value?: number;
  auto?: boolean;
}

const CONTROL_LABELS: Record<string, string> = {
  exposure: "Exposure",
  gain: "Gain",
  white_balance: "White balance",
  focus: "Focus",
  iris: "Iris",
  brightness: "Brightness",
  contrast: "Contrast",
  saturation: "Saturation",
  sharpness: "Sharpness",
  gamma: "Gamma",
  hue: "Hue",
  backlight_compensation: "Backlight compensation",
};

/** "White balance" for "white_balance"; unknown names are shown with spaces. */
export function controlLabel(name: string): string {
  return CONTROL_LABELS[name] ?? name.replaceAll("_", " ");
}

/** All auto: every control that has an automatic mode goes back to it. */
export function allAutoChanges(
  controls: Record<string, ImageControlView>,
): Record<string, ImageChange> {
  return Object.fromEntries(
    Object.entries(controls)
      .filter(([, control]) => control.auto !== null)
      .map(([name]) => [name, { auto: true }]),
  );
}

/** How a device reads in the picker: "EMEET SmartCam S600 #2", the number only when needed. */
export function usbDeviceLabel(device: { name: string; index: number }, sameName: number): string {
  return sameName > 1 ? `${device.name} #${device.index + 1}` : device.name;
}

/** The end of a DirectShow path, enough to tell two USB ports apart on screen. */
export function shortPath(path: string): string {
  const parts = path.split("#");
  return parts.length > 2 ? (parts[2] ?? path) : path;
}

/**
 * Sender for a slider being dragged: one write in flight per control. The
 * first value goes at once; values that come while it is in flight replace
 * each other, and when its reply comes only the latest is sent. So the
 * camera follows as fast as it can answer (a real UVC write can take most
 * of a second), the server never queues a backlog of old values, and they
 * can never be applied out of order. The last value is always sent.
 */
export function createLatestSender<T>(send: (value: T) => Promise<unknown>): {
  push: (value: T) => void;
  cancel: () => void;
} {
  let inFlight = false;
  let stopped = false;
  let pending: { value: T } | null = null;

  const next = () => {
    if (stopped || pending === null) {
      inFlight = false;
      return;
    }
    const { value } = pending;
    pending = null;
    inFlight = true;
    send(value).then(next, next);
  };

  return {
    push(value: T) {
      pending = { value };
      if (!inFlight) next();
    },
    cancel() {
      stopped = true;
      pending = null;
    },
  };
}
